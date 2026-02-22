"""뉴스 선정 및 필터링 모듈 - 출처 다양성 + 품질 기반 선정 + 내용적 점수 + 중복 제거"""

import logging
import re
from collections import defaultdict
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.collector.content_scorer import ContentScorer

logger = logging.getLogger(__name__)


# ============================================================
# 제목 유사도 기반 중복 제거
# ============================================================

# 중복 판정에서 무시할 일반 단어 (stopwords)
TITLE_STOPWORDS = {
    '的', '了', '在', '是', '与', '和', '或', '等', '将', '被', '对', '为',
    '已', '正', '可', '也', '都', '又', '再', '更', '最', '这', '那', '有',
    '中', '上', '下', '内', '外', '前', '后', '新', '大', '小', '多', '少',
    '今日', '今天', '昨日', '昨天', '本周', '本月', '今年', '去年',
    '据悉', '据称', '据报道', '消息', '快讯', '速递', '盘中必读',
    '推出', '发布', '宣布', '公布', '表示', '称', '显示', '报告',
}

# 핵심 주제어 (높은 가중치 부여)
CORE_TOPIC_KEYWORDS = [
    # 기관/거래소
    '沪深', '交易所', '北交所', '上交所', '深交所', '证监会', '央行',
    '发改委', '工信部', '财政部', '商务部', '国资委',
    # 금융 용어
    'IPO', '再融资', '并购', '重组', '增发', '配股', '减持', '增持',
    '融资', '债券', '股票', '基金', 'ETF',
    # 산업
    'AI', '人工智能', '芯片', '半导体', '新能源', '光伏', '电池',
    '汽车', '航天', '航空', '机器人', '量子', '5G', '6G',
    # 기업명 패턴
    '特斯拉', '华为', '腾讯', '阿里', '百度', '比亚迪', '宁德时代',
]

# 중복 판정 임계값 (0.0 ~ 1.0, 높을수록 엄격)
SIMILARITY_THRESHOLD = 0.4  # 낮춰서 더 많은 중복 감지


def extract_title_keywords(title: str) -> set:
    """제목에서 핵심 키워드 추출 (중복 판정용).

    - 핵심 주제어 우선 추출
    - 숫자+단위 패턴 보존 (예: 900亿, 77股, 4100点)
    - 2글자 키워드만 추출 (간결하게)
    """
    if not title:
        return set()

    words = set()

    # 1. 핵심 주제어 추출 (가장 중요)
    for keyword in CORE_TOPIC_KEYWORDS:
        if keyword in title:
            words.add(keyword)

    # 2. 영문 단어 추출 (대문자 변환)
    english_words = re.findall(r'[A-Za-z]{2,}', title)
    words.update(w.upper() for w in english_words)

    # 3. 숫자+단위 패턴 추출 (중요한 식별자)
    num_patterns = re.findall(r'\d+(?:\.\d+)?[亿万兆元%股点个家条项]?', title)
    words.update(p for p in num_patterns if len(p) >= 2)

    # 4. 중국어 2글자 키워드 추출 (핵심만)
    chinese_text = re.sub(r'[^\u4e00-\u9fff]', '', title)
    for i in range(len(chinese_text) - 1):
        word = chinese_text[i:i+2]
        if word not in TITLE_STOPWORDS:
            words.add(word)

    # stopwords 제거
    words = {w for w in words if w not in TITLE_STOPWORDS and len(w) >= 2}

    return words


def calculate_title_similarity(title1: str, title2: str) -> float:
    """두 제목 간 유사도 계산 (가중 Jaccard similarity).

    핵심 주제어 일치 시 가중치 부여.

    Returns:
        0.0 ~ 1.0 (1.0 = 완전 동일)
    """
    keywords1 = extract_title_keywords(title1)
    keywords2 = extract_title_keywords(title2)

    if not keywords1 or not keywords2:
        return 0.0

    intersection = keywords1 & keywords2
    union = keywords1 | keywords2

    if not union:
        return 0.0

    # 기본 Jaccard 유사도
    base_sim = len(intersection) / len(union)

    # 핵심 주제어 일치 보너스
    core_matches = sum(1 for kw in intersection if kw in CORE_TOPIC_KEYWORDS or len(kw) >= 3)
    if core_matches >= 2:
        # 핵심 주제어 2개 이상 일치 시 유사도 증가
        base_sim = min(base_sim * 1.5, 1.0)
    elif core_matches >= 1:
        base_sim = min(base_sim * 1.2, 1.0)

    return base_sim


def is_duplicate_title(title: str, existing_titles: list, threshold: float = SIMILARITY_THRESHOLD) -> tuple:
    """기존 제목들과 중복 여부 판정.

    Args:
        title: 검사할 제목
        existing_titles: 기존 제목 리스트
        threshold: 유사도 임계값

    Returns:
        (is_duplicate, matched_title, similarity)
    """
    for existing in existing_titles:
        similarity = calculate_title_similarity(title, existing)
        if similarity >= threshold:
            return (True, existing, similarity)
    return (False, None, 0.0)


def load_processed_titles() -> list:
    """DB에서 처리된 뉴스 제목 로드 (스킵/폐기/리뷰완료).

    Returns:
        중복 제거 대상 제목 리스트
    """
    try:
        from src.database.models import get_connection
        conn = get_connection()
        cursor = conn.cursor()

        # 1. 스킵된 뉴스 제목
        cursor.execute("""
            SELECT original_title FROM news
            WHERE expert_review_status = 'skipped'
        """)
        skipped = [row['original_title'] for row in cursor.fetchall()]

        # 2. 폐기된 뉴스 제목 (expert_reviews.publish_status = 'discarded')
        cursor.execute("""
            SELECT n.original_title FROM news n
            JOIN expert_reviews er ON n.id = er.news_id
            WHERE er.publish_status IN ('discarded', 'rejected')
        """)
        discarded = [row['original_title'] for row in cursor.fetchall()]

        # 3. 리뷰 완료된 뉴스 제목 (published, draft 포함)
        cursor.execute("""
            SELECT n.original_title FROM news n
            JOIN expert_reviews er ON n.id = er.news_id
            WHERE er.publish_status IN ('published', 'draft')
        """)
        reviewed = [row['original_title'] for row in cursor.fetchall()]

        conn.close()

        all_titles = skipped + discarded + reviewed
        logger.info(f"중복 제거 대상 로드: 스킵 {len(skipped)}, 폐기 {len(discarded)}, 리뷰완료 {len(reviewed)}")

        return all_titles

    except Exception as e:
        logger.error(f"처리된 제목 로드 실패: {e}")
        return []

# 내용적 점수 평가기 (모듈 레벨 싱글턴)
_content_scorer = ContentScorer()

EXCLUDED_KEYWORDS = ['论评', '专栏', '社论', '观点', '评论', '投稿', '广告', 'PR', '新闻稿', '赞助', '专题', '访谈', '座谈', '论坛', '活动', '开幕']
DATA_PATTERNS = [r'\d+%', r'\d+亿', r'\d+万', r'\d+兆', r'\d+元', r'\d+\.\d+%']
CONCRETE_KEYWORDS = ['发布', '公布', '统计', '数据', '报告', '政策', '措施', '方案', '规定', '条例', '增长', '下降', '上涨', '下跌', '同比', '环比']

# 정부 행정 뉴스 제외 키워드
GOVERNMENT_ADMIN_KEYWORDS = [
    '人事任免', '干部', '党委', '组织部', '纪委',
    '关于印发', '办公厅关于', '工作方案', '管理办法',
    '人民政府办公', '通知如下', '现印发给你们'
]

# 단신 뉴스 제외 패턴 (기업 단순 발표, 외국 기업 뉴스)
BRIEF_NEWS_PATTERNS = [
    r'现代汽车.*计划', r'丰田.*计划', r'本田.*计划',  # 외국 자동차 기업 단신
    r'(现代|丰田|本田|日产|大众|通用|福特).*投资.*[万亿韩元|美元|欧元]',
    r'.*：对公司.*产品售价.*调整',  # 기업 가격 조정 공지
    r'.*拟.*收购.*深交所问询',  # 단순 거래소 문의
]

# 지방정부 출처
LOCAL_GOV_SOURCES = ['beijing_gov', 'shanghai_gov', 'shenzhen_gov', 'bbtnews', 'sznews']

# 출처별 최대 선정 건수 제한 (지정하지 않은 출처는 balance_categories의 기본 로직 적용)
SOURCE_MAX_COUNT = {
    # 편중 방지: 에디션당 최대 2건 (볼륨 큰 소스)
    'cls':          2,
    '36kr':         2,
    '21jingji':     2,
    'jiemian':      2,
    'yicai':        2,
    'stdaily':      2,
    'xinhua_finance': 2,
    'sina_finance': 2,
    # 지방 소스 제한
    'shenzhen_gov': 1,
    'bbtnews':      1,
    'sznews':       1,
}

# 중앙 미디어/기관 출처 (중앙정부 포함 — 중앙 보너스 +5 대상)
CENTRAL_SOURCES = [
    'people', 'ce', '36kr', 'stcn',
    'cls', 'jiemian', 'yicai', 'sina_finance',
    '21jingji', 'xinhua_finance',
    # Week 6 전국 매체
    'stdaily', 'cnstock',
    # Week 7 중앙 언론
    'guangming', 'chinadaily', 'jjckb', 'workercn', 'cyol',
]

# 중앙정부 출처 (현재 비활성 — 향후 재추가 시 사용)
CENTRAL_GOV_SOURCES = []

CATEGORIES = {
    '정책': ['政策', '政府', '通知', '规划', '强制', '意见'],
    '거시경제': ['经济', '增长', '消费', '投资', '货币', '利率', '储蓄', '人口', '劳动', '出口', '进口', '贸易', '一带一路'],
    '산업': ['制造', '产业', '工业', '上游', '下游', '开发区', '产业园区'],
    '에너지': ['能源', '电力', '电池', '新能源', '太阳能', '光伏', '氢能', '核能', '核聚变', '钍能', '风能', '风电', '地热'],
    '금융': ['银行', '金融', '融资', '股票', '债券', '证券', '上市'],
    '기업': ['企业', '公司', '股', '高管', '并购', '股东', '项目'],
    '과학기술': ['技术', '科技', 'AI', '机器人', '无人机', '智能制造', '生物', '自动驾驶', '超算', '量子', '航天', '新材料', '6G', '5G', '3D打印'],
    '대외경제': ['中美', '中欧', '中日', '中韩', '中俄', '对华', '贸易战', '关税', '出口管制', '外贸', '双边', 'RCEP', '一带一路', '人民币汇率'],
}

# ============================================================
# 중국 대외경제 뉴스 판별
# ============================================================

# 명시적 중국 양자 관계 키워드 (Level 1 — 확실한 대외경제)
CHINA_BILATERAL_EXPLICIT = [
    '中美', '中欧', '中日', '中韩', '中俄', '中澳', '中印',
    '对华', '涉华',
]

# 무역·관세 키워드 (Level 2 — 중국 맥락 있을 때만)
CHINA_TRADE_KEYWORDS = ['贸易战', '出口管制', '关税', '外贸', '进出口', '双边贸易']

# Level 2에서 중국 맥락 확인용 키워드
CHINA_CONTEXT_SIGNALS = [
    '中国', '中方', '中国出口', '中国进口', '中国经济',
    '人民币', 'A股', '中国商品',
]

# 순수 해외 뉴스 신호 (중국 무관)
PURE_FOREIGN_SIGNALS = [
    'OpenAI', 'Anthropic', 'SpaceX',
    '美股收盘', '美股三大指数', '纳斯达克', '标普500', '道琼斯',
    '华尔街',
]


def is_china_external_economic(title: str, content: str) -> bool:
    """중국 대외경제 뉴스 여부 판별.

    Level 1: 제목 또는 본문에 명시적 양자 관계 키워드(中美, 中欧, 对华 등)가 있으면 True.
    Level 2: 제목에 무역·관세 키워드가 있으면 True
             (중국 경제 미디어가 관세/무역전쟁을 제목에 올린 것 = 중국 경제 관련성 신호).
    순수 해외 기업/시장 뉴스(OpenAI, 미국 주가 등)는 False.
    """
    combined = title + content

    # 순수 해외 신호가 1개 이상이면 조기 제외
    if any(kw in combined for kw in PURE_FOREIGN_SIGNALS):
        return False

    # Level 1: 명시적 양자 관계 (제목 or 본문)
    if any(kw in combined for kw in CHINA_BILATERAL_EXPLICIT):
        return True

    # Level 2: 제목에 무역·관세 키워드 포함 (본문만 있는 경우 제외)
    return any(kw in title for kw in CHINA_TRADE_KEYWORDS)

# 출처별 기본 우선순위 (중앙 미디어 > 지방정부)
SOURCE_PRIORITY = {
    'people': 11,
    'ce': 10,
    'stcn': 9,
    '36kr': 8,
    'beijing_gov': 4,
    'shanghai_gov': 4,
    'shenzhen_gov': 3,
    'cls': 9,
    'jiemian': 8,
    'yicai': 10,
    'sina_finance': 8,
    '21jingji': 9,
    'xinhua_finance': 10,
    # Week 6 지방 언론
    'bbtnews': 6,
    'stdaily': 9,
    'cnstock': 9,
    'sznews': 5,
    # Week 7 중앙 언론
    'guangming': 9,    # 중앙 당보
    'chinadaily': 9,   # 중앙 대외 매체
    'jjckb': 10,       # 경제 전문 (신화사 산하)
    'workercn': 8,     # 중앙 사회매체
    'cyol': 8,         # 중앙 사회매체
}

# 사실 풍부도 관련 키워드
FACT_RICH_KEYWORDS = [
    '数据显示', '统计', '报告', '调查', '研究', '分析',
    '同比', '环比', '增长', '下降', '达到', '突破',
    '第一', '首次', '创新高', '创新低', '历史',
    '全国', '全球', '行业', '市场', '规模'
]

# 범위 관련 키워드 (넓은 뉴스)
BROAD_SCOPE_KEYWORDS = [
    '全国', '全球', '国际', '行业', '市场', '宏观',
    '政策', '战略', '规划', '改革', '转型'
]

# 심층 분석 키워드
DEEP_ANALYSIS_KEYWORDS = [
    '深度', '分析', '解读', '专访', '独家', '调研',
    '背后', '原因', '影响', '趋势', '展望'
]


def is_brief_news(title: str, content: str) -> bool:
    """단신 뉴스 여부 판단"""
    combined = title + content
    for pattern in BRIEF_NEWS_PATTERNS:
        if re.search(pattern, combined):
            return True
    # 제목이 너무 짧고 내용도 짧으면 단신
    if len(title) < 20 and len(content) < 100:
        return True
    return False


def calculate_fact_richness(title: str, content: str) -> int:
    """사실 풍부도 점수 계산 (-10 ~ +20)"""
    combined = title + content
    score = 0

    # 데이터 패턴 개수 (각 +3점)
    data_count = sum(1 for p in DATA_PATTERNS if re.search(p, combined))
    score += min(data_count * 3, 12)  # 최대 12점

    # 사실 풍부 키워드 (각 +2점)
    fact_count = sum(1 for kw in FACT_RICH_KEYWORDS if kw in combined)
    score += min(fact_count * 2, 8)  # 최대 8점

    # 내용 길이 보너스
    if len(content) > 500:
        score += 3
    elif len(content) > 250:
        score += 1
    elif len(content) < 100:
        score -= 5  # 내용이 너무 짧으면 감점

    # 제목만 있고 내용이 거의 없으면 감점
    if len(content) < 50:
        score -= 10

    return score


def calculate_scope_score(title: str, content: str) -> tuple:
    """범위 점수 계산 (넓은 뉴스 vs 심층 뉴스)
    Returns: (scope_score, is_broad)
    - scope_score: 정렬용 점수 (넓은 뉴스가 높음)
    - is_broad: True면 넓은 뉴스, False면 심층 뉴스
    """
    combined = title + content

    broad_count = sum(1 for kw in BROAD_SCOPE_KEYWORDS if kw in combined)
    deep_count = sum(1 for kw in DEEP_ANALYSIS_KEYWORDS if kw in combined)

    # 넓은 뉴스면 높은 점수, 심층 뉴스면 낮은 점수 (정렬 시 넓은 것이 앞으로)
    if broad_count > deep_count:
        return (10 + broad_count, True)
    elif deep_count > broad_count:
        return (5 - deep_count, False)
    else:
        return (7, True)  # 중립


def is_factual_news(title: str, content: str, source: str = "") -> bool:
    """사실 뉴스인지 판단.

    중앙정부 출처(CENTRAL_GOV_SOURCES)는 행정 키워드 필터를 면제한다.
    정책 발표문이 '关于印发', '办公厅关于' 등의 패턴으로 필터링되는 것을 방지.

    EXCLUDED_KEYWORDS는 제목만 검사한다.  본문에는 크롤링 시 혼입된
    웹 페이지 크롬(광고, 댓글란, 스폰서 표기 등)이 포함되어 있어
    '广告', '评论', '活动' 등이 정상 기사를 오탈락시키는 문제가 있었다.
    """
    # 논설/칼럼 제외 — 제목만 검사 (본문 크롬 오탈락 방지)
    if any(kw in title for kw in EXCLUDED_KEYWORDS):
        return False

    # 정부 행정 공지 제외 — 제목만 검사, 중앙정부 출처는 면제
    if source not in CENTRAL_GOV_SOURCES:
        if any(kw in title for kw in GOVERNMENT_ADMIN_KEYWORDS):
            return False

    return True


def has_analytical_value(title: str, content: str, source: str = "") -> bool:
    """분석 가치 판단.

    중앙정부 출처는 '印发+办公' 통지문 필터를 면제한다.
    """
    combined = title + content

    # 정부 단순 통지문 제외 — 중앙정부 출처는 면제
    if source not in CENTRAL_GOV_SOURCES:
        if '印发' in title and '办公' in title:
            return False

    if any(re.search(p, combined) for p in DATA_PATTERNS):
        return True
    if sum(1 for kw in CONCRETE_KEYWORDS if kw in combined) >= 2:
        return True
    return len(title) > 15


def is_domestic_news(title: str, content: str) -> bool:
    """중국 국내 뉴스 판단.

    foreign 키워드가 있으면서 domestic 키워드가 없으면 해외 뉴스로 판정.
    양쪽 다 있으면 domestic 키워드가 foreign 이상이어야 국내 뉴스.
    양쪽 다 없으면 (중국 매체 기사이므로) 국내 뉴스로 기본 판정.
    """
    combined = title + content

    foreign_keywords = [
        # 국가/지역
        '美国', '欧洲', '日本', '韩国', '东南亚', '印度',
        # 해외 시장/통화
        '美股', '美元', '欧元', '日元', '韩元',
        '华尔街', '纳斯达克', '标普', '道琼斯',
        # 해외 기업
        'Anthropic', 'OpenAI', 'Google', 'Microsoft', 'Apple',
        'Amazon', 'Meta', 'Nvidia', 'Tesla', 'Samsung',
        '谷歌', '微软', '苹果公司', '亚马逊', '英伟达',
    ]

    domestic_keywords = [
        # 국가
        '中国', '国内', '本土', '内地',
        # 정부/기관
        '央行', '发改委', '工信部', '财政部', '商务部',
        '国务院', '证监会', '银保监', '国资委',
        # 시장
        'A股', '沪深', '上证', '深证', '北交所', '创业板',
        '沪指', '深成指', '科创板', '人民币',
    ]

    foreign = sum(1 for kw in foreign_keywords if kw in combined)
    domestic = sum(1 for kw in domestic_keywords if kw in combined)

    # 해외 키워드만 있고 국내 맥락 없으면 해외 뉴스
    if foreign > 0 and domestic == 0:
        return False

    # 양쪽 다 있으면 국내 키워드가 해외 이상이어야 국내 뉴스
    return domestic >= foreign or foreign == 0


def is_local_gov_source(source: str) -> bool:
    """지방정부 출처 여부"""
    return source in LOCAL_GOV_SOURCES


def categorize_news(title: str, content: str) -> str:
    """카테고리 분류"""
    combined = title + content
    scores = defaultdict(int)
    for category, keywords in CATEGORIES.items():
        scores[category] = sum(1 for kw in keywords if kw in combined)
    return max(scores.items(), key=lambda x: x[1])[0] if scores else '기타'


def filter_news(news_list: list, enable_dedup: bool = True) -> list:
    """뉴스 필터링 (중복 제거 포함).

    Args:
        news_list: 필터링할 뉴스 리스트
        enable_dedup: 중복 제거 활성화 여부 (기본: True)
    """
    filtered = []

    # 중복 제거를 위한 기존 제목 로드
    if enable_dedup:
        processed_titles = load_processed_titles()
        batch_titles = []  # 현재 배치 내 선정된 제목 (배치 내 중복 방지)
        dedup_count = 0
    else:
        processed_titles = []
        batch_titles = []

    for news in news_list:
        title = news.get('original_title', '')
        content = news.get('original_content', '') or ''
        source = news.get('source', '')

        # 원문 본문이 없는 뉴스는 사용자에게 전달 불가 → 선정 제외
        if not content.strip():
            continue

        if not is_factual_news(title, content, source):
            continue
        if not has_analytical_value(title, content, source):
            continue

        # 단신 뉴스 제외
        if is_brief_news(title, content):
            continue

        # 해외 뉴스 제외 (단, 중국 대외경제 뉴스는 면제)
        is_external_eco = is_china_external_economic(title, content)
        if not is_domestic_news(title, content) and not is_external_eco:
            continue

        # === 중복 제거 ===
        if enable_dedup:
            # 1. 기존 처리된 뉴스와 중복 체크 (스킵/폐기/리뷰완료)
            is_dup, matched, sim = is_duplicate_title(title, processed_titles)
            if is_dup:
                logger.info(f"중복 제외 (기존): [{news.get('id')}] {title[:30]}... ↔ {matched[:30]}... ({sim:.2f})")
                dedup_count += 1
                continue

            # 2. 현재 배치 내 중복 체크
            is_dup, matched, sim = is_duplicate_title(title, batch_titles)
            if is_dup:
                logger.info(f"중복 제외 (배치): [{news.get('id')}] {title[:30]}... ↔ {matched[:30]}... ({sim:.2f})")
                dedup_count += 1
                continue

            # 배치에 현재 제목 추가
            batch_titles.append(title)

        news['category'] = categorize_news(title, content)
        news['is_domestic'] = is_domestic_news(title, content)
        news['is_external_eco'] = is_external_eco
        news['is_local_gov'] = is_local_gov_source(source)

        # 사실 풍부도 점수
        fact_score = calculate_fact_richness(title, content)
        news['fact_richness'] = fact_score

        # 범위 점수 (넓은 vs 심층)
        scope_score, is_broad = calculate_scope_score(title, content)
        news['scope_score'] = scope_score
        news['is_broad'] = is_broad

        # 출처별 기본 우선순위
        source_score = SOURCE_PRIORITY.get(source, 5)

        # 중앙 vs 지방 보너스 (중앙 +5)
        central_bonus = 5 if source in CENTRAL_SOURCES else 0

        # 국내 뉴스 보너스
        domestic_bonus = 6 if news['is_domestic'] else 0

        # 대외경제 보너스 (관세·무역전쟁 등 중국 대외경제 뉴스 우선 반영)
        external_eco_bonus = 8 if is_external_eco else 0

        # 형식적 기준 점수 (기존)
        formal_score = source_score + central_bonus + domestic_bonus + fact_score + external_eco_bonus

        # 내용적 기준 점수 (Content-Based Scoring)
        content_result = _content_scorer.score(title, content, source)
        news['content_score'] = content_result['total_score']
        news['content_breakdown'] = content_result['breakdown']
        news['content_explanation'] = content_result['explanation']

        # 종합 우선순위 점수: 형식적 기준(40%) + 내용적 기준(60%)
        # formal을 0~100으로 정규화 (경험적 최대값 40 기준), content는 이미 0~100
        FORMAL_MAX = 40
        formal_normalized = min(formal_score / FORMAL_MAX, 1.0) * 100
        news['formal_normalized'] = formal_normalized
        news['priority_score'] = formal_normalized * 0.40 + content_result['total_score'] * 0.60

        filtered.append(news)

    if enable_dedup and dedup_count > 0:
        logger.info(f"중복 제거 완료: {dedup_count}건 제외, {len(filtered)}건 통과")

    return filtered


def _exceeds_source_cap(source: str, current_count: int) -> bool:
    """출처별 최대 건수 제한 초과 여부 확인."""
    cap = SOURCE_MAX_COUNT.get(source)
    if cap is not None and current_count >= cap:
        return True
    return False


def balance_categories(news_list: list, target_count: int = 10, max_local_gov: int = 1) -> list:
    """카테고리 + 출처 균형 선정 (지방정부 제한, 출처별 상한, 중요도순 정렬)"""
    by_category = defaultdict(list)
    by_source = defaultdict(int)
    local_gov_count = 0

    for news in news_list:
        category = news.get('category', '기타')
        by_category[category].append(news)

    # 카테고리별 정렬 (우선순위 점수 기준)
    for cat in by_category:
        by_category[cat].sort(
            key=lambda x: (x.get('priority_score', 0), x.get('published_at', '')),
            reverse=True
        )

    selected = []
    main_categories = ['과학기술', '산업', '에너지', '기업', '금융', '정책', '거시경제']

    # 1단계: 각 카테고리에서 1개씩 (출처 중복 최소화, 지방정부 제한)
    for category in main_categories:
        if by_category[category]:
            for news in by_category[category]:
                source = news.get('source', '')
                is_local = news.get('is_local_gov', False)

                # 출처별 최대 건수 제한 체크
                if _exceeds_source_cap(source, by_source.get(source, 0)):
                    continue

                # 지방정부 뉴스 제한 체크
                if is_local and local_gov_count >= max_local_gov:
                    continue

                # 같은 출처에서 이미 2개 이상 선정했으면 스킵
                if by_source.get(source, 0) < 2:
                    selected.append(news)
                    by_source[source] = by_source.get(source, 0) + 1
                    if is_local:
                        local_gov_count += 1
                    by_category[category].remove(news)
                    break

            if len(selected) >= target_count:
                break

    # 2단계: 남은 슬롯 채우기 (출처 다양성 유지, 지방정부 제한)
    if len(selected) < target_count:
        remaining = []
        for cat_news in by_category.values():
            remaining.extend(cat_news)

        remaining.sort(
            key=lambda x: (x.get('priority_score', 0), x.get('published_at', '')),
            reverse=True
        )

        for news in remaining:
            if len(selected) >= target_count:
                break
            source = news.get('source', '')
            is_local = news.get('is_local_gov', False)

            # 출처별 최대 건수 제한 체크
            if _exceeds_source_cap(source, by_source.get(source, 0)):
                continue

            # 지방정부 뉴스 제한 체크
            if is_local and local_gov_count >= max_local_gov:
                continue

            # 같은 출처에서 3개 이상 선정 방지
            if by_source.get(source, 0) < 3:
                selected.append(news)
                by_source[source] = by_source.get(source, 0) + 1
                if is_local:
                    local_gov_count += 1

    # 최종 정렬: 중앙>지방, 넓은>심층, 중요도순
    selected.sort(
        key=lambda x: (
            0 if x.get('is_local_gov', False) else 1,  # 중앙 뉴스 먼저
            x.get('scope_score', 0),                    # 넓은 뉴스 먼저
            x.get('priority_score', 0)                  # 중요도순
        ),
        reverse=True
    )

    return selected[:target_count]

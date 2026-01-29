"""뉴스 선정 및 필터링 모듈 - 출처 다양성 + 품질 기반 선정"""

import re
from collections import defaultdict

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
LOCAL_GOV_SOURCES = ['beijing_gov', 'shanghai_gov', 'shenzhen_gov']

# 중앙 미디어/기관 출처
CENTRAL_SOURCES = ['xinhua', 'people', 'ce', 'caixin', '36kr', 'stcn', 'huxiu']

CATEGORIES = {
    '정책': ['政策', '政府', '通知', '规划', '强制', '意见'],
    '거시경제': ['经济', '增长', '消费', '投资', '货币', '利率', '储蓄', '人口', '劳动', '出口', '进口', '贸易', '一带一路'],
    '산업': ['制造', '产业', '工业', '上游', '下游', '开发区', '产业园区'],
    '에너지': ['能源', '电力', '电池', '新能源', '太阳能', '光伏', '氢能', '核能', '核聚变', '钍能', '风能', '风电', '地热'],
    '금융': ['银行', '金融', '融资', '股票', '债券', '证券', '上市'],
    '기업': ['企业', '公司', '股', '高管', '并购', '股东', '项目'],
    '기술': ['技术', '科技', 'AI', '机器人', '无人机', '智能制造', '生物', '自动驾驶', '超算', '量子', '航天', '新材料', '6G', '5G', '3D打印']
}

# 출처별 기본 우선순위 (중앙 미디어 > 지방정부)
SOURCE_PRIORITY = {
    'xinhua': 12,
    'people': 11,
    'caixin': 10,
    'ce': 10,
    'stcn': 9,
    '36kr': 8,
    'huxiu': 8,
    'beijing_gov': 4,
    'shanghai_gov': 4,
    'shenzhen_gov': 4
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
    if len(title) < 20 and len(content) < 200:
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
    if len(content) > 1000:
        score += 3
    elif len(content) > 500:
        score += 1
    elif len(content) < 200:
        score -= 5  # 내용이 너무 짧으면 감점

    # 제목만 있고 내용이 거의 없으면 감점
    if len(content) < 100:
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


def is_factual_news(title: str, content: str) -> bool:
    """사실 뉴스인지 판단"""
    combined = title + content

    # 논설/칼럼 제외
    if any(kw in combined for kw in EXCLUDED_KEYWORDS):
        return False

    # 정부 행정 공지 제외
    if any(kw in combined for kw in GOVERNMENT_ADMIN_KEYWORDS):
        return False

    return True


def has_analytical_value(title: str, content: str) -> bool:
    """분석 가치 판단"""
    combined = title + content

    # 정부 단순 통지문 제외
    if '印发' in title and '办公' in title:
        return False

    if any(re.search(p, combined) for p in DATA_PATTERNS):
        return True
    if sum(1 for kw in CONCRETE_KEYWORDS if kw in combined) >= 2:
        return True
    return len(title) > 15


def is_domestic_news(title: str, content: str) -> bool:
    """중국 국내 뉴스 판단"""
    combined = title + content
    foreign = sum(1 for kw in ['美国', '欧洲', '日本', '韩国', '东南亚', '国际'] if kw in combined)
    domestic = sum(1 for kw in ['中国', '国内', '本土', '央行', '发改委', '工信部'] if kw in combined)
    return domestic > foreign or foreign <= 1


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


def filter_news(news_list: list) -> list:
    """뉴스 필터링"""
    filtered = []

    for news in news_list:
        title = news.get('original_title', '')
        content = news.get('original_content', '')
        source = news.get('source', '')

        if not is_factual_news(title, content):
            continue
        if not has_analytical_value(title, content):
            continue

        # 단신 뉴스 제외
        if is_brief_news(title, content):
            continue

        news['category'] = categorize_news(title, content)
        news['is_domestic'] = is_domestic_news(title, content)
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
        domestic_bonus = 3 if news['is_domestic'] else 0

        # 종합 우선순위 점수
        news['priority_score'] = source_score + central_bonus + domestic_bonus + fact_score

        filtered.append(news)

    return filtered


def balance_categories(news_list: list, target_count: int = 10, max_local_gov: int = 1) -> list:
    """카테고리 + 출처 균형 선정 (지방정부 제한, 중요도순 정렬)"""
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
    main_categories = ['정책', '거시경제', '산업', '에너지', '금융', '기업', '기술']

    # 1단계: 각 카테고리에서 1개씩 (출처 중복 최소화, 지방정부 제한)
    for category in main_categories:
        if by_category[category]:
            for news in by_category[category]:
                source = news.get('source', '')
                is_local = news.get('is_local_gov', False)

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

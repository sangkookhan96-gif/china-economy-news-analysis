"""뉴스 선정 및 필터링 모듈 - 출처 다양성 추가"""

import re
from collections import defaultdict

EXCLUDED_KEYWORDS = ['论评', '专栏', '社论', '观点', '评论', '投稿', '广告', 'PR', '新闻稿', '赞助', '专题', '访谈', '座谈', '论坛', '活动', '开幕']
DATA_PATTERNS = [r'\d+%', r'\d+亿', r'\d+万', r'\d+兆', r'\d+元', r'\d+\.\d+%']
CONCRETE_KEYWORDS = ['发布', '公布', '统计', '数据', '报告', '政策', '措施', '方案', '规定', '条例', '增长', '下降', '上涨', '下跌', '同比', '环比']

# 정부 행정 뉴스 제외 키워드 (추가)
GOVERNMENT_ADMIN_KEYWORDS = [
    '人事任免', '干部', '党委', '组织部', '纪委',
    '关于印发', '办公厅关于', '工作方案', '管理办法',
    '人民政府办公', '通知如下', '现印发给你们'
]

CATEGORIES = {
    '정책': ['政策', '政府', '通知', '规划', '强制', '意见'],
    '거시경제': ['经济', '增长', '消费', '投资', '货币', '利率', '储蓄', '人口', '劳动', '出口', '进口', '贸易', '一带一路'],
    '산업': ['制造', '产业', '工业', '上游', '下游', '开发区', '产业园区'],
    '에너지': ['能源', '电力', '电池', '新能源', '太阳能', '光伏', '氢能', '核能', '核聚变', '钍能', '风能', '风电', '地热'],
    '금융': ['银行', '金融', '融资', '股票', '债券', '证券', '上市'],
    '기업': ['企业', '公司', '股', '高管', '并购', '股东', '项目'],
    '기술': ['技术', '科技', 'AI', '机器人', '无人机', '智能制造', '生物', '自动驾驶', '超算', '量子', '航天', '新材料', '6G', '5G', '3D打印']
}

# 출처별 우선순위 (미디어 > 정부)
SOURCE_PRIORITY = {
    '36kr': 10,
    'caixin': 10,
    'people': 9,
    'ce': 9,
    'stcn': 9,
    'xinhua': 8,
    'huxiu': 8,
    'beijing_gov': 3,
    'shanghai_gov': 3,
    'shenzhen_gov': 3
}

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
        
        news['category'] = categorize_news(title, content)
        news['is_domestic'] = is_domestic_news(title, content)
        
        # 출처별 우선순위 점수 적용
        source_score = SOURCE_PRIORITY.get(source, 5)
        domestic_bonus = 5 if news['is_domestic'] else 0
        news['priority_score'] = source_score + domestic_bonus
        
        filtered.append(news)
    
    return filtered

def balance_categories(news_list: list, target_count: int = 10) -> list:
    """카테고리 + 출처 균형 선정"""
    by_category = defaultdict(list)
    by_source = defaultdict(int)
    
    for news in news_list:
        category = news.get('category', '기타')
        by_category[category].append(news)
    
    # 카테고리별 정렬 (우선순위 + 날짜)
    for cat in by_category:
        by_category[cat].sort(
            key=lambda x: (x.get('priority_score', 0), x.get('published_at', '')),
            reverse=True
        )
    
    selected = []
    main_categories = ['정책', '거시경제', '산업', '에너지', '금융', '기업', '기술']
    
    # 1단계: 각 카테고리에서 1개씩 (출처 중복 최소화)
    for category in main_categories:
        if by_category[category]:
            for news in by_category[category]:
                source = news.get('source', '')
                # 같은 출처에서 이미 2개 이상 선정했으면 스킵
                if by_source.get(source, 0) < 2:
                    selected.append(news)
                    by_source[source] = by_source.get(source, 0) + 1
                    by_category[category].remove(news)
                    break
            
            if len(selected) >= target_count:
                return selected
    
    # 2단계: 남은 슬롯 채우기 (출처 다양성 유지)
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
            # 같은 출처에서 3개 이상 선정 방지
            if by_source.get(source, 0) < 3:
                selected.append(news)
                by_source[source] = by_source.get(source, 0) + 1
    
    return selected[:target_count]

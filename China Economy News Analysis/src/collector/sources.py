"""News source definitions for Week 1-3."""

# Week 1: Core 4 sources (국가/경제/금융 매체)
NEWS_SOURCES = {
    # Week 1 - Core sources
    "xinhua": {
        "name": "新华社 (신화통신)",
        "name_ko": "신화통신",
        "url": "http://www.xinhuanet.com/fortune/",
        "rss": "http://www.xinhuanet.com/fortune/news_list.rss",
        "type": "national",
        "priority": 1,
        "week": 1,
        "enabled": True,
    },
    "people": {
        "name": "人民日报 (인민일보)",
        "name_ko": "인민일보",
        "url": "http://finance.people.com.cn/",
        "rss": None,  # Web crawling required
        "type": "national",
        "priority": 1,
        "week": 1,
        "enabled": True,
    },
    "ce": {
        "name": "经济日报 (경제일보)",
        "name_ko": "경제일보",
        "url": "http://www.ce.cn/",
        "rss": None,
        "type": "economic",
        "priority": 1,
        "week": 1,
        "enabled": True,
    },
    "stcn": {
        "name": "证券时报 (증권시보)",
        "name_ko": "증권시보",
        "url": "https://www.stcn.com/",
        "rss": None,
        "type": "financial",
        "priority": 1,
        "week": 1,
        "enabled": True,
    },
    # Week 2 - Tech & Independent media
    "caixin": {
        "name": "财新 (차이신)",
        "name_ko": "차이신",
        "url": "https://www.caixin.com/",
        "rss": None,
        "type": "independent",
        "priority": 1,
        "week": 2,
        "enabled": True,
    },
    "36kr": {
        "name": "36氪 (36Kr)",
        "name_ko": "36Kr",
        "url": "https://36kr.com/",
        "rss": "https://36kr.com/feed",
        "type": "tech",
        "priority": 1,
        "week": 1,
        "enabled": True,
    },
    "huxiu": {
        "name": "虎嗅 (Huxiu)",
        "name_ko": "후시우",
        "url": "https://www.huxiu.com/",
        "rss": "https://www.huxiu.com/rss/0.xml",
        "type": "tech",
        "priority": 2,
        "week": 2,
        "enabled": True,
    },
    # Week 3 - Government channels & additional tech
    "tmtpost": {
        "name": "钛媒体 (TMTPost)",
        "name_ko": "티미디어",
        "url": "https://www.tmtpost.com/",
        "rss": None,
        "type": "tech",
        "priority": 2,
        "week": 3,
        "enabled": False,
    },
    "beijing_gov": {
        "name": "北京市政府",
        "name_ko": "베이징시 정부",
        "url": "https://www.beijing.gov.cn/",
        "rss": None,
        "type": "government",
        "priority": 1,
        "week": 3,
        "enabled": True,
    },
    "shanghai_gov": {
        "name": "上海市政府",
        "name_ko": "상하이시 정부",
        "url": "https://www.shanghai.gov.cn/",
        "rss": None,
        "type": "government",
        "priority": 1,
        "week": 3,
        "enabled": True,
    },
    "shenzhen_gov": {
        "name": "深圳市工信局",
        "name_ko": "선전시 공업정보화국",
        "url": "http://gxj.sz.gov.cn/",
        "rss": None,
        "type": "government",
        "priority": 1,
        "week": 3,
        "enabled": True,
    },
}


def get_enabled_sources():
    """Get list of currently enabled news sources."""
    return {k: v for k, v in NEWS_SOURCES.items() if v["enabled"]}


def get_sources_by_week(week: int):
    """Get sources for a specific week."""
    return {k: v for k, v in NEWS_SOURCES.items() if v["week"] <= week}


def enable_week(week: int):
    """Enable all sources up to and including the specified week."""
    for source in NEWS_SOURCES.values():
        if source["week"] <= week:
            source["enabled"] = True

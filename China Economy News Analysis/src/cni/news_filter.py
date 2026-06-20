"""CNI News Quality Filter.

Filters news before pipeline processing.
"""

import re

MIN_CONTENT_LENGTH = 500

VIDEO_KW = ["视频", "watch", "video", "直播", "播放", "观看", "短视频"]
PAYWALL_KW = ["订阅", "付费", "会员", "登录后", "subscribe", "premium", "VIP",
              "付费阅读", "开通会员"]
CHINA_ECON_KW = ["中国", "中國", "经济", "產業", "企业", "政策", "投资", "市场",
                 "银行", "金融", "人民币", "GDP", "贸易", "科技", "半导体",
                 "新能源", "国务院", "央行", "A股", "港股"]


def filter_news(news: dict) -> dict:
    """Filter news by quality criteria.

    Args:
        news: dict with at least 'original_content' and 'original_title'

    Returns:
        {"is_valid": bool, "reason": str or None}
    """
    content = news.get("original_content") or ""
    title = news.get("original_title") or ""
    text = title + " " + content

    # 1. Too short
    if len(content) < MIN_CONTENT_LENGTH:
        return {"is_valid": False, "reason": "short"}

    # 2. Video content
    text_lower = text.lower()
    for kw in VIDEO_KW:
        if kw.lower() in text_lower:
            # Only reject if video keywords are dominant (in title or first 100 chars)
            check_zone = (title + " " + content[:100]).lower()
            if kw.lower() in check_zone:
                return {"is_valid": False, "reason": "video"}

    # 3. Paywall
    for kw in PAYWALL_KW:
        if kw.lower() in text_lower:
            return {"is_valid": False, "reason": "paywall"}

    # 4. China relevance
    has_china = any(kw in text for kw in CHINA_ECON_KW)
    if not has_china:
        return {"is_valid": False, "reason": "non_china"}

    return {"is_valid": True, "reason": None}

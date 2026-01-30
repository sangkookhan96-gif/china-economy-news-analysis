"""날짜 파싱 개선 패치"""

from datetime import datetime

def fix_published_date(published_at):
    """날짜가 None이면 현재 시각 반환"""
    if published_at is None:
        return datetime.now()
    return published_at

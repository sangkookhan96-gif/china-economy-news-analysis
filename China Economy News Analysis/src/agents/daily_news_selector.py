"""Edition-based news selection algorithm for expert review queue.

Selects up to 10 news items per edition (morning/afternoon/evening) using the
canonical filtering pipeline from news_filter.py (filter_news + balance_categories).

Edition schedule:
  - morning:   00:00–06:59 articles, selected at 07:00
  - afternoon: 07:00–13:59 articles, selected at 14:00
  - evening:   14:00–21:59 articles, selected at 22:00
"""

import argparse
import logging
from collections import Counter
from datetime import datetime, time

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.database.models import get_connection
from src.collector.news_filter import filter_news, balance_categories

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Edition configuration
# ---------------------------------------------------------------------------

EDITION_CONFIG = {
    'morning': {
        'label': '오전판',
        'title': '오늘 오전 뉴스 헤드라인',
        'start': time(0, 0),
        'end': time(6, 59, 59),
    },
    'afternoon': {
        'label': '오후판',
        'title': '오늘 오후 뉴스 헤드라인',
        'start': time(7, 0),
        'end': time(13, 59, 59),
    },
    'evening': {
        'label': '저녁/반판',
        'title': '오늘 밤 뉴스 헤드라인',
        'start': time(14, 0),
        'end': time(21, 59, 59),
    },
}


def get_current_edition() -> str:
    """Determine the current edition based on the current time."""
    now = datetime.now().time()
    if now < time(7, 0):
        return 'morning'
    elif now < time(14, 0):
        return 'afternoon'
    else:
        return 'evening'


def get_edition_time_window(edition: str, target_date=None):
    """Return (start_datetime_str, end_datetime_str) for the given edition."""
    if target_date is None:
        target_date = datetime.now().date()
    config = EDITION_CONFIG[edition]
    start_dt = datetime.combine(target_date, config['start'])
    end_dt = datetime.combine(target_date, config['end'])
    return start_dt.strftime("%Y-%m-%d %H:%M:%S"), end_dt.strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# Core selection functions
# ---------------------------------------------------------------------------

def get_eligible_candidates(conn, edition: str) -> list:
    """Fetch eligible news candidates for the given edition's time window.

    Only considers articles whose published_at falls within the edition's
    time range and that have not been assigned to any edition yet.
    """
    cursor = conn.cursor()
    window_start, window_end = get_edition_time_window(edition)

    # Central government sources excluded from expert review
    excluded_sources = ('ndrc', 'pboc', 'mofcom', 'nbs', 'gov', 'xinhuanet')

    cursor.execute("""
        SELECT id, original_title, original_content, published_at, source
        FROM news
        WHERE
            analyzed_at IS NOT NULL
            AND (expert_review_status = 'none' OR expert_review_status IS NULL)
            AND expert_review_status != 'skipped'
            AND published_at IS NOT NULL
            AND published_at >= ?
            AND published_at <= ?
            AND edition IS NULL
            AND importance_score <= 1.0
            AND COALESCE(translated_title, '') != ''
            AND source NOT IN (?, ?, ?, ?, ?, ?)
        ORDER BY published_at DESC
    """, (window_start, window_end, *excluded_sources))

    candidates = []
    for row in cursor.fetchall():
        candidates.append({
            'id': row['id'],
            'original_title': row['original_title'] or '',
            'original_content': row['original_content'] or '',
            'published_at': row['published_at'],
            'source': row['source'] or '',
        })

    return candidates


def select_edition_news(edition: str, target_count: int = 10) -> list:
    """Select news items for a specific edition's expert review queue.

    Uses the canonical filter_news() and balance_categories() from
    news_filter.py.

    Returns:
        List of selected news IDs.
    """
    conn = get_connection()

    try:
        candidates = get_eligible_candidates(conn, edition)

        if not candidates:
            logger.warning(f"[{edition}] No eligible candidates found")
            return []

        logger.info(f"[{edition}] Found {len(candidates)} eligible candidates")

        # Apply the canonical filtering pipeline
        filtered = filter_news(candidates)
        logger.info(f"[{edition}] After filter_news: {len(filtered)} items")

        selected = balance_categories(filtered, target_count=target_count)
        logger.info(f"[{edition}] After balance_categories: {len(selected)} items")

        # Log selection summary
        if selected:
            categories = Counter(n.get('category', '기타') for n in selected)
            sources = Counter(n.get('source', '') for n in selected)
            logger.info(f"[{edition}] Category distribution: {dict(categories)}")
            logger.info(f"[{edition}] Source distribution: {dict(sources)}")

        return [item['id'] for item in selected]

    finally:
        conn.close()


def update_selected_status(news_ids: list, edition: str) -> int:
    """Update selected items to 'queued_today' and assign edition.

    Returns:
        Number of items updated.
    """
    if not news_ids:
        return 0

    conn = get_connection()
    cursor = conn.cursor()

    try:
        placeholders = ",".join("?" * len(news_ids))
        cursor.execute(f"""
            UPDATE news
            SET expert_review_status = 'queued_today',
                edition = ?,
                updated_at = ?
            WHERE id IN ({placeholders})
        """, [edition, datetime.now()] + news_ids)

        conn.commit()
        updated = cursor.rowcount
        logger.info(f"[{edition}] Updated {updated} news items to 'queued_today'")
        return updated

    finally:
        conn.close()


def reset_stale_queue() -> int:
    """Reset 'queued_today' items from previous days that were never reviewed.

    Only resets items whose updated_at date is before today.
    Does NOT touch any items from today's editions.

    Returns:
        Number of items reset.
    """
    conn = get_connection()
    cursor = conn.cursor()

    try:
        today_str = datetime.now().strftime("%Y-%m-%d")
        cursor.execute("""
            UPDATE news
            SET expert_review_status = 'none',
                edition = NULL,
                updated_at = ?
            WHERE expert_review_status = 'queued_today'
              AND DATE(updated_at) < ?
        """, (datetime.now(), today_str))

        conn.commit()
        reset_count = cursor.rowcount

        if reset_count > 0:
            logger.info(f"Reset {reset_count} stale items from previous days")

        return reset_count

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run_edition_selection(edition: str = None) -> dict:
    """Run the selection process for a specific edition.

    1. Reset stale queue items from previous days
    2. Select new items for this edition
    3. Update their status and edition

    Returns:
        Summary dict with selection results.
    """
    if edition is None:
        edition = get_current_edition()

    config = EDITION_CONFIG[edition]
    logger.info(f"Starting {config['label']} ({edition}) edition selection")

    # Reset stale queue from previous days
    reset_count = reset_stale_queue()

    # Select new items for this edition
    selected_ids = select_edition_news(edition, target_count=10)

    # Update status
    updated_count = update_selected_status(selected_ids, edition)

    result = {
        "edition": edition,
        "label": config['label'],
        "reset_count": reset_count,
        "selected_count": len(selected_ids),
        "updated_count": updated_count,
        "selected_ids": selected_ids,
        "timestamp": datetime.now().isoformat(),
    }

    logger.info(f"Edition selection complete: {result}")
    return result


# Backward-compatible alias
def run_daily_selection() -> dict:
    """Backward-compatible wrapper. Delegates to run_edition_selection."""
    return run_edition_selection()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    """Run edition selection from command line."""
    parser = argparse.ArgumentParser(description="Edition News Selector")
    parser.add_argument(
        "--edition",
        choices=['morning', 'afternoon', 'evening'],
        default=None,
        help="Edition to select for (default: auto-detect based on current time)",
    )
    args = parser.parse_args()

    result = run_edition_selection(edition=args.edition)
    print(f"Edition selection completed ({result['label']}):")
    print(f"  - Edition: {result['edition']}")
    print(f"  - Reset from stale queue: {result['reset_count']}")
    print(f"  - Selected: {result['selected_count']}")
    print(f"  - Updated to queued_today: {result['updated_count']}")
    print(f"  - News IDs: {result['selected_ids']}")


if __name__ == "__main__":
    main()

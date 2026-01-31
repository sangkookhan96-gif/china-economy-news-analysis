"""Daily news selection algorithm for expert review queue.

Selects exactly 10 news items daily using the canonical filtering pipeline
from news_filter.py (filter_news + balance_categories), ensuring consistency
with run_collector.py's filtering logic.
"""

import logging
from datetime import datetime, timedelta

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.database.models import get_connection
from src.collector.news_filter import filter_news, balance_categories

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def get_eligible_candidates(conn) -> list:
    """Fetch all eligible news candidates for selection.

    Strict 24-hour freshness filter: only news with an explicit published_at
    date within the last 24 hours are eligible.
    News without published_at or older than 24 hours is unconditionally excluded.
    """
    cursor = conn.cursor()

    # Strict 24-hour cutoff — no exceptions
    cutoff_time = datetime.now() - timedelta(hours=24)
    cutoff_str = cutoff_time.strftime("%Y-%m-%d %H:%M:%S")

    cursor.execute("""
        SELECT id, original_title, original_content, published_at, source
        FROM news
        WHERE
            analyzed_at IS NOT NULL
            AND (expert_review_status = 'none' OR expert_review_status IS NULL)
            AND expert_review_status != 'skipped'
            AND published_at IS NOT NULL
            AND published_at >= ?
            AND importance_score <= 1.0
            AND COALESCE(translated_title, '') != ''
        ORDER BY published_at DESC
    """, (cutoff_str,))

    candidates = []
    for row in cursor.fetchall():
        # Double-check: enforce 24h cutoff in Python
        if row['published_at'] < cutoff_str:
            continue
        candidates.append({
            'id': row['id'],
            'original_title': row['original_title'] or '',
            'original_content': row['original_content'] or '',
            'published_at': row['published_at'],
            'source': row['source'] or '',
        })

    return candidates


def select_daily_news(target_count: int = 10) -> list:
    """Select exactly target_count news items for daily expert review.

    Uses the canonical filter_news() and balance_categories() from
    news_filter.py — the same pipeline used by run_collector.py.

    Returns:
        List of selected news IDs.
    """
    conn = get_connection()

    try:
        candidates = get_eligible_candidates(conn)

        if not candidates:
            logger.warning("No eligible candidates found for daily selection")
            return []

        logger.info(f"Found {len(candidates)} eligible candidates")

        # Apply the canonical filtering pipeline (same as run_collector.py)
        filtered = filter_news(candidates)
        logger.info(f"After filter_news: {len(filtered)} items")

        selected = balance_categories(filtered, target_count=target_count)
        logger.info(f"After balance_categories: {len(selected)} items")

        # Log selection summary
        if selected:
            from collections import Counter
            categories = Counter(n.get('category', '기타') for n in selected)
            sources = Counter(n.get('source', '') for n in selected)
            logger.info(f"Category distribution: {dict(categories)}")
            logger.info(f"Source distribution: {dict(sources)}")

        return [item['id'] for item in selected]

    finally:
        conn.close()


def update_selected_status(news_ids: list) -> int:
    """Update expert_review_status to 'queued_today' for selected items.

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
                updated_at = ?
            WHERE id IN ({placeholders})
        """, [datetime.now()] + news_ids)

        conn.commit()
        updated = cursor.rowcount
        logger.info(f"Updated {updated} news items to 'queued_today' status")
        return updated

    finally:
        conn.close()


def reset_previous_queue() -> int:
    """Reset yesterday's 'queued_today' items that weren't reviewed.

    Items that were 'queued_today' but not reviewed become 'none' again
    so they can be considered for future selection.

    Returns:
        Number of items reset.
    """
    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            UPDATE news
            SET expert_review_status = 'none',
                updated_at = ?
            WHERE expert_review_status = 'queued_today'
        """, (datetime.now(),))

        conn.commit()
        reset_count = cursor.rowcount

        if reset_count > 0:
            logger.info(f"Reset {reset_count} items from previous queue")

        return reset_count

    finally:
        conn.close()


def run_daily_selection() -> dict:
    """Run the complete daily selection process.

    1. Reset previous day's queue
    2. Select new items
    3. Update their status

    Returns:
        Summary dict with selection results.
    """
    logger.info("Starting daily news selection")

    # Reset previous queue
    reset_count = reset_previous_queue()

    # Select new items
    selected_ids = select_daily_news(target_count=10)

    # Update status
    updated_count = update_selected_status(selected_ids)

    result = {
        "reset_count": reset_count,
        "selected_count": len(selected_ids),
        "updated_count": updated_count,
        "selected_ids": selected_ids,
        "timestamp": datetime.now().isoformat(),
    }

    logger.info(f"Daily selection complete: {result}")
    return result


def main():
    """Run daily selection from command line."""
    result = run_daily_selection()
    print(f"Daily selection completed:")
    print(f"  - Reset from previous queue: {result['reset_count']}")
    print(f"  - Selected: {result['selected_count']}")
    print(f"  - Updated to queued_today: {result['updated_count']}")
    print(f"  - News IDs: {result['selected_ids']}")


if __name__ == "__main__":
    main()

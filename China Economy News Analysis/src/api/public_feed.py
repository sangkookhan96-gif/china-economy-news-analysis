"""Public feed API for user-facing news display.

Provides functions to retrieve expert-reviewed news for public consumption.
Supports 3-edition-per-day publishing (morning/afternoon/evening).
"""

import sqlite3
from typing import Optional
from datetime import datetime, date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from config.settings import DATABASE_PATH


def get_connection() -> sqlite3.Connection:
    """Get database connection with row factory."""
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_published_news(limit: int = 10, offset: int = 0) -> list[dict]:
    """Retrieve expert-reviewed news for public display.

    Only returns news where expert_comment IS NOT NULL.

    Args:
        limit: Maximum number of news items to return (default: 10)
        offset: Number of items to skip for pagination (default: 0)

    Returns:
        List of news dictionaries with fields:
        - id: News ID
        - headline: Translated title (Korean)
        - expert_review: Expert's comment/review
        - original_article: Original content
        - source: News source name
        - date: Publication date (ISO format)
        - importance: Importance score (0.0-1.0)
        - category: Industry category
        - summary: AI-generated summary
        - edition: Edition code (morning/afternoon/evening)
    """
    conn = get_connection()
    cursor = conn.cursor()

    query = """
        SELECT
            n.id,
            n.card_headline,
            n.translated_title AS headline,
            er.expert_comment AS expert_review,
            n.original_content AS original_article,
            n.source,
            n.published_at AS date,
            n.importance_score AS importance,
            n.industry_category AS category,
            n.summary,
            n.edition
        FROM news n
        INNER JOIN expert_reviews er ON n.id = er.news_id
        WHERE er.expert_comment IS NOT NULL
          AND er.publish_status = 'published'
          AND n.original_content IS NOT NULL AND TRIM(n.original_content) != ''
        ORDER BY n.published_at DESC
        LIMIT ? OFFSET ?
    """

    cursor.execute(query, (limit, offset))
    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]


def get_published_news_count() -> int:
    """Get total count of published (expert-reviewed) news.

    Returns:
        Total number of news items with expert comments
    """
    conn = get_connection()
    cursor = conn.cursor()

    query = """
        SELECT COUNT(*)
        FROM news n
        INNER JOIN expert_reviews er ON n.id = er.news_id
        WHERE er.expert_comment IS NOT NULL
          AND er.publish_status = 'published'
          AND n.original_content IS NOT NULL AND TRIM(n.original_content) != ''
    """

    cursor.execute(query)
    count = cursor.fetchone()[0]
    conn.close()

    return count


def get_news_by_id(news_id: int) -> Optional[dict]:
    """Get a single news item by ID.

    Args:
        news_id: The news item ID

    Returns:
        News dictionary if found and has expert review, None otherwise
    """
    conn = get_connection()
    cursor = conn.cursor()

    query = """
        SELECT
            n.id,
            n.card_headline,
            n.translated_title AS headline,
            er.expert_comment AS expert_review,
            n.original_content AS original_article,
            n.original_url,
            n.source,
            n.published_at AS date,
            n.importance_score AS importance,
            n.industry_category AS category,
            n.summary,
            er.ai_comment,
            er.ai_final_review,
            n.edition
        FROM news n
        INNER JOIN expert_reviews er ON n.id = er.news_id
        WHERE n.id = ? AND er.expert_comment IS NOT NULL
          AND er.publish_status = 'published'
          AND n.original_content IS NOT NULL AND TRIM(n.original_content) != ''
    """

    cursor.execute(query, (news_id,))
    row = cursor.fetchone()
    conn.close()

    return dict(row) if row else None


def get_published_news_by_date(target_date: date, limit: int = 50) -> list[dict]:
    """Get published news for a specific date.

    Args:
        target_date: The date to filter by
        limit: Maximum number of results

    Returns:
        List of news dictionaries for that date
    """
    conn = get_connection()
    cursor = conn.cursor()

    query = """
        SELECT
            n.id,
            n.card_headline,
            n.translated_title AS headline,
            er.expert_comment AS expert_review,
            n.original_content AS original_article,
            n.source,
            n.published_at AS date,
            n.importance_score AS importance,
            n.industry_category AS category,
            n.summary,
            n.edition
        FROM news n
        INNER JOIN expert_reviews er ON n.id = er.news_id
        WHERE er.expert_comment IS NOT NULL
          AND er.publish_status = 'published'
          AND n.original_content IS NOT NULL AND TRIM(n.original_content) != ''
          AND DATE(n.published_at) = ?
        ORDER BY n.importance_score DESC, n.published_at DESC
        LIMIT ?
    """

    cursor.execute(query, (target_date.isoformat(), limit))
    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]


def get_available_dates() -> list[str]:
    """Get list of dates that have published news.

    Returns:
        List of date strings (ISO format) in descending order
    """
    conn = get_connection()
    cursor = conn.cursor()

    query = """
        SELECT DISTINCT DATE(n.published_at) AS news_date
        FROM news n
        INNER JOIN expert_reviews er ON n.id = er.news_id
        WHERE er.expert_comment IS NOT NULL
          AND er.publish_status = 'published'
          AND n.original_content IS NOT NULL AND TRIM(n.original_content) != ''
        ORDER BY news_date DESC
    """

    cursor.execute(query)
    rows = cursor.fetchall()
    conn.close()

    return [row['news_date'] for row in rows if row['news_date']]


# ---------------------------------------------------------------------------
# Edition headline titles
# ---------------------------------------------------------------------------

EDITION_TITLES = {
    'morning': '오늘 오전 뉴스 헤드라인',
    'afternoon': '오늘 오후 뉴스 헤드라인',
    'evening': '오늘 밤 뉴스 헤드라인',
}


def get_today_headlines(target_date: Optional[date] = None, edition: Optional[str] = None) -> dict:
    """Get headlines summary for a specific edition.

    Args:
        target_date: Date to get headlines for (default: today)
        edition: 'morning', 'afternoon', or 'evening' (default: auto-detect)

    Returns:
        Dictionary with date, edition, title, and sorted headlines list
    """
    if target_date is None:
        target_date = date.today()

    # Auto-detect edition based on current time
    if edition is None:
        current_hour = datetime.now().hour
        if current_hour < 7:
            edition = 'morning'
        elif current_hour < 14:
            edition = 'afternoon'
        else:
            edition = 'evening'

    title = EDITION_TITLES.get(edition, '오늘 뉴스 헤드라인')

    conn = get_connection()
    cursor = conn.cursor()

    query = """
        SELECT
            n.id,
            COALESCE(n.card_headline, SUBSTR(n.translated_title, 1, 20)) AS headline,
            n.translated_title AS full_title,
            n.industry_category AS category,
            n.importance_score AS importance,
            er.created_at AS review_date
        FROM news n
        INNER JOIN expert_reviews er ON n.id = er.news_id
        WHERE er.expert_comment IS NOT NULL
          AND er.publish_status = 'published'
          AND DATE(er.created_at) = ?
          AND n.edition = ?
        ORDER BY n.importance_score DESC, er.created_at DESC
        LIMIT 10
    """

    cursor.execute(query, (target_date.isoformat(), edition))
    rows = cursor.fetchall()
    conn.close()

    headlines = []
    for i, row in enumerate(rows, 1):
        headlines.append({
            "rank": i,
            "id": row["id"],
            "headline": row["headline"] or row["full_title"][:20] + "...",
            "category": row["category"] or "",
            "importance": row["importance"] or 0.5
        })

    return {
        "date": target_date.isoformat(),
        "edition": edition,
        "title": title,
        "count": len(headlines),
        "headlines": headlines
    }


def get_all_edition_headlines(target_date: Optional[date] = None) -> list[dict]:
    """Get headlines for all editions of a given date.

    Returns:
        List of edition headline dicts (morning, afternoon, evening),
        only including editions that have at least one published headline.
    """
    if target_date is None:
        target_date = date.today()

    results = []
    for edition in ['morning', 'afternoon', 'evening']:
        data = get_today_headlines(target_date=target_date, edition=edition)
        if data['count'] > 0:
            results.append(data)
    return results

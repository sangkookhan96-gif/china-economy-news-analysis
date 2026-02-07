"""Public feed API for user-facing news display.

Provides functions to retrieve expert-reviewed news for public consumption.
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
            n.summary
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
            er.ai_final_review
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
            n.summary
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

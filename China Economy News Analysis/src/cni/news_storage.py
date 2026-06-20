"""CNI News Storage Layer (Wrapper).

Wraps existing news table. No new tables created.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.database.models import get_connection


def get_raw_news(news_id: int) -> dict | None:
    """Get raw news data from existing news table."""
    conn = get_connection()
    row = conn.execute("""
        SELECT id, original_title, original_content, source, original_url,
               published_at, content_type, translated_title, summary,
               importance_score, industry_category
        FROM news WHERE id = ?
    """, (news_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_unprocessed_cni_news(limit: int = 50) -> list[dict]:
    """Get news not yet processed by CNI pipeline."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT n.id, n.original_title, n.original_content, n.source,
               n.original_url, n.published_at, n.content_type,
               n.translated_title, n.summary, n.importance_score
        FROM news n
        WHERE n.analyzed_at IS NOT NULL
          AND n.original_content IS NOT NULL
          AND LENGTH(n.original_content) > 0
          AND n.id NOT IN (SELECT news_id FROM cni_summaries)
        ORDER BY n.collected_at DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_news_full_text(news_id: int) -> str:
    """Get original_content for a news article."""
    conn = get_connection()
    row = conn.execute(
        "SELECT original_content FROM news WHERE id = ?", (news_id,)
    ).fetchone()
    conn.close()
    return row["original_content"] if row and row["original_content"] else ""

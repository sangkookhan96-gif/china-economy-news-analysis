"""CNI Review Queue — manage review workflow."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.database.kg_models import get_kg_connection
from src.cni.summary_store import init_cni_tables


def add_to_review_queue(news_id: int, refined_summary: str):
    """Add refined summary to review queue."""
    init_cni_tables()
    conn = get_kg_connection()
    # Avoid duplicates
    existing = conn.execute(
        "SELECT id FROM cni_review_queue WHERE news_id = ?", (news_id,)
    ).fetchone()
    if existing:
        conn.execute("""
            UPDATE cni_review_queue
            SET refined_summary = ?, status = 'pending'
            WHERE news_id = ?
        """, (refined_summary, news_id))
    else:
        conn.execute("""
            INSERT INTO cni_review_queue (news_id, refined_summary, status)
            VALUES (?, ?, 'pending')
        """, (news_id, refined_summary))
    conn.commit()
    conn.close()


def get_pending_reviews(limit: int = 20) -> list[dict]:
    """Get pending review items."""
    conn = get_kg_connection()
    rows = conn.execute("""
        SELECT rq.*, n.original_title, n.source, cs.summary_zh
        FROM cni_review_queue rq
        JOIN news n ON rq.news_id = n.id
        LEFT JOIN cni_summaries cs ON rq.news_id = cs.news_id
        WHERE rq.status = 'pending'
        ORDER BY rq.created_at DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def approve_review(review_id: int):
    """Approve a review."""
    conn = get_kg_connection()
    conn.execute("""
        UPDATE cni_review_queue
        SET status = 'approved', reviewed_at = datetime('now')
        WHERE id = ?
    """, (review_id,))
    conn.commit()
    conn.close()


def reject_review(review_id: int):
    """Reject a review."""
    conn = get_kg_connection()
    conn.execute("""
        UPDATE cni_review_queue
        SET status = 'rejected', reviewed_at = datetime('now')
        WHERE id = ?
    """, (review_id,))
    conn.commit()
    conn.close()


def edit_review(review_id: int, edited_text: str):
    """Edit and approve a review."""
    conn = get_kg_connection()
    conn.execute("""
        UPDATE cni_review_queue
        SET edited_text = ?, status = 'edited', reviewed_at = datetime('now')
        WHERE id = ?
    """, (edited_text, review_id))
    conn.commit()
    conn.close()

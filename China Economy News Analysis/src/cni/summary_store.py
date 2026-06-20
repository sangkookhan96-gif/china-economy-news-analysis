"""CNI Summary Store — cni_summaries + cni_review_queue tables."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.database.kg_models import get_kg_connection


def init_cni_tables():
    """Create CNI tables if not exist."""
    conn = get_kg_connection()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS cni_summaries (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            news_id             INTEGER NOT NULL UNIQUE,
            summary_zh          TEXT,
            summary_ko          TEXT,
            refined_ko          TEXT,
            filter_result       TEXT,
            filter_reason       TEXT,
            kg_processed        BOOLEAN DEFAULT FALSE,
            translation_status  TEXT DEFAULT 'pending'
                CHECK(translation_status IN ('pending','translated','refined','rejected')),
            created_at          TEXT DEFAULT (datetime('now')),
            updated_at          TEXT DEFAULT (datetime('now'))
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS cni_review_queue (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            news_id         INTEGER NOT NULL,
            refined_summary TEXT,
            status          TEXT DEFAULT 'pending'
                CHECK(status IN ('pending','approved','rejected','edited')),
            edited_text     TEXT,
            reviewed_at     TEXT,
            created_at      TEXT DEFAULT (datetime('now'))
        )
    """)

    conn.execute("CREATE INDEX IF NOT EXISTS idx_cni_summaries_news ON cni_summaries(news_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cni_summaries_status ON cni_summaries(translation_status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cni_review_status ON cni_review_queue(status)")

    conn.commit()
    conn.close()


def save_summary(news_id: int, summary_zh: str = None,
                 filter_result: str = "valid", filter_reason: str = None,
                 translation_status: str = "pending"):
    """Save initial summary (auto stage)."""
    conn = get_kg_connection()
    conn.execute("""
        INSERT OR REPLACE INTO cni_summaries
        (news_id, summary_zh, filter_result, filter_reason, translation_status, updated_at)
        VALUES (?, ?, ?, ?, ?, datetime('now'))
    """, (news_id, summary_zh, filter_result, filter_reason, translation_status))
    conn.commit()
    conn.close()


def update_translation(news_id: int, summary_ko: str):
    """Save manual translation. Creates record if not exists."""
    conn = get_kg_connection()

    existing = conn.execute(
        "SELECT id FROM cni_summaries WHERE news_id = ?", (news_id,)
    ).fetchone()

    if existing:
        conn.execute("""
            UPDATE cni_summaries
            SET summary_ko = ?, translation_status = 'translated', updated_at = datetime('now')
            WHERE news_id = ?
        """, (summary_ko, news_id))
    else:
        # Record doesn't exist (news was selected but not processed by auto stage)
        # Get summary_zh from news table if available
        news_row = conn.execute(
            "SELECT summary_zh FROM news WHERE id = ?", (news_id,)
        ).fetchone()
        summary_zh = news_row["summary_zh"] if news_row and news_row["summary_zh"] else None

        conn.execute("""
            INSERT INTO cni_summaries
            (news_id, summary_zh, summary_ko, filter_result, translation_status)
            VALUES (?, ?, ?, 'valid', 'translated')
        """, (news_id, summary_zh, summary_ko))

    conn.commit()
    conn.close()


def update_refined(news_id: int, refined_ko: str):
    """Save refined Korean text. Creates record if not exists."""
    conn = get_kg_connection()

    existing = conn.execute(
        "SELECT id FROM cni_summaries WHERE news_id = ?", (news_id,)
    ).fetchone()

    if existing:
        conn.execute("""
            UPDATE cni_summaries
            SET refined_ko = ?, translation_status = 'refined', updated_at = datetime('now')
            WHERE news_id = ?
        """, (refined_ko, news_id))
    else:
        conn.execute("""
            INSERT INTO cni_summaries
            (news_id, refined_ko, filter_result, translation_status)
            VALUES (?, ?, 'valid', 'refined')
        """, (news_id, refined_ko))

    conn.commit()
    conn.close()


def mark_kg_processed(news_id: int):
    """Mark as KG processed."""
    conn = get_kg_connection()
    conn.execute("""
        UPDATE cni_summaries SET kg_processed = TRUE, updated_at = datetime('now')
        WHERE news_id = ?
    """, (news_id,))
    conn.commit()
    conn.close()


def get_summary(news_id: int) -> dict | None:
    """Get summary for a news article."""
    conn = get_kg_connection()
    row = conn.execute(
        "SELECT * FROM cni_summaries WHERE news_id = ?", (news_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_pending_translations(limit: int = 20) -> list[dict]:
    """Get items awaiting manual translation."""
    conn = get_kg_connection()
    rows = conn.execute("""
        SELECT cs.*, n.original_title, n.source
        FROM cni_summaries cs
        JOIN news n ON cs.news_id = n.id
        WHERE cs.translation_status = 'pending'
          AND cs.filter_result = 'valid'
        ORDER BY cs.created_at DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_pending_refinements(limit: int = 20) -> list[dict]:
    """Get items awaiting Ollama refinement."""
    conn = get_kg_connection()
    rows = conn.execute("""
        SELECT cs.*, n.original_title
        FROM cni_summaries cs
        JOIN news n ON cs.news_id = n.id
        WHERE cs.translation_status = 'translated'
        ORDER BY cs.updated_at DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_cni_stats() -> dict:
    """Get CNI pipeline statistics."""
    conn = get_kg_connection()
    stats = {}
    stats["total"] = conn.execute("SELECT COUNT(*) FROM cni_summaries").fetchone()[0]
    stats["valid"] = conn.execute(
        "SELECT COUNT(*) FROM cni_summaries WHERE filter_result='valid'").fetchone()[0]
    stats["rejected"] = conn.execute(
        "SELECT COUNT(*) FROM cni_summaries WHERE filter_result='rejected'").fetchone()[0]
    stats["pending"] = conn.execute(
        "SELECT COUNT(*) FROM cni_summaries WHERE translation_status='pending' AND filter_result='valid'").fetchone()[0]
    stats["translated"] = conn.execute(
        "SELECT COUNT(*) FROM cni_summaries WHERE translation_status='translated'").fetchone()[0]
    stats["refined"] = conn.execute(
        "SELECT COUNT(*) FROM cni_summaries WHERE translation_status='refined'").fetchone()[0]
    stats["kg_processed"] = conn.execute(
        "SELECT COUNT(*) FROM cni_summaries WHERE kg_processed=TRUE").fetchone()[0]
    conn.close()
    return stats

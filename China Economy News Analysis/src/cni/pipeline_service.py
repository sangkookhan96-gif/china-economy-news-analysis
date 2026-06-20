"""CNI Pipeline Service — Dual-compatible state management.

Rules:
- pipeline_status NOT NULL → new pipeline mode
- pipeline_status IS NULL → legacy mode (fallback)
- NEVER INSERT into expert_reviews
- NEVER modify/delete triggers
- All state transitions logged
"""

import logging
import sqlite3
import time
from datetime import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from src.database.models import get_connection


def _retry_on_lock(fn, *, attempts: int = 6, base_delay: float = 0.15):
    """SQLite 락 재시도 래퍼.

    WAL의 SQLITE_BUSY_SNAPSHOT('database is locked')은 busy_timeout으로 풀리지
    않으므로(읽기→쓰기 충돌) 앱 레벨에서 트랜잭션을 재시도한다. 백그라운드
    쓰기 폭주 중에도 사용자 버튼(상태 전이)이 죽지 않게 한다.
    """
    for i in range(attempts):
        try:
            return fn()
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower() and i < attempts - 1:
                time.sleep(base_delay * (i + 1))
                continue
            raise

LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "pipeline_service.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
    force=True,
)
logger = logging.getLogger("pipeline_service")

# Valid pipeline_status values
VALID_STATES = {"selected", "processing", "skipped", "translated", "published", "unpublished", "failed"}

# Valid transitions
TRANSITIONS = {
    None: {"selected"},
    "selected": {"processing", "skipped", "translated", "published"},
    "processing": {"translated", "failed", "selected"},  # 완료/실패/초기화
    "skipped": {"selected"},  # allow re-selection (복원)
    "translated": {"published", "unpublished", "selected"},  # 공개/비공개/초기화
    "published": {"unpublished"},  # 비공개 전환
    "unpublished": {"published", "selected"},  # 재공개 또는 초기화
    "failed": {"selected", "processing"},  # 재시도 또는 초기화
}


def validate_quality_gate(news_id: int, target_state: str) -> dict:
    """품질 게이트 — 상태 전이 전 데이터 품질 검증.

    Returns: {"ok": bool, "errors": list[str]}
    """
    errors = []
    conn = get_connection()

    news = conn.execute(
        "SELECT summary_zh, title_zh, card_headline, hansanguk_tip FROM news WHERE id=?",
        (news_id,)
    ).fetchone()
    cni = conn.execute(
        "SELECT summary_ko FROM cni_summaries WHERE news_id=?", (news_id,)
    ).fetchone()
    conn.close()

    if not news:
        return {"ok": False, "errors": ["뉴스를 찾을 수 없습니다"]}

    summary_zh = news["summary_zh"] or ""
    summary_ko = (cni["summary_ko"] if cni else "") or ""
    card_headline = news["card_headline"] or ""

    if target_state == "translated":
        # selected → translated: 요약+번역 결과 검증
        if len(summary_zh) < 200:
            errors.append(f"중문 요약 부족 ({len(summary_zh)}자 < 200자)")
        if len(summary_ko) < 50:
            errors.append(f"한국어 번역 부족 ({len(summary_ko)}자 < 50자)")

    elif target_state == "published":
        # translated → published: 공개 전 최종 검증
        if len(card_headline) < 4:
            errors.append(f"헤드라인 없음 ({len(card_headline)}자 < 4자)")
        if len(summary_ko) < 50:
            errors.append(f"한국어 번역 부족 ({len(summary_ko)}자 < 50자)")

    return {"ok": len(errors) == 0, "errors": errors}


def reset_to_selected(news_id: int) -> dict:
    """초기화: translated/unpublished → selected (재처리 허용)."""
    conn = get_connection()
    row = conn.execute(
        "SELECT pipeline_status FROM news WHERE id=?", (news_id,)
    ).fetchone()
    conn.close()

    if not row:
        return {"ok": False, "error": "뉴스를 찾을 수 없습니다"}

    current = row["pipeline_status"]
    if current not in ("translated", "unpublished"):
        return {"ok": False, "error": f"{current} 상태에서는 초기화할 수 없습니다"}

    return set_pipeline_status(news_id, "selected")


def restore_skipped(news_id: int) -> dict:
    """복원: skipped → selected (재활성화)."""
    return set_pipeline_status(news_id, "selected")


def get_news_status(news: dict) -> str:
    """Dual-compatible status lookup."""
    ps = news.get("pipeline_status")
    if ps and ps in VALID_STATES:
        return ps
    return news.get("expert_review_status", "none")


def is_published(news_id: int) -> bool:
    """Check if news is published in either system."""
    conn = get_connection()
    # New system
    row = conn.execute(
        "SELECT pipeline_status FROM news WHERE id = ?", (news_id,)
    ).fetchone()
    if row and row["pipeline_status"] == "published":
        conn.close()
        return True
    # Legacy
    row2 = conn.execute(
        "SELECT publish_status FROM expert_reviews WHERE news_id = ?", (news_id,)
    ).fetchone()
    conn.close()
    return row2 and row2["publish_status"] == "published"


def set_pipeline_status(news_id: int, new_status: str) -> dict:
    """Set pipeline_status with transition validation and logging.

    Returns: {"ok": bool, "error": str or None}
    """
    if new_status not in VALID_STATES:
        return {"ok": False, "error": f"Invalid status: {new_status}"}

    def _do():
        conn = get_connection()
        try:
            # 검증→갱신을 하나의 IMMEDIATE 트랜잭션으로 묶어 읽기→쓰기 스냅샷 충돌 방지.
            conn.isolation_level = None
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT pipeline_status FROM news WHERE id = ?", (news_id,)
            ).fetchone()
            if not row:
                conn.execute("ROLLBACK")
                return {"ok": False, "error": f"News {news_id} not found"}
            current = row["pipeline_status"]
            if new_status not in TRANSITIONS.get(current, set()):
                conn.execute("ROLLBACK")
                logger.warning(f"Invalid transition: #{news_id} {current} → {new_status}")
                return {"ok": False, "error": f"Cannot transition {current} → {new_status}"}
            conn.execute(
                "UPDATE news SET pipeline_status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (new_status, news_id))
            conn.execute("COMMIT")
            logger.info(f"State: #{news_id} {current} → {new_status}")
            return {"ok": True, "error": None}
        finally:
            conn.close()

    return _retry_on_lock(_do)


def set_pipeline_selected(news_ids: list[int]):
    """Explicitly set pipeline_status = 'selected' for a batch.

    Called after daily_news_selector finishes.
    Only sets if pipeline_status IS NULL (prevent re-selection).
    """
    if not news_ids:
        return 0

    conn = get_connection()
    placeholders = ",".join("?" * len(news_ids))
    result = conn.execute(f"""
        UPDATE news SET pipeline_status = 'selected', updated_at = CURRENT_TIMESTAMP
        WHERE id IN ({placeholders})
          AND pipeline_status IS NULL
    """, news_ids)
    count = result.rowcount
    conn.commit()
    conn.close()

    logger.info(f"Batch selected: {count}/{len(news_ids)} news items")
    return count


def publish_news(news_id: int) -> dict:
    """Publish news — dual system sync.

    Prerequisites:
    - card_headline must exist (Korean headline)
    - cni_summaries.summary_ko must exist (Korean translation)

    1. Validate Korean content exists
    2. Set pipeline_status = 'published'
    3. If expert_reviews row exists → update publish_status = 'published'
       If not → DO NOT INSERT (trigger avoidance)
    """
    # 0. Validate: Korean translation must exist
    conn = get_connection()
    news = conn.execute(
        "SELECT card_headline FROM news WHERE id = ?", (news_id,)
    ).fetchone()
    if not news or not news["card_headline"]:
        conn.close()
        logger.warning(f"Publish blocked: #{news_id} has no card_headline (Korean)")
        return {"ok": False, "error": "한국어 헤드라인이 없습니다. 번역을 먼저 입력하세요."}

    cni = conn.execute(
        "SELECT summary_ko FROM cni_summaries WHERE news_id = ?", (news_id,)
    ).fetchone()
    if not cni or not cni["summary_ko"]:
        conn.close()
        logger.warning(f"Publish blocked: #{news_id} has no summary_ko")
        return {"ok": False, "error": "한국어 번역이 없습니다. 번역을 먼저 입력하세요."}
    conn.close()

    # 1. New system
    result = set_pipeline_status(news_id, "published")
    if not result["ok"]:
        return result

    # 2. Clear from recommendation box (queued_today → commented)
    conn = get_connection()
    conn.execute("""
        UPDATE news SET expert_review_status = 'commented'
        WHERE id = ? AND expert_review_status = 'queued_today'
    """, (news_id,))

    # 3. Legacy sync (update only, never insert)
    existing = conn.execute(
        "SELECT id FROM expert_reviews WHERE news_id = ?", (news_id,)
    ).fetchone()

    if existing:
        conn.execute("""
            UPDATE expert_reviews
            SET publish_status = 'published',
                publish_status_updated_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE news_id = ?
        """, (news_id,))
        logger.info(f"Legacy sync: #{news_id} expert_reviews → published")
    else:
        logger.info(f"Legacy sync: #{news_id} no expert_reviews row (skip INSERT)")

    # Record published_at timestamp
    conn.execute("""
        UPDATE cni_summaries SET published_at = datetime('now', '+9 hours')
        WHERE news_id = ?
    """, (news_id,))

    conn.commit()
    conn.close()
    return {"ok": True, "error": None}


def unpublish_news(news_id: int) -> dict:
    """비공개 전환: published/translated → unpublished."""
    result = set_pipeline_status(news_id, "unpublished")
    if not result["ok"]:
        return result

    conn = get_connection()
    # 추천함에서 제거 (queued_today → commented)
    conn.execute("""
        UPDATE news SET expert_review_status = 'commented'
        WHERE id = ? AND expert_review_status = 'queued_today'
    """, (news_id,))
    # Legacy sync
    conn.execute("""
        UPDATE expert_reviews
        SET publish_status = 'draft',
            publish_status_updated_at = CURRENT_TIMESTAMP
        WHERE news_id = ?
    """, (news_id,))
    conn.commit()
    conn.close()

    logger.info(f"Unpublished: #{news_id}")
    return {"ok": True, "error": None}


def skip_news(news_id: int) -> dict:
    """번역불요: selected → skipped."""
    result = set_pipeline_status(news_id, "skipped")
    if not result["ok"]:
        return result

    # 추천함에서 제거 (queued_today → commented)
    conn = get_connection()
    conn.execute("""
        UPDATE news SET expert_review_status = 'commented'
        WHERE id = ? AND expert_review_status = 'queued_today'
    """, (news_id,))
    conn.commit()
    conn.close()

    logger.info(f"Skipped: #{news_id}")
    return result


def rollback_to_legacy(news_id: int):
    """Emergency rollback: set pipeline_status = NULL."""
    conn = get_connection()
    conn.execute(
        "UPDATE news SET pipeline_status = NULL WHERE id = ?", (news_id,)
    )
    conn.commit()
    conn.close()
    logger.warning(f"ROLLBACK: #{news_id} pipeline_status → NULL")


def get_selected_news(limit: int = 30) -> list[dict]:
    """Get news in 'selected' state for CNI processing."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT n.id, n.original_title, n.original_content, n.source,
               n.original_url, n.importance_score, n.edition,
               n.pipeline_status, n.summary_zh, n.title_zh,
               n.translated_title, n.card_headline
        FROM news n
        WHERE n.pipeline_status = 'selected'
        ORDER BY n.importance_score DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_pipeline_stats() -> dict:
    """Get pipeline status distribution."""
    conn = get_connection()
    stats = {}
    for status in ["selected", "skipped", "translated", "published"]:
        count = conn.execute(
            "SELECT COUNT(*) FROM news WHERE pipeline_status = ?", (status,)
        ).fetchone()[0]
        stats[status] = count
    stats["legacy"] = conn.execute(
        "SELECT COUNT(*) FROM news WHERE pipeline_status IS NULL AND expert_review_status = 'commented'"
    ).fetchone()[0]
    stats["total_published_legacy"] = conn.execute(
        "SELECT COUNT(*) FROM expert_reviews WHERE publish_status = 'published'"
    ).fetchone()[0]
    conn.close()
    return stats

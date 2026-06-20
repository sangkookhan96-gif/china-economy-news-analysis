"""공개 즉시 번역 보완 — CLI 워커.

Streamlit과 분리된 별도 OS 프로세스로 실행되어 LLM 재번역을 수행한다.
공개 핸들러가 단건(`--news-id`)을 detached로 spawn하고, systemd 타이머가
주기적으로 `--sweep` 하여 누락/실패분을 회수한다.

사용:
    python3 -m src.cni.refine_worker --news-id 12345
    python3 -m src.cni.refine_worker --sweep
    python3 -m src.cni.refine_worker --news-id 12345 --dry-run --show
    python3 -m src.cni.refine_worker --sweep --force --limit 50

설계: docs/onpublish_refine_design.md
"""

import sys
import argparse
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.database.models import get_connection
from src.cni.onpublish_refine import refine_news, init_refine_schema, FIELDS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("refine_worker")

MAX_RETRIES = 3


def _set_status(news_id: int, status: str, inc_retry: bool = False, done: bool = False):
    conn = get_connection()
    if inc_retry:
        conn.execute(
            "UPDATE news SET refine_status=?, refine_retries=COALESCE(refine_retries,0)+1 WHERE id=?",
            (status, news_id))
    elif done:
        conn.execute(
            "UPDATE news SET refine_status=?, refined_done_at=datetime('now') WHERE id=?",
            (status, news_id))
    else:
        conn.execute("UPDATE news SET refine_status=? WHERE id=?", (status, news_id))
    conn.commit()
    conn.close()


def _print_report(report: dict, show: bool):
    nid = report.get("news_id")
    if report.get("error"):
        print(f"[#{nid}] ERROR: {report['error']}")
        return
    for field, r in report.get("fields", {}).items():
        dec = r.get("decision")
        reason = r.get("reason", "")
        m = r.get("metrics", {})
        print(f"[#{nid}] {field:9s} → {dec:18s} {reason}  {m if m else ''}")
        if show and r.get("new"):
            print(f"    OLD: {(r.get('old') or '')[:160]}")
            print(f"    NEW: {(r.get('new') or '')[:160]}")


def process_one(news_id: int, dry_run: bool, force: bool, show: bool) -> bool:
    """단건 처리. 성공(예외 없음) True. dry_run이면 상태 변경 안 함."""
    try:
        report = refine_news(news_id, fields=FIELDS, dry_run=dry_run, force=force)
        _print_report(report, show)
        if not dry_run:
            _set_status(news_id, "done", done=True)
        return True
    except Exception as e:
        logger.exception("process_one failed: %s", news_id)
        if not dry_run:
            _set_status(news_id, "failed", inc_retry=True)
        return False


def sweep(limit: int, dry_run: bool, force: bool, show: bool):
    """refine_status가 pending이거나, 재시도 상한 미만의 failed 건을 회수 처리."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT id FROM news "
        "WHERE refine_status='pending' "
        "   OR (refine_status='failed' AND COALESCE(refine_retries,0) < ?) "
        "ORDER BY published_at DESC LIMIT ?",
        (MAX_RETRIES, limit)).fetchall()
    conn.close()
    ids = [r["id"] for r in rows]
    logger.info("sweep: %d targets", len(ids))
    ok = fail = 0
    for nid in ids:
        if process_one(nid, dry_run, force, show):
            ok += 1
        else:
            fail += 1
    logger.info("sweep done: ok=%d fail=%d", ok, fail)

    # 재시도 상한 초과 건 경고
    if not dry_run:
        _notify_stuck()


def _notify_stuck():
    conn = get_connection()
    stuck = conn.execute(
        "SELECT COUNT(*) c FROM news WHERE refine_status='failed' "
        "AND COALESCE(refine_retries,0) >= ?", (MAX_RETRIES,)).fetchone()["c"]
    conn.close()
    if stuck:
        try:
            from src.utils.notifications import NotificationManager
            NotificationManager().notify_opinion_conflict(
                0, f"번역 보완 {stuck}건이 {MAX_RETRIES}회 실패 — 점검 필요")
        except Exception:
            logger.warning("번역 보완 실패 누적 %d건 (알림 전송 실패)", stuck)


def main():
    ap = argparse.ArgumentParser(description="공개 즉시 번역 보완 워커")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--news-id", type=int, help="단건 처리")
    g.add_argument("--sweep", action="store_true", help="pending/failed 일괄 회수")
    ap.add_argument("--dry-run", action="store_true", help="DB 미변경, diff만 출력")
    ap.add_argument("--force", action="store_true", help="멱등 무시 재처리")
    ap.add_argument("--show", action="store_true", help="before/after 본문 출력")
    ap.add_argument("--limit", type=int, default=100, help="sweep 최대 건수")
    args = ap.parse_args()

    init_refine_schema()
    if args.sweep:
        sweep(args.limit, args.dry_run, args.force, args.show)
    else:
        process_one(args.news_id, args.dry_run, args.force, args.show)


if __name__ == "__main__":
    main()

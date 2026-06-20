"""CNI Process Queue — Background FIFO processor.

사용자가 대시보드에서 [요약번역] 버튼 클릭 → 큐 등록 → 백그라운드 순차 처리.
CPU 환경: 1건 ~5분, 10건 ~50분.

Usage:
    python -m src.cni.process_queue              # 큐에 있는 모든 processing 뉴스 처리
    python -m src.cni.process_queue --once       # 1건만 처리
"""

import time
import logging
import argparse
from datetime import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.database.models import get_connection
from src.cni.pipeline_service import set_pipeline_status
from src.cni.generate_cni_fields import generate_enhanced
from src.cni.summary_store import update_translation, update_refined, init_cni_tables

LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / f"process_queue_{datetime.now().strftime('%Y%m%d')}.log",
                            encoding="utf-8"),
        logging.StreamHandler(),
    ],
    force=True,
)
logger = logging.getLogger("process_queue")

MAX_RETRIES = 2


def get_queue() -> list:
    """Get news items queued for processing (FIFO by updated_at)."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT id, original_title, original_content, importance_score
        FROM news
        WHERE pipeline_status = 'processing'
          AND original_content IS NOT NULL
          AND LENGTH(original_content) > 100
        ORDER BY updated_at ASC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def enqueue_news(news_id: int) -> dict:
    """큐에 등록: selected → processing."""
    return set_pipeline_status(news_id, "processing")


def process_one(news_id: int, original_title: str, original_content: str,
                retry: int = 0) -> dict:
    """1건 처리: headline → summary → tip → Papago (Qwen2.5 단일 모델).

    Returns: {"ok": bool, "error": str or None, "gen_time": float}
    """
    logger.info(f"{'='*50}")
    logger.info(f"  Processing #{news_id} (retry={retry})")
    logger.info(f"{'='*50}")

    t0 = time.time()

    try:
        result = generate_enhanced(news_id, original_title, original_content,
                                    enable_papago=True)

        if not result:
            dur = time.time() - t0
            logger.error(f"  #{news_id} generate_enhanced returned None ({dur:.0f}s)")
            if retry < MAX_RETRIES:
                logger.info(f"  Retry {retry + 1}/{MAX_RETRIES}...")
                return process_one(news_id, original_title, original_content, retry + 1)
            set_pipeline_status(news_id, "failed")
            return {"ok": False, "error": "generation_failed", "gen_time": dur}

        # DB 저장
        conn = get_connection()
        conn.execute("""
            UPDATE news SET summary_zh=?, title_zh=?, card_headline=?,
                           hansanguk_tip=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
        """, (result.get("summary_zh"), result.get("title_zh"),
              result.get("card_headline"), result.get("hansanguk_tip"), news_id))
        conn.commit()
        conn.close()

        # 번역 저장
        if result.get("summary_ko"):
            update_translation(news_id, result["summary_ko"])
            update_refined(news_id, result["summary_ko"])
            set_pipeline_status(news_id, "translated")
        else:
            # Papago 실패 — summary_zh만 있음
            set_pipeline_status(news_id, "translated")
            logger.warning(f"  #{news_id} no Korean translation (Papago failed)")

        dur = time.time() - t0
        logger.info(f"  #{news_id} DONE in {dur:.0f}s → translated")

        # 비동기 학습 트리거 (그래프 확장 + CQ)
        try:
            from src.kg.learning_loop import trigger_async_learning
            trigger_async_learning(
                news_id, original_title,
                result.get("summary_zh", ""),
                result.get("summary_ko", "")
            )
        except Exception:
            pass  # 학습 실패 무시

        # 성능 로그
        _plog = LOG_DIR / f"perf_{datetime.now().strftime('%Y%m%d')}.log"
        with open(_plog, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat()}|#{news_id}|"
                    f"total={dur:.0f}s|retry={retry}|"
                    f"zh={len(result.get('summary_zh',''))}|"
                    f"ko={len(result.get('summary_ko',''))}|"
                    f"hl={result.get('title_zh','')[:30]}\n")

        return {"ok": True, "error": None, "gen_time": dur}

    except Exception as e:
        dur = time.time() - t0
        logger.error(f"  #{news_id} EXCEPTION: {e} ({dur:.0f}s)")
        if retry < MAX_RETRIES:
            logger.info(f"  Retry {retry + 1}/{MAX_RETRIES}...")
            return process_one(news_id, original_title, original_content, retry + 1)
        set_pipeline_status(news_id, "failed")
        return {"ok": False, "error": str(e), "gen_time": dur}


def run_queue(once: bool = False):
    """큐의 모든 뉴스를 순차 처리.

    Args:
        once: True면 1건만 처리 후 종료
    """
    init_cni_tables()

    queue = get_queue()
    if not queue:
        logger.info("Queue empty — nothing to process.")
        return {"processed": 0, "success": 0, "failed": 0}

    total = len(queue)
    logger.info(f"Queue: {total} news items to process")
    logger.info(f"Estimated time: ~{total * 5} minutes")

    stats = {"processed": 0, "success": 0, "failed": 0, "total_time": 0}

    for i, item in enumerate(queue, 1):
        logger.info(f"\n[{i}/{total}] #{item['id']}: {(item['original_title'] or '')[:40]}...")

        result = process_one(item["id"], item["original_title"], item["original_content"])
        stats["processed"] += 1
        stats["total_time"] += result.get("gen_time", 0)

        if result["ok"]:
            stats["success"] += 1
        else:
            stats["failed"] += 1

        if once:
            break

    # 최종 리포트
    logger.info(f"\n{'='*50}")
    logger.info(f"  QUEUE REPORT")
    logger.info(f"  Processed: {stats['processed']}/{total}")
    logger.info(f"  Success: {stats['success']}")
    logger.info(f"  Failed: {stats['failed']}")
    logger.info(f"  Total time: {stats['total_time']:.0f}s ({stats['total_time']/60:.1f}min)")
    if stats['processed'] > 0:
        logger.info(f"  Avg per news: {stats['total_time']/stats['processed']:.0f}s")
    logger.info(f"{'='*50}")

    return stats


LOCK_FILE = LOG_DIR / "process_queue.lock"


def _acquire_lock() -> bool:
    """PID lock — 동시 실행 방지."""
    import os
    if LOCK_FILE.exists():
        try:
            old_pid = int(LOCK_FILE.read_text().strip())
            # 프로세스 살아있는지 확인
            os.kill(old_pid, 0)
            return False  # 이전 실행 중
        except (ProcessLookupError, ValueError):
            pass  # 이전 프로세스 종료됨 — lock 해제
    LOCK_FILE.write_text(str(os.getpid()))
    return True


def _release_lock():
    if LOCK_FILE.exists():
        LOCK_FILE.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CNI Process Queue")
    parser.add_argument("--once", action="store_true", help="Process only 1 item")
    args = parser.parse_args()

    if not _acquire_lock():
        logger.info("Another process_queue is running — skipping.")
    else:
        try:
            run_queue(once=args.once)
        finally:
            _release_lock()

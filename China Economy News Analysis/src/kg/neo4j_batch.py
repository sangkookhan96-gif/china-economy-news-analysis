"""Neo4j Batch Processor — 기존 뉴스를 비동기로 그래프에 적재.

Usage:
    python -m src.kg.neo4j_batch                # 미처리 전체
    python -m src.kg.neo4j_batch --limit 50     # 50건만
    python -m src.kg.neo4j_batch --stats        # 통계만
"""

import logging
import time
import argparse
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.database.models import get_connection
from src.kg.quality_batch_v2 import extract_quality_v2
from src.kg.extractor import save_validated_result
from src.kg.neo4j_adapter import (
    init_schema, save_extraction_result, get_graph_stats, is_available, close
)

LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / f"neo4j_batch_{datetime.now().strftime('%Y%m%d')}.log",
                            encoding="utf-8"),
        logging.StreamHandler(),
    ],
    force=True,
)
logger = logging.getLogger("neo4j_batch")


def get_unprocessed(limit: int = 100) -> list:
    """KG 미처리 published 뉴스 조회."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT DISTINCT n.id, n.original_title, n.summary_zh, n.summary,
               n.original_content, n.source, n.published_at
        FROM news n
        LEFT JOIN expert_reviews er ON n.id = er.news_id
        LEFT JOIN kg_extraction_log kl ON n.id = kl.news_id
        WHERE (n.pipeline_status = 'published' OR er.publish_status = 'published')
          AND kl.news_id IS NULL
          AND n.original_content IS NOT NULL AND LENGTH(n.original_content) > 100
        ORDER BY n.updated_at DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def process_one(news: dict) -> dict:
    """1건 추출 → SQLite + Neo4j 저장."""
    nid = news["id"]
    title = news["original_title"] or ""
    summary = news["summary_zh"] or news["summary"] or ""
    expert = ""  # 배치에서는 expert_comment 미사용

    # 1. Qwen 추출
    result, _, _ = extract_quality_v2(nid, title, summary, expert)

    if not result.success:
        return {"ok": False, "reason": ",".join(result.failure_types),
                "entities": 0, "events": 0, "relations": 0}

    # 2. SQLite 저장 (기존 로직)
    try:
        save_validated_result(nid, result)
    except Exception as e:
        logger.warning(f"  SQLite save failed: {e}")

    # 3. Neo4j 저장 (비침투 — 실패해도 계속)
    neo_stats = {"entities": 0, "events": 0, "relations": 0, "ok": False}
    try:
        neo_stats = save_extraction_result(nid, title, result)
    except Exception as e:
        logger.warning(f"  Neo4j save failed (non-blocking): {e}")

    return {
        "ok": True,
        "entities": result.entity_count,
        "events": result.event_count,
        "relations": result.relation_count,
        "neo4j": neo_stats.get("ok", False),
    }


def run_batch(limit: int = 100):
    """배치 실행."""
    if not is_available():
        logger.error("Neo4j unavailable — aborting batch")
        return

    init_schema()

    queue = get_unprocessed(limit)
    total = len(queue)
    logger.info(f"=== Neo4j Batch: {total} news to process ===")

    if total == 0:
        logger.info("No unprocessed news.")
        return

    stats = {"ok": 0, "fail": 0, "neo4j_ok": 0,
             "total_entities": 0, "total_events": 0, "total_relations": 0}
    start = time.time()

    for i, news in enumerate(queue, 1):
        nid = news["id"]
        title = (news["original_title"] or "")[:40]
        logger.info(f"[{i:03d}/{total}] #{nid}: {title}...")

        result = process_one(news)

        if result["ok"]:
            stats["ok"] += 1
            stats["total_entities"] += result["entities"]
            stats["total_events"] += result["events"]
            stats["total_relations"] += result["relations"]
            if result.get("neo4j"):
                stats["neo4j_ok"] += 1
            logger.info(f"  E:{result['entities']} V:{result['events']} "
                        f"R:{result['relations']} neo4j:{result.get('neo4j')}")
        else:
            stats["fail"] += 1
            logger.warning(f"  FAIL: {result.get('reason', 'unknown')}")

        if i % 20 == 0:
            elapsed = time.time() - start
            eta = elapsed / i * (total - i)
            logger.info(f"  --- PROGRESS: {i}/{total} | OK:{stats['ok']} "
                        f"FAIL:{stats['fail']} | ETA:{eta/60:.0f}min ---")

    elapsed = time.time() - start
    graph = get_graph_stats()

    report = f"""
{'='*60}
  NEO4J BATCH COMPLETE
  Duration: {elapsed:.0f}s ({elapsed/60:.1f}min)
  Processed: {total}
  Success: {stats['ok']} (Neo4j: {stats['neo4j_ok']})
  Failed: {stats['fail']}
  Entities: {stats['total_entities']}
  Events: {stats['total_events']}
  Relations: {stats['total_relations']}

  Graph Stats: {graph}
{'='*60}
"""
    logger.info(report)
    close()


def show_stats():
    """그래프 통계 출력."""
    stats = get_graph_stats()
    print(f"Neo4j available: {stats.get('available', False)}")
    if stats.get("available"):
        print(f"  Nodes: {stats.get('nodes', 0)}")
        print(f"  Relations: {stats.get('relations', 0)}")
        print(f"  Labels: {stats.get('labels', {})}")
    close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--stats", action="store_true")
    args = parser.parse_args()

    if args.stats:
        show_stats()
    else:
        run_batch(args.limit)

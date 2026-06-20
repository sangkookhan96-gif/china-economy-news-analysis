"""Daily KG Update — detect new reviews and extract to KG.

Finds published expert reviews not yet in KG, runs quality extraction,
normalizes, and generates signals.

Usage:
    python3 run_kg_update.py              # process all unprocessed
    python3 run_kg_update.py --limit 20   # process up to 20
    python3 run_kg_update.py --dry-run    # show what would be processed
"""

import argparse
import json
import time
import logging
import subprocess
from datetime import datetime
from pathlib import Path
import sys
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.database.models import get_connection
from src.database.kg_models import get_kg_connection, init_kg_db
from src.kg.extractor import (
    save_validated_result, EXTRACTION_MODEL, _parse_json_response,
    _validate_and_clean, _log_to_db,
)
from src.kg.validator import validate_extraction
from src.kg.normalizer import run_normalization

LOG_DIR = Path(__file__).resolve().parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
log_file = LOG_DIR / f"kg_update_{datetime.now().strftime('%Y%m%d')}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(),
    ],
    force=True,
)
logger = logging.getLogger("kg_update")

# ── Config (from quality_batch_v2 / production_batch) ──
OLLAMA_URL = "http://localhost:11434/api/generate"
TIMEOUT = 400
HARD_TIMEOUT = 600
SUMMARY_LIMIT = 400
EXPERT_LIMIT = 600
RESTART_EVERY = 10

# Use quality v2 base prompt
from src.kg.quality_batch_v2 import (
    BASE_PROMPT, RELATION_FORCE_PROMPT, RELATION_FALLBACK_PROMPT,
    EVENT_FORCE_PROMPT, VALID_REL_TYPES,
    _trim, _prepare, _call, _parse_array, _try_add_relations,
    extract_quality_v2, restart_ollama,
)


def get_unprocessed_reviews(limit: int = None) -> list:
    """Find published reviews not yet in KG."""
    conn = get_connection()
    cur = conn.cursor()

    sql = """
        SELECT n.id, n.translated_title, n.original_title, n.summary,
               er.expert_comment, er.review_completed_at
        FROM news n
        JOIN expert_reviews er ON n.id = er.news_id
        WHERE er.expert_comment IS NOT NULL
          AND er.publish_status = 'published'
          AND n.id NOT IN (SELECT DISTINCT news_id FROM kg_news_entity_map)
        ORDER BY er.review_completed_at DESC
    """
    if limit:
        sql += f" LIMIT {limit}"

    rows = cur.fetchall() if not limit else conn.execute(sql).fetchall()
    if limit:
        pass  # already fetched
    else:
        rows = conn.execute(sql).fetchall()
    conn.close()
    return rows


def run_update(limit: int = None, dry_run: bool = False):
    """Main update loop."""
    # Ensure KG tables exist
    init_kg_db()

    rows = get_unprocessed_reviews(limit)
    n = len(rows)

    logger.info(f"=== KG UPDATE: {n} unprocessed reviews found ===")

    if dry_run:
        logger.info("DRY RUN — no extraction will be performed")
        for r in rows[:20]:
            title = r["translated_title"] or r["original_title"] or ""
            logger.info(f"  #{r['id']}: {title[:50]}...")
        if n > 20:
            logger.info(f"  ... and {n - 20} more")
        return

    if n == 0:
        logger.info("No new reviews to process. KG is up to date.")
        return

    # Extract
    save_counts = {"saved": 0, "skipped": 0, "entities_new": 0,
                   "entities_merged": 0, "events": 0, "relations": 0}

    start = time.time()

    for i, row in enumerate(rows, 1):
        nid = row["id"]
        title = row["translated_title"] or row["original_title"] or ""
        summary = row["summary"] or ""
        expert = row["expert_comment"] or ""

        if i > 1 and (i - 1) % RESTART_EVERY == 0:
            restart_ollama()

        # 30-doc cooldown
        if i > 1 and (i - 1) % 30 == 0:
            logger.info("  >> Cooldown 60s...")
            time.sleep(60)

        logger.info(f"[{i:03d}/{n}] #{nid}: {title[:50]}...")

        result, _, retry_label = extract_quality_v2(nid, title, summary, expert)

        status = "PASS" if result.success else "FAIL"
        logger.info(f"  {status} | E:{result.entity_count} V:{result.event_count}"
                     f" R:{result.relation_count} | {result.duration_ms}ms")

        if result.success:
            counts = save_validated_result(nid, result)
            save_counts["saved"] += 1
            for k in ["entities_new", "entities_merged", "events", "relations"]:
                save_counts[k] += counts.get(k, 0)
        else:
            save_counts["skipped"] += 1

    duration = time.time() - start

    # Run normalization on new data
    logger.info("\n--- Running normalization ---")
    run_normalization()

    # Summary
    kconn = get_kg_connection()
    kg = {}
    for t in ["kg_entities", "kg_events", "kg_relations"]:
        kg[t] = kconn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    active = kconn.execute("SELECT COUNT(*) FROM kg_entities WHERE status='active'").fetchone()[0]
    kconn.close()

    report = f"""
{'='*60}
  KG UPDATE COMPLETE
  Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}
  Duration: {duration:.0f}s ({duration/60:.1f}min)
{'='*60}
  Processed:    {n}
  Saved:        {save_counts['saved']}
  Skipped:      {save_counts['skipped']}
  New entities: {save_counts['entities_new']}
  Merged:       {save_counts['entities_merged']}
  Events:       {save_counts['events']}
  Relations:    {save_counts['relations']}

  KG Totals:
    Entities:   {kg['kg_entities']} (active: {active})
    Events:     {kg['kg_events']}
    Relations:  {kg['kg_relations']}
{'='*60}
"""
    logger.info(report)
    print(report)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Daily KG Update")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run_update(limit=args.limit, dry_run=args.dry_run)

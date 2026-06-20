"""CNI Pipeline — 2-stage semi-auto news processing.

Stage 1 (auto):  collect → filter → summarize(zh) → KG
Stage 2 (manual): user translates → refine(ko) → review queue
"""

import logging
import time
from datetime import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.cni.news_storage import get_unprocessed_cni_news
from src.cni.news_filter import filter_news
from src.cni.summarizer import summarize_zh
from src.cni.summary_store import (
    init_cni_tables, save_summary, update_refined, mark_kg_processed,
    get_summary, get_cni_stats,
)
from src.cni.postprocess import refine_korean
from src.cni.review_queue import add_to_review_queue

LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / f"cni_pipeline_{datetime.now().strftime('%Y%m%d')}.log",
                            encoding="utf-8"),
        logging.StreamHandler(),
    ],
    force=True,
)
logger = logging.getLogger("cni_pipeline")


def prepare_kg_input(news: dict, summary_zh: str) -> dict:
    """Prepare input for KG extractor. No full_text direct input."""
    return {
        "title": news.get("original_title", ""),
        "summary": summary_zh if summary_zh else (news.get("original_content", "") or "")[:400],
        "expert_comment": "",
    }


def run_auto_stage(limit: int = 50):
    """Stage 1: Automatic processing (filter → summarize → KG).

    Run via cron/timer. No user interaction needed.
    """
    init_cni_tables()

    news_list = get_unprocessed_cni_news(limit)
    n = len(news_list)
    logger.info(f"=== CNI Auto Stage: {n} unprocessed news ===")

    if n == 0:
        logger.info("No new news to process.")
        return

    counts = {"valid": 0, "rejected": 0, "kg_ok": 0, "kg_fail": 0}
    start = time.time()

    for i, news in enumerate(news_list, 1):
        nid = news["id"]
        title = news.get("original_title", "")[:50]
        logger.info(f"[{i:03d}/{n}] #{nid}: {title}...")

        # 1. Filter
        result = filter_news(news)
        if not result["is_valid"]:
            save_summary(nid, filter_result="rejected",
                         filter_reason=result["reason"],
                         translation_status="rejected")
            counts["rejected"] += 1
            logger.info(f"  REJECTED ({result['reason']})")
            continue

        # 2. Summarize (Chinese)
        content = news.get("original_content", "") or ""
        zh_summary = summarize_zh(content)
        logger.info(f"  Summary: {zh_summary[:60]}...")

        # 3. Save (pending translation)
        save_summary(nid, summary_zh=zh_summary,
                     filter_result="valid",
                     translation_status="pending")
        counts["valid"] += 1

        # 4. KG extraction (using existing extractor)
        try:
            from src.kg.quality_batch_v2 import extract_quality_v2
            from src.kg.extractor import save_validated_result

            kg_result, _, _ = extract_quality_v2(
                nid, news.get("original_title", ""),
                zh_summary, "")

            if kg_result.success:
                save_validated_result(nid, kg_result)
                mark_kg_processed(nid)
                counts["kg_ok"] += 1
                logger.info(f"  KG: E:{kg_result.entity_count} V:{kg_result.event_count} R:{kg_result.relation_count}")
            else:
                counts["kg_fail"] += 1
                logger.info(f"  KG: FAIL {kg_result.failure_types}")
        except Exception as e:
            counts["kg_fail"] += 1
            logger.error(f"  KG error: {e}")

    duration = time.time() - start
    stats = get_cni_stats()

    report = f"""
{'='*60}
  CNI AUTO STAGE COMPLETE
  Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}
  Duration: {duration:.0f}s ({duration/60:.1f}min)
{'='*60}
  Processed:    {n}
  Valid:        {counts['valid']}
  Rejected:     {counts['rejected']}
  KG success:   {counts['kg_ok']}
  KG fail:      {counts['kg_fail']}

  --- CNI Totals ---
  Total summaries:    {stats['total']}
  Valid:              {stats['valid']}
  Pending translation: {stats['pending']}
  Translated:         {stats['translated']}
  Refined:            {stats['refined']}
{'='*60}
"""
    logger.info(report)
    print(report)


def run_post_translation(news_id: int) -> dict:
    """Stage 2: After user manually translates, run refinement.

    Called from dashboard UI after user pastes Korean translation.
    """
    row = get_summary(news_id)
    if not row:
        return {"error": f"No summary found for news {news_id}"}

    if not row.get("summary_ko"):
        return {"error": f"No Korean translation yet for news {news_id}"}

    # 1. Refine
    refined = refine_korean(row["summary_ko"])

    # 2. Save
    update_refined(news_id, refined)

    # 3. Add to review queue
    add_to_review_queue(news_id, refined)

    return {"status": "ok", "refined": refined}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    run_auto_stage(args.limit)

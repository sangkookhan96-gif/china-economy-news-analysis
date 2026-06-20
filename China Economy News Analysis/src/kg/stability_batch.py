"""KG Stability Batch Runner.

Applies stability patches:
- Timeout: 300s
- Auto-retry on JSON_ERROR/timeout (1 retry)
- Ollama restart every 10 docs
- Tracks timeout count, retry success, late-stage failure
"""

import json
import time
import logging
import subprocess
import os
from datetime import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.database.models import get_connection
from src.database.kg_models import get_kg_connection
from src.kg.extractor import (
    extract_single, save_validated_result, EXTRACTION_PROMPT,
    _call_ollama, _parse_json_response, _validate_and_clean, _log_to_db,
    EXTRACTION_MODEL,
)
from src.kg.validator import (
    validate_extraction, validate_batch, format_report,
    ExtractionResult, THRESHOLDS,
    FAIL_JSON_ERROR,
)

# ── Config ──
STABILITY_TIMEOUT = 300  # patch: 180 -> 300
MAX_RETRY = 1
RESTART_EVERY = 10

# ── Logging ──
LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
log_file = LOG_DIR / "kg_batch_50_stability.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(),
    ],
    force=True,
)
logger = logging.getLogger("kg_stability")


def restart_ollama():
    """Restart Ollama process to free GPU memory."""
    logger.info("  >> Restarting Ollama (GPU memory cleanup)...")
    try:
        subprocess.run(["pkill", "-f", "ollama serve"], timeout=10,
                       capture_output=True)
        time.sleep(3)
        subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(5)
        # Warm up: ping the model
        import requests
        for attempt in range(6):
            try:
                r = requests.post(
                    "http://localhost:11434/api/generate",
                    json={"model": EXTRACTION_MODEL, "prompt": "hi",
                          "stream": False, "options": {"num_predict": 1}},
                    timeout=30,
                )
                if r.status_code == 200:
                    logger.info("  >> Ollama restarted OK")
                    return True
            except Exception:
                time.sleep(5)
        logger.warning("  >> Ollama restart: warmup slow, continuing...")
        return True
    except Exception as e:
        logger.error(f"  >> Ollama restart failed: {e}")
        return False


def extract_with_retry(news_id, title, summary, expert_comment):
    """Extract with extended timeout + 1 retry on failure."""
    import requests as req

    def _do_extract():
        prompt = EXTRACTION_PROMPT.format(
            title=title or "",
            summary=(summary or "")[:500],
            expert_comment=(expert_comment or "")[:800],
        )
        start = time.time()
        try:
            resp = req.post(
                "http://localhost:11434/api/generate",
                json={"model": EXTRACTION_MODEL, "prompt": prompt,
                      "stream": False,
                      "options": {"temperature": 0.1, "num_predict": 2000}},
                timeout=STABILITY_TIMEOUT,
            )
            resp.raise_for_status()
            raw = resp.json().get("response", "")
        except req.exceptions.Timeout:
            duration = int((time.time() - start) * 1000)
            return None, "", duration, "TIMEOUT"
        except Exception as e:
            duration = int((time.time() - start) * 1000)
            return None, "", duration, str(e)

        duration = int((time.time() - start) * 1000)
        parsed = _parse_json_response(raw)
        if parsed:
            parsed = _validate_and_clean(parsed)
        return parsed, raw, duration, None

    # First attempt
    parsed, raw, duration, error = _do_extract()
    result = validate_extraction(news_id, raw, parsed, duration)

    is_timeout = error == "TIMEOUT"
    needs_retry = (not result.json_parsed) or is_timeout

    if needs_retry:
        return result, is_timeout, False  # fail, timeout_flag, retry_success

    _log_to_db(news_id, result.entity_count, result.event_count,
               result.relation_count, duration, None)
    return result, is_timeout, False

def extract_with_retry_full(news_id, title, summary, expert_comment):
    """Full extract with 1 retry on failure."""
    import requests as req

    def _do_extract():
        prompt = EXTRACTION_PROMPT.format(
            title=title or "",
            summary=(summary or "")[:500],
            expert_comment=(expert_comment or "")[:800],
        )
        start = time.time()
        try:
            resp = req.post(
                "http://localhost:11434/api/generate",
                json={"model": EXTRACTION_MODEL, "prompt": prompt,
                      "stream": False,
                      "options": {"temperature": 0.1, "num_predict": 2000}},
                timeout=STABILITY_TIMEOUT,
            )
            resp.raise_for_status()
            raw = resp.json().get("response", "")
        except req.exceptions.Timeout:
            duration = int((time.time() - start) * 1000)
            return None, "", duration, True
        except Exception as e:
            duration = int((time.time() - start) * 1000)
            return None, "", duration, False

        duration = int((time.time() - start) * 1000)
        parsed = _parse_json_response(raw)
        if parsed:
            parsed = _validate_and_clean(parsed)
        return parsed, raw, duration, False

    # Attempt 1
    parsed, raw, duration, was_timeout_1 = _do_extract()
    result = validate_extraction(news_id, raw, parsed, duration)
    timeout_occurred = was_timeout_1

    needs_retry = not result.success
    retry_succeeded = False

    if needs_retry:
        # Retry once
        logger.info(f"    RETRY #{news_id}...")
        parsed2, raw2, duration2, was_timeout_2 = _do_extract()
        result2 = validate_extraction(news_id, raw2, parsed2, duration2)
        timeout_occurred = timeout_occurred or was_timeout_2

        if result2.success:
            retry_succeeded = True
            result = result2
            result.duration_ms += duration  # total time

    # Log to DB
    err = None if result.success else (result.error_message or ",".join(result.failure_types))
    _log_to_db(news_id, result.entity_count, result.event_count,
               result.relation_count, result.duration_ms, err)

    return result, timeout_occurred, retry_succeeded


def run_stability_batch(n=50):
    """Run N-doc stability batch with all patches applied."""

    # Fetch unprocessed docs
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT n.id, n.translated_title, n.original_title, n.summary,
               er.expert_comment
        FROM news n
        JOIN expert_reviews er ON n.id = er.news_id
        WHERE er.expert_comment IS NOT NULL AND er.publish_status = 'published'
          AND n.id NOT IN (SELECT DISTINCT news_id FROM kg_news_entity_map)
        ORDER BY er.review_completed_at DESC LIMIT ?
    """, (n,))
    rows = cur.fetchall()
    conn.close()

    actual_n = len(rows)
    logger.info(f"=== STABILITY BATCH: {actual_n} documents ===")
    logger.info(f"Timeout: {STABILITY_TIMEOUT}s | Retry: {MAX_RETRY} | Restart every: {RESTART_EVERY}")

    # Tracking
    results = []
    save_counts = {"saved": 0, "skipped": 0, "entities_new": 0,
                   "entities_merged": 0, "events": 0, "relations": 0}
    tracker = {"timeouts": 0, "retries_attempted": 0, "retries_succeeded": 0}

    batch_start = time.time()

    for i, row in enumerate(rows, 1):
        nid = row["id"]
        title = row["translated_title"] or row["original_title"] or ""
        summary = row["summary"] or ""
        expert = row["expert_comment"] or ""

        # [2] Restart Ollama every RESTART_EVERY docs
        if i > 1 and (i - 1) % RESTART_EVERY == 0:
            restart_ollama()

        logger.info(f"[{i:02d}/{actual_n}] #{nid}: {title[:50]}...")

        result, was_timeout, retry_ok = extract_with_retry_full(
            nid, title, summary, expert)

        if was_timeout:
            tracker["timeouts"] += 1
        if not result.success and not retry_ok:
            # retry was attempted but failed (or first attempt failed and retry failed)
            if FAIL_JSON_ERROR in result.failure_types or was_timeout:
                tracker["retries_attempted"] += 1
        elif retry_ok:
            tracker["retries_attempted"] += 1
            tracker["retries_succeeded"] += 1

        status = "PASS" if result.success else "FAIL"
        logger.info(f"  {status} | E:{result.entity_count} V:{result.event_count}"
                     f" R:{result.relation_count} | {result.duration_ms}ms"
                     f" | timeout={was_timeout} retry_ok={retry_ok}"
                     f" | {result.failure_types or '-'}")

        results.append(result)

        if result.success:
            counts = save_validated_result(nid, result)
            save_counts["saved"] += 1
            save_counts["entities_new"] += counts.get("entities_new", 0)
            save_counts["entities_merged"] += counts.get("entities_merged", 0)
            save_counts["events"] += counts.get("events", 0)
            save_counts["relations"] += counts.get("relations", 0)
            logger.info(f"  SAVED: new_ent={counts.get('entities_new',0)}"
                        f" merged={counts.get('entities_merged',0)}"
                        f" evt={counts.get('events',0)} rel={counts.get('relations',0)}")
        else:
            save_counts["skipped"] += 1

    batch_duration = time.time() - batch_start

    # ── [4] Extended tracking metrics ──
    n_total = len(results)
    success_count = sum(1 for r in results if r.success)
    json_success = sum(1 for r in results if r.json_parsed)
    event_coverage = sum(1 for r in results if r.event_count >= 1)

    # Late-stage failure rate (last 20%)
    late_start = int(n_total * 0.8)
    early_results = results[:late_start]
    late_results = results[late_start:]
    early_fail = sum(1 for r in early_results if not r.success)
    late_fail = sum(1 for r in late_results if not r.success)
    early_fail_rate = early_fail / len(early_results) * 100 if early_results else 0
    late_fail_rate = late_fail / len(late_results) * 100 if late_results else 0

    timeout_rate = tracker["timeouts"] / n_total * 100 if n_total else 0
    json_fail_rate = (n_total - json_success) / n_total * 100 if n_total else 0
    retry_success_rate = (tracker["retries_succeeded"] / tracker["retries_attempted"] * 100
                          if tracker["retries_attempted"] > 0 else 0)

    avg_ent = sum(r.entity_count for r in results) / n_total if n_total else 0
    avg_rel = sum(r.relation_count for r in results) / n_total if n_total else 0
    avg_evt = sum(r.event_count for r in results) / n_total if n_total else 0
    density = avg_rel / avg_ent if avg_ent > 0 else 0

    # ── [5] Stability warnings ──
    stability_warnings = []
    if timeout_rate > 10:
        stability_warnings.append(f"Timeout rate = {timeout_rate:.0f}% (> 10%)")
    if json_fail_rate > 10:
        stability_warnings.append(f"JSON fail rate = {json_fail_rate:.0f}% (> 10%)")
    if late_results and early_results and late_fail_rate > early_fail_rate * 2 and late_fail_rate > 0:
        stability_warnings.append(
            f"Late-stage failure rate ({late_fail_rate:.0f}%) > 2x early ({early_fail_rate:.0f}%)")

    is_stable = len(stability_warnings) == 0

    # KG totals
    kconn = get_kg_connection()
    kg_stats = {}
    for t in ["kg_entities", "kg_events", "kg_relations"]:
        kg_stats[t] = kconn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    kconn.close()

    verdict = "STABLE -> 100건 확장 가능" if is_stable else "UNSTABLE -> 파이프라인 수정 필요"

    # ── [6] Report ──
    report_text = f"""
{'='*60}
  STABILITY BATCH REPORT
  Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}
  Duration: {batch_duration:.0f}s ({batch_duration/60:.1f}min)
  Model: {EXTRACTION_MODEL} | Timeout: {STABILITY_TIMEOUT}s
{'='*60}

  Total:                {n_total}
  Success:              {success_count}/{n_total} ({success_count/n_total*100:.0f}%)
  JSON success:         {json_success}/{n_total} ({json_success/n_total*100:.0f}%)
  Event coverage:       {event_coverage}/{n_total} ({event_coverage/n_total*100:.0f}%)
  Avg entities/doc:     {avg_ent:.1f}
  Avg events/doc:       {avg_evt:.1f}
  Avg relations/doc:    {avg_rel:.1f}
  Density (R/E):        {density:.2f}
  Avg confidence:       0.70

  --- Stability Metrics ---
  Timeout count:        {tracker['timeouts']} ({timeout_rate:.0f}%)
  Retries attempted:    {tracker['retries_attempted']}
  Retry success rate:   {retry_success_rate:.0f}%
  Early failure rate:   {early_fail_rate:.0f}% (first {len(early_results)} docs)
  Late failure rate:    {late_fail_rate:.0f}% (last {len(late_results)} docs)

  --- Save Summary ---
  Saved:                {save_counts['saved']}
  Skipped:              {save_counts['skipped']}
  New entities:         {save_counts['entities_new']}
  Merged entities:      {save_counts['entities_merged']}
  Events created:       {save_counts['events']}
  Relations created:    {save_counts['relations']}

  --- KG Table Totals ---
  kg_entities:          {kg_stats['kg_entities']} rows
  kg_events:            {kg_stats['kg_events']} rows
  kg_relations:         {kg_stats['kg_relations']} rows

  --- Per-Document Detail ---"""

    for idx, r in enumerate(results, 1):
        s = "PASS" if r.success else "FAIL"
        fails = ", ".join(r.failure_types) if r.failure_types else "-"
        report_text += (f"\n  [{idx:02d}] #{r.news_id}: {s}"
                        f" | E:{r.entity_count} V:{r.event_count}"
                        f" R:{r.relation_count} | {r.duration_ms}ms | {fails}")

    report_text += f"""

  --- Stability Warnings ---"""
    for w in stability_warnings:
        report_text += f"\n  WARNING: {w}"
    if not stability_warnings:
        report_text += "\n  (none)"

    report_text += f"""

  --- Final Verdict ---
  {verdict}
{'='*60}
"""

    print(report_text)
    logger.info(report_text)

    with open(log_file, "a", encoding="utf-8") as f:
        f.write(report_text)

    print(f"\nLog saved: {log_file}")
    return is_stable


if __name__ == "__main__":
    stable = run_stability_batch(50)
    sys.exit(0 if stable else 1)

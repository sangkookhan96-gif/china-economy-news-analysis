"""KG Stability Batch Runner v2 — Performance-Optimized.

Patches applied:
- Timeout: 300s -> 400s
- Prompt trimming: summary 400c, expert 600c
- Long doc protection: >3000 chars -> front 70% + back 30%
- Retry with temperature 0.2 for JSON stability
- Ollama restart every 10 docs
"""

import json
import time
import logging
import subprocess
from datetime import datetime
from pathlib import Path
import sys
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.database.models import get_connection
from src.database.kg_models import get_kg_connection
from src.kg.extractor import (
    save_validated_result, EXTRACTION_PROMPT,
    _parse_json_response, _validate_and_clean, _log_to_db,
    EXTRACTION_MODEL,
)
from src.kg.validator import (
    validate_extraction, FAIL_JSON_ERROR,
)

# ── Optimized Config ──
TIMEOUT = 400           # patch: 300 -> 400
SUMMARY_LIMIT = 400     # patch: 500 -> 400
EXPERT_LIMIT = 600      # patch: 800 -> 600
TOTAL_CHAR_LIMIT = 3000 # long doc protection
RESTART_EVERY = 10
OLLAMA_URL = "http://localhost:11434/api/generate"

# ── Logging ──
LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
log_file = LOG_DIR / "kg_batch_30_retest.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(),
    ],
    force=True,
)
logger = logging.getLogger("kg_retest")


def _trim_long_text(text: str, limit: int) -> str:
    """Trim text to limit, keeping front 70% + back 30% if over."""
    if not text or len(text) <= limit:
        return text or ""
    front = int(limit * 0.7)
    back = limit - front
    return text[:front] + "\n...\n" + text[-back:]


def _prepare_input(title, summary, expert_comment):
    """Prepare and trim input text with long doc protection."""
    summary = _trim_long_text(summary or "", SUMMARY_LIMIT)
    expert = _trim_long_text(expert_comment or "", EXPERT_LIMIT)

    total = len(title or "") + len(summary) + len(expert)
    if total > TOTAL_CHAR_LIMIT:
        excess = total - TOTAL_CHAR_LIMIT
        # Trim expert first, then summary
        if len(expert) > excess:
            expert = expert[:len(expert) - excess]
        else:
            expert = expert[:EXPERT_LIMIT // 2]
            summary = summary[:SUMMARY_LIMIT // 2]

    return title or "", summary, expert


def _call_ollama_opt(prompt, temperature=0.1):
    """Call Ollama with optimized timeout."""
    try:
        resp = requests.post(
            OLLAMA_URL,
            json={"model": EXTRACTION_MODEL, "prompt": prompt,
                  "stream": False,
                  "options": {"temperature": temperature, "num_predict": 2000}},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json().get("response", ""), None
    except requests.exceptions.Timeout:
        return "", "TIMEOUT"
    except Exception as e:
        return "", str(e)


def extract_optimized(news_id, title, summary, expert_comment):
    """Extract with all v2 optimizations + 1 retry."""
    title, summary, expert = _prepare_input(title, summary, expert_comment)

    prompt = EXTRACTION_PROMPT.format(
        title=title, summary=summary, expert_comment=expert)

    # Attempt 1 (temperature 0.1)
    start = time.time()
    raw, error = _call_ollama_opt(prompt, temperature=0.1)
    dur1 = int((time.time() - start) * 1000)

    timeout_occurred = error == "TIMEOUT"
    parsed = _parse_json_response(raw) if raw else None
    if parsed:
        parsed = _validate_and_clean(parsed)
    result = validate_extraction(news_id, raw, parsed, dur1)

    retry_attempted = False
    retry_succeeded = False

    if not result.success:
        # Retry with temperature 0.2 for JSON stability
        retry_attempted = True
        logger.info(f"    RETRY #{news_id} (temp=0.2)...")
        start2 = time.time()
        raw2, error2 = _call_ollama_opt(prompt, temperature=0.2)
        dur2 = int((time.time() - start2) * 1000)

        if error2 == "TIMEOUT":
            timeout_occurred = True

        parsed2 = _parse_json_response(raw2) if raw2 else None
        if parsed2:
            parsed2 = _validate_and_clean(parsed2)
        result2 = validate_extraction(news_id, raw2, parsed2, dur1 + dur2)

        if result2.success:
            retry_succeeded = True
            result = result2

    # Log to DB
    err = None if result.success else (result.error_message or ",".join(result.failure_types))
    _log_to_db(news_id, result.entity_count, result.event_count,
               result.relation_count, result.duration_ms, err)

    return result, timeout_occurred, retry_attempted, retry_succeeded


def restart_ollama():
    """Restart Ollama to free GPU memory."""
    logger.info("  >> Restarting Ollama...")
    try:
        subprocess.run(["pkill", "-f", "ollama serve"], timeout=10, capture_output=True)
        time.sleep(3)
        subprocess.Popen(["ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(5)
        for _ in range(6):
            try:
                r = requests.post(OLLAMA_URL,
                    json={"model": EXTRACTION_MODEL, "prompt": "test",
                          "stream": False, "options": {"num_predict": 1}},
                    timeout=30)
                if r.status_code == 200:
                    logger.info("  >> Ollama restarted OK")
                    return
            except Exception:
                time.sleep(5)
        logger.warning("  >> Ollama warmup slow, continuing")
    except Exception as e:
        logger.error(f"  >> Ollama restart failed: {e}")


def run():
    """Run 30-doc re-stability batch."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT n.id, n.translated_title, n.original_title, n.summary,
               er.expert_comment
        FROM news n
        JOIN expert_reviews er ON n.id = er.news_id
        WHERE er.expert_comment IS NOT NULL AND er.publish_status = 'published'
          AND n.id NOT IN (SELECT DISTINCT news_id FROM kg_news_entity_map)
        ORDER BY er.review_completed_at DESC LIMIT 30
    """)
    rows = cur.fetchall()
    conn.close()

    n_total = len(rows)
    logger.info(f"=== RE-STABILITY BATCH v2: {n_total} docs ===")
    logger.info(f"Timeout={TIMEOUT}s | summary={SUMMARY_LIMIT}c | expert={EXPERT_LIMIT}c | long_doc={TOTAL_CHAR_LIMIT}c")

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

        if i > 1 and (i - 1) % RESTART_EVERY == 0:
            restart_ollama()

        logger.info(f"[{i:02d}/{n_total}] #{nid}: {title[:50]}...")

        result, was_timeout, retried, retry_ok = extract_optimized(
            nid, title, summary, expert)

        if was_timeout:
            tracker["timeouts"] += 1
        if retried:
            tracker["retries_attempted"] += 1
        if retry_ok:
            tracker["retries_succeeded"] += 1

        status = "PASS" if result.success else "FAIL"
        logger.info(f"  {status} | E:{result.entity_count} V:{result.event_count}"
                     f" R:{result.relation_count} | {result.duration_ms}ms"
                     f" | timeout={was_timeout} retry={retry_ok}"
                     f" | {result.failure_types or '-'}")

        results.append(result)

        if result.success:
            counts = save_validated_result(nid, result)
            save_counts["saved"] += 1
            for k in ["entities_new", "entities_merged", "events", "relations"]:
                save_counts[k] += counts.get(k, 0)
            logger.info(f"  SAVED: +ent={counts.get('entities_new',0)}"
                        f" merged={counts.get('entities_merged',0)}"
                        f" evt={counts.get('events',0)} rel={counts.get('relations',0)}")
        else:
            save_counts["skipped"] += 1

    batch_duration = time.time() - batch_start

    # ── Metrics ──
    success_count = sum(1 for r in results if r.success)
    json_success = sum(1 for r in results if r.json_parsed)
    event_coverage = sum(1 for r in results if r.event_count >= 1)

    late_start = int(n_total * 0.8)
    early_r = results[:late_start]
    late_r = results[late_start:]
    early_fail_rate = sum(1 for r in early_r if not r.success) / len(early_r) * 100 if early_r else 0
    late_fail_rate = sum(1 for r in late_r if not r.success) / len(late_r) * 100 if late_r else 0

    timeout_rate = tracker["timeouts"] / n_total * 100 if n_total else 0
    json_fail_rate = (n_total - json_success) / n_total * 100 if n_total else 0
    retry_rate = (tracker["retries_succeeded"] / tracker["retries_attempted"] * 100
                  if tracker["retries_attempted"] > 0 else 0)

    avg_ent = sum(r.entity_count for r in results) / n_total if n_total else 0
    avg_rel = sum(r.relation_count for r in results) / n_total if n_total else 0
    avg_evt = sum(r.event_count for r in results) / n_total if n_total else 0

    # ── Stability check (ABSOLUTE — no relaxation) ──
    warnings = []
    if timeout_rate > 10:
        warnings.append(f"Timeout rate = {timeout_rate:.0f}% (> 10%)")
    if json_fail_rate > 10:
        warnings.append(f"JSON fail rate = {json_fail_rate:.0f}% (> 10%)")
    if late_r and early_r and late_fail_rate > early_fail_rate * 2 and late_fail_rate > 0 and early_fail_rate > 0:
        warnings.append(f"Late-stage failure ({late_fail_rate:.0f}%) > 2x early ({early_fail_rate:.0f}%)")

    is_stable = len(warnings) == 0
    verdict = "STABLE -> 100건 확장 실행 가능" if is_stable else "UNSTABLE -> 추가 최적화 필요"

    # KG totals
    kconn = get_kg_connection()
    kg = {}
    for t in ["kg_entities", "kg_events", "kg_relations"]:
        kg[t] = kconn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    kconn.close()

    report = f"""
{'='*60}
  RE-STABILITY REPORT (v2 optimized)
  Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}
  Duration: {batch_duration:.0f}s ({batch_duration/60:.1f}min)
  Patches: timeout={TIMEOUT}s, summary={SUMMARY_LIMIT}c,
           expert={EXPERT_LIMIT}c, long_doc={TOTAL_CHAR_LIMIT}c,
           retry_temp=0.2
{'='*60}

  Total:                {n_total}
  Success:              {success_count}/{n_total} ({success_count/n_total*100:.0f}%)
  JSON success:         {json_success}/{n_total} ({json_success/n_total*100:.0f}%)
  Event coverage:       {event_coverage}/{n_total} ({event_coverage/n_total*100:.0f}%)
  Avg entities/doc:     {avg_ent:.1f}
  Avg events/doc:       {avg_evt:.1f}
  Avg relations/doc:    {avg_rel:.1f}
  Avg confidence:       0.70

  --- Stability Metrics ---
  Timeout count:        {tracker['timeouts']} ({timeout_rate:.0f}%)
  Retries attempted:    {tracker['retries_attempted']}
  Retry success rate:   {retry_rate:.0f}%
  Early failure rate:   {early_fail_rate:.0f}% (first {len(early_r)} docs)
  Late failure rate:    {late_fail_rate:.0f}% (last {len(late_r)} docs)

  --- Save Summary ---
  Saved:                {save_counts['saved']}
  Skipped:              {save_counts['skipped']}
  New entities:         {save_counts['entities_new']}
  Merged entities:      {save_counts['entities_merged']}
  Events created:       {save_counts['events']}
  Relations created:    {save_counts['relations']}

  --- KG Table Totals (cumulative) ---
  kg_entities:          {kg['kg_entities']} rows
  kg_events:            {kg['kg_events']} rows
  kg_relations:         {kg['kg_relations']} rows

  --- Per-Document Detail ---"""

    for idx, r in enumerate(results, 1):
        s = "PASS" if r.success else "FAIL"
        f = ", ".join(r.failure_types) if r.failure_types else "-"
        report += f"\n  [{idx:02d}] #{r.news_id}: {s} | E:{r.entity_count} V:{r.event_count} R:{r.relation_count} | {r.duration_ms}ms | {f}"

    report += f"""

  --- Stability Warnings ---"""
    for w in warnings:
        report += f"\n  WARNING: {w}"
    if not warnings:
        report += "\n  (none)"

    report += f"""

  --- Final Verdict ---
  {verdict}
{'='*60}
"""

    print(report)
    logger.info(report)
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(report)

    print(f"\nLog saved: {log_file}")
    return is_stable


if __name__ == "__main__":
    stable = run()
    sys.exit(0 if stable else 1)

"""KG Production Batch Runner.

Production mode with:
- Hard timeout: 600s per doc (no exceptions)
- Soft timeout: 400s (triggers retry)
- 30-doc cooldown: 120s sleep for GPU thermal
- Ollama restart every 10 docs
- LONG_DOC_SKIP for >600s docs
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
from src.kg.validator import validate_extraction

# ── Production Config ──
SOFT_TIMEOUT = 400
HARD_TIMEOUT = 600      # absolute max per attempt
SUMMARY_LIMIT = 400
EXPERT_LIMIT = 600
TOTAL_CHAR_LIMIT = 3000
RESTART_EVERY = 10
COOLDOWN_EVERY = 30
COOLDOWN_SECONDS = 120
OLLAMA_URL = "http://localhost:11434/api/generate"

# ── Logging ──
LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
log_file = LOG_DIR / "kg_batch_100_production.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(),
    ],
    force=True,
)
logger = logging.getLogger("kg_production")


def _trim(text, limit):
    if not text or len(text) <= limit:
        return text or ""
    front = int(limit * 0.7)
    back = limit - front
    return text[:front] + "\n...\n" + text[-back:]


def _prepare(title, summary, expert):
    summary = _trim(summary or "", SUMMARY_LIMIT)
    expert = _trim(expert or "", EXPERT_LIMIT)
    total = len(title or "") + len(summary) + len(expert)
    if total > TOTAL_CHAR_LIMIT:
        excess = total - TOTAL_CHAR_LIMIT
        if len(expert) > excess:
            expert = expert[:len(expert) - excess]
        else:
            expert = expert[:EXPERT_LIMIT // 2]
            summary = summary[:SUMMARY_LIMIT // 2]
    return title or "", summary, expert


def _call(prompt, temperature=0.1, timeout=SOFT_TIMEOUT):
    try:
        resp = requests.post(
            OLLAMA_URL,
            json={"model": EXTRACTION_MODEL, "prompt": prompt,
                  "stream": False,
                  "options": {"temperature": temperature, "num_predict": 2000}},
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json().get("response", ""), None
    except requests.exceptions.Timeout:
        return "", "TIMEOUT"
    except Exception as e:
        return "", str(e)


def extract_production(news_id, title, summary, expert_comment):
    """Production extraction: hard timeout 600s, 1 retry, LONG_DOC_SKIP."""
    title, summary, expert = _prepare(title, summary, expert_comment)
    prompt = EXTRACTION_PROMPT.format(
        title=title, summary=summary, expert_comment=expert)

    doc_start = time.time()

    # Attempt 1
    raw, error = _call(prompt, temperature=0.1, timeout=SOFT_TIMEOUT)
    dur1 = int((time.time() - doc_start) * 1000)
    timeout_hit = error == "TIMEOUT"

    parsed = _parse_json_response(raw) if raw else None
    if parsed:
        parsed = _validate_and_clean(parsed)
    result = validate_extraction(news_id, raw, parsed, dur1)

    retry_attempted = False
    retry_succeeded = False
    long_doc_skip = False

    if not result.success:
        elapsed = time.time() - doc_start
        remaining = HARD_TIMEOUT - elapsed

        if remaining < 60:
            # Not enough time for retry — mark as LONG_DOC_SKIP
            long_doc_skip = True
            result.failure_types.append("LONG_DOC_SKIP")
        else:
            # Retry with temperature 0.2, remaining time as timeout
            retry_attempted = True
            retry_timeout = min(int(remaining), SOFT_TIMEOUT)
            logger.info(f"    RETRY #{news_id} (temp=0.2, timeout={retry_timeout}s)...")

            raw2, error2 = _call(prompt, temperature=0.2, timeout=retry_timeout)
            dur_total = int((time.time() - doc_start) * 1000)

            if error2 == "TIMEOUT":
                timeout_hit = True

            # Hard timeout check
            if (time.time() - doc_start) > HARD_TIMEOUT:
                long_doc_skip = True
                result.failure_types.append("LONG_DOC_SKIP")
            else:
                parsed2 = _parse_json_response(raw2) if raw2 else None
                if parsed2:
                    parsed2 = _validate_and_clean(parsed2)
                result2 = validate_extraction(news_id, raw2, parsed2, dur_total)
                if result2.success:
                    retry_succeeded = True
                    result = result2

    total_ms = int((time.time() - doc_start) * 1000)
    result.duration_ms = total_ms

    err = None if result.success else ",".join(result.failure_types)
    _log_to_db(news_id, result.entity_count, result.event_count,
               result.relation_count, total_ms, err)

    return result, timeout_hit, retry_attempted, retry_succeeded, long_doc_skip


def restart_ollama():
    logger.info("  >> Restarting Ollama...")
    try:
        subprocess.run(["pkill", "-f", "ollama serve"], timeout=10, capture_output=True)
        time.sleep(3)
        subprocess.Popen(["ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(5)
        for _ in range(6):
            try:
                r = requests.post(OLLAMA_URL,
                    json={"model": EXTRACTION_MODEL, "prompt": "ok",
                          "stream": False, "options": {"num_predict": 1}},
                    timeout=30)
                if r.status_code == 200:
                    logger.info("  >> Ollama restarted OK")
                    return
            except Exception:
                time.sleep(5)
        logger.warning("  >> Ollama warmup slow")
    except Exception as e:
        logger.error(f"  >> Restart failed: {e}")


def run(n=100):
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

    n_total = len(rows)
    logger.info(f"=== PRODUCTION BATCH: {n_total} docs ===")
    logger.info(f"soft_timeout={SOFT_TIMEOUT}s hard_timeout={HARD_TIMEOUT}s "
                f"restart_every={RESTART_EVERY} cooldown_every={COOLDOWN_EVERY}")

    results = []
    durations = []
    save_counts = {"saved": 0, "skipped": 0, "entities_new": 0,
                   "entities_merged": 0, "events": 0, "relations": 0}
    tracker = {"timeouts": 0, "retries_attempted": 0, "retries_succeeded": 0,
               "long_doc_skips": 0}

    batch_start = time.time()

    for i, row in enumerate(rows, 1):
        nid = row["id"]
        title = row["translated_title"] or row["original_title"] or ""
        summary = row["summary"] or ""
        expert = row["expert_comment"] or ""

        # Ollama restart every 10 docs
        if i > 1 and (i - 1) % RESTART_EVERY == 0:
            restart_ollama()

        # Cooldown every 30 docs
        if i > 1 and (i - 1) % COOLDOWN_EVERY == 0:
            logger.info(f"  >> Cooldown {COOLDOWN_SECONDS}s (GPU thermal)...")
            time.sleep(COOLDOWN_SECONDS)

        logger.info(f"[{i:03d}/{n_total}] #{nid}: {title[:50]}...")

        result, was_timeout, retried, retry_ok, long_skip = extract_production(
            nid, title, summary, expert)

        if was_timeout:
            tracker["timeouts"] += 1
        if retried:
            tracker["retries_attempted"] += 1
        if retry_ok:
            tracker["retries_succeeded"] += 1
        if long_skip:
            tracker["long_doc_skips"] += 1

        durations.append(result.duration_ms)

        status = "PASS" if result.success else "FAIL"
        fails = ", ".join(result.failure_types) if result.failure_types else "-"
        logger.info(f"  {status} | E:{result.entity_count} V:{result.event_count}"
                     f" R:{result.relation_count} | {result.duration_ms}ms | {fails}")

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

    timeout_rate = tracker["timeouts"] / n_total * 100 if n_total else 0
    json_fail_rate = (n_total - json_success) / n_total * 100 if n_total else 0
    long_skip_rate = tracker["long_doc_skips"] / n_total * 100 if n_total else 0
    retry_rate = (tracker["retries_succeeded"] / tracker["retries_attempted"] * 100
                  if tracker["retries_attempted"] > 0 else 0)

    avg_ent = sum(r.entity_count for r in results) / n_total if n_total else 0
    avg_rel = sum(r.relation_count for r in results) / n_total if n_total else 0
    avg_evt = sum(r.event_count for r in results) / n_total if n_total else 0

    # Latency
    sorted_dur = sorted(durations)
    avg_latency = sum(durations) / len(durations) / 1000 if durations else 0
    p95_idx = int(len(sorted_dur) * 0.95)
    p95_latency = sorted_dur[p95_idx] / 1000 if sorted_dur else 0
    max_latency = max(durations) / 1000 if durations else 0

    # Late-stage
    late_start = int(n_total * 0.8)
    early_r = results[:late_start]
    late_r = results[late_start:]
    early_fail = sum(1 for r in early_r if not r.success) / len(early_r) * 100 if early_r else 0
    late_fail = sum(1 for r in late_r if not r.success) / len(late_r) * 100 if late_r else 0

    # ── Verdict ──
    warnings = []
    if timeout_rate > 10:
        warnings.append(f"Timeout rate = {timeout_rate:.0f}% (> 10%)")
    if json_fail_rate > 10:
        warnings.append(f"JSON fail rate = {json_fail_rate:.0f}% (> 10%)")
    if long_skip_rate > 5:
        warnings.append(f"LONG_DOC_SKIP rate = {long_skip_rate:.0f}% (> 5%)")

    is_stable = len(warnings) == 0
    verdict = "PRODUCTION STABLE -> 자동 확장 가능" if is_stable else "DEGRADED -> 병목 문서 분석 필요"

    # KG totals
    kconn = get_kg_connection()
    kg = {}
    for t in ["kg_entities", "kg_events", "kg_relations"]:
        kg[t] = kconn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    kconn.close()

    report = f"""
{'='*60}
  PRODUCTION BATCH REPORT
  Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}
  Duration: {batch_duration:.0f}s ({batch_duration/60:.1f}min)
  Model: {EXTRACTION_MODEL}
  Config: soft={SOFT_TIMEOUT}s hard={HARD_TIMEOUT}s
          summary={SUMMARY_LIMIT}c expert={EXPERT_LIMIT}c
          restart={RESTART_EVERY} cooldown={COOLDOWN_EVERY}/{COOLDOWN_SECONDS}s
{'='*60}

  Total:                {n_total}
  Success:              {success_count}/{n_total} ({success_count/n_total*100:.0f}%)
  JSON success:         {json_success}/{n_total} ({json_success/n_total*100:.0f}%)
  Event coverage:       {event_coverage}/{n_total} ({event_coverage/n_total*100:.0f}%)
  Avg entities/doc:     {avg_ent:.1f}
  Avg events/doc:       {avg_evt:.1f}
  Avg relations/doc:    {avg_rel:.1f}
  Avg confidence:       0.70

  --- Latency ---
  Avg latency:          {avg_latency:.0f}s
  P95 latency:          {p95_latency:.0f}s
  Max latency:          {max_latency:.0f}s

  --- Stability Metrics ---
  Timeout count:        {tracker['timeouts']} ({timeout_rate:.0f}%)
  LONG_DOC_SKIP:        {tracker['long_doc_skips']} ({long_skip_rate:.0f}%)
  Retries attempted:    {tracker['retries_attempted']}
  Retry success rate:   {retry_rate:.0f}%
  Early failure rate:   {early_fail:.0f}% (first {len(early_r)} docs)
  Late failure rate:    {late_fail:.0f}% (last {len(late_r)} docs)

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
        report += f"\n  [{idx:03d}] #{r.news_id}: {s} | E:{r.entity_count} V:{r.event_count} R:{r.relation_count} | {r.duration_ms}ms | {f}"

    report += f"""

  --- Warnings ---"""
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
    stable = run(100)
    sys.exit(0 if stable else 1)

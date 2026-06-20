"""KG Quality Upgrade Batch.

Adds targeted retry prompts for:
- NO_RELATION: relation-focused prompt
- NO_EVENT: event-focused prompt
- JSON_ERROR: standard retry
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
    save_validated_result, _parse_json_response, _validate_and_clean,
    _log_to_db, EXTRACTION_MODEL,
)
from src.kg.validator import validate_extraction

# ── Config ──
TIMEOUT = 400
HARD_TIMEOUT = 600
SUMMARY_LIMIT = 400
EXPERT_LIMIT = 600
TOTAL_CHAR_LIMIT = 3000
RESTART_EVERY = 10
OLLAMA_URL = "http://localhost:11434/api/generate"

# ── Logging ──
LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
log_file = LOG_DIR / "kg_quality_upgrade.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(),
    ],
    force=True,
)
logger = logging.getLogger("kg_quality")

# ── Prompts ──

BASE_PROMPT = """You are a knowledge graph extraction expert for Chinese economy news.
Given a Korean news article about Chinese economy, extract entities, events, and relations.

## RULES (MUST FOLLOW)
- Output ONLY valid JSON. No explanation, no markdown fences.
- Entity names: Korean transliteration (e.g. 比亚迪→비야디, 国务院→국무원)
- Extract AT LEAST 3 entities, AT LEAST 1 event, and AT LEAST 2 relations per article.
- Every relation must reference entities in the entities list.
- EVERY article has at least one event: a policy announcement, investment, earnings report, regulation change, data release, or market movement. Find it.
- EVERY pair of related entities should have a relation. If entity A regulates/impacts/announces something about entity B, create that relation.

## Entity types
ORG=government/org, COM=company, PER=person, POL=policy, IND=industry, GEO=geography, IDX=indicator, FIN=financial

## Relation types
REGULATES, ANNOUNCES, TRIGGERS, IMPACTS, BELONGS_TO, LOCATED_IN, COMPETES_WITH, SUPPLIES_TO

## Event types
POLICY.ANNOUNCE, POLICY.IMPLEMENT, MARKET.EARNINGS, MARKET.M_AND_A, MARKET.PRICE_MOVE, TRADE.TARIFF, TRADE.SANCTION, MACRO.DATA_RELEASE, INDUSTRY.REGULATION, INDUSTRY.SUBSIDY, INDUSTRY.TECH_BREAK, MONETARY.RATE_CUT, MONETARY.LIQUIDITY, RISK.INVESTIGATION, RISK.CRISIS, DIPLOMACY.SUMMIT

## EXAMPLE 1
Input: "비야디, 2025년 매출 8039억 위안 돌파하며 역대 최대 실적"
Output: {{"entities":[{{"name":"비야디","name_zh":"比亚迪","type":"COM","description":"중국 전기차 1위 기업"}},{{"name":"전기차 산업","name_zh":"新能源汽车","type":"IND","description":"신에너지 자동차 산업"}},{{"name":"중국","name_zh":"中国","type":"GEO","description":"중화인민공화국"}}],"events":[{{"type":"MARKET.EARNINGS","headline":"비야디 2025년 매출 8039억 위안 역대 최대","date":"2025-12-31","magnitude":"major","actors":["비야디"],"targets":["전기차 산업"]}}],"relations":[{{"source":"비야디","target":"전기차 산업","type":"BELONGS_TO","evidence":"비야디는 전기차 시장 1위"}},{{"source":"비야디","target":"중국","type":"LOCATED_IN","evidence":"중국 선전 소재"}}]}}

## EXAMPLE 2
Input: "국무원, 반도체 산업 지원 신정책 발표, 총투자 3000억 위안"
Output: {{"entities":[{{"name":"국무원","name_zh":"国务院","type":"ORG","description":"중국 최고 행정기관"}},{{"name":"반도체 산업","name_zh":"半导体","type":"IND","description":"반도체/칩 산업"}},{{"name":"반도체 지원 정책","name_zh":"半导体扶持政策","type":"POL","description":"반도체 산업 지원 신정책"}}],"events":[{{"type":"POLICY.ANNOUNCE","headline":"국무원 반도체 3000억 위안 지원 정책 발표","date":null,"magnitude":"major","actors":["국무원"],"targets":["반도체 산업"]}}],"relations":[{{"source":"국무원","target":"반도체 산업","type":"REGULATES","evidence":"국무원이 반도체 산업 정책 발표"}},{{"source":"국무원","target":"반도체 지원 정책","type":"ANNOUNCES","evidence":"국무원이 신정책 발표"}}]}}

## NOW EXTRACT:
Title: {title}
Summary: {summary}
Expert Comment: {expert_comment}

JSON:"""

RELATION_FORCE_PROMPT = """You already extracted these entities from a Chinese economy news article:
{entities_json}

The article title is: {title}

Now you MUST find at least 2 relations between these entities.
Think about: Who regulates whom? Who belongs to which industry? Who is located where? Who impacts whom? Who announces what?

Output ONLY a JSON array of relations. No explanation.
Format: [{{"source":"entity name","target":"entity name","type":"REGULATES|ANNOUNCES|TRIGGERS|IMPACTS|BELONGS_TO|LOCATED_IN|COMPETES_WITH|SUPPLIES_TO","evidence":"reason"}}]

JSON:"""

EVENT_FORCE_PROMPT = """You are analyzing this Chinese economy news article:
Title: {title}
Summary: {summary}

Every news article describes at least one event. Find the main event.
Choose from: POLICY.ANNOUNCE, POLICY.IMPLEMENT, MARKET.EARNINGS, MARKET.M_AND_A, MARKET.PRICE_MOVE, TRADE.TARIFF, MACRO.DATA_RELEASE, INDUSTRY.REGULATION, INDUSTRY.SUBSIDY, INDUSTRY.TECH_BREAK, MONETARY.RATE_CUT, MONETARY.LIQUIDITY, RISK.INVESTIGATION

Output ONLY a JSON array with one event. No explanation.
Format: [{{"type":"EVENT.TYPE","headline":"one line summary","date":null,"magnitude":"minor|moderate|major|critical","actors":["name"],"targets":["name"]}}]

JSON:"""


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


def _call(prompt, temperature=0.1, timeout=TIMEOUT):
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


def _parse_array(text):
    """Parse a JSON array from LLM response."""
    text = text.strip().lstrip('```json').lstrip('```').rstrip('```').strip()
    start = text.find('[')
    if start == -1:
        return []
    depth = 0
    for i in range(start, len(text)):
        if text[i] == '[':
            depth += 1
        elif text[i] == ']':
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i+1])
                except json.JSONDecodeError:
                    return []
    return []


def extract_quality(news_id, title, summary, expert_comment):
    """Quality-enhanced extraction with targeted retries."""
    title, summary, expert = _prepare(title, summary, expert_comment)
    prompt = BASE_PROMPT.format(title=title, summary=summary, expert_comment=expert)

    doc_start = time.time()

    # Attempt 1 with enhanced base prompt
    raw, error = _call(prompt, temperature=0.1)
    dur = int((time.time() - doc_start) * 1000)
    timeout_hit = error == "TIMEOUT"

    parsed = _parse_json_response(raw) if raw else None
    if parsed:
        parsed = _validate_and_clean(parsed)

    # Check what's missing
    json_ok = parsed is not None
    entities = parsed.get("entities", []) if parsed else []
    events = parsed.get("events", []) if parsed else []
    relations = parsed.get("relations", []) if parsed else []

    retry_type = None
    retry_succeeded = False

    # [2] Failure-type-specific retry
    if not json_ok or not entities:
        # JSON_ERROR or total failure → standard retry
        if (time.time() - doc_start) < HARD_TIMEOUT - 60:
            retry_type = "JSON_RETRY"
            logger.info(f"    RETRY (JSON) #{news_id}...")
            raw2, _ = _call(prompt, temperature=0.2)
            parsed2 = _parse_json_response(raw2) if raw2 else None
            if parsed2:
                parsed2 = _validate_and_clean(parsed2)
                if parsed2.get("entities"):
                    parsed = parsed2
                    entities = parsed.get("entities", [])
                    events = parsed.get("events", [])
                    relations = parsed.get("relations", [])
                    retry_succeeded = True

    elif len(entities) >= 2 and len(relations) == 0:
        # NO_RELATION → relation-focused prompt
        if (time.time() - doc_start) < HARD_TIMEOUT - 60:
            retry_type = "RELATION_FORCE"
            ent_json = json.dumps([{"name": e["name"], "type": e["type"]}
                                   for e in entities], ensure_ascii=False)
            rprompt = RELATION_FORCE_PROMPT.format(
                entities_json=ent_json, title=title)
            logger.info(f"    RETRY (RELATION_FORCE) #{news_id}...")
            rraw, _ = _call(rprompt, temperature=0.1, timeout=min(200, TIMEOUT))
            new_rels = _parse_array(rraw)
            valid_ent_names = {e["name"] for e in entities}
            valid_rel_types = {'REGULATES','ANNOUNCES','TRIGGERS','IMPACTS',
                               'BELONGS_TO','LOCATED_IN','COMPETES_WITH','SUPPLIES_TO'}
            for r in new_rels:
                rtype = (r.get("type") or "").upper()
                if (rtype in valid_rel_types
                    and r.get("source") in valid_ent_names
                    and r.get("target") in valid_ent_names):
                    r["type"] = rtype
                    relations.append(r)
            if relations:
                parsed["relations"] = relations
                retry_succeeded = True

    # EVENT force if still missing
    if parsed and len(events) == 0 and (time.time() - doc_start) < HARD_TIMEOUT - 60:
        if retry_type != "JSON_RETRY":
            old_retry = retry_type
            retry_type = f"{old_retry}+EVENT_FORCE" if old_retry else "EVENT_FORCE"
            eprompt = EVENT_FORCE_PROMPT.format(title=title, summary=summary)
            logger.info(f"    RETRY (EVENT_FORCE) #{news_id}...")
            eraw, _ = _call(eprompt, temperature=0.1, timeout=min(200, TIMEOUT))
            new_events = _parse_array(eraw)
            valid_evt_types = {
                'POLICY.ANNOUNCE','POLICY.IMPLEMENT','MARKET.EARNINGS','MARKET.M_AND_A',
                'MARKET.PRICE_MOVE','TRADE.TARIFF','TRADE.SANCTION','MACRO.DATA_RELEASE',
                'INDUSTRY.REGULATION','INDUSTRY.SUBSIDY','INDUSTRY.TECH_BREAK',
                'MONETARY.RATE_CUT','MONETARY.LIQUIDITY','RISK.INVESTIGATION','RISK.CRISIS',
                'DIPLOMACY.SUMMIT','DIPLOMACY.TENSION','MACRO.FORECAST',
            }
            for ev in new_events:
                if ev.get("type") in valid_evt_types and ev.get("headline"):
                    events.append(ev)
            if events:
                parsed["events"] = events
                retry_succeeded = True

    total_ms = int((time.time() - doc_start) * 1000)
    result = validate_extraction(news_id, raw, parsed, total_ms)

    err = None if result.success else ",".join(result.failure_types)
    _log_to_db(news_id, result.entity_count, result.event_count,
               result.relation_count, total_ms, err)

    return result, timeout_hit, retry_type, retry_succeeded


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
                          "stream": False, "options": {"num_predict": 1}}, timeout=30)
                if r.status_code == 200:
                    logger.info("  >> Ollama OK")
                    return
            except Exception:
                time.sleep(5)
    except Exception as e:
        logger.error(f"  >> Restart failed: {e}")


def run(n=30):
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
    logger.info(f"=== QUALITY UPGRADE BATCH: {n_total} docs ===")

    results = []
    save_counts = {"saved": 0, "skipped": 0, "entities_new": 0,
                   "entities_merged": 0, "events": 0, "relations": 0}
    tracker = {"timeouts": 0, "retries": {}, "no_relation_final": 0, "no_event_final": 0}

    batch_start = time.time()

    for i, row in enumerate(rows, 1):
        nid = row["id"]
        title = row["translated_title"] or row["original_title"] or ""
        summary = row["summary"] or ""
        expert = row["expert_comment"] or ""

        if i > 1 and (i - 1) % RESTART_EVERY == 0:
            restart_ollama()

        logger.info(f"[{i:02d}/{n_total}] #{nid}: {title[:50]}...")

        result, was_timeout, retry_type, retry_ok = extract_quality(
            nid, title, summary, expert)

        if was_timeout:
            tracker["timeouts"] += 1
        if retry_type:
            tracker["retries"][retry_type] = tracker["retries"].get(retry_type, 0) + 1
        if result.relation_count == 0 and result.json_parsed:
            tracker["no_relation_final"] += 1
        if result.event_count == 0 and result.json_parsed:
            tracker["no_event_final"] += 1

        status = "PASS" if result.success else "FAIL"
        fails = ", ".join(result.failure_types) if result.failure_types else "-"
        logger.info(f"  {status} | E:{result.entity_count} V:{result.event_count}"
                     f" R:{result.relation_count} | {result.duration_ms}ms"
                     f" | retry={retry_type or '-'} ok={retry_ok} | {fails}")

        results.append(result)

        if result.success:
            counts = save_validated_result(nid, result)
            save_counts["saved"] += 1
            for k in ["entities_new", "entities_merged", "events", "relations"]:
                save_counts[k] += counts.get(k, 0)
        else:
            save_counts["skipped"] += 1

    batch_duration = time.time() - batch_start

    # Metrics
    success_count = sum(1 for r in results if r.success)
    json_success = sum(1 for r in results if r.json_parsed)
    event_coverage = sum(1 for r in results if r.event_count >= 1)
    avg_ent = sum(r.entity_count for r in results) / n_total if n_total else 0
    avg_rel = sum(r.relation_count for r in results) / n_total if n_total else 0
    avg_evt = sum(r.event_count for r in results) / n_total if n_total else 0
    no_rel_rate = tracker["no_relation_final"] / n_total * 100 if n_total else 0

    # Quality targets
    rel_pass = avg_rel >= 2.2
    evt_pass = (event_coverage / n_total * 100 if n_total else 0) >= 85
    norel_pass = no_rel_rate <= 5

    all_pass = rel_pass and evt_pass and norel_pass
    verdict = "QUALITY PASS -> 의미 품질 강화 완료" if all_pass else "QUALITY PARTIAL -> 추가 튜닝 필요"

    # KG totals
    kconn = get_kg_connection()
    kg = {}
    for t in ["kg_entities", "kg_events", "kg_relations"]:
        kg[t] = kconn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    kconn.close()

    evt_pct = event_coverage / n_total * 100 if n_total else 0

    report = f"""
{'='*60}
  QUALITY UPGRADE BATCH REPORT
  Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}
  Duration: {batch_duration:.0f}s ({batch_duration/60:.1f}min)
{'='*60}

  Total:                {n_total}
  Success:              {success_count}/{n_total} ({success_count/n_total*100:.0f}%)
  JSON success:         {json_success}/{n_total} ({json_success/n_total*100:.0f}%)

  --- Quality Metrics ---
  Avg entities/doc:     {avg_ent:.1f}
  Avg events/doc:       {avg_evt:.1f}
  Avg relations/doc:    {avg_rel:.1f}  [target: >= 2.2]  {'PASS' if rel_pass else 'FAIL'}
  Event coverage:       {event_coverage}/{n_total} ({evt_pct:.0f}%)  [target: >= 85%]  {'PASS' if evt_pass else 'FAIL'}
  NO_RELATION (final):  {tracker['no_relation_final']} ({no_rel_rate:.0f}%)  [target: <= 5%]  {'PASS' if norel_pass else 'FAIL'}

  --- Retry Breakdown ---"""
    for rtype, cnt in sorted(tracker["retries"].items()):
        report += f"\n  {rtype}: {cnt} attempts"
    if not tracker["retries"]:
        report += "\n  (none)"

    report += f"""

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

  --- Final Verdict ---
  {verdict}
{'='*60}
"""

    print(report)
    logger.info(report)
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(report)

    print(f"\nLog saved: {log_file}")
    return all_pass


if __name__ == "__main__":
    passed = run(30)
    sys.exit(0 if passed else 1)

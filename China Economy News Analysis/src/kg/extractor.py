"""KG Entity/Event/Relation Extractor v2.

Validation-first extraction pipeline.
All extractions must pass validate_extraction() before DB save.
confidence < 0.6 → never saved. Duplicate entities → merge.
"""

import json
import hashlib
import logging
import time
import re
import requests
from datetime import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from src.database.kg_models import get_kg_connection
from src.database.models import get_connection
from src.kg.validator import (
    validate_extraction, validate_batch, format_report,
    save_report_to_log, ExtractionResult, THRESHOLDS,
)

# ── Logging ──
LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
_log_file = LOG_DIR / f"kg_extraction_{datetime.now().strftime('%Y%m%d')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(_log_file, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

# ── Config ──
OLLAMA_URL = "http://localhost:11434/api/generate"
EXTRACTION_MODEL = "qwen2.5:14b"
MIN_CONFIDENCE_TO_SAVE = THRESHOLDS["min_confidence_to_save"]  # 0.6

# ── Type sets from CEKG design ──
ENTITY_TYPES = {'ORG', 'COM', 'PER', 'POL', 'IND', 'GEO', 'IDX', 'FIN'}

EVENT_TYPES = {
    'POLICY.ANNOUNCE', 'POLICY.IMPLEMENT', 'POLICY.AMEND', 'POLICY.REPEAL',
    'MONETARY.RATE_CUT', 'MONETARY.RATE_HIKE', 'MONETARY.RRR_CUT', 'MONETARY.LIQUIDITY',
    'MARKET.IPO', 'MARKET.EARNINGS', 'MARKET.M_AND_A', 'MARKET.DEFAULT', 'MARKET.PRICE_MOVE',
    'TRADE.TARIFF', 'TRADE.SANCTION', 'TRADE.AGREEMENT',
    'MACRO.DATA_RELEASE', 'MACRO.FORECAST',
    'INDUSTRY.REGULATION', 'INDUSTRY.SUBSIDY', 'INDUSTRY.TECH_BREAK',
    'DIPLOMACY.SUMMIT', 'DIPLOMACY.TENSION',
    'RISK.CRISIS', 'RISK.INVESTIGATION',
}

RELATION_TYPES = {
    'REGULATES', 'ANNOUNCES', 'TRIGGERS', 'IMPACTS',
    'BELONGS_TO', 'LOCATED_IN', 'COMPETES_WITH', 'SUPPLIES_TO',
    'MEASURES', 'SUCCEEDS', 'CONTRADICTS', 'SUPERSEDES',
    'RELATED_TO',
}

# ── Few-shot Prompt ──
EXTRACTION_PROMPT = """You are a knowledge graph extraction expert for Chinese economy news.
Given a Korean news article about Chinese economy, extract entities, events, and relations.

## RULES (MUST FOLLOW)
- Output ONLY valid JSON. No explanation, no markdown fences.
- Entity names: Korean transliteration (e.g. 比亚迪→비야디, 国务院→국무원)
- Extract AT LEAST 3 entities and AT LEAST 1 relation per article.
- Every relation must reference entities that appear in the entities list.

## Entity types
ORG=government/org, COM=company, PER=person, POL=policy, IND=industry, GEO=geography, IDX=indicator, FIN=financial

## Relation types
REGULATES, ANNOUNCES, TRIGGERS, IMPACTS, BELONGS_TO, LOCATED_IN, COMPETES_WITH, SUPPLIES_TO

## Event types
POLICY.ANNOUNCE, POLICY.IMPLEMENT, MARKET.EARNINGS, MARKET.M_AND_A, MARKET.PRICE_MOVE, TRADE.TARIFF, TRADE.SANCTION, MACRO.DATA_RELEASE, INDUSTRY.REGULATION, INDUSTRY.SUBSIDY, INDUSTRY.TECH_BREAK, MONETARY.RATE_CUT, MONETARY.LIQUIDITY, RISK.INVESTIGATION, RISK.CRISIS, DIPLOMACY.SUMMIT

## EXAMPLE 1
Input: "비야디, 2025년 매출 8039억 위안 돌파하며 역대 최대 실적"
Output: {{"entities":[{{"name":"비야디","name_zh":"比亚迪","type":"COM","description":"중국 전기차 1위 기업"}},{{"name":"전기차 산업","name_zh":"新能源汽车","type":"IND","description":"신에너지 자동차 산업"}},{{"name":"중국","name_zh":"中国","type":"GEO","description":"중화인민공화국"}}],"events":[{{"type":"MARKET.EARNINGS","headline":"비야디, 2025년 매출 8039억 위안 역대 최대","date":"2025-12-31","magnitude":"major","actors":["비야디"],"targets":["전기차 산업"]}}],"relations":[{{"source":"비야디","target":"전기차 산업","type":"BELONGS_TO","evidence":"비야디는 중국 전기차 시장 1위 기업"}},{{"source":"비야디","target":"중국","type":"LOCATED_IN","evidence":"중국 선전 소재 기업"}}]}}

## EXAMPLE 2
Input: "국무원, 반도체 산업 지원 신정책 발표, 총투자 3000억 위안"
Output: {{"entities":[{{"name":"국무원","name_zh":"国务院","type":"ORG","description":"중국 최고 행정기관"}},{{"name":"반도체 산업","name_zh":"半导体","type":"IND","description":"반도체/칩 산업"}},{{"name":"반도체 지원 정책","name_zh":"半导体扶持政策","type":"POL","description":"반도체 산업 지원 신정책"}}],"events":[{{"type":"POLICY.ANNOUNCE","headline":"국무원, 반도체 산업 지원 3000억 위안 정책 발표","date":null,"magnitude":"major","actors":["국무원"],"targets":["반도체 산업"]}}],"relations":[{{"source":"국무원","target":"반도체 산업","type":"REGULATES","evidence":"국무원이 반도체 산업 지원 정책 발표"}},{{"source":"국무원","target":"반도체 지원 정책","type":"ANNOUNCES","evidence":"국무원이 신정책을 발표"}}]}}

## NOW EXTRACT FROM THIS ARTICLE:
Title: {title}
Summary: {summary}
Expert Comment: {expert_comment}

JSON:"""


def _generate_id(prefix: str, type_code: str, name: str) -> str:
    """Generate deterministic KG ID from name."""
    h = hashlib.md5(name.encode()).hexdigest()[:8]
    return f"KG-{prefix}-{type_code}-{h}"


def _call_ollama(prompt: str, model: str = EXTRACTION_MODEL, timeout: int = 180) -> str:
    """Call Ollama API and return response text."""
    try:
        resp = requests.post(
            OLLAMA_URL,
            json={"model": model, "prompt": prompt, "stream": False,
                  "options": {"temperature": 0.1, "num_predict": 2000}},
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json().get("response", "")
    except Exception as e:
        logger.error(f"Ollama call failed: {e}")
        return ""


def _parse_json_response(text: str) -> dict | None:
    """Extract JSON from LLM response, handling markdown fences."""
    text = text.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    text = text.strip()

    start = text.find('{')
    if start == -1:
        return None

    depth = 0
    for i in range(start, len(text)):
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i+1])
                except json.JSONDecodeError:
                    return None
    return None


def _validate_and_clean(data: dict) -> dict:
    """Validate types and clean extracted data."""
    valid_entities = []
    for e in data.get("entities", []):
        etype = (e.get("type") or "").upper()
        if etype in ENTITY_TYPES and e.get("name"):
            e["type"] = etype
            valid_entities.append(e)

    valid_events = []
    for ev in data.get("events", []):
        etype = ev.get("type", "")
        if etype in EVENT_TYPES and ev.get("headline"):
            valid_events.append(ev)

    valid_relations = []
    for r in data.get("relations", []):
        rtype = (r.get("type") or "").upper()
        if rtype in RELATION_TYPES and r.get("source") and r.get("target"):
            r["type"] = rtype
            valid_relations.append(r)

    return {
        "entities": valid_entities,
        "events": valid_events,
        "relations": valid_relations,
    }


def extract_single(news_id: int, title: str, summary: str,
                    expert_comment: str) -> ExtractionResult:
    """Extract from one news article and return validated result.

    Does NOT save to DB. Caller decides whether to save based on validation.
    """
    prompt = EXTRACTION_PROMPT.format(
        title=title or "",
        summary=(summary or "")[:500],
        expert_comment=(expert_comment or "")[:800],
    )

    start_time = time.time()
    raw = _call_ollama(prompt)
    duration_ms = int((time.time() - start_time) * 1000)

    if not raw:
        result = validate_extraction(news_id, "", None, duration_ms)
        result.error_message = "Ollama returned empty response"
        _log_to_db(news_id, 0, 0, 0, duration_ms, result.error_message)
        return result

    parsed = _parse_json_response(raw)
    if parsed:
        parsed = _validate_and_clean(parsed)

    result = validate_extraction(news_id, raw, parsed, duration_ms)

    _log_to_db(news_id, result.entity_count, result.event_count,
               result.relation_count, duration_ms,
               result.error_message if not result.success else None)

    return result


def _log_to_db(news_id, entities, events, relations, duration_ms, error):
    """Log extraction attempt to kg_extraction_log."""
    try:
        conn = get_kg_connection()
        conn.execute("""
            INSERT INTO kg_extraction_log
            (news_id, entities_extracted, events_extracted, relations_extracted,
             extraction_method, duration_ms, error_message)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (news_id, entities, events, relations,
              f"ollama_{EXTRACTION_MODEL}", duration_ms, error))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Failed to log extraction: {e}")


def save_validated_result(news_id: int, result: ExtractionResult) -> dict:
    """Save a VALIDATED extraction to KG tables.

    Rules enforced:
    - confidence < 0.6 → not saved
    - duplicate entity (same ID) → merge (increment mention_count)
    """
    if not result.success or not result.parsed_data:
        return {"error": "Cannot save: validation not passed", "saved": False}

    conn = get_kg_connection()
    today = datetime.now().strftime("%Y-%m-%d")
    entity_name_to_id = {}
    counts = {"entities_new": 0, "entities_merged": 0,
              "events": 0, "relations": 0, "relations_skipped_low_conf": 0}

    data = result.parsed_data

    # 1. Entities (merge duplicates)
    for e in data.get("entities", []):
        name = e["name"].strip()
        etype = e["type"]
        eid = _generate_id("ENT", etype, name)
        entity_name_to_id[name] = eid

        existing = conn.execute(
            "SELECT kg_entity_id, mention_count FROM kg_entities WHERE kg_entity_id = ?",
            (eid,)
        ).fetchone()

        if existing:
            conn.execute("""
                UPDATE kg_entities
                SET mention_count = mention_count + 1,
                    last_seen_date = ?,
                    updated_at = datetime('now')
                WHERE kg_entity_id = ?
            """, (today, eid))
            counts["entities_merged"] += 1
        else:
            aliases = [name]
            if e.get("name_zh"):
                aliases.append(e["name_zh"])
            conn.execute("""
                INSERT INTO kg_entities
                (kg_entity_id, canonical_name, canonical_name_zh, aliases,
                 entity_type, description, first_seen_date, last_seen_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (eid, name, e.get("name_zh", ""),
                  json.dumps(aliases, ensure_ascii=False),
                  etype, e.get("description", ""), today, today))
            counts["entities_new"] += 1

        conn.execute("""
            INSERT INTO kg_news_entity_map (news_id, kg_entity_id, role, extraction_confidence)
            VALUES (?, ?, 'mention', 0.7)
        """, (news_id, eid))

    # 2. Events
    for ev in data.get("events", []):
        etype = ev["type"]
        type_code = etype.split(".")[-1] if "." in etype else etype
        evid = _generate_id("EVT", type_code, ev["headline"])

        existing = conn.execute(
            "SELECT kg_event_id FROM kg_events WHERE kg_event_id = ?", (evid,)
        ).fetchone()
        if not existing:
            actors = json.dumps(
                [entity_name_to_id.get(a, a) for a in ev.get("actors", [])],
                ensure_ascii=False)
            targets = json.dumps(
                [entity_name_to_id.get(t, t) for t in ev.get("targets", [])],
                ensure_ascii=False)
            conn.execute("""
                INSERT INTO kg_events
                (kg_event_id, event_type, headline, event_date, magnitude,
                 actors, targets, source_news_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (evid, etype, ev["headline"], ev.get("date"),
                  ev.get("magnitude", "moderate"), actors, targets, news_id))
            counts["events"] += 1

        conn.execute("""
            INSERT INTO kg_news_event_map (news_id, kg_event_id, is_primary)
            VALUES (?, ?, 1)
        """, (news_id, evid))

    # 3. Relations (confidence < 0.6 → skip)
    base_confidence = 0.7  # default for LLM-extracted relations
    for r in data.get("relations", []):
        if base_confidence < MIN_CONFIDENCE_TO_SAVE:
            counts["relations_skipped_low_conf"] += 1
            continue

        src_id = entity_name_to_id.get(r["source"].strip())
        tgt_id = entity_name_to_id.get(r["target"].strip())
        if not src_id or not tgt_id:
            continue

        rid = _generate_id("REL", r["type"], f"{src_id}-{tgt_id}-{r['type']}")
        existing = conn.execute(
            "SELECT kg_relation_id FROM kg_relations WHERE kg_relation_id = ?", (rid,)
        ).fetchone()
        if not existing:
            conn.execute("""
                INSERT INTO kg_relations
                (kg_relation_id, source_id, target_id, relation_type,
                 confidence, strength, evidence, status)
                VALUES (?, ?, ?, ?, ?, 'medium', ?, 'active')
            """, (rid, src_id, tgt_id, r["type"], base_confidence,
                  r.get("evidence", "")))
            counts["relations"] += 1

    conn.commit()
    conn.close()
    counts["saved"] = True
    return counts


# ── Public API ──

def run_sample_test(n: int = 5) -> str:
    """Run extraction on N recent reviewed news, validate, and return report.

    Does NOT save to DB. For validation only.
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT n.id, n.translated_title, n.original_title, n.summary,
               er.expert_comment
        FROM news n
        JOIN expert_reviews er ON n.id = er.news_id
        WHERE er.expert_comment IS NOT NULL AND er.publish_status = 'published'
        ORDER BY er.review_completed_at DESC LIMIT ?
    """, (n,))
    rows = cur.fetchall()
    conn.close()

    logger.info(f"=== Sample test: {len(rows)} documents ===")
    results = []
    for row in rows:
        nid = row["id"]
        title = row["translated_title"] or row["original_title"] or ""
        summary = row["summary"] or ""
        expert = row["expert_comment"] or ""

        logger.info(f"Extracting #{nid}: {title[:50]}...")
        result = extract_single(nid, title, summary, expert)
        logger.info(f"  -> {'PASS' if result.success else 'FAIL'}"
                    f" E:{result.entity_count} V:{result.event_count}"
                    f" R:{result.relation_count} {result.duration_ms}ms"
                    f" {result.failure_types}")
        results.append(result)

    report = validate_batch(results)
    report_text = format_report(report)
    log_path = save_report_to_log(report_text)

    logger.info(f"Report saved to {log_path}")
    print(report_text)
    return report_text


def run_batch_save(n: int = 30) -> str:
    """Run extraction on N news, validate each, save only passing results.

    Prerequisite: sample test must have passed first.
    """
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

    logger.info(f"=== Batch save: {len(rows)} documents ===")
    results = []
    save_counts = {"saved": 0, "skipped": 0}

    for row in rows:
        nid = row["id"]
        title = row["translated_title"] or row["original_title"] or ""
        summary = row["summary"] or ""
        expert = row["expert_comment"] or ""

        logger.info(f"Extracting #{nid}: {title[:50]}...")
        result = extract_single(nid, title, summary, expert)
        results.append(result)

        if result.success:
            counts = save_validated_result(nid, result)
            save_counts["saved"] += 1
            logger.info(f"  -> SAVED {counts}")
        else:
            save_counts["skipped"] += 1
            logger.info(f"  -> SKIPPED (validation fail: {result.failure_types})")

    report = validate_batch(results)
    report_text = format_report(report)
    report_text += f"\n\n  --- Save Summary ---\n"
    report_text += f"  Saved: {save_counts['saved']}, Skipped: {save_counts['skipped']}\n"

    # Add KG stats
    kconn = get_kg_connection()
    for table in ['kg_entities', 'kg_events', 'kg_relations']:
        count = kconn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        report_text += f"  {table}: {count} rows\n"
    kconn.close()

    log_path = save_report_to_log(report_text)
    logger.info(f"Report saved to {log_path}")
    print(report_text)
    return report_text


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "batch":
        run_batch_save(30)
    else:
        run_sample_test(5)

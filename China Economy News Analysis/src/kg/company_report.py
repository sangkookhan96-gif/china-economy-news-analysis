"""Company Intelligence Report Generator.

Generates actionable company reports from KG signals, events, and relations.
"""

import json
import logging
import requests
import time
from datetime import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from src.database.kg_models import get_kg_connection
from src.kg.signal_engine import generate_signals, aggregate_signals
from src.kg.query import search_entity, get_entity_relations, get_entity_events

LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
log_file = LOG_DIR / "company_report.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(),
    ],
    force=True,
)
logger = logging.getLogger("company_report")

OLLAMA_URL = "http://localhost:11434/api/generate"
REPORT_MODEL = "qwen2.5:14b"

VERDICT_PROMPT = """You are a Chinese economy investment analyst. Based on the data below, write a brief company assessment in Korean.

Company: {company}
Type: {entity_type}

Recent Events:
{events}

Signals:
{signals}

Relations:
{relations}

Write in Korean:
1. 종합 판단 (성장 / 리스크 / 중립) — one word + 1-2 sentence explanation
2. 한 줄 결론 (투자 관점에서 한 문장)

Be specific. Reference actual events and data. Output in Korean only."""


def generate_company_report(company_name: str) -> str:
    """Generate full intelligence report for a company."""
    lines = []
    lines.append("=" * 60)
    lines.append(f"  COMPANY INTELLIGENCE REPORT: {company_name}")
    lines.append(f"  Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("=" * 60)

    # Find entity
    entities = search_entity(company_name)
    if not entities:
        msg = f"  Entity '{company_name}' not found in KG."
        lines.append(msg)
        return "\n".join(lines)

    ent = entities[0]
    eid = ent["kg_entity_id"]
    lines.append(f"\n  Entity: {ent['canonical_name']} ({ent['entity_type']})")
    lines.append(f"  Chinese: {ent.get('canonical_name_zh', '-')}")
    lines.append(f"  Mentions: {ent['mention_count']}")

    # 1. Events
    events = get_entity_events(eid)
    lines.append(f"\n  --- 1. 핵심 이벤트 ({len(events)}건) ---")
    for i, ev in enumerate(events[:5], 1):
        lines.append(f"  {i}. [{ev['magnitude']}] {ev['headline']}")
        lines.append(f"     Type: {ev['event_type']} | Date: {ev.get('event_date', '-')}")

    # 2. Relations
    rels = get_entity_relations(eid)
    all_rels = rels["outgoing"] + rels["incoming"]
    lines.append(f"\n  --- 2. 관계 네트워크 ({len(all_rels)}개) ---")
    for r in rels["outgoing"][:5]:
        lines.append(f"  → [{r['relation_type']}] {r['target_name']} ({r['target_type']})")
    for r in rels["incoming"][:5]:
        lines.append(f"  ← [{r['relation_type']}] {r['source_name']} ({r['source_type']})")

    # 3. Signals
    signals = generate_signals(company_name)
    agg = aggregate_signals(signals)
    company_agg = agg.get(ent["canonical_name"], agg.get(company_name, {}))

    lines.append(f"\n  --- 3. 시그널 요약 ---")
    if company_agg:
        lines.append(f"  Net Direction: {company_agg.get('net_direction', '-')}")
        lines.append(f"  Total: {company_agg.get('total_signals', 0)} "
                     f"(+{company_agg.get('positive', 0)} "
                     f"-{company_agg.get('negative', 0)} "
                     f"={company_agg.get('neutral', 0)})")
        lines.append(f"  Avg Confidence: {company_agg.get('avg_confidence', 0):.2f}")

        pos_sigs = [s for s in company_agg.get("signals", []) if s["direction"] == "POSITIVE"]
        neg_sigs = [s for s in company_agg.get("signals", []) if s["direction"] == "NEGATIVE"]

        if pos_sigs:
            lines.append("\n  Positive Signals:")
            for s in pos_sigs[:3]:
                lines.append(f"    + {s['headline'][:60]} (conf={s['confidence']})")

        if neg_sigs:
            lines.append("\n  Risk Signals:")
            for s in neg_sigs[:3]:
                lines.append(f"    - {s['headline'][:60]} (conf={s['confidence']})")
    else:
        # Aggregate across all matching entities
        all_sigs = signals
        if all_sigs:
            pos = [s for s in all_sigs if s["direction"] == "POSITIVE"]
            neg = [s for s in all_sigs if s["direction"] == "NEGATIVE"]
            lines.append(f"  Total signals: {len(all_sigs)} (+{len(pos)} -{len(neg)})")
            if pos:
                lines.append("  Positive:")
                for s in pos[:3]:
                    lines.append(f"    + {s['headline'][:60]} (conf={s['confidence']})")
            if neg:
                lines.append("  Risk:")
                for s in neg[:3]:
                    lines.append(f"    - {s['headline'][:60]} (conf={s['confidence']})")

    # 4. LLM Verdict
    lines.append(f"\n  --- 4. 종합 판단 ---")

    events_text = "\n".join(f"- [{e['magnitude']}] {e['headline']}" for e in events[:5])
    signals_text = "\n".join(
        f"- [{s['direction']}] {s['headline'][:50]} (conf={s['confidence']})"
        for s in signals[:5])
    rels_text = "\n".join(
        f"- [{r['relation_type']}] → {r['target_name']}" for r in rels["outgoing"][:5])

    try:
        prompt = VERDICT_PROMPT.format(
            company=ent["canonical_name"],
            entity_type=ent["entity_type"],
            events=events_text or "(no events)",
            signals=signals_text or "(no signals)",
            relations=rels_text or "(no relations)",
        )
        resp = requests.post(
            OLLAMA_URL,
            json={"model": REPORT_MODEL, "prompt": prompt,
                  "stream": False, "options": {"temperature": 0.3, "num_predict": 500}},
            timeout=300,
        )
        resp.raise_for_status()
        verdict = resp.json().get("response", "").strip()
        lines.append(f"  {verdict}")
    except Exception as e:
        lines.append(f"  (LLM verdict generation failed: {e})")

    lines.append("\n" + "=" * 60)

    report = "\n".join(lines)
    logger.info(report)
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"\n{'#'*60}\n{datetime.now().isoformat()}\n{report}\n")

    return report


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--company", default="CATL")
    args = parser.parse_args()
    print(generate_company_report(args.company))

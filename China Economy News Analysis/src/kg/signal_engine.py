"""KG Signal Engine.

Converts KG events + relations into actionable investment/industry signals.
Signal types: POLICY, INVESTMENT, RISK, DEMAND
"""

import json
import re
import logging
import requests
import time
from datetime import datetime
from collections import defaultdict
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from src.database.kg_models import get_kg_connection

LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
log_file = LOG_DIR / "kg_signal_engine.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(),
    ],
    force=True,
)
logger = logging.getLogger("kg_signal")

OLLAMA_URL = "http://localhost:11434/api/generate"
SIGNAL_MODEL = "qwen2.5:14b"

# ── Signal classification keywords ──

POSITIVE_KW = [
    "지원", "확대", "성장", "증가", "상승", "돌파", "투자", "증설",
    "신정책", "혁신", "협력", "수출", "확장", "개방", "보조금",
    "support", "growth", "expand", "invest", "boost", "subsidy",
    "突破", "增长", "扩大", "投资", "支持", "补贴",
]

NEGATIVE_KW = [
    "하락", "감소", "압박", "위축", "규제", "제재", "리스크", "위기",
    "불이행", "제한", "축소", "적자", "처벌", "과태료", "벌금",
    "decline", "risk", "sanction", "restriction", "penalty",
    "下降", "风险", "制裁", "限制", "处罚",
]

INVESTMENT_KW = [
    "투자", "증설", "공장", "인수", "합병", "IPO", "상장", "자금",
    "invest", "factory", "M&A", "acquisition", "capex",
    "投资", "建厂", "收购", "合并",
]

RISK_KW = [
    "규제", "제재", "관세", "벌금", "조사", "부채", "디폴트",
    "가격 하락", "마진 압박", "과잉", "횡령", "사기",
    "regulation", "tariff", "penalty", "investigation", "debt",
    "监管", "关税", "罚款", "调查",
]

DEMAND_KW = [
    "출하", "수요", "매출", "판매", "주문", "수출",
    "shipment", "demand", "revenue", "sales", "orders",
    "出货", "需求", "销售", "订单",
]


def _classify_direction(headline: str, event_type: str) -> str:
    """Classify signal direction from headline keywords."""
    text = headline.lower()
    pos = sum(1 for kw in POSITIVE_KW if kw.lower() in text)
    neg = sum(1 for kw in NEGATIVE_KW if kw.lower() in text)

    # Event type bias
    if event_type in ("RISK.INVESTIGATION", "RISK.CRISIS", "TRADE.SANCTION", "TRADE.TARIFF"):
        neg += 2
    elif event_type in ("INDUSTRY.SUBSIDY", "POLICY.ANNOUNCE"):
        pos += 1

    if pos > neg:
        return "POSITIVE"
    elif neg > pos:
        return "NEGATIVE"
    return "NEUTRAL"


def _classify_signal_type(headline: str, event_type: str) -> str:
    """Determine signal type."""
    text = headline.lower()

    if event_type.startswith("POLICY") or any(kw.lower() in text for kw in ["정책", "규제", "법률", "policy"]):
        return "POLICY_SIGNAL"
    if any(kw.lower() in text for kw in INVESTMENT_KW):
        return "INVESTMENT_SIGNAL"
    if any(kw.lower() in text for kw in RISK_KW):
        return "RISK_SIGNAL"
    if any(kw.lower() in text for kw in DEMAND_KW):
        return "DEMAND_SIGNAL"
    if event_type.startswith("RISK"):
        return "RISK_SIGNAL"
    if event_type.startswith("MARKET.EARNINGS"):
        return "DEMAND_SIGNAL"
    if event_type.startswith("TRADE"):
        return "RISK_SIGNAL"

    return "POLICY_SIGNAL"  # default for Chinese economy news


def _calc_confidence(magnitude: str, direction: str, entity_mentions: int) -> float:
    """Calculate signal confidence."""
    base = 0.5
    if magnitude == "critical":
        base = 0.9
    elif magnitude == "major":
        base = 0.75
    elif magnitude == "moderate":
        base = 0.6

    # Higher confidence for clear direction
    if direction != "NEUTRAL":
        base += 0.05

    # Boost for well-known entities
    if entity_mentions >= 5:
        base += 0.05
    elif entity_mentions >= 10:
        base += 0.1

    return min(base, 0.95)


def generate_signals(entity_name: str = None) -> list[dict]:
    """Generate signals from KG events linked to entities."""
    conn = get_kg_connection()

    query = """
        SELECT DISTINCT
            ent.kg_entity_id, ent.canonical_name, ent.entity_type, ent.mention_count,
            e.kg_event_id, e.event_type, e.headline, e.magnitude, e.event_date,
            r.relation_type, r.evidence
        FROM kg_events e
        JOIN kg_news_event_map nem ON e.kg_event_id = nem.kg_event_id
        JOIN kg_news_entity_map nenm ON nem.news_id = nenm.news_id
        JOIN kg_entities ent ON nenm.kg_entity_id = ent.kg_entity_id
        LEFT JOIN kg_relations r ON (r.source_id = ent.kg_entity_id OR r.target_id = ent.kg_entity_id)
        WHERE ent.status = 'active'
    """
    params = []
    if entity_name:
        query += " AND (ent.canonical_name LIKE ? OR ent.canonical_name_zh LIKE ?)"
        params.extend([f"%{entity_name}%", f"%{entity_name}%"])

    query += " ORDER BY ent.mention_count DESC, e.event_date DESC NULLS LAST"
    rows = conn.execute(query, params).fetchall()
    conn.close()

    # Group by entity + event
    seen = set()
    signals = []

    for r in rows:
        key = (r["kg_entity_id"], r["kg_event_id"])
        if key in seen:
            continue
        seen.add(key)

        direction = _classify_direction(r["headline"], r["event_type"])
        signal_type = _classify_signal_type(r["headline"], r["event_type"])
        confidence = _calc_confidence(r["magnitude"], direction, r["mention_count"])

        evidence_parts = [r["headline"]]
        if r["relation_type"]:
            evidence_parts.append(f"[{r['relation_type']}] {r['evidence'] or ''}")

        signals.append({
            "entity": r["canonical_name"],
            "entity_type": r["entity_type"],
            "signal_type": signal_type,
            "direction": direction,
            "confidence": round(confidence, 2),
            "event_type": r["event_type"],
            "headline": r["headline"],
            "magnitude": r["magnitude"],
            "date": r["event_date"],
            "evidence": " | ".join(evidence_parts)[:200],
        })

    return signals


def aggregate_signals(signals: list[dict]) -> dict:
    """Aggregate signals by entity."""
    by_entity = defaultdict(list)
    for s in signals:
        by_entity[s["entity"]].append(s)

    aggregated = {}
    for entity, sigs in by_entity.items():
        pos = [s for s in sigs if s["direction"] == "POSITIVE"]
        neg = [s for s in sigs if s["direction"] == "NEGATIVE"]
        neu = [s for s in sigs if s["direction"] == "NEUTRAL"]
        avg_conf = sum(s["confidence"] for s in sigs) / len(sigs) if sigs else 0

        # Net direction
        if len(pos) > len(neg) * 1.5:
            net = "POSITIVE"
        elif len(neg) > len(pos) * 1.5:
            net = "NEGATIVE"
        else:
            net = "MIXED"

        # Conflicting signals lower confidence
        if pos and neg:
            avg_conf *= 0.85

        aggregated[entity] = {
            "total_signals": len(sigs),
            "positive": len(pos),
            "negative": len(neg),
            "neutral": len(neu),
            "net_direction": net,
            "avg_confidence": round(avg_conf, 2),
            "signals": sigs,
        }

    return aggregated


def format_signals_report(entity_name: str = None) -> str:
    """Generate formatted signal report."""
    signals = generate_signals(entity_name)
    aggregated = aggregate_signals(signals)

    lines = []
    lines.append("=" * 60)
    lines.append("  SIGNAL ENGINE REPORT")
    lines.append(f"  Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    if entity_name:
        lines.append(f"  Filter: {entity_name}")
    lines.append(f"  Total signals: {len(signals)}")
    lines.append("=" * 60)

    # Sort by total signals
    for entity, agg in sorted(aggregated.items(), key=lambda x: -x[1]["total_signals"])[:20]:
        lines.append(f"\n  [{agg['net_direction']}] {entity} "
                     f"(signals: {agg['total_signals']}, "
                     f"+{agg['positive']} -{agg['negative']} ={agg['neutral']}, "
                     f"conf: {agg['avg_confidence']:.2f})")

        for s in agg["signals"][:5]:
            arrow = {"POSITIVE": "+", "NEGATIVE": "-", "NEUTRAL": "="}[s["direction"]]
            lines.append(f"    {arrow} [{s['signal_type']}] {s['headline'][:60]}")
            lines.append(f"      ({s['event_type']}, {s['magnitude']}, conf={s['confidence']})")

    lines.append("\n" + "=" * 60)

    report = "\n".join(lines)
    logger.info(report)
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"\n{'#'*60}\n{datetime.now().isoformat()}\n{report}\n")
    return report


if __name__ == "__main__":
    print(format_signals_report())

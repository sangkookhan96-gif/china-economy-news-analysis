"""KG Trend Scanner.

Scans KG for industry-level trends by aggregating signals.
Outputs: Top Growing, High Risk, Policy Driven sectors.
"""

import json
import logging
from datetime import datetime
from collections import defaultdict
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from src.database.kg_models import get_kg_connection
from src.kg.signal_engine import generate_signals, aggregate_signals

LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
log_file = LOG_DIR / "trend_scanner.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(),
    ],
    force=True,
)
logger = logging.getLogger("trend_scanner")


def _get_industry_signals() -> dict:
    """Get all signals grouped by industry."""
    conn = get_kg_connection()

    # Get industry entities
    industries = conn.execute("""
        SELECT kg_entity_id, canonical_name, mention_count
        FROM kg_entities
        WHERE entity_type = 'IND' AND status = 'active'
        ORDER BY mention_count DESC
    """).fetchall()

    # Get company→industry mapping
    company_industry = {}
    rows = conn.execute("""
        SELECT r.source_id, t.canonical_name as industry
        FROM kg_relations r
        JOIN kg_entities t ON r.target_id = t.kg_entity_id
        WHERE r.relation_type = 'BELONGS_TO' AND t.entity_type = 'IND'
    """).fetchall()
    for r in rows:
        company_industry[r["source_id"]] = r["industry"]

    # Get all signals
    all_signals = generate_signals()

    # Map signals to industries
    industry_signals = defaultdict(list)

    for s in all_signals:
        # Direct industry match
        if s["entity_type"] == "IND":
            industry_signals[s["entity"]].append(s)
        # Company → industry mapping
        elif s["entity_type"] == "COM":
            ent_results = conn.execute(
                "SELECT kg_entity_id FROM kg_entities WHERE canonical_name = ? AND status='active'",
                (s["entity"],)).fetchone()
            if ent_results:
                ind = company_industry.get(ent_results["kg_entity_id"])
                if ind:
                    industry_signals[ind].append(s)

    # Also map signals from policy events that target industries
    for s in all_signals:
        if s["signal_type"] == "POLICY_SIGNAL" and s["entity_type"] == "ORG":
            # Check if this org regulates any industry
            targets = conn.execute("""
                SELECT t.canonical_name FROM kg_relations r
                JOIN kg_entities t ON r.target_id = t.kg_entity_id
                WHERE r.source_id IN (SELECT kg_entity_id FROM kg_entities WHERE canonical_name = ?)
                  AND t.entity_type = 'IND'
            """, (s["entity"],)).fetchall()
            for t in targets:
                industry_signals[t["canonical_name"]].append(s)

    conn.close()
    return dict(industry_signals)


def scan_trends() -> dict:
    """Scan for industry trends."""
    industry_signals = _get_industry_signals()

    trends = {
        "growing": [],     # Top growing industries
        "at_risk": [],     # High risk industries
        "policy_driven": [],  # Policy-driven sectors
    }

    for industry, signals in industry_signals.items():
        if not signals:
            continue

        pos = sum(1 for s in signals if s["direction"] == "POSITIVE")
        neg = sum(1 for s in signals if s["direction"] == "NEGATIVE")
        policy = sum(1 for s in signals if s["signal_type"] == "POLICY_SIGNAL")
        invest = sum(1 for s in signals if s["signal_type"] == "INVESTMENT_SIGNAL")
        risk = sum(1 for s in signals if s["signal_type"] == "RISK_SIGNAL")
        avg_conf = sum(s["confidence"] for s in signals) / len(signals)

        entry = {
            "industry": industry,
            "total_signals": len(signals),
            "positive": pos,
            "negative": neg,
            "policy_signals": policy,
            "investment_signals": invest,
            "risk_signals": risk,
            "avg_confidence": round(avg_conf, 2),
            "top_events": [s["headline"][:60] for s in signals[:3]],
        }

        # Classify
        if pos > neg and pos >= 2:
            trends["growing"].append(entry)
        if neg >= 2 or risk >= 2:
            trends["at_risk"].append(entry)
        if policy >= 2:
            trends["policy_driven"].append(entry)

    # Sort
    trends["growing"].sort(key=lambda x: -x["positive"])
    trends["at_risk"].sort(key=lambda x: -x["negative"])
    trends["policy_driven"].sort(key=lambda x: -x["policy_signals"])

    return trends


def format_trend_report() -> str:
    """Generate formatted trend report."""
    trends = scan_trends()

    lines = []
    lines.append("=" * 60)
    lines.append("  INDUSTRY TREND SCANNER REPORT")
    lines.append(f"  Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("=" * 60)

    # Growing
    lines.append(f"\n  === TOP GROWING INDUSTRIES ({len(trends['growing'])}) ===")
    for i, t in enumerate(trends["growing"][:8], 1):
        lines.append(f"\n  {i}. {t['industry']}")
        lines.append(f"     Signals: {t['total_signals']} (+{t['positive']} -{t['negative']})")
        lines.append(f"     Confidence: {t['avg_confidence']:.2f}")
        for ev in t["top_events"]:
            lines.append(f"     • {ev}")

    # At Risk
    lines.append(f"\n  === HIGH RISK INDUSTRIES ({len(trends['at_risk'])}) ===")
    for i, t in enumerate(trends["at_risk"][:8], 1):
        lines.append(f"\n  {i}. {t['industry']}")
        lines.append(f"     Signals: {t['total_signals']} (+{t['positive']} -{t['negative']}, risk={t['risk_signals']})")
        lines.append(f"     Confidence: {t['avg_confidence']:.2f}")
        for ev in t["top_events"]:
            lines.append(f"     • {ev}")

    # Policy Driven
    lines.append(f"\n  === POLICY DRIVEN SECTORS ({len(trends['policy_driven'])}) ===")
    for i, t in enumerate(trends["policy_driven"][:8], 1):
        lines.append(f"\n  {i}. {t['industry']}")
        lines.append(f"     Signals: {t['total_signals']} (policy={t['policy_signals']})")
        lines.append(f"     Confidence: {t['avg_confidence']:.2f}")
        for ev in t["top_events"]:
            lines.append(f"     • {ev}")

    # Summary counts
    lines.append(f"\n  === SUMMARY ===")
    lines.append(f"  Growing sectors:      {len(trends['growing'])}")
    lines.append(f"  At-risk sectors:      {len(trends['at_risk'])}")
    lines.append(f"  Policy-driven sectors: {len(trends['policy_driven'])}")

    lines.append("\n" + "=" * 60)

    report = "\n".join(lines)
    logger.info(report)
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"\n{'#'*60}\n{datetime.now().isoformat()}\n{report}\n")

    return report


if __name__ == "__main__":
    print(format_trend_report())

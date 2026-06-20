"""KG Pipeline Validator.

Validates entire KG pipeline: data health, signal quality, trend coverage.

Usage:
    python3 -m src.kg.validate_pipeline
    python3 -m src.kg.validate_pipeline --check confidence,signal_count,trend_coverage
"""

import argparse
import logging
from datetime import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from src.database.kg_models import get_kg_connection
from src.kg.signal_engine import generate_signals, aggregate_signals
from src.kg.trend_scanner import scan_trends

LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "validate_pipeline.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
    force=True,
)
logger = logging.getLogger("validate")

# Thresholds
THRESHOLDS = {
    "min_active_entities": 100,
    "min_events": 50,
    "min_relations": 100,
    "min_avg_confidence": 0.65,
    "min_total_signals": 50,
    "min_trend_categories": 3,
    "max_orphan_rate": 0.40,
    "min_entity_with_relations": 0.50,
}


def validate(checks: list[str] = None, verbose: bool = False):
    """Run pipeline validation."""
    conn = get_kg_connection()
    results = []

    # ── 1. KG Health ──
    active = conn.execute("SELECT COUNT(*) FROM kg_entities WHERE status='active'").fetchone()[0]
    events = conn.execute("SELECT COUNT(*) FROM kg_events").fetchone()[0]
    relations = conn.execute("SELECT COUNT(*) FROM kg_relations").fetchone()[0]

    orphans = conn.execute("""
        SELECT COUNT(*) FROM kg_entities e
        WHERE status = 'active'
          AND NOT EXISTS (SELECT 1 FROM kg_relations WHERE source_id = e.kg_entity_id OR target_id = e.kg_entity_id)
    """).fetchone()[0]
    orphan_rate = orphans / active if active else 1

    ent_with_rel = active - orphans
    rel_rate = ent_with_rel / active if active else 0

    results.append(("Active entities", active, f">= {THRESHOLDS['min_active_entities']}",
                     active >= THRESHOLDS["min_active_entities"]))
    results.append(("Events", events, f">= {THRESHOLDS['min_events']}",
                     events >= THRESHOLDS["min_events"]))
    results.append(("Relations", relations, f">= {THRESHOLDS['min_relations']}",
                     relations >= THRESHOLDS["min_relations"]))
    results.append(("Orphan rate", f"{orphan_rate:.0%}", f"<= {THRESHOLDS['max_orphan_rate']:.0%}",
                     orphan_rate <= THRESHOLDS["max_orphan_rate"]))
    results.append(("Entity-with-relations rate", f"{rel_rate:.0%}",
                     f">= {THRESHOLDS['min_entity_with_relations']:.0%}",
                     rel_rate >= THRESHOLDS["min_entity_with_relations"]))
    conn.close()

    # ── 2. Signal Quality ──
    if not checks or "signal_count" in checks or "confidence" in checks:
        signals = generate_signals()
        agg = aggregate_signals(signals)

        total_signals = len(signals)
        avg_conf = sum(s["confidence"] for s in signals) / len(signals) if signals else 0

        results.append(("Total signals", total_signals, f">= {THRESHOLDS['min_total_signals']}",
                         total_signals >= THRESHOLDS["min_total_signals"]))
        results.append(("Avg signal confidence", f"{avg_conf:.2f}",
                         f">= {THRESHOLDS['min_avg_confidence']}",
                         avg_conf >= THRESHOLDS["min_avg_confidence"]))

        # Per-entity signal count
        entities_with_3plus = sum(1 for a in agg.values() if a["total_signals"] >= 3)
        results.append(("Entities with ≥3 signals", entities_with_3plus, ">= 5",
                         entities_with_3plus >= 5))

    # ── 3. Trend Coverage ──
    if not checks or "trend_coverage" in checks:
        trends = scan_trends()
        categories_with_data = sum(1 for k, v in trends.items() if v)

        results.append(("Trend categories with data", categories_with_data,
                         f">= {THRESHOLDS['min_trend_categories']}",
                         categories_with_data >= THRESHOLDS["min_trend_categories"]))
        results.append(("Growing industries", len(trends.get("growing", [])), ">= 3",
                         len(trends.get("growing", [])) >= 3))
        results.append(("Policy-driven sectors", len(trends.get("policy_driven", [])), ">= 3",
                         len(trends.get("policy_driven", [])) >= 3))

    # ── Report ──
    all_pass = all(r[3] for r in results)
    verdict = "PIPELINE VALID" if all_pass else "PIPELINE DEGRADED"

    report = f"""
{'='*60}
  PIPELINE VALIDATION REPORT
  Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}
{'='*60}

  {'Check':<35} {'Value':<15} {'Threshold':<15} {'Status'}
  {'-'*75}"""

    for name, value, threshold, passed in results:
        status = "PASS" if passed else "FAIL"
        report += f"\n  {name:<35} {str(value):<15} {threshold:<15} {status}"

    # Verbose: entity/event/relation details
    if verbose:
        vconn = get_kg_connection()
        report += f"\n\n  --- Verbose: Top Entities (by degree) ---"
        top = vconn.execute("""
            SELECT e.canonical_name, e.entity_type, e.mention_count,
                (SELECT COUNT(*) FROM kg_relations WHERE source_id = e.kg_entity_id OR target_id = e.kg_entity_id) as degree
            FROM kg_entities e WHERE e.status='active' ORDER BY degree DESC LIMIT 15
        """).fetchall()
        for i, r in enumerate(top, 1):
            report += f"\n  {i:2d}. {r['canonical_name']} ({r['entity_type']}) degree={r['degree']} mentions={r['mention_count']}"

        report += f"\n\n  --- Verbose: Event Type Distribution ---"
        for r in vconn.execute("SELECT event_type, COUNT(*) as c FROM kg_events GROUP BY event_type ORDER BY c DESC LIMIT 10"):
            report += f"\n  {r['event_type']}: {r['c']}"
        report += f"\n\n  --- Verbose: Relation Type Distribution ---"
        for r in vconn.execute("SELECT relation_type, COUNT(*) as c FROM kg_relations GROUP BY relation_type ORDER BY c DESC"):
            report += f"\n  {r['relation_type']}: {r['c']}"
        vconn.close()

    report += f"""

  --- Verdict ---
  {verdict}
{'='*60}
"""

    print(report)
    logger.info(report)
    return all_pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", type=str, default=None)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    checks = args.check.split(",") if args.check else None
    passed = validate(checks, verbose=args.verbose)
    sys.exit(0 if passed else 1)

"""Full KG Pipeline — Extract → Normalize → Signal → Report → Trend.

Usage:
    python3 run_full_pipeline.py
    python3 run_full_pipeline.py --companies "BYD,CATL" --trend top5
    python3 run_full_pipeline.py --skip-extract  # only signal+report+trend
"""

import argparse
import time
import logging
from datetime import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

LOG_DIR = Path(__file__).resolve().parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
REPORT_DIR = Path(__file__).resolve().parent / "reports"
REPORT_DIR.mkdir(exist_ok=True)

log_file = LOG_DIR / f"full_pipeline_{datetime.now().strftime('%Y%m%d')}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(),
    ],
    force=True,
)
logger = logging.getLogger("full_pipeline")


def run(companies: list[str] = None, trend_limit: int = 10,
        skip_extract: bool = False):
    """Run full pipeline."""
    start = time.time()
    date_str = datetime.now().strftime("%Y-%m-%d")

    logger.info(f"=== FULL KG PIPELINE — {date_str} ===")

    report_parts = []
    report_parts.append(f"# KG Daily Report — {date_str}\n")

    # ── Step 1: Extract new reviews ──
    if not skip_extract:
        logger.info("\n[Step 1/5] KG Extraction...")
        from run_kg_update import run_update
        run_update(dry_run=False)
        report_parts.append("## 1. Extraction: Complete\n")
    else:
        logger.info("\n[Step 1/5] Skipped (--skip-extract)")
        report_parts.append("## 1. Extraction: Skipped\n")

    # ── Step 2: Normalize ──
    logger.info("\n[Step 2/5] Normalization...")
    from src.kg.normalizer import run_normalization
    run_normalization()
    report_parts.append("## 2. Normalization: Complete\n")

    # ── Step 3: Signals ──
    logger.info("\n[Step 3/5] Signal Generation...")
    from src.kg.signal_engine import format_signals_report
    signal_report = format_signals_report()
    report_parts.append("## 3. Signals\n")
    report_parts.append(f"```\n{signal_report}\n```\n")

    # ── Step 4: Company Reports ──
    if companies:
        logger.info(f"\n[Step 4/5] Company Reports: {companies}")
        from src.kg.company_report import generate_company_report
        for company in companies:
            logger.info(f"  Generating report for {company}...")
            cr = generate_company_report(company)
            report_parts.append(f"## 4. Company Report: {company}\n")
            report_parts.append(f"```\n{cr}\n```\n")
    else:
        logger.info("\n[Step 4/5] No companies specified, skipping")

    # ── Step 5: Trend Scanner ──
    logger.info(f"\n[Step 5/5] Trend Scanner (top {trend_limit})...")
    from src.kg.trend_scanner import format_trend_report
    trend_report = format_trend_report()
    report_parts.append("## 5. Industry Trends\n")
    report_parts.append(f"```\n{trend_report}\n```\n")

    # ── Save report ──
    duration = time.time() - start
    report_parts.append(f"\n---\nGenerated in {duration:.0f}s ({duration/60:.1f}min)\n")

    full_report = "\n".join(report_parts)
    report_path = REPORT_DIR / f"{date_str}_full_report.md"
    report_path.write_text(full_report, encoding="utf-8")

    logger.info(f"\n=== PIPELINE COMPLETE in {duration:.0f}s ===")
    logger.info(f"Report saved: {report_path}")
    print(f"\nReport saved: {report_path}")

    return str(report_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Full KG Pipeline")
    parser.add_argument("--companies", type=str, default="BYD,CATL")
    parser.add_argument("--trend", type=str, default="top10")
    parser.add_argument("--skip-extract", action="store_true")
    parser.add_argument("--output", type=str, default=None,
                        help="Custom output path for report")
    parser.add_argument("--force", action="store_true",
                        help="Force re-run even if report exists")
    args = parser.parse_args()

    companies = [c.strip() for c in args.companies.split(",")] if args.companies else None
    trend_n = int(args.trend.replace("top", "")) if "top" in args.trend else 10

    result_path = run(companies=companies, trend_limit=trend_n,
                      skip_extract=args.skip_extract)

    # Copy to custom output path if specified
    if args.output:
        import shutil
        from pathlib import Path
        out_path = Path(args.output).resolve()
        src_path = Path(result_path).resolve()
        if out_path != src_path:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src_path), str(out_path))
            print(f"Report also saved to: {args.output}")

#!/usr/bin/env python3
"""Management CLI for China Economy News Analysis.

Usage:
    python manage.py healthcheck          - System health status
    python manage.py select --edition X   - Run edition selection manually
"""

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def cmd_healthcheck():
    """Display system health status."""
    from src.database.models import get_connection

    conn = get_connection()
    cursor = conn.cursor()
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    issues = []

    # 1. Today collected
    cursor.execute(
        "SELECT COUNT(*) as cnt FROM news WHERE DATE(collected_at) = ?", (today,)
    )
    collected = cursor.fetchone()["cnt"]

    # 2. Today analyzed
    cursor.execute(
        "SELECT COUNT(*) as cnt FROM news WHERE DATE(analyzed_at) = ?", (today,)
    )
    analyzed = cursor.fetchone()["cnt"]

    # 3. Edition counts
    cursor.execute("""
        SELECT edition, COUNT(*) as cnt FROM news
        WHERE DATE(updated_at) = ? AND edition IS NOT NULL
        GROUP BY edition
    """, (today,))
    editions = {row["edition"]: row["cnt"] for row in cursor.fetchall()}

    # 4. Last scheduler run (most recent collected_at)
    cursor.execute("SELECT MAX(collected_at) as last_run FROM news")
    last_run_row = cursor.fetchone()
    last_run = last_run_row["last_run"] if last_run_row else None

    # 5. Scheduler recency
    scheduler_ok = False
    if last_run:
        try:
            last_dt = datetime.strptime(last_run[:19], "%Y-%m-%d %H:%M:%S")
            minutes_ago = (now - last_dt).total_seconds() / 60
            scheduler_ok = minutes_ago < 90
        except ValueError:
            minutes_ago = -1
    else:
        minutes_ago = -1

    # 6. Ollama status
    ollama_ok = False
    try:
        import requests
        resp = requests.get("http://localhost:11434/api/tags", timeout=3)
        ollama_ok = resp.ok
    except Exception:
        pass

    # 7. DB size
    from config.settings import DATABASE_PATH
    db_path = Path(DATABASE_PATH)
    db_size_mb = db_path.stat().st_size / (1024 * 1024) if db_path.exists() else 0

    conn.close()

    # --- Determine status ---
    if collected < 5:
        issues.append("수집 부족")
    if analyzed < 5 and now.hour >= 9:
        issues.append("분석 부족")
    if not scheduler_ok:
        issues.append("스케줄러 미작동")
    if not ollama_ok:
        issues.append("Ollama 다운")

    expected_editions = []
    if now.hour >= 8:
        expected_editions.append("morning")
    if now.hour >= 15:
        expected_editions.append("afternoon")
    if now.hour >= 23:
        expected_editions.append("evening")
    for ed in expected_editions:
        if ed not in editions:
            issues.append(f"{ed} 에디션 미선정")

    if not issues:
        status = "OK"
    elif len(issues) <= 2:
        status = "WARN"
    else:
        status = "FAIL"

    # --- Output ---
    print(f"{'=' * 50}")
    print(f"  Health Check  |  {now.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'=' * 50}")
    print(f"  Status:           {status}")
    print(f"  Today collected:  {collected}")
    print(f"  Today analyzed:   {analyzed}")
    print(f"  Editions today:   {editions if editions else '(none)'}")
    if last_run:
        print(f"  Last scheduler:   {last_run[:19]} ({int(minutes_ago)}min ago)")
    else:
        print(f"  Last scheduler:   (never)")
    print(f"  Ollama:           {'UP' if ollama_ok else 'DOWN'}")
    print(f"  DB size:          {db_size_mb:.1f} MB")
    if issues:
        print(f"  Issues:           {', '.join(issues)}")
    print(f"{'=' * 50}")

    return 0 if status == "OK" else 1


def cmd_select(edition: str, lookback_days: int = 3):
    """Run edition selection manually."""
    from src.agents.daily_news_selector import run_edition_selection

    result = run_edition_selection(edition=edition, lookback_days=lookback_days)
    print(f"Edition: {result['label']} ({result['edition']})")
    print(f"Selected: {result['selected_count']}")
    print(f"IDs: {result['selected_ids']}")


def main():
    parser = argparse.ArgumentParser(description="China Economy News Management CLI")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("healthcheck", help="Show system health status")

    sel = sub.add_parser("select", help="Run edition selection")
    sel.add_argument("--edition", required=True, choices=["morning", "afternoon", "evening"])
    sel.add_argument("--lookback-days", type=int, default=3)

    args = parser.parse_args()

    if args.command == "healthcheck":
        sys.exit(cmd_healthcheck())
    elif args.command == "select":
        cmd_select(args.edition, args.lookback_days)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()

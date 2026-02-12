#!/usr/bin/env python3
"""Main entry point for news collection (crawl + enrich only).

News selection, translation, headline generation, and AI analysis are handled
by the edition-based selection pipeline (daily_news_selector.py) triggered via cron.
"""

import argparse
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.database.models import init_db, get_connection
from src.collector.crawler import NewsCrawler


def main():
    parser = argparse.ArgumentParser(description="China Economy News Collector")
    parser.add_argument("--init-db", action="store_true")
    parser.add_argument("--crawl", action="store_true")
    parser.add_argument("--auto", action="store_true",
                        help="Run crawl + enrich (selection is now managed by edition cron)")
    args = parser.parse_args()

    if args.init_db or not Path("data/news.db").exists():
        print("Initializing database...")
        init_db()

    if args.auto:
        args.crawl = True

    if args.crawl:
        print("Starting news collection...")
        crawler = NewsCrawler()
        results = crawler.crawl_all()
        print(f"\n수집 완료: 총 {results['total']}개, 신규 {results['new']}개")

        # Enrich: fetch full article content for items with empty content
        print("\n원문 본문 수집 중...")
        enriched = crawler.enrich_news_content(limit=50)
        print(f"✓ {enriched}건 원문 수집 완료")

        # Fill in missing published_at
        conn = get_connection()
        cursor = conn.cursor()
        now = datetime.now()
        cursor.execute(
            "UPDATE news SET published_at = ? WHERE published_at IS NULL",
            (now,)
        )
        conn.commit()
        conn.close()

    if not any([args.init_db, args.crawl, args.auto]):
        parser.print_help()


if __name__ == "__main__":
    main()

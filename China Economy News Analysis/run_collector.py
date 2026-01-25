#!/usr/bin/env python3
"""Main entry point for news collection and analysis."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.database.models import init_db
from src.collector.crawler import NewsCrawler


def main():
    parser = argparse.ArgumentParser(description="China Economy News Collector")
    parser.add_argument("--init-db", action="store_true", help="Initialize database")
    parser.add_argument("--crawl", action="store_true", help="Run news crawler")
    parser.add_argument("--analyze", action="store_true", help="Run AI analyzer")
    parser.add_argument("--limit", type=int, default=10, help="Limit for analysis")
    args = parser.parse_args()

    if args.init_db or not Path("data/news.db").exists():
        print("Initializing database...")
        init_db()

    if args.crawl:
        print("Starting news collection...")
        crawler = NewsCrawler()
        results = crawler.crawl_all()
        print(f"\n수집 완료: 총 {results['total']}개, 신규 {results['new']}개")

    if args.analyze:
        from src.analyzer.claude_analyzer import ClaudeAnalyzer
        print(f"Starting AI analysis (limit: {args.limit})...")
        analyzer = ClaudeAnalyzer()
        results = analyzer.analyze_unanalyzed(limit=args.limit)
        print(f"분석 완료: {len(results)}개")

    if not any([args.init_db, args.crawl, args.analyze]):
        parser.print_help()


if __name__ == "__main__":
    main()

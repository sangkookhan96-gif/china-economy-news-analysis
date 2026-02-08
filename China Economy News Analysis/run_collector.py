#!/usr/bin/env python3
"""Main entry point for news collection and analysis."""

import argparse
import sys
from pathlib import Path
from datetime import datetime, timedelta
from collections import Counter

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.database.models import init_db, get_connection
from src.collector.crawler import NewsCrawler


def main():
    parser = argparse.ArgumentParser(description="China Economy News Collector")
    parser.add_argument("--init-db", action="store_true")
    parser.add_argument("--crawl", action="store_true")
    parser.add_argument("--analyze", action="store_true")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--filter", action="store_true")
    parser.add_argument("--auto", action="store_true")
    args = parser.parse_args()

    if args.init_db or not Path("data/news.db").exists():
        print("Initializing database...")
        init_db()

    if args.auto:
        args.crawl = args.filter = args.analyze = True

    selected_ids = []

    if args.crawl:
        print("Starting news collection...")
        crawler = NewsCrawler()
        results = crawler.crawl_all()
        print(f"\n수집 완료: 총 {results['total']}개, 신규 {results['new']}개")

        # Enrich: fetch full article content for items with empty content
        print("\n원문 본문 수집 중...")
        enriched = crawler.enrich_news_content(limit=50)
        print(f"✓ {enriched}건 원문 수집 완료")

        conn = get_connection()
        cursor = conn.cursor()

        now = datetime.now()
        cursor.execute(
            "UPDATE news SET published_at = ? WHERE published_at IS NULL",
            (now,)
        )
        conn.commit()

        if args.filter:
            from src.collector.news_filter import filter_news, balance_categories

            print("\n뉴스 필터링 및 선정 중...")
            start_time = now - timedelta(hours=24)

            cursor.execute("""
                SELECT id, original_title, original_content, published_at, source
                FROM news
                WHERE published_at >= ?
                  AND id NOT IN (SELECT news_id FROM expert_reviews)
                  AND expert_review_status NOT IN ('skipped', 'commented')
                ORDER BY published_at DESC
            """, (start_time,))

            recent_news = [
                {
                    "id": r[0],
                    "original_title": r[1],
                    "original_content": r[2],
                    "published_at": r[3],
                    "source": r[4],
                }
                for r in cursor.fetchall()
            ]

            filtered = filter_news(recent_news)
            selected = balance_categories(filtered, target_count=10)

            if selected:
                selected_ids = [n["id"] for n in selected]

                cursor.execute(
                    "UPDATE news SET expert_review_status='none', is_selected=0"
                )

                for nid in selected_ids:
                    cursor.execute("""
                        UPDATE news
                        SET expert_review_status='queued_today',
                            is_selected=1,
                            analyzed_at=COALESCE(analyzed_at, ?)
                        WHERE id=?
                    """, (now, nid))

                conn.commit()

                print(f"\n✓ {len(selected_ids)}개 뉴스 선정 완료")

        conn.close()

    # 무료 번역: 선정된 뉴스 제목을 Google Translate로 한국어 번역
    if selected_ids:
        print("\n제목 번역 시작 (Google Translate)")
        from src.utils.translator import translate_zh_to_ko

        conn = get_connection()
        cursor = conn.cursor()

        for news_id in selected_ids:
            cursor.execute(
                "SELECT original_title, translated_title FROM news WHERE id=?",
                (news_id,)
            )
            row = cursor.fetchone()
            if row and not row['translated_title']:
                ko_title = translate_zh_to_ko(row['original_title'])
                if ko_title:
                    cursor.execute(
                        "UPDATE news SET translated_title=? WHERE id=?",
                        (ko_title, news_id)
                    )
                    print(f"✓ 번역: {ko_title[:50]}...")
                else:
                    print(f"✗ 번역 실패: {row['original_title'][:40]}")

        conn.commit()
        conn.close()

    # 카드 헤드라인 생성 (18자 이내)
    if selected_ids:
        print("\n카드 헤드라인 생성 시작")
        from src.utils.headline_generator import generate_headline, save_headline

        conn = get_connection()
        cursor = conn.cursor()

        for news_id in selected_ids:
            cursor.execute(
                "SELECT translated_title, card_headline FROM news WHERE id=?",
                (news_id,)
            )
            row = cursor.fetchone()
            if row and row['translated_title'] and not row['card_headline']:
                headline = generate_headline(row['translated_title'])
                if headline:
                    save_headline(news_id, headline)
                    print(f"✓ 헤드라인: {headline}")
                else:
                    print(f"✗ 헤드라인 생성 실패: {row['translated_title'][:30]}")

        conn.close()

    if args.analyze and selected_ids:
        print("\nAI 분석 시작")
        from src.analyzer.claude_analyzer import ClaudeAnalyzer

        conn = get_connection()
        cursor = conn.cursor()
        analyzer = ClaudeAnalyzer()

        for news_id in selected_ids:
            try:
                cursor.execute(
                    "SELECT original_title FROM news WHERE id=?",
                    (news_id,)
                )
                title = cursor.fetchone()[0]
                analyzer.analyze_news(news_id)
                print(f"✓ 분석 완료: {title[:50]}...")
            except Exception as e:
                print(f"✗ 분석 실패: {e}")

        conn.close()

    if not any([args.init_db, args.crawl, args.analyze, args.auto]):
        parser.print_help()


if __name__ == "__main__":
    main()

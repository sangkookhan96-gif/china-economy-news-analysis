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
    parser.add_argument("--filter", action="store_true", help="Apply news filtering")
    parser.add_argument("--auto", action="store_true", help="Auto mode: crawl + filter + analyze")
    args = parser.parse_args()

    if args.init_db or not Path("data/news.db").exists():
        print("Initializing database...")
        init_db()

    if args.auto:
        args.crawl = True
        args.filter = True
        args.analyze = True

    if args.crawl:
        print("Starting news collection...")
        crawler = NewsCrawler()
        results = crawler.crawl_all()
        print(f"\n수집 완료: 총 {results['total']}개, 신규 {results['new']}개")
        
        from src.database.models import get_connection
        from datetime import datetime
        conn = get_connection()
        cursor = conn.cursor()
        now = datetime.now()
        cursor.execute("UPDATE news SET published_at = ? WHERE published_at IS NULL", (now,))
        fixed = cursor.rowcount
        if fixed > 0:
            print(f"✓ 날짜 없는 {fixed}개 뉴스를 현재 시각으로 설정")
        conn.commit()
        conn.close()
        
        if args.filter:
            from src.collector.news_filter import filter_news, balance_categories
            from datetime import timedelta
            from collections import Counter
            
            print("\n뉴스 필터링 및 선정 중...")
            conn = get_connection()
            cursor = conn.cursor()
            
            # 최근 24시간 뉴스 (expert_review 조건 제거)
            now = datetime.now()
            start_time = now - timedelta(hours=24)
            
            print(f"기간: 최근 24시간 ({start_time.strftime('%Y-%m-%d %H:%M')} ~ {now.strftime('%Y-%m-%d %H:%M')})")
            
            cursor.execute("""
                SELECT id, original_title, original_content, published_at, source
                FROM news 
                WHERE published_at >= ?
                ORDER BY published_at DESC
            """, (start_time,))
            
            recent_news = [{'id': r[0], 'original_title': r[1], 'original_content': r[2], 
                          'published_at': r[3], 'source': r[4]} for r in cursor.fetchall()]
            
            print(f"대상 뉴스: {len(recent_news)}개")
            filtered = filter_news(recent_news)
            print(f"\n필터링 후: {len(filtered)}개")
            selected = balance_categories(filtered, target_count=10)
            print(f"최종 선정: {len(selected)}개")
            
            if selected:
                categories = Counter(n.get('category', '기타') for n in selected)
                print("\n카테고리별 분포:")
                for cat, count in categories.items():
                    print(f"  {cat}: {count}개")
                
                print("\n선정된 뉴스:")
                for i, news in enumerate(selected, 1):
                    print(f"{i}. [{news['category']}] {news['original_title'][:60]}")
                
                selected_ids = [news['id'] for news in selected]
                
                if args.analyze:
                    print("\n" + "="*60)
                    print("AI 분석 시작 (한국어 200자)")
                    print("="*60)
                    
                    from src.analyzer.claude_analyzer import ClaudeAnalyzer
                    analyzer = ClaudeAnalyzer()
                    
                    for news_id in selected_ids:
                        print(f"\n분석 중: ID {news_id}")
                        try:
                            cursor.execute("SELECT original_title FROM news WHERE id = ?", (news_id,))
                            title = cursor.fetchone()[0]
                            
                            results = analyzer.analyze_unanalyzed(limit=1)
                            if results:
                                # 분석 결과를 해당 뉴스에 적용
                                for result in results:
                                    if result.get('id') == news_id:
                                        print(f"✓ 완료: {title[:50]}...")
                                        cursor.execute("UPDATE news SET expert_review = ai_analysis WHERE id = ?", (news_id,))
                                        conn.commit()
                                        break
                            else:
                                print(f"⚠ 분석 결과 없음")
                        except Exception as e:
                            print(f"✗ 실패: {e}")
                    
                    print("\n" + "="*60)
                    print("AI 분석 완료")
                    print("="*60)
            else:
                print("\n⚠ 선정된 뉴스가 없습니다.")
            
            conn.close()

    elif args.analyze:
        from src.analyzer.claude_analyzer import ClaudeAnalyzer
        print(f"Starting AI analysis (limit: {args.limit})...")
        analyzer = ClaudeAnalyzer()
        results = analyzer.analyze_unanalyzed(limit=args.limit)
        print(f"분석 완료: {len(results)}개")

    if not any([args.init_db, args.crawl, args.analyze, args.auto]):
        parser.print_help()


if __name__ == "__main__":
    main()

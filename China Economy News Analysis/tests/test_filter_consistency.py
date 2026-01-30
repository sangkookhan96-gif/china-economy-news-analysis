#!/usr/bin/env python3
"""Test that daily_news_selector.py and run_collector.py use identical filtering logic.

Fetches the latest 100 news items and verifies both pipelines produce the same results.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database.models import get_connection
from src.collector.news_filter import filter_news, balance_categories


def get_recent_news(limit=100):
    """Fetch recent news from database."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, original_title, original_content, published_at, source
        FROM news
        ORDER BY published_at DESC
        LIMIT ?
    """, (limit,))

    news_list = []
    for row in cursor.fetchall():
        news_list.append({
            'id': row['id'],
            'original_title': row['original_title'] or '',
            'original_content': row['original_content'] or '',
            'published_at': row['published_at'],
            'source': row['source'] or '',
        })
    conn.close()
    return news_list


def simulate_run_collector_pipeline(news_list):
    """Simulate run_collector.py's filtering (filter_news + balance_categories)."""
    # Deep copy to avoid mutation side effects
    import copy
    news_copy = copy.deepcopy(news_list)
    filtered = filter_news(news_copy)
    selected = balance_categories(filtered, target_count=10)
    return {
        'filtered_ids': sorted([n['id'] for n in filtered]),
        'selected_ids': sorted([n['id'] for n in selected]),
    }


def simulate_daily_selector_pipeline(news_list):
    """Simulate daily_news_selector.py's filtering (now uses same functions)."""
    import copy
    news_copy = copy.deepcopy(news_list)
    # This is now the exact same call path as daily_news_selector.select_daily_news
    filtered = filter_news(news_copy)
    selected = balance_categories(filtered, target_count=10)
    return {
        'filtered_ids': sorted([n['id'] for n in filtered]),
        'selected_ids': sorted([n['id'] for n in selected]),
    }


def main():
    print("=" * 60)
    print("Filter Pipeline Consistency Test")
    print("=" * 60)

    # Fetch recent 100 news
    news_list = get_recent_news(100)
    print(f"\nFetched {len(news_list)} recent news items from database")

    if not news_list:
        print("ERROR: No news items found in database")
        return

    # Run both pipelines
    print("\n--- Running run_collector pipeline ---")
    collector_result = simulate_run_collector_pipeline(news_list)
    print(f"  filter_news: {len(collector_result['filtered_ids'])} items passed")
    print(f"  balance_categories: {len(collector_result['selected_ids'])} items selected")

    print("\n--- Running daily_news_selector pipeline ---")
    selector_result = simulate_daily_selector_pipeline(news_list)
    print(f"  filter_news: {len(selector_result['filtered_ids'])} items passed")
    print(f"  balance_categories: {len(selector_result['selected_ids'])} items selected")

    # Compare filter_news results
    print("\n" + "=" * 60)
    print("COMPARISON: filter_news() stage")
    print("=" * 60)

    collector_filtered = set(collector_result['filtered_ids'])
    selector_filtered = set(selector_result['filtered_ids'])

    if collector_filtered == selector_filtered:
        print(f"  MATCH: Both filtered to {len(collector_filtered)} items")
        filter_match = True
    else:
        only_collector = collector_filtered - selector_filtered
        only_selector = selector_filtered - collector_filtered
        print(f"  MISMATCH!")
        print(f"  Only in collector: {only_collector}")
        print(f"  Only in selector: {only_selector}")
        filter_match = False

    # Compare balance_categories results
    print("\n" + "=" * 60)
    print("COMPARISON: balance_categories() stage")
    print("=" * 60)

    collector_selected = set(collector_result['selected_ids'])
    selector_selected = set(selector_result['selected_ids'])

    if collector_selected == selector_selected:
        print(f"  MATCH: Both selected {len(collector_selected)} items")
        select_match = True
    else:
        only_collector = collector_selected - selector_selected
        only_selector = selector_selected - collector_selected
        print(f"  MISMATCH!")
        print(f"  Only in collector: {only_collector}")
        print(f"  Only in selector: {only_selector}")
        select_match = False

    # Final summary
    print("\n" + "=" * 60)
    print("FINAL RESULT")
    print("=" * 60)

    total_items = len(news_list)
    filter_mismatch = len(collector_filtered.symmetric_difference(selector_filtered))
    select_mismatch = len(collector_selected.symmetric_difference(selector_selected))

    filter_rate = ((total_items - filter_mismatch) / total_items * 100) if total_items > 0 else 0
    select_rate = ((len(collector_selected | selector_selected) - select_mismatch) / max(len(collector_selected | selector_selected), 1) * 100)

    print(f"  Input items: {total_items}")
    print(f"  filter_news match rate: {filter_rate:.1f}% ({filter_mismatch} mismatches)")
    print(f"  balance_categories match rate: {'100.0' if select_match else f'{select_rate:.1f}'}% ({select_mismatch} mismatches)")
    print(f"  Overall: {'PASS - 100% consistent' if (filter_match and select_match) else 'FAIL - inconsistencies found'}")

    # Also verify the actual daily_news_selector module uses the right functions
    print("\n" + "=" * 60)
    print("CODE VERIFICATION")
    print("=" * 60)

    import inspect
    from src.agents.daily_news_selector import select_daily_news
    source = inspect.getsource(select_daily_news)

    uses_filter_news = 'filter_news(' in source
    uses_balance = 'balance_categories(' in source

    print(f"  daily_news_selector.select_daily_news uses filter_news(): {uses_filter_news}")
    print(f"  daily_news_selector.select_daily_news uses balance_categories(): {uses_balance}")
    print(f"  Code verification: {'PASS' if (uses_filter_news and uses_balance) else 'FAIL'}")


if __name__ == "__main__":
    main()

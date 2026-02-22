"""Edition-based news selection algorithm for expert review queue.

Selects up to 10 news items per edition (morning/afternoon/evening) using the
canonical filtering pipeline from news_filter.py (filter_news + balance_categories).

Each edition covers from yesterday 00:00 up to the edition's cutoff time so
that articles from the previous night are not missed:
  - morning:   어제 00:00 → 오늘 07:00,  selected at 07:00
  - afternoon: 어제 00:00 → 오늘 13:00,  selected at 13:00
  - evening:   어제 00:00 → 오늘 20:00,  selected at 20:00
"""

import argparse
import logging
from collections import Counter
from datetime import datetime, time, timedelta

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.database.models import get_connection
from src.collector.news_filter import filter_news, balance_categories

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Edition configuration
# ---------------------------------------------------------------------------

EDITION_CONFIG = {
    'morning': {
        'label': '오전판',
        'title': '오늘 오전 뉴스 헤드라인',
        'cutoff': time(7, 0),
    },
    'afternoon': {
        'label': '오후판',
        'title': '오늘 오후 뉴스 헤드라인',
        'cutoff': time(13, 0),
    },
    'evening': {
        'label': '저녁/반판',
        'title': '오늘 밤 뉴스 헤드라인',
        'cutoff': time(20, 0),
    },
}


def get_current_edition() -> str:
    """Determine the current edition based on the current time."""
    now = datetime.now().time()
    if now < time(7, 0):
        return 'morning'
    elif now < time(14, 0):
        return 'afternoon'
    else:
        return 'evening'


def get_edition_time_window(edition: str, target_date=None, lookback_days=1):
    """Return (start_datetime_str, end_datetime_str) for the given edition.

    Window starts at (today - lookback_days) 00:00 and ends at the edition's
    cutoff time today.  Default lookback_days=1 (yesterday) preserves the
    original behaviour; higher values widen the window to capture older
    unselected articles.
    """
    if target_date is None:
        target_date = datetime.now().date()
    config = EDITION_CONFIG[edition]
    start_date = target_date - timedelta(days=lookback_days)
    start_dt = datetime.combine(start_date, time(0, 0))
    end_dt = datetime.combine(target_date, config['cutoff'])
    return start_dt.strftime("%Y-%m-%d %H:%M:%S"), end_dt.strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# Source diversity configuration
# ---------------------------------------------------------------------------

# 주요 경제 매체: 매 에디션에 최소 1건 이상 후보 보장 대상
MAJOR_ECONOMIC_SOURCES = [
    'xinhua_finance', 'cnstock', 'yicai',
    'cls', '21jingji', 'sina_finance', 'stcn',
    'jiemian', 'stdaily',
    'jjckb',  # 经济参考报 (신화사 산하 경제전문지)
]

# 소스별 후보 상한 (filter_news 이전, 과다 편중 방지)
MAX_PER_SOURCE_BEFORE_FILTER = 8

# 소스 보충 시 DB에서 가져올 최대 건수
SUPPLEMENT_PER_SOURCE = 3

# 중앙정부 소스 (선정 제외 대상 — balance_sources에서도 제외)
EXCLUDED_SOURCES = ('ndrc', 'pboc', 'mofcom', 'nbs', 'gov', 'xinhuanet', 'gov_cn', 'mof', 'caixin')


# ---------------------------------------------------------------------------
# Core selection functions
# ---------------------------------------------------------------------------

def _base_candidate_query_conditions():
    """Return the shared WHERE conditions and params for candidate queries."""
    placeholders = ','.join('?' for _ in EXCLUDED_SOURCES)
    conditions = f"""
        analyzed_at IS NOT NULL
        AND (expert_review_status = 'none' OR expert_review_status IS NULL)
        AND expert_review_status != 'skipped'
        AND edition IS NULL
        AND importance_score <= 1.0
        AND COALESCE(translated_title, '') != ''
        AND COALESCE(expert_review, '') = ''
        AND source NOT IN ({placeholders})
    """
    params = list(EXCLUDED_SOURCES)
    return conditions, params


def get_eligible_candidates(conn, edition: str, lookback_days: int = 1) -> list:
    """Fetch eligible news candidates for the given edition's time window.

    Uses COALESCE(published_at, collected_at) so that articles missing a
    published_at timestamp are matched by their collection time instead.
    """
    cursor = conn.cursor()
    window_start, window_end = get_edition_time_window(edition, lookback_days=lookback_days)
    base_cond, base_params = _base_candidate_query_conditions()

    cursor.execute(f"""
        SELECT id, original_title, original_content,
               COALESCE(published_at, collected_at) AS effective_time,
               source
        FROM news
        WHERE {base_cond}
            AND COALESCE(published_at, collected_at) >= ?
            AND COALESCE(published_at, collected_at) <= ?
        ORDER BY COALESCE(published_at, collected_at) DESC
    """, base_params + [window_start, window_end])

    candidates = []
    for row in cursor.fetchall():
        candidates.append({
            'id': row['id'],
            'original_title': row['original_title'] or '',
            'original_content': row['original_content'] or '',
            'published_at': row['effective_time'],
            'source': row['source'] or '',
        })

    return candidates


def get_editorial_candidates(conn) -> list:
    """Fetch editorial pick candidates and match them against the DB.

    Calls fetch_editorial_picks() from the crawler, then looks up each URL
    in the database.  Only returns rows that have been analyzed (analyzed_at
    IS NOT NULL), are not yet assigned to an edition, and have a non-empty
    translated_title.  No time-window filter is applied so that high-quality
    articles are not missed due to timing.
    """
    from src.collector.crawler import NewsCrawler

    crawler = NewsCrawler()
    picks = crawler.fetch_editorial_picks()
    if not picks:
        return []

    cursor = conn.cursor()
    candidates = []
    seen_ids = set()
    base_cond, base_params = _base_candidate_query_conditions()

    for pick in picks:
        url = pick["original_url"]
        cursor.execute(f"""
            SELECT id, original_title, original_content,
                   COALESCE(published_at, collected_at) AS effective_time,
                   source
            FROM news
            WHERE original_url = ?
              AND {base_cond}
        """, [url] + base_params)

        row = cursor.fetchone()
        if row and row['id'] not in seen_ids:
            seen_ids.add(row['id'])
            candidates.append({
                'id': row['id'],
                'original_title': row['original_title'] or '',
                'original_content': row['original_content'] or '',
                'published_at': row['effective_time'],
                'source': row['source'] or '',
            })

    logger.info(f"Editorial candidates: {len(candidates)} matched in DB (from {len(picks)} picks)")
    return candidates


def balance_sources(candidates: list, conn) -> list:
    """Ensure source diversity in the candidate pool before filter_news().

    1. Cap over-represented sources at MAX_PER_SOURCE_BEFORE_FILTER.
    2. For each major economic source with < 2 candidates, supplement from
       DB without time-window restriction, preferring articles WITH content.
    """
    source_counts = Counter(c['source'] for c in candidates)

    # --- Step 1: cap over-represented sources ---
    balanced = []
    cap_counts = Counter()
    for c in candidates:
        src = c['source']
        if cap_counts[src] < MAX_PER_SOURCE_BEFORE_FILTER:
            balanced.append(c)
            cap_counts[src] += 1

    capped = len(candidates) - len(balanced)
    if capped:
        logger.info(f"balance_sources: {capped}건 상한 초과 제거")

    # --- Step 2: supplement underrepresented major sources ---
    existing_ids = {c['id'] for c in balanced}
    cursor = conn.cursor()
    base_cond, base_params = _base_candidate_query_conditions()
    supplemented = {}

    for source in MAJOR_ECONOMIC_SOURCES:
        current = cap_counts[source]
        if current >= 2:
            continue

        need = SUPPLEMENT_PER_SOURCE - current
        if need <= 0:
            continue

        # Prefer articles WITH content (original_content != '')
        cursor.execute(f"""
            SELECT id, original_title, original_content,
                   COALESCE(published_at, collected_at) AS effective_time,
                   source
            FROM news
            WHERE source = ?
              AND {base_cond}
            ORDER BY
                CASE WHEN original_content IS NOT NULL AND original_content != '' THEN 0 ELSE 1 END,
                COALESCE(published_at, collected_at) DESC
            LIMIT ?
        """, [source] + base_params + [need])

        added = 0
        for row in cursor.fetchall():
            if row['id'] not in existing_ids:
                balanced.append({
                    'id': row['id'],
                    'original_title': row['original_title'] or '',
                    'original_content': row['original_content'] or '',
                    'published_at': row['effective_time'],
                    'source': row['source'] or '',
                })
                existing_ids.add(row['id'])
                added += 1

        if added:
            supplemented[source] = added

    if supplemented:
        logger.info(f"balance_sources: 소스 보충 {supplemented}")

    logger.info(
        f"balance_sources: 입력 {len(candidates)}건 → 출력 {len(balanced)}건 "
        f"(소스 {len(set(c['source'] for c in balanced))}개)"
    )
    return balanced


def get_fallback_candidates(conn, existing_ids: set, need: int) -> list:
    """Pull additional candidates from DB when the pipeline yields < target.

    No time-window restriction.  Prefers articles with content from sources
    not yet represented in the selected set.
    """
    cursor = conn.cursor()
    base_cond, base_params = _base_candidate_query_conditions()

    placeholders = ",".join("?" * len(existing_ids)) if existing_ids else "''"
    id_params = list(existing_ids) if existing_ids else []

    cursor.execute(f"""
        SELECT id, original_title, original_content,
               COALESCE(published_at, collected_at) AS effective_time,
               source
        FROM news
        WHERE {base_cond}
            AND id NOT IN ({placeholders})
            AND original_content IS NOT NULL AND original_content != ''
        ORDER BY COALESCE(published_at, collected_at) DESC
        LIMIT ?
    """, base_params + id_params + [need * 3])

    fallback = []
    for row in cursor.fetchall():
        fallback.append({
            'id': row['id'],
            'original_title': row['original_title'] or '',
            'original_content': row['original_content'] or '',
            'published_at': row['effective_time'],
            'source': row['source'] or '',
        })

    if fallback:
        logger.info(f"fallback: DB에서 {len(fallback)}건 추가 후보 확보")

    return fallback


def select_edition_news(edition: str, target_count: int = 10, lookback_days: int = 1) -> list:
    """Select news items for a specific edition's expert review queue.

    Uses the canonical filter_news() and balance_categories() from
    news_filter.py.

    Returns:
        List of selected news IDs.
    """
    conn = get_connection()

    try:
        # 1. 시간 윈도우 후보
        candidates = get_eligible_candidates(conn, edition, lookback_days=lookback_days)
        logger.info(f"[{edition}] 시간윈도우 후보: {len(candidates)}건")

        # 2. 에디토리얼 픽 합류
        editorial = get_editorial_candidates(conn)
        if editorial:
            seen_ids = {c['id'] for c in candidates}
            added = 0
            for ec in editorial:
                if ec['id'] not in seen_ids:
                    candidates.append(ec)
                    seen_ids.add(ec['id'])
                    added += 1
            if added:
                logger.info(f"[{edition}] 에디토리얼 추가: +{added}건")

        # 3. 소스 다양성 균형화 (filter_news 이전)
        candidates = balance_sources(candidates, conn)

        if not candidates:
            logger.warning(f"[{edition}] No eligible candidates found")
            return []

        # 4. 필터링 파이프라인
        filtered = filter_news(candidates)
        logger.info(f"[{edition}] filter_news 후: {len(filtered)}건")

        selected = balance_categories(filtered, target_count=target_count)
        logger.info(f"[{edition}] balance_categories 후: {len(selected)}건")

        # 5. 후보 부족 시 보완 로직
        if len(selected) < target_count:
            shortage = target_count - len(selected)
            logger.info(f"[{edition}] 목표 미달 ({len(selected)}/{target_count}), 보완 로직 실행")

            selected_ids = {item['id'] for item in selected}
            fallback = get_fallback_candidates(conn, selected_ids, shortage)
            if fallback:
                fb_filtered = filter_news(fallback, enable_dedup=True)
                fb_selected = balance_categories(
                    fb_filtered, target_count=shortage
                )
                selected.extend(fb_selected)
                logger.info(f"[{edition}] 보완 후: {len(selected)}건")

        # 6. 최종 로그
        if selected:
            categories = Counter(n.get('category', '기타') for n in selected)
            sources = Counter(n.get('source', '') for n in selected)
            logger.info(f"[{edition}] 최종 카테고리: {dict(categories)}")
            logger.info(f"[{edition}] 최종 소스: {dict(sources)}")

        return [item['id'] for item in selected]

    finally:
        conn.close()


def update_selected_status(news_ids: list, edition: str) -> int:
    """Update selected items to 'queued_today' and assign edition.

    Returns:
        Number of items updated.
    """
    if not news_ids:
        return 0

    conn = get_connection()
    cursor = conn.cursor()

    try:
        placeholders = ",".join("?" * len(news_ids))
        cursor.execute(f"""
            UPDATE news
            SET expert_review_status = 'queued_today',
                edition = ?,
                updated_at = ?
            WHERE id IN ({placeholders})
        """, [edition, datetime.now()] + news_ids)

        conn.commit()
        updated = cursor.rowcount
        logger.info(f"[{edition}] Updated {updated} news items to 'queued_today'")
        return updated

    finally:
        conn.close()


def reset_stale_queue() -> int:
    """Reset 'queued_today' items from previous days that were never reviewed.

    Only resets items whose updated_at date is before today.
    Does NOT touch any items from today's editions.

    Returns:
        Number of items reset.
    """
    conn = get_connection()
    cursor = conn.cursor()

    try:
        today_str = datetime.now().strftime("%Y-%m-%d")
        cursor.execute("""
            UPDATE news
            SET expert_review_status = 'none',
                edition = NULL,
                updated_at = ?
            WHERE expert_review_status = 'queued_today'
              AND DATE(updated_at) < ?
        """, (datetime.now(), today_str))

        conn.commit()
        reset_count = cursor.rowcount

        if reset_count > 0:
            logger.info(f"Reset {reset_count} stale items from previous days")

        return reset_count

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run_edition_selection(edition: str = None, lookback_days: int = 1) -> dict:
    """Run the selection process for a specific edition.

    1. Reset stale queue items from previous days
    2. Select new items for this edition
    3. Update their status and edition

    Returns:
        Summary dict with selection results.
    """
    if edition is None:
        edition = get_current_edition()

    config = EDITION_CONFIG[edition]
    logger.info(f"Starting {config['label']} ({edition}) edition selection (lookback={lookback_days}days)")

    # Reset stale queue from previous days
    reset_count = reset_stale_queue()

    # Select new items for this edition
    selected_ids = select_edition_news(edition, target_count=10, lookback_days=lookback_days)

    # Update status
    updated_count = update_selected_status(selected_ids, edition)

    result = {
        "edition": edition,
        "label": config['label'],
        "reset_count": reset_count,
        "selected_count": len(selected_ids),
        "updated_count": updated_count,
        "selected_ids": selected_ids,
        "timestamp": datetime.now().isoformat(),
    }

    logger.info(f"Edition selection complete: {result}")
    return result


# Backward-compatible alias
def run_daily_selection() -> dict:
    """Backward-compatible wrapper. Delegates to run_edition_selection."""
    return run_edition_selection()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    """Run edition selection from command line."""
    parser = argparse.ArgumentParser(description="Edition News Selector")
    parser.add_argument(
        "--edition",
        choices=['morning', 'afternoon', 'evening'],
        default=None,
        help="Edition to select for (default: auto-detect based on current time)",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=1,
        help="Number of days to look back for candidates (default: 1)",
    )
    args = parser.parse_args()

    result = run_edition_selection(edition=args.edition, lookback_days=args.lookback_days)
    print(f"Edition selection completed ({result['label']}):")
    print(f"  - Edition: {result['edition']}")
    print(f"  - Reset from stale queue: {result['reset_count']}")
    print(f"  - Selected: {result['selected_count']}")
    print(f"  - Updated to queued_today: {result['updated_count']}")
    print(f"  - News IDs: {result['selected_ids']}")


if __name__ == "__main__":
    main()

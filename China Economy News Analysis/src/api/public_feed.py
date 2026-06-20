"""Public feed API for user-facing news display.

Provides functions to retrieve expert-reviewed news for public consumption.
Supports 3-edition-per-day publishing (morning/afternoon/evening).
"""

import sqlite3
from typing import Optional
from datetime import datetime, date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from config.settings import DATABASE_PATH


def get_connection() -> sqlite3.Connection:
    """Get database connection with row factory."""
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ============================================================
# None-safe row mappers (③ None 안전 처리 중앙화)
# DB row → dict 변환 시 모든 None 처리를 한 곳에서 담당.
# 각 mapper는 해당 쿼리의 컬럼 목록에만 의존한다.
# ============================================================

def _map_headline_row(rank: int, row) -> dict:
    """headline 쿼리 row → None-safe dict.

    컬럼: id, headline (COALESCE), full_title, category, importance
    """
    return {
        "rank": rank,
        "id": row["id"],
        "headline": row["headline"] or (row["full_title"] or "")[:36],
        "category": row["category"] or "",
        "importance": row["importance"] or 0.5,
    }


def get_published_news(limit: int = 10, offset: int = 0) -> list[dict]:
    """Retrieve expert-reviewed news for public display.

    Only returns news where expert_comment IS NOT NULL.

    Args:
        limit: Maximum number of news items to return (default: 10)
        offset: Number of items to skip for pagination (default: 0)

    Returns:
        List of news dictionaries with fields:
        - id: News ID
        - headline: Translated title (Korean)
        - expert_review: Expert's comment/review
        - original_article: Original content
        - source: News source name
        - date: Publication date (ISO format)
        - importance: Importance score (0.0-1.0)
        - category: Industry category
        - summary: AI-generated summary
        - edition: Edition code (morning/afternoon/evening)
    """
    conn = get_connection()
    cursor = conn.cursor()

    query = """
        SELECT * FROM (
            -- Legacy: expert_reviews based
            SELECT
                n.id,
                n.card_headline,
                COALESCE(n.card_headline, n.translated_title) AS headline,
                er.expert_comment AS expert_review,
                n.original_content AS original_article,
                n.source,
                n.published_at AS date,
                n.importance_score AS importance,
                n.industry_category AS category,
                n.summary,
                n.edition
            FROM news n
            INNER JOIN expert_reviews er ON n.id = er.news_id
            WHERE er.expert_comment IS NOT NULL
              AND er.publish_status = 'published'
              AND n.original_content IS NOT NULL AND TRIM(n.original_content) != ''

            UNION ALL

            -- CNI pipeline: pipeline_status based (no expert_reviews needed)
            -- 한국어 번역(summary_ko)이 있는 뉴스만 공개
            SELECT
                n.id,
                n.card_headline,
                n.card_headline AS headline,
                COALESCE(cs.refined_ko, cs.summary_ko) AS expert_review,
                n.original_content AS original_article,
                n.source,
                n.published_at AS date,
                n.importance_score AS importance,
                n.industry_category AS category,
                COALESCE(cs.refined_ko, cs.summary_ko, n.summary) AS summary,
                n.edition
            FROM news n
            INNER JOIN cni_summaries cs ON n.id = cs.news_id
            WHERE n.pipeline_status = 'published'
              AND n.card_headline IS NOT NULL
              AND cs.summary_ko IS NOT NULL
              AND n.id NOT IN (
                  SELECT news_id FROM expert_reviews WHERE publish_status = 'published'
              )
        )
        ORDER BY date DESC
        LIMIT ? OFFSET ?
    """

    cursor.execute(query, (limit, offset))
    rows = cursor.fetchall()
    conn.close()

    import re

    def _clean_review(text):
        """Clean CNI translation for public display."""
        if not text:
            return text
        lines = text.strip().split('\n')
        cleaned = []
        for line in lines:
            stripped = line.strip()
            # Remove title/summary prefixes
            if re.match(r'^(제목|표제|标题)\s*[:：]', stripped):
                continue
            stripped = re.sub(r'^(요약|적요|摘要)\s*[:：]\s*', '', stripped)
            # Remove structure tags
            stripped = re.sub(r'\[핵심\s*사실\]\s*:?\s*', '', stripped)
            stripped = re.sub(r'\[배경\]\s*:?\s*', '', stripped)
            stripped = re.sub(r'\[영향과?\s*의미\]\s*:?\s*', '', stripped)
            stripped = re.sub(r'【[^】]+】\s*:?\s*', '', stripped)
            stripped = re.sub(r'핵심\s*사실\s*[:：]\s*', '', stripped)
            stripped = re.sub(r'배경\s*[:：]\s*', '', stripped)
            stripped = re.sub(r'영향과?\s*의미\s*[:：]\s*', '', stripped)
            if stripped:
                cleaned.append(stripped)
        return '\n'.join(cleaned).strip()

    def _has_chinese(text):
        return bool(re.search(r'[\u4e00-\u9fff\u3400-\u4dbf]', text or ''))

    result = []
    for row in rows:
        d = dict(row)
        if d.get('expert_review'):
            d['expert_review'] = _clean_review(d['expert_review'])
            # Remove markdown bold
            d['expert_review'] = d['expert_review'].replace('**', '')

        # headline에 중국어 잔존 시: card_headline 우선, 둘 다 중국어면 중국어 제거
        headline = d.get('headline') or ''
        if _has_chinese(headline):
            card_hl = d.get('card_headline') or ''
            if card_hl and not _has_chinese(card_hl):
                d['headline'] = card_hl
            else:
                # 중국어 문자 제거 후 사용
                cleaned = re.sub(r'[\u4e00-\u9fff\u3400-\u4dbf]+', '', headline)
                cleaned = re.sub(r'\s+', ' ', cleaned).strip()
                d['headline'] = cleaned if cleaned else headline

        result.append(d)

    return result


def get_published_news_count() -> int:
    """Get total count of published (expert-reviewed) news.

    Returns:
        Total number of news items with expert comments
    """
    conn = get_connection()
    cursor = conn.cursor()

    query = """
        SELECT
            (SELECT COUNT(*) FROM news n
             INNER JOIN expert_reviews er ON n.id = er.news_id
             WHERE er.expert_comment IS NOT NULL
               AND er.publish_status = 'published'
               AND n.original_content IS NOT NULL AND TRIM(n.original_content) != '')
            +
            (SELECT COUNT(*) FROM news n
             INNER JOIN cni_summaries cs ON n.id = cs.news_id
             WHERE n.pipeline_status = 'published'
               AND n.card_headline IS NOT NULL
               AND cs.summary_ko IS NOT NULL
               AND n.id NOT IN (
                   SELECT news_id FROM expert_reviews WHERE publish_status = 'published'
               ))
    """

    cursor.execute(query)
    count = cursor.fetchone()[0]
    conn.close()

    return count


def get_news_by_id(news_id: int) -> Optional[dict]:
    """Get a single news item by ID.

    Args:
        news_id: The news item ID

    Returns:
        News dictionary if found and has expert review, None otherwise
    """
    conn = get_connection()
    cursor = conn.cursor()

    # Try legacy first
    query_legacy = """
        SELECT
            n.id,
            n.card_headline,
            COALESCE(n.card_headline, n.translated_title) AS headline,
            er.expert_comment AS expert_review,
            n.original_content AS original_article,
            n.original_url,
            n.source,
            n.published_at AS date,
            n.importance_score AS importance,
            n.industry_category AS category,
            n.summary,
            er.ai_comment,
            er.ai_final_review,
            n.edition
        FROM news n
        INNER JOIN expert_reviews er ON n.id = er.news_id
        WHERE n.id = ? AND er.expert_comment IS NOT NULL
          AND er.publish_status = 'published'
    """
    cursor.execute(query_legacy, (news_id,))
    row = cursor.fetchone()

    # Try CNI if legacy not found
    if not row:
        query_cni = """
            SELECT
                n.id,
                n.card_headline,
                n.card_headline AS headline,
                COALESCE(cs.refined_ko, cs.summary_ko) AS expert_review,
                n.original_content AS original_article,
                n.original_url,
                n.source,
                n.published_at AS date,
                n.importance_score AS importance,
                n.industry_category AS category,
                COALESCE(cs.refined_ko, cs.summary_ko, n.summary) AS summary,
                n.hansanguk_tip,
                n.analysis_ko,
                n.evidence_sentences,
                NULL AS ai_comment,
                NULL AS ai_final_review,
                n.edition
            FROM news n
            INNER JOIN cni_summaries cs ON n.id = cs.news_id
            WHERE n.id = ?
              AND n.pipeline_status = 'published'
              AND cs.summary_ko IS NOT NULL
        """
        cursor.execute(query_cni, (news_id,))
        row = cursor.fetchone()

    conn.close()

    if not row:
        return None

    import re
    result = dict(row)

    # Clean review text
    def _clean(text):
        if not text:
            return text
        lines = text.strip().split('\n')
        cleaned = []
        for line in lines:
            s = line.strip()
            if re.match(r'^(제목|표제|标题)\s*[:：]', s):
                continue
            s = re.sub(r'^(요약|적요|摘要)\s*[:：]\s*', '', s)
            s = re.sub(r'\[핵심\s*사실\]\s*:?\s*', '', s)
            s = re.sub(r'\[배경\]\s*:?\s*', '', s)
            s = re.sub(r'\[영향과?\s*의미\]\s*:?\s*', '', s)
            s = re.sub(r'【[^】]+】\s*:?\s*', '', s)
            if s:
                cleaned.append(s)
        return '\n'.join(cleaned).strip()

    if result.get('expert_review'):
        result['expert_review'] = _clean(result['expert_review'])

    # Clean headline Chinese residue
    headline = result.get('headline') or ''
    if re.search(r'[\u4e00-\u9fff]', headline):
        card_hl = result.get('card_headline') or ''
        if card_hl and not re.search(r'[\u4e00-\u9fff]', card_hl):
            result['headline'] = card_hl
        else:
            result['headline'] = re.sub(r'[\u4e00-\u9fff\u3400-\u4dbf]+', '', headline).strip()

    return result


def get_published_news_by_date(target_date: date, limit: int = 50) -> list[dict]:
    """Get published news for a specific date.

    Args:
        target_date: The date to filter by
        limit: Maximum number of results

    Returns:
        List of news dictionaries for that date
    """
    conn = get_connection()
    cursor = conn.cursor()

    query = """
        SELECT
            n.id,
            n.card_headline,
            n.translated_title AS headline,
            er.expert_comment AS expert_review,
            n.original_content AS original_article,
            n.source,
            n.published_at AS date,
            n.importance_score AS importance,
            n.industry_category AS category,
            n.summary,
            n.edition
        FROM news n
        INNER JOIN expert_reviews er ON n.id = er.news_id
        WHERE er.expert_comment IS NOT NULL
          AND er.publish_status = 'published'
          AND n.original_content IS NOT NULL AND TRIM(n.original_content) != ''
          AND DATE(n.published_at) = ?
        ORDER BY n.importance_score DESC, n.published_at DESC
        LIMIT ?
    """

    cursor.execute(query, (target_date.isoformat(), limit))
    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]


def get_available_dates() -> list[str]:
    """Get list of dates that have published news.

    Returns:
        List of date strings (ISO format) in descending order
    """
    conn = get_connection()
    cursor = conn.cursor()

    query = """
        SELECT DISTINCT DATE(n.published_at) AS news_date
        FROM news n
        INNER JOIN expert_reviews er ON n.id = er.news_id
        WHERE er.expert_comment IS NOT NULL
          AND er.publish_status = 'published'
          AND n.original_content IS NOT NULL AND TRIM(n.original_content) != ''
        ORDER BY news_date DESC
    """

    cursor.execute(query)
    rows = cursor.fetchall()
    conn.close()

    return [row['news_date'] for row in rows if row['news_date']]


# ---------------------------------------------------------------------------
# Edition headline titles
# ---------------------------------------------------------------------------

EDITION_TITLES = {
    'morning': '오늘 오전 뉴스 헤드라인',
    'afternoon': '오늘 오후 뉴스 헤드라인',
    'evening': '오늘 밤 뉴스 헤드라인',
}


def get_today_headlines(target_date: Optional[date] = None, edition: Optional[str] = None) -> dict:
    """Get headlines summary for a specific edition.

    Args:
        target_date: Date to get headlines for (default: today)
        edition: 'morning', 'afternoon', or 'evening' (default: auto-detect)

    Returns:
        Dictionary with date, edition, title, and sorted headlines list
    """
    if target_date is None:
        target_date = date.today()

    # Auto-detect edition based on current time
    if edition is None:
        current_hour = datetime.now().hour
        if current_hour < 7:
            edition = 'morning'
        elif current_hour < 14:
            edition = 'afternoon'
        else:
            edition = 'evening'

    title = EDITION_TITLES.get(edition, '오늘 뉴스 헤드라인')

    conn = get_connection()
    cursor = conn.cursor()

    query = """
        SELECT * FROM (
            -- Legacy: expert_reviews based
            SELECT
                n.id,
                COALESCE(n.card_headline, SUBSTR(n.translated_title, 1, 20)) AS headline,
                n.translated_title AS full_title,
                n.industry_category AS category,
                n.importance_score AS importance,
                er.created_at AS review_date
            FROM news n
            INNER JOIN expert_reviews er ON n.id = er.news_id
            WHERE er.expert_comment IS NOT NULL
              AND er.publish_status = 'published'
              AND DATE(er.created_at) = ?
              AND n.edition = ?

            UNION ALL

            -- CNI pipeline: 사용자가 오늘 공개한 뉴스만 (published_at 기준)
            SELECT
                n.id,
                n.card_headline AS headline,
                n.card_headline AS full_title,
                n.industry_category AS category,
                n.importance_score AS importance,
                cs.published_at AS review_date
            FROM news n
            INNER JOIN cni_summaries cs ON n.id = cs.news_id
            WHERE n.pipeline_status = 'published'
              AND n.card_headline IS NOT NULL
              AND cs.summary_ko IS NOT NULL
              AND cs.published_at IS NOT NULL
              AND DATE(cs.published_at) = ?
              AND n.id NOT IN (
                  SELECT news_id FROM expert_reviews WHERE publish_status = 'published'
              )
        )
        ORDER BY importance DESC, review_date DESC
        LIMIT 10
    """

    cursor.execute(query, (target_date.isoformat(), edition,
                           target_date.isoformat()))
    rows = cursor.fetchall()
    conn.close()

    headlines = [_map_headline_row(i, row) for i, row in enumerate(rows, 1)]

    return {
        "date": target_date.isoformat(),
        "edition": edition,
        "title": title,
        "count": len(headlines),
        "headlines": headlines
    }


def search_published_news(query: str, limit: int = 20) -> list[dict]:
    """Search published news by keyword in title and summary."""
    conn = get_connection()
    cursor = conn.cursor()
    like = f"%{query}%"
    cursor.execute("""
        SELECT n.id, n.card_headline, n.translated_title AS headline,
               er.expert_comment AS expert_review,
               n.original_content AS original_article,
               n.source, n.published_at AS date,
               n.importance_score AS importance,
               n.industry_category AS category,
               n.summary, n.edition
        FROM news n
        INNER JOIN expert_reviews er ON n.id = er.news_id
        WHERE er.expert_comment IS NOT NULL
          AND er.publish_status = 'published'
          AND n.original_content IS NOT NULL AND TRIM(n.original_content) != ''
          AND (n.translated_title LIKE ? OR n.summary LIKE ?)
        ORDER BY n.importance_score DESC, n.published_at DESC
        LIMIT ?
    """, (like, like, limit))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_all_edition_headlines(target_date: Optional[date] = None) -> list[dict]:
    """Get unified headlines for today (no edition duplication).

    Returns single consolidated headline list, not separated by edition.
    """
    if target_date is None:
        target_date = date.today()

    conn = get_connection()
    cursor = conn.cursor()

    query = """
        SELECT * FROM (
            -- Legacy: expert_reviews based
            SELECT
                n.id,
                COALESCE(n.card_headline, SUBSTR(n.translated_title, 1, 20)) AS headline,
                n.translated_title AS full_title,
                n.industry_category AS category,
                n.importance_score AS importance,
                er.publish_status_updated_at AS review_date
            FROM news n
            INNER JOIN expert_reviews er ON n.id = er.news_id
            WHERE er.expert_comment IS NOT NULL
              AND er.publish_status = 'published'
              AND DATE(er.publish_status_updated_at) = ?

            UNION ALL

            -- CNI pipeline
            SELECT
                n.id,
                n.card_headline AS headline,
                n.card_headline AS full_title,
                n.industry_category AS category,
                n.importance_score AS importance,
                cs.published_at AS review_date
            FROM news n
            INNER JOIN cni_summaries cs ON n.id = cs.news_id
            WHERE n.pipeline_status = 'published'
              AND n.card_headline IS NOT NULL
              AND cs.summary_ko IS NOT NULL
              AND cs.published_at IS NOT NULL
              AND DATE(cs.published_at) = ?
              AND n.id NOT IN (
                  SELECT news_id FROM expert_reviews WHERE publish_status = 'published'
              )
        )
        ORDER BY importance DESC, review_date DESC
    """

    cursor.execute(query, (target_date.isoformat(), target_date.isoformat()))
    rows = cursor.fetchall()
    conn.close()

    # Deduplicate by news id
    seen = set()
    unique_rows = []
    for row in rows:
        if row['id'] not in seen:
            seen.add(row['id'])
            unique_rows.append(row)

    headlines = [_map_headline_row(i, row) for i, row in enumerate(unique_rows, 1)]

    if not headlines:
        return []

    return [{
        "date": target_date.isoformat(),
        "edition": "today",
        "title": "오늘의 뉴스 헤드라인",
        "headlines": headlines,
        "count": len(headlines),
    }]

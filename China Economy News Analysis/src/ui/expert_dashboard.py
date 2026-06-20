"""Expert Dashboard - Streamlit UI for news review and commentary."""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime, timedelta
import json
import re
import sys
import os
import hmac
import html
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.database.models import get_connection
from src.utils.report_exporter import ReportExporter
from src.utils.notifications import (
    NotificationManager, toggle_bookmark, set_tags, get_tags,
    get_all_tags, get_bookmarked_news, update_news_classification
)
from src.utils.markdown_review import MarkdownReviewManager
from src.utils.headline_generator import generate_headline, save_headline, get_headline
from config.settings import CLAUDE_MODEL
from src.collector.news_filter import SOURCE_PRIORITY
from config.gics_taxonomy import get_korean_label

# Card headline constants
MAX_HEADLINE_LENGTH = 18

def _has_chinese(text: str) -> bool:
    """Return True if text contains CJK (Chinese) characters."""
    return bool(re.search(r'[\u4e00-\u9fff\u3400-\u4dbf]', text or ''))

# 8가지 기준 한글 라벨 매핑
SCORE_AXIS_LABELS = {
    "policy_hierarchy": "정책위계",
    "corporate_hierarchy": "기업위계",
    "strategic_industry": "전략산업",
    "economic_scale": "경제규모",
    "geographic_significance": "지리",
    "time_sensitivity": "시간민감도",
    "international_impact": "국제영향",
    "social_impact": "사회영향",
}

# 부스터 한글 라벨
BOOSTER_LABELS = {
    "top_leader": "🔴 최고지도자 언급",
    "state_council": "🟠 국무원 발표",
    "soe_strategic": "🟡 중앙기업+전략산업",
}


def create_score_radar_chart(breakdown: dict) -> go.Figure:
    """score_breakdown JSON으로 8축 레이더 차트 생성."""
    keys = list(SCORE_AXIS_LABELS.keys())
    labels = [SCORE_AXIS_LABELS[k] for k in keys]
    values = [breakdown.get(k, 0) for k in keys]

    # 차트를 닫기 위해 첫 번째 값 반복
    labels_closed = labels + [labels[0]]
    values_closed = values + [values[0]]

    # 점수에 따른 fill 색상 결정 (최대 점수 기준)
    max_score = max(values) if values else 0
    if max_score >= 80:
        line_color = "rgba(220, 53, 69, 0.9)"   # 빨강
        fill_color = "rgba(220, 53, 69, 0.25)"
    elif max_score >= 60:
        line_color = "rgba(255, 152, 0, 0.9)"    # 주황
        fill_color = "rgba(255, 152, 0, 0.25)"
    else:
        line_color = "rgba(158, 158, 158, 0.9)"  # 회색
        fill_color = "rgba(158, 158, 158, 0.25)"

    fig = go.Figure()
    fig.add_trace(go.Scatterpolar(
        r=values_closed,
        theta=labels_closed,
        fill='toself',
        fillcolor=fill_color,
        line=dict(color=line_color, width=2),
        marker=dict(size=5),
    ))

    fig.update_layout(
        polar=dict(
            radialaxis=dict(visible=True, range=[0, 100], tickvals=[20, 40, 60, 80, 100]),
        ),
        showlegend=False,
        margin=dict(l=40, r=40, t=20, b=20),
        height=280,
    )
    return fig


def get_top_news(limit: int = 10, industry: str = None, days: int = 7,
                 bookmarked_only: bool = False, tag_filter: str = None,
                 queued_only: bool = False, edition_filter: str = None) -> pd.DataFrame:
    """Get top news sorted by importance score."""
    conn = get_connection()

    query = """
        SELECT n.*,
               er.expert_comment,
               er.ai_final_review,
               er.opinion_conflict,
               er.review_completed_at,
               er.publish_status
        FROM news n
        LEFT JOIN expert_reviews er ON n.id = er.news_id
        WHERE n.analyzed_at IS NOT NULL
    """
    params = []

    if queued_only:
        query += " AND n.expert_review_status = 'queued_today'"
        if edition_filter and edition_filter != '전체':
            query += " AND n.edition = ?"
            params.append(edition_filter)
    else:
        query += " AND n.collected_at >= datetime('now', ?)"
        params.append(f'-{days} days')

    if industry and industry != "전체":
        query += " AND n.industry_category = ?"
        params.append(industry)

    if bookmarked_only:
        query += " AND n.is_bookmarked = TRUE"

    if tag_filter and tag_filter != "전체":
        query += " AND n.tags LIKE ?"
        params.append(f'%"{tag_filter}"%')

    query += " ORDER BY n.importance_score DESC LIMIT ?"
    params.append(limit)

    df = pd.read_sql_query(query, conn, params=params)
    conn.close()
    return df


def search_news(query: str, days: int = 7, scope: str = "전체") -> pd.DataFrame:
    """Search news by keyword. scope: 전체|제목|요약"""
    conn = get_connection()
    like = f"%{query}%"
    if scope == "제목":
        cond = "(n.translated_title LIKE ? OR n.original_title LIKE ?)"
        cond_params = [like, like]
    elif scope == "요약":
        cond = "n.summary LIKE ?"
        cond_params = [like]
    else:
        cond = "(n.translated_title LIKE ? OR n.summary LIKE ? OR n.original_title LIKE ?)"
        cond_params = [like, like, like]
    sql = f"""
        SELECT n.*,
               er.expert_comment,
               er.ai_final_review,
               er.opinion_conflict,
               er.review_completed_at,
               er.publish_status
        FROM news n
        LEFT JOIN expert_reviews er ON n.id = er.news_id
        WHERE n.analyzed_at IS NOT NULL
          AND n.collected_at >= datetime('now', ?)
          AND {cond}
        ORDER BY n.importance_score DESC
        LIMIT 50
    """
    df = pd.read_sql_query(sql, conn, params=[f'-{days} days'] + cond_params)
    conn.close()
    return df


def get_news_detail(news_id: int) -> dict:
    """Get single news detail."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT n.*,
               er.id as review_id,
               er.ai_comment,
               er.expert_comment,
               er.ai_final_review,
               er.opinion_conflict,
               er.review_started_at,
               er.review_completed_at
        FROM news n
        LEFT JOIN expert_reviews er ON n.id = er.news_id
        WHERE n.id = ?
    """, (news_id,))
    row = cursor.fetchone()
    conn.close()

    if row:
        return dict(row)
    return None


def save_expert_comment(news_id: int, comment: str) -> bool:
    """Save expert comment. Auto-publish only if original_content exists."""
    conn = get_connection()
    cursor = conn.cursor()

    try:
        # Check if news has original content
        cursor.execute("SELECT original_content FROM news WHERE id = ?", (news_id,))
        news_row = cursor.fetchone()
        has_content = bool(news_row and news_row['original_content'] and news_row['original_content'].strip())
        status = 'published' if has_content else 'draft'

        # Check if review exists
        cursor.execute("SELECT id FROM expert_reviews WHERE news_id = ?", (news_id,))
        existing = cursor.fetchone()

        now = datetime.now()

        if existing:
            cursor.execute("""
                UPDATE expert_reviews SET
                    expert_comment = ?,
                    review_completed_at = ?,
                    publish_status = ?,
                    publish_status_updated_at = ?,
                    updated_at = ?
                WHERE news_id = ?
            """, (comment, now, status, now, now, news_id))
        else:
            cursor.execute("""
                INSERT INTO expert_reviews
                (news_id, expert_comment, review_started_at, review_completed_at,
                 publish_status, publish_status_updated_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (news_id, comment, now, now, status, now, now, now))

        conn.commit()
        success = True
    except Exception as e:
        st.error(f"저장 실패: {e}")
        success = False
    finally:
        conn.close()

    # Auto-generate card headline after successful review save
    if success:
        _auto_generate_headline(news_id)

    return success


def _auto_generate_headline(news_id: int):
    """Auto-generate card headline if missing after expert review."""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT edition, translated_title, card_headline FROM news WHERE id = ?",
            (news_id,)
        )
        row = cursor.fetchone()
        conn.close()

        if row and row['translated_title'] and not row['card_headline']:
            from src.utils.headline_generator import generate_and_save_headline
            headline = generate_and_save_headline(news_id, row['translated_title'])
            if headline:
                st.toast(f"카드 헤드라인 자동 생성: {headline}")
    except Exception as e:
        # Non-fatal: log but don't block the review save
        print(f"Auto headline generation failed for news {news_id}: {e}")


def has_original_content(news_id: int) -> bool:
    """Check if news has non-empty original_content."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT original_content FROM news WHERE id = ?", (news_id,))
    row = cursor.fetchone()
    conn.close()
    return bool(row and row['original_content'] and row['original_content'].strip())


def skip_news(news_id: int) -> bool:
    """Mark news as skipped (비공개). Removes from queued list, not published."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            UPDATE news SET
                expert_review_status = 'skipped',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (news_id,))
        conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        st.error(f"비공개 처리 실패: {e}")
        return False
    finally:
        conn.close()


def restore_skipped_news(news_id: int) -> bool:
    """Restore skipped news back to queued_today."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            UPDATE news SET
                expert_review_status = 'queued_today',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND expert_review_status = 'skipped'
        """, (news_id,))
        conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        st.error(f"복원 실패: {e}")
        return False
    finally:
        conn.close()


def get_reviews_by_status(status: str = 'draft', limit: int = 50) -> pd.DataFrame:
    """Get reviews filtered by publish_status."""
    conn = get_connection()
    # 'discarded' includes legacy 'rejected' status
    if status == 'discarded':
        query = """
            SELECT n.id, n.translated_title, n.card_headline, n.original_title, n.original_content,
                   n.importance_score, n.industry_category, n.source, n.summary,
                   n.published_at, n.original_url,
                   er.expert_comment, er.ai_final_review, er.opinion_conflict,
                   er.review_completed_at, er.publish_status, er.admin_note,
                   er.publish_status_updated_at
            FROM news n
            JOIN expert_reviews er ON n.id = er.news_id
            WHERE er.expert_comment IS NOT NULL
              AND er.publish_status IN ('discarded', 'rejected')
            ORDER BY er.publish_status_updated_at DESC
            LIMIT ?
        """
        df = pd.read_sql_query(query, conn, params=[limit])
    else:
        query = """
            SELECT n.id, n.translated_title, n.card_headline, n.original_title, n.original_content,
                   n.importance_score, n.industry_category, n.source, n.summary,
                   n.published_at, n.original_url,
                   er.expert_comment, er.ai_final_review, er.opinion_conflict,
                   er.review_completed_at, er.publish_status, er.admin_note,
                   er.publish_status_updated_at
            FROM news n
            JOIN expert_reviews er ON n.id = er.news_id
            WHERE er.expert_comment IS NOT NULL
              AND er.publish_status = ?
            ORDER BY er.review_completed_at DESC
            LIMIT ?
        """
        df = pd.read_sql_query(query, conn, params=[status, limit])
    conn.close()
    return df


def update_expert_comment(news_id: int, comment: str, title: str = None) -> bool:
    """수정된 리뷰 내용 및 제목 저장. publish_status는 'published' 유지.
    제목 변경 시 card_headline도 자동 재생성."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        now = datetime.now()
        cursor.execute("""
            UPDATE expert_reviews SET
                expert_comment = ?,
                updated_at = ?
            WHERE news_id = ?
        """, (comment, now, news_id))
        if title and title.strip():
            cursor.execute("""
                UPDATE news SET
                    translated_title = ?,
                    updated_at = ?
                WHERE id = ?
            """, (title.strip(), now, news_id))
        conn.commit()
        success = cursor.rowcount > 0
    except Exception as e:
        st.error(f"수정 저장 실패: {e}")
        return False
    finally:
        conn.close()

    # 제목 변경 시 card_headline 자동 재생성
    if success and title and title.strip():
        try:
            from src.utils.headline_generator import generate_and_save_headline
            generate_and_save_headline(news_id, title.strip())
        except Exception as e:
            st.warning(f"헤드라인 재생성 실패 (제목 저장은 완료): {e}")

    return success


def update_publish_status(news_id: int, new_status: str, admin_note: str = None) -> bool:
    """Update publish_status for a single review."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        now = datetime.now()
        cursor.execute("""
            UPDATE expert_reviews SET
                publish_status = ?,
                admin_note = ?,
                publish_status_updated_at = ?,
                updated_at = ?
            WHERE news_id = ?
        """, (new_status, admin_note, now, now, news_id))
        conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        st.error(f"상태 변경 실패: {e}")
        return False
    finally:
        conn.close()


def bulk_update_publish_status(news_ids: list, new_status: str) -> int:
    """Bulk update publish_status for multiple reviews."""
    if not news_ids:
        return 0
    conn = get_connection()
    cursor = conn.cursor()
    try:
        now = datetime.now()
        placeholders = ','.join(['?'] * len(news_ids))
        cursor.execute(f"""
            UPDATE expert_reviews SET
                publish_status = ?,
                publish_status_updated_at = ?,
                updated_at = ?
            WHERE news_id IN ({placeholders})
        """, [new_status, now, now] + list(news_ids))
        conn.commit()
        return cursor.rowcount
    except Exception as e:
        st.error(f"일괄 상태 변경 실패: {e}")
        return 0
    finally:
        conn.close()


def generate_ai_final_review(news_id: int) -> str:
    """Generate AI final review comparing expert and AI opinions."""
    news = get_news_detail(news_id)
    if not news:
        return "뉴스를 찾을 수 없습니다."

    if not news.get('expert_comment'):
        return "전문가 코멘트가 없습니다."

    raise RuntimeError("Anthropic API has been disabled. System uses Ollama only.")


def get_statistics() -> dict:
    """Get dashboard statistics."""
    conn = get_connection()
    cursor = conn.cursor()

    stats = {}

    # Total news
    cursor.execute("SELECT COUNT(*) FROM news")
    stats['total_news'] = cursor.fetchone()[0]

    # Analyzed news
    cursor.execute("SELECT COUNT(*) FROM news WHERE analyzed_at IS NOT NULL")
    stats['analyzed_news'] = cursor.fetchone()[0]

    # Expert reviewed
    cursor.execute("SELECT COUNT(*) FROM expert_reviews WHERE expert_comment IS NOT NULL")
    stats['reviewed_news'] = cursor.fetchone()[0]

    # Opinion conflicts
    cursor.execute("SELECT COUNT(*) FROM expert_reviews WHERE opinion_conflict = 1")
    stats['conflicts'] = cursor.fetchone()[0]

    # Today's news
    cursor.execute("SELECT COUNT(*) FROM news WHERE date(collected_at) = date('now')")
    stats['today_news'] = cursor.fetchone()[0]

    # Bookmarked news
    cursor.execute("SELECT COUNT(*) FROM news WHERE is_bookmarked = TRUE")
    stats['bookmarked'] = cursor.fetchone()[0]

    # Today's queued (selected) news stats
    cursor.execute("SELECT COUNT(*) FROM news WHERE expert_review_status = 'queued_today'")
    stats['queued_today'] = cursor.fetchone()[0]

    cursor.execute("""
        SELECT COUNT(*) FROM news n
        JOIN expert_reviews er ON n.id = er.news_id
        WHERE n.expert_review_status = 'queued_today'
          AND er.expert_comment IS NOT NULL
    """)
    stats['queued_reviewed'] = cursor.fetchone()[0]

    stats['queued_pending'] = stats['queued_today'] - stats['queued_reviewed']

    cursor.execute("""
        SELECT AVG(importance_score) FROM news
        WHERE expert_review_status = 'queued_today'
    """)
    avg = cursor.fetchone()[0]
    stats['queued_avg_importance'] = round(avg, 2) if avg else 0

    # Publish status stats
    try:
        cursor.execute("SELECT COUNT(*) FROM expert_reviews WHERE publish_status = 'draft' AND expert_comment IS NOT NULL")
        stats['pending_approval'] = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM expert_reviews WHERE publish_status = 'published'")
        stats['published_reviews'] = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM expert_reviews WHERE publish_status IN ('rejected', 'discarded')")
        stats['discarded_reviews'] = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM news WHERE expert_review_status = 'skipped'")
        stats['skipped_news'] = cursor.fetchone()[0]
    except:
        stats['pending_approval'] = 0
        stats['published_reviews'] = 0
        stats['discarded_reviews'] = 0
        stats['skipped_news'] = 0

    # Per-edition queue counts
    for ed in ['morning', 'afternoon', 'evening']:
        try:
            cursor.execute(
                "SELECT COUNT(*) FROM news WHERE expert_review_status = 'queued_today' AND edition = ?",
                (ed,)
            )
            stats[f'queued_{ed}'] = cursor.fetchone()[0]
        except:
            stats[f'queued_{ed}'] = 0

    # Unread notifications
    try:
        cursor.execute("SELECT COUNT(*) FROM notifications WHERE is_read = FALSE")
        stats['unread_notifications'] = cursor.fetchone()[0]
    except:
        stats['unread_notifications'] = 0

    conn.close()
    return stats


def apply_custom_css():
    """Apply modern Google-style CSS design."""
    st.markdown("""
    <style>
    /* Google Fonts */
    @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@300;400;500;700&family=Roboto:wght@300;400;500;700&display=swap');

    /* Global Styles */
    .stApp {
        font-family: 'Noto Sans KR', 'Roboto', sans-serif;
    }

    /* Header Banner */
    .header-banner {
        background: linear-gradient(135deg, #f5f7fa 0%, #e8eef5 100%);
        padding: 1.2rem 1.5rem;
        border-radius: 16px;
        margin-bottom: 2rem;
        box-shadow: 0 4px 20px rgba(0,0,0,0.1);
        display: flex;
        align-items: center;
        gap: 1.5rem;
    }

    .header-content {
        flex-shrink: 0;
        min-width: 20%;
    }

    .header-images-container {
        flex: 1;
        display: flex;
        justify-content: flex-end;
        gap: 12px;
    }

    .city-image-wrapper {
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: 4px;
    }

    .header-image {
        width: 180px;
        height: 90px;
        border-radius: 10px;
        object-fit: cover;
        object-position: center;
        box-shadow: 0 4px 12px rgba(0,0,0,0.15);
        transition: transform 0.3s ease;
    }

    .header-image:hover {
        transform: scale(1.05);
    }

    .city-label {
        font-size: 0.75rem;
        color: #666;
        font-weight: 500;
    }

    .header-title {
        color: #1a1a1a;
        font-size: 1.6rem;
        font-weight: 700;
        margin: 0;
    }

    .header-subtitle {
        color: #555;
        font-size: 0.85rem;
        font-weight: 400;
        margin-top: 0.4rem;
    }

    /* Card Styles */
    .news-card {
        background: white;
        border-radius: 12px;
        padding: 1.5rem;
        margin-bottom: 1rem;
        box-shadow: 0 2px 8px rgba(0,0,0,0.08);
        border: 1px solid #e0e0e0;
        transition: all 0.3s ease;
    }

    .news-card:hover {
        box-shadow: 0 4px 16px rgba(0,0,0,0.12);
        transform: translateY(-2px);
    }

    /* Importance Badges */
    .badge-critical {
        background: linear-gradient(135deg, #d32f2f 0%, #c62828 100%);
        color: white;
        padding: 0.3rem 0.8rem;
        border-radius: 20px;
        font-size: 0.8rem;
        font-weight: 500;
    }

    .badge-high {
        background: linear-gradient(135deg, #f57c00 0%, #ef6c00 100%);
        color: white;
        padding: 0.3rem 0.8rem;
        border-radius: 20px;
        font-size: 0.8rem;
        font-weight: 500;
    }

    .badge-medium {
        background: linear-gradient(135deg, #fbc02d 0%, #f9a825 100%);
        color: #333;
        padding: 0.3rem 0.8rem;
        border-radius: 20px;
        font-size: 0.8rem;
        font-weight: 500;
    }

    .badge-low {
        background: linear-gradient(135deg, #43a047 0%, #388e3c 100%);
        color: white;
        padding: 0.3rem 0.8rem;
        border-radius: 20px;
        font-size: 0.8rem;
        font-weight: 500;
    }

    /* Stat Cards */
    .stat-card {
        background: linear-gradient(135deg, #1a237e 0%, #0d47a1 100%);
        color: white;
        padding: 1.2rem;
        border-radius: 12px;
        text-align: center;
        box-shadow: 0 4px 12px rgba(26, 35, 126, 0.3);
    }

    .stat-number {
        font-size: 2rem;
        font-weight: 700;
        margin: 0;
    }

    .stat-label {
        font-size: 0.85rem;
        opacity: 0.9;
        margin-top: 0.3rem;
    }

    /* Sidebar Styling */
    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, #fafafa 0%, #f5f5f5 100%);
    }

    [data-testid="stSidebar"] .stSelectbox label,
    [data-testid="stSidebar"] .stSlider label,
    [data-testid="stSidebar"] .stCheckbox label {
        color: #1a237e;
        font-weight: 500;
    }

    /* Tab Styling */
    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
        background: #f5f5f5;
        padding: 0.5rem;
        border-radius: 12px;
    }

    .stTabs [data-baseweb="tab"] {
        border-radius: 8px;
        padding: 0.5rem 1rem;
        font-weight: 500;
    }

    .stTabs [aria-selected="true"] {
        background: #1a237e !important;
        color: white !important;
    }

    /* Button Styling */
    .stButton > button {
        background: linear-gradient(135deg, #1a237e 0%, #0d47a1 100%);
        color: white;
        border: none;
        border-radius: 8px;
        padding: 0.5rem 1.5rem;
        font-weight: 500;
        transition: all 0.3s ease;
    }

    .stButton > button:hover {
        background: linear-gradient(135deg, #0d47a1 0%, #1565c0 100%);
        box-shadow: 0 4px 12px rgba(13, 71, 161, 0.4);
    }

    /* Expander Styling */
    .streamlit-expanderHeader {
        background: #f8f9fa;
        border-radius: 8px;
        font-weight: 500;
    }

    /* Info/Warning/Success boxes */
    .stAlert {
        border-radius: 8px;
    }

    /* Hide Streamlit branding */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}

    /* Custom scrollbar */
    ::-webkit-scrollbar {
        width: 8px;
        height: 8px;
    }

    ::-webkit-scrollbar-track {
        background: #f1f1f1;
        border-radius: 4px;
    }

    ::-webkit-scrollbar-thumb {
        background: #1a237e;
        border-radius: 4px;
    }

    ::-webkit-scrollbar-thumb:hover {
        background: #0d47a1;
    }
    </style>
    """, unsafe_allow_html=True)


def render_header():
    """Render the header banner with Beijing, Shanghai, and Shenzhen skyline images."""
    st.markdown("""
    <div class="header-banner">
        <div class="header-content">
            <h1 class="header-title">한상국의 쉬운 중국경제뉴스 해설</h1>
            <p class="header-subtitle">AI 기반 중국 경제 뉴스 분석 및 전문가 리뷰 플랫폼</p>
        </div>
        <div class="header-images-container">
            <div class="city-image-wrapper">
                <img src="https://images.pexels.com/photos/34809836/pexels-photo-34809836.jpeg?auto=compress&cs=tinysrgb&w=400"
                     alt="Beijing CBD with China Zun Tower"
                     class="header-image">
                <span class="city-label">베이징</span>
            </div>
            <div class="city-image-wrapper">
                <img src="https://images.unsplash.com/photo-1474181487882-5abf3f0ba6c2?w=400&q=80"
                     alt="Shanghai Pudong Lujiazui Skyline"
                     class="header-image">
                <span class="city-label">상하이</span>
            </div>
            <div class="city-image-wrapper">
                <img src="https://images.pexels.com/photos/20828135/pexels-photo-20828135.jpeg?auto=compress&cs=tinysrgb&w=400"
                     alt="Shenzhen Skyline with Skyscrapers"
                     class="header-image">
                <span class="city-label">선전</span>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)


# 번역 교정 게시판 — 교정 유형별 라벨/색상
_TC_ISSUE_LABELS = {
    "tense→past":      ("시제 교정", "#c62828"),       # 미래·추정형 → 과거형
    "perspective→중국": ("시점 교정", "#1565c0"),       # 우리나라/자국 → 중국
    "cn_self→중국":     ("자기지칭 교정", "#1565c0"),    # 我国 등 → 중국
}


def _tc_issue_label(issue_type: str):
    if issue_type in _TC_ISSUE_LABELS:
        return _TC_ISSUE_LABELS[issue_type]
    if str(issue_type).startswith("political"):
        return ("정치 중립화", "#6a1b9a")
    return (str(issue_type), "#555")


def get_translation_corrections(days: int = 2) -> pd.DataFrame:
    """최근 N일간 '공개된' 뉴스에 대해 플랫폼이 자동 교정한 번역 오류 내역."""
    conn = get_connection()
    try:
        df = pd.read_sql_query(f"""
            SELECT tc.news_id, tc.issue_type, tc.before_text, tc.after_text,
                   tc.created_at,
                   COALESCE(NULLIF(n.card_headline, ''), n.translated_title,
                            n.original_title) AS title,
                   cs.published_at
            FROM translation_corrections tc
            JOIN news n ON n.id = tc.news_id
            LEFT JOIN cni_summaries cs ON cs.news_id = tc.news_id
            WHERE cs.published_at IS NOT NULL
              AND datetime(cs.published_at) >= datetime('now', '-{int(days)} days')
            ORDER BY cs.published_at DESC, tc.id
        """, conn)
    except Exception:
        # 테이블 미생성(아직 교정 이력 없음) 등 — 빈 보드로 처리
        df = pd.DataFrame()
    finally:
        conn.close()
    return df


def get_qc_audit_runs(limit: int = 9) -> pd.DataFrame:
    """일일 공개 직후 QC 점검 실행 이력."""
    conn = get_connection()
    try:
        df = pd.read_sql_query(
            "SELECT run_at, edition, n_checked, n_corrected, by_type "
            "FROM qc_audit_runs ORDER BY id DESC LIMIT ?", conn, params=[limit])
    except Exception:
        df = pd.DataFrame()
    finally:
        conn.close()
    return df


def render_translation_board():
    """번역 교정 게시판 — QC 점검 이력 + 공개 뉴스 오류·수정 내역(칼라 카드 대체)."""
    df = get_translation_corrections(days=2)
    n = 0 if df.empty else len(df)
    st.markdown(f"""
    <div style="display:flex;align-items:center;gap:0.5rem;margin-bottom:0.5rem;">
        <span style="font-size:1.1rem;font-weight:700;color:#1a237e;">📋 번역 교정 게시판</span>
        <span style="font-size:0.78rem;color:#fff;background:#1565c0;border-radius:10px;padding:1px 9px;">최근 2일</span>
        <span style="font-size:0.78rem;color:#666;">자동 교정 {n}건</span>
    </div>
    """, unsafe_allow_html=True)

    # ── 일일 QC 점검 이력 (공개 직후 자동 점검) ──
    _ED = {"morning": "🌅아침", "afternoon": "☀️오후", "evening": "🌙저녁", "manual": "수동"}
    qc = get_qc_audit_runs()
    if not qc.empty:
        items = []
        for _, r in qc.iterrows():
            ts = str(r["run_at"] or "")[:16].replace("T", " ")
            ed = _ED.get(str(r["edition"]), str(r["edition"]))
            try:
                bt = json.loads(r["by_type"] or "{}")
            except Exception:
                bt = {}
            bt_s = ", ".join(f"{k} {v}" for k, v in bt.items()) if bt else ""
            items.append(
                f'<div style="font-size:0.82rem;line-height:1.5;padding:2px 0;">'
                f'<span style="color:#666;">🕒 {html.escape(ts)}</span> '
                f'<span style="background:#37474f;color:#fff;border-radius:4px;padding:1px 6px;font-size:0.72rem;">{ed}</span> '
                f'점검 <b>{int(r["n_checked"])}</b>건 · 수정 <b style="color:#1b5e20;">{int(r["n_corrected"])}</b>건'
                f'{("  ·  " + html.escape(bt_s)) if bt_s else ""}</div>')
        st.markdown(
            '<div style="background:#eceff1;border-radius:8px;padding:6px 12px;margin-bottom:8px;">'
            '<div style="font-size:0.8rem;font-weight:700;color:#37474f;margin-bottom:2px;">🔍 일일 공개 직후 QC 점검 이력</div>'
            + "".join(items) + '</div>', unsafe_allow_html=True)

    if df.empty:
        st.info("최근 2일간 공개된 뉴스에서 자동 교정된 번역 오류가 없습니다. "
                "(새 뉴스가 번역·공개되면 시제·시점 오류와 수정 내용이 여기 표시됩니다.)")
        return

    rows_html = []
    for nid, g in df.groupby("news_id", sort=False):
        pub = str(g.iloc[0]["published_at"] or "")[:16].replace("T", " ")
        title = html.escape(str(g.iloc[0]["title"] or "")[:60])
        items = []
        for _, r in g.iterrows():
            name, color = _tc_issue_label(r["issue_type"])
            before = html.escape(str(r["before_text"] or "").strip()) or "—"
            after = html.escape(str(r["after_text"] or "").strip()) or "—"
            items.append(
                f'<div style="margin:3px 0;font-size:0.86rem;line-height:1.5;">'
                f'<span style="background:{color};color:#fff;border-radius:4px;'
                f'padding:1px 7px;font-size:0.72rem;margin-right:6px;">{name}</span>'
                f'<span style="color:#b71c1c;">오류: {before}</span>'
                f'<span style="color:#999;margin:0 6px;">→</span>'
                f'<span style="color:#1b5e20;font-weight:600;">수정: {after}</span>'
                f'</div>'
            )
        rows_html.append(
            f'<div style="border-bottom:1px solid #eee;padding:9px 4px;">'
            f'<div style="font-size:0.78rem;color:#666;margin-bottom:3px;">'
            f'🕒 공개 {html.escape(pub)} &nbsp;·&nbsp; '
            f'<b style="color:#222;">{title}</b></div>'
            f'{"".join(items)}</div>'
        )

    st.markdown(
        '<div style="background:#fff;border:1px solid #e0e0e0;border-radius:12px;'
        'padding:6px 14px;max-height:360px;overflow-y:auto;'
        'box-shadow:0 2px 8px rgba(0,0,0,0.06);">'
        + "".join(rows_html) + '</div>',
        unsafe_allow_html=True)


def render_today_overview(stats):
    """Render today's selected news overview panel."""
    queued = stats.get('queued_today', 0)
    reviewed = stats.get('queued_reviewed', 0)
    pending = stats.get('queued_pending', 0)
    avg_imp = stats.get('queued_avg_importance', 0)

    if queued == 0:
        return

    progress_pct = int((reviewed / queued) * 100) if queued > 0 else 0

    st.markdown(f"""
    <div style="background: linear-gradient(135deg, #e3f2fd 0%, #f3e5f5 100%);
                border-radius: 12px; padding: 1.2rem 1.5rem; margin-bottom: 1.5rem;
                border-left: 5px solid #1565c0;">
        <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 1rem;">
            <div>
                <h3 style="margin: 0 0 0.3rem 0; color: #1565c0; font-size: 1.1rem;">
                    📌 오늘 선정 뉴스
                </h3>
                <p style="margin: 0; color: #555; font-size: 0.85rem;">
                    전문가 리뷰 대기 중인 뉴스가 <b>{pending}건</b> 있습니다
                </p>
            </div>
            <div style="display: flex; gap: 2rem; align-items: center;">
                <div style="text-align: center;">
                    <p style="margin: 0; font-size: 1.8rem; font-weight: 700; color: #1565c0;">{queued}</p>
                    <p style="margin: 0; font-size: 0.75rem; color: #777;">선정</p>
                </div>
                <div style="text-align: center;">
                    <p style="margin: 0; font-size: 1.8rem; font-weight: 700; color: #2e7d32;">{reviewed}</p>
                    <p style="margin: 0; font-size: 0.75rem; color: #777;">리뷰 완료</p>
                </div>
                <div style="text-align: center;">
                    <p style="margin: 0; font-size: 1.8rem; font-weight: 700; color: #e65100;">{pending}</p>
                    <p style="margin: 0; font-size: 0.75rem; color: #777;">대기</p>
                </div>
                <div style="text-align: center;">
                    <p style="margin: 0; font-size: 1.8rem; font-weight: 700; color: #6a1b9a;">{avg_imp:.2f}</p>
                    <p style="margin: 0; font-size: 0.75rem; color: #777;">평균 중요도</p>
                </div>
            </div>
        </div>
        <div style="margin-top: 0.8rem; background: #e0e0e0; border-radius: 6px; height: 8px; overflow: hidden;">
            <div style="width: {progress_pct}%; height: 100%;
                        background: linear-gradient(90deg, #2e7d32, #66bb6a);
                        border-radius: 6px; transition: width 0.3s;"></div>
        </div>
        <p style="margin: 0.3rem 0 0 0; font-size: 0.75rem; color: #888; text-align: right;">
            리뷰 진행률 {progress_pct}%
            &nbsp;|&nbsp;
            오전 {stats.get('queued_morning', 0)}건
            &middot; 오후 {stats.get('queued_afternoon', 0)}건
            &middot; 저녁 {stats.get('queued_evening', 0)}건
        </p>
    </div>
    """, unsafe_allow_html=True)

    # Compact table of today's selected news
    df = get_top_news(limit=20, queued_only=True)
    if not df.empty:
        table_data = []
        for _, row in df.iterrows():
            imp = row['importance_score'] or 0
            if imp >= 0.8:
                badge = "🔴"
            elif imp >= 0.6:
                badge = "🟠"
            elif imp >= 0.4:
                badge = "🟡"
            else:
                badge = "🟢"

            has_review = pd.notna(row.get('expert_comment')) and row.get('expert_comment')
            status = "✅" if has_review else "⏳"

            title = row['original_title'] or row['translated_title'] or ''
            if len(title) > 50:
                title = title[:50] + "…"

            table_data.append({
                "": badge,
                "제목": title,
                "중요도": f"{imp:.2f}",
                "출처": row.get('source', '-'),
                "산업": get_korean_label(row.get('industry_category') or ''),
                "리뷰": status,
            })

        st.dataframe(
            pd.DataFrame(table_data),
            use_container_width=True,
            hide_index=True,
            height=min(len(table_data) * 35 + 38, 400),
        )


def _auth_token() -> str:
    """현재 자격증명에서 파생한 로그인 토큰.

    URL 쿼리파라미터에 저장해 웹소켓 재접속·새로고침에도 로그인이 유지되게 한다
    (st.session_state는 세션마다 초기화되므로 모바일에서 연결이 끊기면 풀림).
    비밀번호를 바꾸면 토큰도 바뀌어 기존 URL 토큰은 자동 무효화된다.
    """
    cfg_user = os.environ.get("DASHBOARD_USER", "")
    cfg_pw = os.environ.get("DASHBOARD_PW", "")
    if not cfg_pw:
        return ""
    import hashlib
    return hashlib.sha256(f"{cfg_user}:{cfg_pw}:cni-dash-v1".encode()).hexdigest()[:32]


def restore_login_from_token() -> None:
    """URL 토큰이 유효하면 로그인 세션을 복원 (재접속 후 자동 로그인)."""
    tok = _auth_token()
    if tok and st.query_params.get("auth") == tok:
        st.session_state["login"] = True


def login_page():
    """관리자 로그인 페이지."""
    st.title("🔐 관리자 로그인")
    st.markdown("---")

    col1, col2, col3 = st.columns([1, 2, 1])

    with col2:
        user = st.text_input("ID")
        pw = st.text_input("PW", type="password")

        if st.button("로그인", use_container_width=True):
            # 자격증명은 환경변수에서 로드 (평문 하드코딩 제거).
            # DASHBOARD_USER / DASHBOARD_PW 는 systemd EnvironmentFile 로 주입.
            cfg_user = os.environ.get("DASHBOARD_USER", "")
            cfg_pw = os.environ.get("DASHBOARD_PW", "")
            ok = bool(cfg_pw) and hmac.compare_digest(user, cfg_user) and hmac.compare_digest(pw, cfg_pw)
            if ok:
                st.session_state["login"] = True
                # URL에 로그인 토큰 저장 → 재접속/새로고침 시 자동 로그인 유지
                st.query_params["auth"] = _auth_token()
                st.rerun()
            elif not cfg_pw:
                st.error("인증이 구성되지 않았습니다 (DASHBOARD_USER/DASHBOARD_PW 미설정)")
            else:
                st.error("ID 또는 PW가 틀렸습니다")


def main():
    """Main Streamlit app."""
    st.set_page_config(
        page_title="한상국의 쉬운 중국경제뉴스 해설",
        page_icon="🇨🇳",
        layout="wide",
        # "collapsed": 데스크톱·모바일 모두 사이드바를 접은 채로 시작 (» 버튼으로 펼침)
        initial_sidebar_state="collapsed"
    )

    # PWA 메타태그 + 설치 배너
    st.markdown("""
    <!-- PWA Manifest -->
    <link rel="manifest" href="/app/static/manifest.json?v=2">
    <meta name="theme-color" content="#BFDFFF">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
    <link rel="apple-touch-icon" href="/app/static/CNI-icon.png">
    <link rel="icon" type="image/png" href="/app/static/CNI-icon.png">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-title" content="중국경제">
    <meta name="mobile-web-app-capable" content="yes">

    <!-- Open Graph for KakaoTalk -->
    <meta property="og:type" content="website">
    <meta property="og:title" content="한상국의 쉬운 중국경제뉴스 해설">
    <meta property="og:description" content="매일 3회 업데이트 | 20여개 중국 언론 | AI 자동 선정">
    <meta property="og:image" content="http://chinanewsinsight.com/app/static/CNI-icon.png">
    <meta property="og:url" content="http://chinanewsinsight.com">

    <script>
    (function() {
        // iOS Safari 홈 화면 추가 안내 배너
        const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent) && !window.MSStream;
        const isInStandaloneMode = ('standalone' in window.navigator) && (window.navigator.standalone);

        if (isIOS && !isInStandaloneMode) {
            const bannerDismissed = localStorage.getItem('iosBannerDismissed');
            if (!bannerDismissed) {
                setTimeout(function() {
                    const banner = document.createElement('div');
                    banner.id = 'ios-install-banner';
                    banner.style.cssText = `
                        position: fixed; bottom: 0; left: 0; right: 0;
                        background: linear-gradient(135deg, #DC143C 0%, #FF6347 100%);
                        color: white; padding: 16px;
                        box-shadow: 0 -4px 12px rgba(0,0,0,0.15);
                        z-index: 999999;
                        font-family: 'Noto Sans KR', sans-serif;
                        animation: slideUp 0.3s ease-out;
                    `;
                    banner.innerHTML = `
                        <style>
                            @keyframes slideUp {
                                from { transform: translateY(100%); }
                                to { transform: translateY(0); }
                            }
                        </style>
                        <div style="display:flex;align-items:center;justify-content:space-between;">
                            <div style="flex:1;">
                                <div style="font-weight:600;margin-bottom:4px;">
                                    📱 앱처럼 사용하기
                                </div>
                                <div style="font-size:13px;opacity:0.95;">
                                    하단 공유 버튼(□↑) → "홈 화면에 추가"
                                </div>
                            </div>
                            <button id="dismiss-banner" style="
                                background:white;color:#DC143C;border:none;
                                padding:8px 16px;border-radius:6px;
                                font-weight:600;cursor:pointer;margin-left:12px;
                            ">확인</button>
                        </div>
                    `;
                    document.body.appendChild(banner);
                    document.getElementById('dismiss-banner').addEventListener('click', function() {
                        banner.style.animation = 'slideUp 0.3s ease-out reverse';
                        setTimeout(function() { banner.remove(); }, 300);
                        localStorage.setItem('iosBannerDismissed', 'true');
                    });
                }, 2000);
            }
        }

        // Android Chrome beforeinstallprompt
        let deferredPrompt;
        window.addEventListener('beforeinstallprompt', (e) => {
            e.preventDefault();
            deferredPrompt = e;
            setTimeout(() => {
                if (deferredPrompt) {
                    deferredPrompt.prompt();
                    deferredPrompt.userChoice.then(() => { deferredPrompt = null; });
                }
            }, 3000);
        });
    })();
    </script>
    """, unsafe_allow_html=True)

    # 로그인 체크 활성화 — 인증 통과 전까지 대시보드 차단
    # 먼저 URL 토큰으로 세션 복원 시도 (재접속/새로고침에도 로그인 유지)
    restore_login_from_token()
    if "login" not in st.session_state or not st.session_state["login"]:
        login_page()
        st.stop()

    # Apply custom CSS
    apply_custom_css()

    # Render header banner
    render_header()

    # Get statistics first
    stats = get_statistics()

    # 번역 교정 게시판 (기존 통계 칼라 카드 대체)
    render_translation_board()
    st.markdown("<br>", unsafe_allow_html=True)

    # Today's selected news overview
    render_today_overview(stats)

    # Sidebar filters
    with st.sidebar:
        st.markdown("### 🎛️ 필터 설정")

        # Industry filter — DB에서 실제 사용 중인 카테고리 동적 조회
        _conn_ind = get_connection()
        _cur_ind = _conn_ind.cursor()
        _cur_ind.execute("""
            SELECT DISTINCT industry_category FROM news
            WHERE industry_category IS NOT NULL AND industry_category != ''
            ORDER BY industry_category
        """)
        _db_cats = [r[0] for r in _cur_ind.fetchall()]
        _conn_ind.close()
        industry_options = ["전체"] + _db_cats

        def _industry_fmt(code):
            if code == "전체":
                return "전체 산업"
            return get_korean_label(code)

        selected_industry = st.selectbox(
            "산업 분류",
            industry_options,
            format_func=_industry_fmt
        )

        days_range = st.slider("📅 기간 (일)", 1, 30, 7)

        news_limit = st.slider("📰 표시 뉴스 수", 5, 30, 10)

        st.markdown("---")

        # Bookmark filter
        bookmarked_only = st.checkbox("⭐ 북마크만 보기", value=False)

        # Tag filter
        all_tags = get_all_tags()
        tag_options = ["전체"] + all_tags
        selected_tag = st.selectbox("🏷️ 태그 필터", tag_options)

        st.markdown("---")
        st.markdown("### 🔍 검색")
        search_query = st.text_input("검색어", placeholder="예: 반도체")
        search_scope = st.radio(
            "검색 범위",
            ["전체", "제목", "요약"],
            horizontal=True,
        )
        search_days = st.slider("검색 기간 (일)", 1, 90, 30)

        st.markdown("---")

        # Notification badge
        if stats['unread_notifications'] > 0:
            st.error(f"🔔 새 알림 {stats['unread_notifications']}개")

        # Footer info
        st.markdown("---")
        st.markdown("""
        <div style="text-align: center; color: #666; font-size: 0.8rem;">
            <p>📡 데이터 소스</p>
            <p style="font-size: 0.7rem;">베이징 · 상하이 · 선전<br>인민일보 · 경제일보 · 차이신<br>36Kr · 후시우</p>
        </div>
        """, unsafe_allow_html=True)

    # Main content
    notification_manager = NotificationManager()
    unread_count = stats['unread_notifications']
    notification_label = f"🔔 알림 ({unread_count})" if unread_count > 0 else "🔔 알림"

    pending_label = f"✅ 리뷰 승인 ({stats.get('pending_approval', 0)})" if stats.get('pending_approval', 0) > 0 else "✅ 리뷰 승인"

    tab1, tab2, tab3, tab4, tab_approve, tab5, tab6, tab7, tab8, tab_kg, tab_cni, tab_published, tab_hidden = st.tabs([
        "📬 추천함", "⭐ 북마크", "📂 Markdown 리뷰",
        "📝 리뷰 완료", pending_label, notification_label, "📥 리포트 내보내기",
        "📊 카테고리 분석", "📡 소스 분석", "🧠 Knowledge Graph", "📝 CNI 번역", "📢 공개함", "🔒 비공개함"
    ])

    with tab1:
        st.subheader("📋 오늘의 선정 뉴스")

        # Edition filter
        edition_options = {'전체': '전체 에디션', 'morning': '오전판', 'afternoon': '오후판', 'evening': '저녁/반판'}
        selected_edition = st.selectbox(
            "에디션 선택",
            options=list(edition_options.keys()),
            format_func=lambda x: edition_options[x],
            key="edition_filter"
        )

        if search_query:
            df = search_news(search_query, days=search_days, scope=search_scope)
        else:
            df = get_top_news(
                limit=news_limit,
                industry=selected_industry,
                days=days_range,
                bookmarked_only=bookmarked_only,
                tag_filter=selected_tag,
                queued_only=True,
                edition_filter=selected_edition if selected_edition != '전체' else None,
            )

        # unpublished는 비공개함으로 분리 (선정 알고리즘 결과는 모두 표시)
        if not df.empty and 'pipeline_status' in df.columns:
            df = df[df['pipeline_status'] != 'unpublished']

        # ── 정렬: 처리 필요한 뉴스 우선 ──
        if not df.empty and 'pipeline_status' in df.columns:
            _sort_order = {'selected': 0, None: 1, 'translated': 2, 'published': 3, 'skipped': 4}
            df = df.copy()
            df['_sort_key'] = df['pipeline_status'].map(lambda x: _sort_order.get(x, 1))
            df = df.sort_values(['_sort_key', 'importance_score'], ascending=[True, False])
            df = df.drop(columns=['_sort_key'])

        # Display persistent save feedback from session state
        if st.session_state.get("save_success_msg"):
            st.success(st.session_state.pop("save_success_msg"))
        if st.session_state.get("save_error_msg"):
            st.error(st.session_state.pop("save_error_msg"))

        if df.empty:
            st.info("선정된 뉴스가 없습니다. 일일 뉴스 선정을 먼저 실행해주세요.")
        else:
            for idx, row in df.iterrows():
                news_id = row['id']

                # Card container
                with st.container():
                    # Header with importance badge
                    importance = row['importance_score'] or 0
                    if importance >= 0.8:
                        badge = "🔴 매우 중요"
                    elif importance >= 0.6:
                        badge = "🟠 중요"
                    elif importance >= 0.4:
                        badge = "🟡 보통"
                    else:
                        badge = "🟢 낮음"

                    # Review status
                    has_review = pd.notna(row.get('expert_comment')) and row.get('expert_comment')
                    has_conflict = row.get('opinion_conflict')

                    status_badges = []
                    if has_review:
                        status_badges.append("✅ 리뷰완료")
                    if has_conflict:
                        status_badges.append("⚠️ 의견충돌")

                    # Publish status badge
                    pub_status = row.get('publish_status', '')
                    if pub_status == 'published':
                        status_badges.append("📢게시됨")
                    elif pub_status == 'draft' and has_review:
                        status_badges.append("⏳승인대기")
                    elif pub_status in ('rejected', 'discarded'):
                        status_badges.append("🗑폐기됨")

                    status_text = " | ".join(status_badges) if status_badges else "📝 리뷰대기"

                    # Title and metadata — 요약번역 전에는 중국어 원제 표시
                    _ps = row.get('pipeline_status') or ''
                    if _ps in ('translated', 'published') and row.get('card_headline'):
                        title = row['card_headline']
                    elif _ps in ('translated', 'published'):
                        title = row['translated_title'] or row['original_title']
                    else:
                        title = row['original_title'] or row['translated_title']
                    is_bookmarked = row.get('is_bookmarked') or False
                    bookmark_icon = "⭐" if is_bookmarked else "☆"

                    # ── Pipeline state & stuck detection ──
                    _ps = row.get('pipeline_status') or ''
                    _is_stuck = False
                    _stuck_msg = ""
                    if _ps in ('selected', 'translated'):
                        try:
                            from datetime import datetime as _dt, timedelta as _td
                            _updated = row.get('updated_at')
                            if _updated:
                                if isinstance(_updated, str):
                                    _updated = _dt.strptime(_updated[:19], "%Y-%m-%d %H:%M:%S")
                                _hours = (_dt.now() - _updated).total_seconds() / 3600
                                if _ps == 'selected' and _hours > 3:
                                    _is_stuck = True
                                    _stuck_msg = f"⚠️ {_hours:.0f}시간 미처리"
                                elif _ps == 'translated' and _hours > 6:
                                    _is_stuck = True
                                    _stuck_msg = f"⏰ {_hours:.0f}시간 미결정"
                        except Exception:
                            pass

                    # ── Progress Indicator ──
                    def _render_progress(state):
                        steps = [
                            ("선정됨", "selected"),
                            ("번역완료", "translated"),
                            ("게시완료", "published"),
                        ]
                        if state == 'skipped':
                            return "<span style='color:#4CAF50'>✓ 선정됨</span> → <span style='color:#9E9E9E'>⊘ 불요처리</span>"
                        if state == 'unpublished':
                            return "<span style='color:#4CAF50'>✓ 선정됨</span> → <span style='color:#4CAF50'>✓ 번역완료</span> → <span style='color:#F44336'>🔒 비공개</span>"
                        if state == 'processing':
                            return "<span style='color:#4CAF50'>✓ 선정됨</span> → <span style='color:#FF9800;font-weight:bold'>⏳ 처리중...</span> → <span style='color:#BDBDBD'>○ 게시완료</span>"
                        if state == 'failed':
                            return "<span style='color:#4CAF50'>✓ 선정됨</span> → <span style='color:#F44336;font-weight:bold'>✗ 처리실패</span>"
                        parts = []
                        state_order = {"selected": 0, "translated": 1, "published": 2}
                        current_idx = state_order.get(state, -1)
                        for i, (label, _) in enumerate(steps):
                            if i < current_idx:
                                parts.append(f"<span style='color:#4CAF50'>✓ {label}</span>")
                            elif i == current_idx:
                                parts.append(f"<span style='color:#1976D2;font-weight:bold'>▶ {label}</span>")
                            else:
                                parts.append(f"<span style='color:#BDBDBD'>○ {label}</span>")
                        return " → ".join(parts)

                    # ── Card Header Row ──
                    col1, col2, col3, col4, col5 = st.columns([0.38, 0.28, 0.14, 0.09, 0.11])

                    with col1:
                        _title_style = ""
                        if _ps == 'published':
                            _title_style = "opacity:0.55"
                        elif _ps == 'skipped':
                            _title_style = "opacity:0.4;text-decoration:line-through"
                        elif _is_stuck:
                            _title_style = "border-left:3px solid #F44336;padding-left:6px"

                        if _title_style:
                            st.markdown(f"<span style='{_title_style}'>{title}</span>", unsafe_allow_html=True)
                        else:
                            st.markdown(f"**{title}**")
                    with col2:
                        # Progress bar
                        _prog_html = _render_progress(_ps or 'selected')
                        if _is_stuck:
                            _prog_html += f" <span style='color:#F44336;font-size:12px'>{_stuck_msg}</span>"
                        st.markdown(f"<div style='font-size:12px;line-height:1.8'>{_prog_html}</div>", unsafe_allow_html=True)
                    with col3:
                        edition_label = {'morning': '오전', 'afternoon': '오후', 'evening': '저녁'}.get(
                            row.get('edition', '') or '', ''
                        )
                        edition_tag = f"[{edition_label}판] " if edition_label else ""
                        st.caption(f"{edition_tag}{badge}")
                    with col4:
                        if st.button(bookmark_icon, key=f"bookmark_{news_id}", help="북마크 토글"):
                            toggle_bookmark(news_id)
                            st.rerun()
                    with col5:
                        with st.popover("📝", help="빠른 리뷰"):
                            st.markdown(f"**{(title or '')[:40]}...**")
                            stance = st.radio(
                                "AI 분석 평가",
                                ["동의", "부분동의", "반대"],
                                key=f"stance_{news_id}",
                                horizontal=True,
                            )
                            quick_comment = st.text_input(
                                "한줄 코멘트",
                                key=f"qcomment_{news_id}",
                                placeholder="핵심 의견을 입력하세요",
                            )
                            if st.button("저장", key=f"qsave_{news_id}", type="primary"):
                                full_comment = f"[{stance}] {quick_comment}" if quick_comment else f"[{stance}]"
                                try:
                                    db_ok = save_expert_comment(news_id, full_comment)
                                    if db_ok:
                                        md_mgr = MarkdownReviewManager()
                                        md_mgr.save_review(
                                            news_id=news_id,
                                            content=full_comment,
                                            news=dict(row) if row is not None else None,
                                            auto_commit=True,
                                        )
                                        if has_original_content(news_id):
                                            st.session_state["save_success_msg"] = f"리뷰 저장 및 공개 완료 (리뷰 완료 탭에서 확인)"
                                        else:
                                            st.session_state["save_success_msg"] = f"리뷰 저장 완료 (⚠️ 원문 없음 - 비공개 상태)"
                                    else:
                                        st.session_state["save_error_msg"] = f"DB 저장 실패 (뉴스 {news_id})"
                                except Exception as e:
                                    st.session_state["save_error_msg"] = f"저장 중 오류: {e}"
                                st.rerun()

                    # ══════════════════════════════════════════
                    # CNI State-Based Action Section
                    # ══════════════════════════════════════════
                    if _ps == 'selected':
                        # ── STATE: selected → 큐 등록 방식 ──
                        _sa, _sb = st.columns([0.7, 0.3])
                        with _sa:
                            if st.button("📋 요약번역", key=f"translate_{news_id}", type="primary", help="백그라운드 큐에 등록 (~5분 소요)"):
                                from src.cni.process_queue import enqueue_news
                                _eq = enqueue_news(news_id)
                                if _eq.get("ok"):
                                    st.session_state["save_success_msg"] = f"#{news_id} 처리 큐에 등록됨 (백그라운드 ~5분)"
                                else:
                                    st.session_state["save_error_msg"] = f"#{news_id} 큐 등록 실패: {_eq.get('error')}"
                                st.rerun()
                        with _sb:
                            if st.button("🚫 불요", key=f"skip_{news_id}", help="요약·번역 불요 (폐기)"):
                                if skip_news(news_id):
                                    st.session_state["save_success_msg"] = f"#{news_id} 불요 처리 완료"
                                    st.rerun()

                    elif _ps == 'processing':
                        # ── STATE: processing → 대기 중 표시 ──
                        st.info(f"⏳ 백그라운드 처리 중... (헤드라인→요약→번역, ~5분 소요)")

                    elif _ps == 'failed':
                        # ── STATE: failed → 재시도/초기화 ──
                        st.error("처리 실패")
                        _fa, _fb = st.columns(2)
                        with _fa:
                            if st.button("🔄 재시도", key=f"retry_{news_id}", type="primary"):
                                from src.cni.process_queue import enqueue_news
                                from src.cni.pipeline_service import set_pipeline_status as _sps
                                _sps(news_id, "selected")  # failed → selected
                                enqueue_news(news_id)       # selected → processing
                                st.session_state["save_success_msg"] = f"#{news_id} 재시도 큐 등록"
                                st.rerun()
                        with _fb:
                            if st.button("🚫 불요", key=f"skip_{news_id}"):
                                from src.cni.pipeline_service import set_pipeline_status as _sps
                                _sps(news_id, "selected")  # failed → selected first
                                skip_news(news_id)          # selected → skipped
                                st.session_state["save_success_msg"] = f"#{news_id} 불요 처리"
                                st.rerun()

                    elif _ps == 'translated':
                        # ── STATE: translated ──
                        # session_state 기반: form이 텍스트 rerun 차단, 버튼은 밖에서 독립 동작
                        try:
                            from src.cni.pipeline_service import set_pipeline_status, publish_news, validate_quality_gate, reset_to_selected
                            from src.cni.pipeline_service import unpublish_news as _unpub_fn
                            from src.cni.summary_store import update_translation, update_refined, get_summary as get_cni_summary
                            from src.cni.postprocess import refine_korean

                            _cni_row = get_cni_summary(news_id)
                            _existing_hl = row.get('card_headline') or ''
                            _existing_ko = (_cni_row.get('summary_ko') or _cni_row.get('refined_ko') or '') if _cni_row else ''
                            _existing_tip = row.get('hansanguk_tip') or ''

                            # session_state 초기화 (최초 1회)
                            _sk_hl = f"cni_hl_{news_id}"
                            _sk_ko = f"cni_ko_{news_id}"
                            _sk_tip = f"cni_tip_{news_id}"
                            if _sk_hl not in st.session_state:
                                st.session_state[_sk_hl] = _existing_hl
                            if _sk_ko not in st.session_state:
                                st.session_state[_sk_ko] = _existing_ko
                            if _sk_tip not in st.session_state:
                                st.session_state[_sk_tip] = _existing_tip

                            # 중문 원문 참조
                            _title_zh = row.get('title_zh') or ''
                            _summary_zh = row.get('summary_zh') or ''
                            if _title_zh or _summary_zh:
                                st.caption(f"중문: {_title_zh[:50]}... | {_summary_zh[:50]}...")

                            # 편집 필드 — st.form으로 감싸 blur(포커스 이탈)마다 전체 페이지가
                            # rerun되는 것을 차단한다. form 없이 두면 칸을 벗어날 때마다 rerun→
                            # 위젯 노드 재생성→커서·스크롤이 튀어 선택/복사/붙여넣기가 불가능했다.
                            # 폼 안에서는 '입력 확정' 클릭 시점에만 한 번 commit된다.
                            # (공개/비공개 등 액션 버튼은 폼 밖에 두어 독립 동작 — CLAUDE.md §9)
                            with st.form(key=f"cni_edit_form_{news_id}", border=False):
                                st.text_input("📰 헤드라인 (최대 72자)", key=_sk_hl, max_chars=72)
                                st.text_area("📝 한국어 요약", key=_sk_ko, height=150)
                                st.text_area("💡 한상국의 팁", key=_sk_tip, height=80)
                                _btn_confirm = st.form_submit_button(
                                    "✏️ 입력 확정 (저장)", type="primary", use_container_width=True)

                            # 품질 게이트
                            _qg = validate_quality_gate(news_id, "published")
                            _can_publish = _qg["ok"]
                            if not _can_publish:
                                for _err in _qg["errors"]:
                                    st.error(f"게시 차단: {_err}")

                            # 버튼 (form 밖 — 각각 독립 동작)
                            _bc1, _bc2, _bc3, _bc4, _bc5 = st.columns(5)
                            with _bc1:
                                _btn_pub = st.button("📢 공개", key=f"cni_pub_{news_id}", type="primary", disabled=(not _can_publish))
                            with _bc2:
                                _btn_unpub = st.button("🔒 비공개", key=f"cni_unpub_{news_id}")
                            with _bc3:
                                _btn_save = st.button("💾 수정저장", key=f"cni_save_{news_id}")
                            with _bc4:
                                _btn_tip = st.button("💡 팁생성", key=f"cni_tip_gen_{news_id}", help="on-demand 팁 (~8초)")
                            with _bc5:
                                _btn_reset = st.button("🔄 초기화", key=f"cni_reset_{news_id}")

                            # session_state에서 현재 값 읽기
                            _hl_val = st.session_state.get(_sk_hl, '')
                            _ko_val = st.session_state.get(_sk_ko, '')
                            _tip_val = st.session_state.get(_sk_tip, '')

                            def _save_fields_tr(_nid, _hl, _ko, _tip):
                                from src.database.models import get_connection as _gc
                                _c = _gc()
                                if _hl and _hl.strip():
                                    _c.execute("UPDATE news SET card_headline=? WHERE id=?", (_hl.strip()[:72], _nid))
                                if _tip and _tip.strip():
                                    _c.execute("UPDATE news SET hansanguk_tip=? WHERE id=?", (_tip.strip()[:500], _nid))
                                else:
                                    _c.execute("UPDATE news SET hansanguk_tip=NULL WHERE id=?", (_nid,))
                                _c.commit()
                                _c.close()
                                if _ko and _ko.strip():
                                    update_translation(_nid, _ko.strip())

                            if _btn_pub:
                                _save_fields_tr(news_id, _hl_val, _ko_val, _tip_val)
                                publish_news(news_id)
                                # refine_korean()은 Ollama LLM 호출(최대 60초)이라 클릭 핸들러에서
                                # 동기 실행하면 웹소켓이 끊겨 로그인이 풀린다(모바일 "공개 반응 없음" 원인).
                                # → 백그라운드 스레드로 분리해 UI는 즉시 반환. 공개 피드는
                                #   COALESCE(refined_ko, summary_ko)라 refine 완료 전엔 summary_ko로 노출.
                                if _ko_val and _ko_val.strip():
                                    import threading
                                    _ko_snap = _ko_val.strip()
                                    def _bg_refine(_nid=news_id, _ko=_ko_snap):
                                        try:
                                            update_refined(_nid, refine_korean(_ko))
                                        except Exception:
                                            pass
                                    threading.Thread(target=_bg_refine, daemon=True).start()
                                st.session_state["save_success_msg"] = f"#{news_id} 공개 완료"
                                st.rerun()
                            elif _btn_unpub:
                                _save_fields_tr(news_id, _hl_val, _ko_val, _tip_val)
                                _unpub_fn(news_id)
                                st.session_state["save_success_msg"] = f"#{news_id} 비공개 → 비공개함으로 이동"
                                st.rerun()
                            elif _btn_save:
                                _save_fields_tr(news_id, _hl_val, _ko_val, _tip_val)
                                st.session_state["save_success_msg"] = f"#{news_id} 수정 저장 완료"
                                st.rerun()
                            elif _btn_tip:
                                # generate_tip_ondemand는 Ollama+Papago 동기호출(재시도 포함 최대 수 분)이라
                                # 클릭 핸들러에서 직접 돌리면 세션 스레드가 막혀 웹소켓이 끊긴다(2026-06-16 사고).
                                # 백그라운드 스레드로 돌리고 UI는 즉시 반환 — 결과는 DB 저장되며 새로고침 시 표시.
                                import threading
                                from src.cni.generate_cni_fields import generate_tip_ondemand

                                def _bg_tip(nid=news_id):
                                    try:
                                        generate_tip_ondemand(nid)  # 내부에서 news.hansanguk_tip 저장
                                    except Exception:
                                        pass
                                threading.Thread(target=_bg_tip, daemon=True).start()
                                st.session_state["save_success_msg"] = (
                                    f"#{news_id} 팁 생성 시작 (백그라운드 ~1분, 새로고침 후 확인)")
                                st.rerun()
                            elif _btn_reset:
                                reset_to_selected(news_id)
                                st.session_state["save_success_msg"] = f"#{news_id} 초기화 (재처리 가능)"
                                st.rerun()
                            elif _btn_confirm:
                                # 폼 제출 = 3개 필드 일괄 저장(공개는 별도 버튼)
                                _save_fields_tr(news_id, _hl_val, _ko_val, _tip_val)
                                st.session_state["save_success_msg"] = f"#{news_id} 입력 확정 저장 완료"
                                st.rerun()

                        except Exception as _cni_err:
                            st.caption(f"CNI 로드 실패: {_cni_err}")

                    elif _ps == 'published':
                        # ── STATE: published ──
                        with st.expander("✅ 게시완료 — 수정/비공개", expanded=False):
                            try:
                                from src.cni.pipeline_service import unpublish_news
                                from src.cni.summary_store import update_translation, update_refined, get_summary as get_cni_summary
                                from src.cni.postprocess import refine_korean

                                _cni_row = get_cni_summary(news_id)
                                _existing_hl = row.get('card_headline') or ''
                                _existing_ko = (_cni_row.get('summary_ko') or _cni_row.get('refined_ko') or '') if _cni_row else ''
                                _existing_tip = row.get('hansanguk_tip') or ''

                                _sk_hl = f"cni_hl_{news_id}"
                                _sk_ko = f"cni_ko_{news_id}"
                                _sk_tip = f"cni_tip_{news_id}"
                                if _sk_hl not in st.session_state:
                                    st.session_state[_sk_hl] = _existing_hl
                                if _sk_ko not in st.session_state:
                                    st.session_state[_sk_ko] = _existing_ko
                                if _sk_tip not in st.session_state:
                                    st.session_state[_sk_tip] = _existing_tip

                                st.text_input("📰 헤드라인", key=_sk_hl, max_chars=72)
                                st.text_area("📝 한국어 요약", key=_sk_ko, height=120)
                                st.text_area("💡 한상국의 팁", key=_sk_tip, height=60)

                                _hl_val = st.session_state.get(_sk_hl, '')
                                _ko_val = st.session_state.get(_sk_ko, '')
                                _tip_val = st.session_state.get(_sk_tip, '')

                                _pc1, _pc2 = st.columns(2)
                                with _pc1:
                                    if st.button("💾 수정저장", key=f"cni_save_{news_id}", type="primary"):
                                        if _ko_val and _ko_val.strip():
                                            update_translation(news_id, _ko_val.strip())
                                        from src.database.models import get_connection as _gc
                                        _conn = _gc()
                                        if _hl_val and _hl_val.strip():
                                            _conn.execute("UPDATE news SET card_headline=? WHERE id=?", (_hl_val.strip()[:72], news_id))
                                        if _tip_val and _tip_val.strip():
                                            _conn.execute("UPDATE news SET hansanguk_tip=? WHERE id=?", (_tip_val.strip()[:500], news_id))
                                        else:
                                            _conn.execute("UPDATE news SET hansanguk_tip=NULL WHERE id=?", (news_id,))
                                        _conn.commit()
                                        _conn.close()
                                        # refine_korean(LLM, ~수초)은 백그라운드로 — UI 즉시 반환(연결 끊김 방지)
                                        if _ko_val and _ko_val.strip():
                                            import threading
                                            _ko_snap = _ko_val.strip()
                                            def _bg_refine2(_nid=news_id, _ko=_ko_snap):
                                                try:
                                                    update_refined(_nid, refine_korean(_ko))
                                                except Exception:
                                                    pass
                                            threading.Thread(target=_bg_refine2, daemon=True).start()
                                        st.session_state["save_success_msg"] = f"#{news_id} 수정 저장 완료 (즉시 반영)"
                                        st.rerun()
                                with _pc2:
                                    if st.button("🔒 비공개", key=f"cni_unpub_{news_id}"):
                                        unpublish_news(news_id)
                                        st.session_state["save_success_msg"] = f"#{news_id} 비공개 전환"
                                        st.rerun()
                            except Exception as _cni_err:
                                st.caption(f"CNI 로드 실패: {_cni_err}")

                    elif _ps == 'skipped':
                        # ── STATE: skipped → 복원만 가능 ──
                        if st.button("🔄 복원", key=f"cni_restore_{news_id}", help="selected로 복귀 (재처리 가능)"):
                            from src.cni.pipeline_service import restore_skipped
                            restore_skipped(news_id)
                            st.session_state["save_success_msg"] = f"#{news_id} 복원 완료 (재처리 가능)"
                            st.rerun()

                    elif not _ps:
                        # ── Legacy (pipeline_status=NULL) → 요약번역/불요 선택 ──
                        if not has_review:
                            _la, _lb = st.columns([0.7, 0.3])
                            with _la:
                                if st.button("📋 요약번역", key=f"translate_{news_id}", help="백그라운드 큐 등록 (~5분)"):
                                    from src.cni.pipeline_service import set_pipeline_selected
                                    from src.cni.process_queue import enqueue_news
                                    set_pipeline_selected([news_id])
                                    enqueue_news(news_id)
                                    st.session_state["save_success_msg"] = f"#{news_id} 처리 큐에 등록됨"
                                    st.rerun()
                            with _lb:
                                if st.button("🚫 불요", key=f"skip_{news_id}", help="요약·번역 불요 (폐기)"):
                                    from src.cni.pipeline_service import set_pipeline_selected
                                    set_pipeline_selected([news_id])
                                    if skip_news(news_id):
                                        st.session_state["save_success_msg"] = f"#{news_id} 불요 처리 완료"
                                        st.rerun()

                    # Promoted original-article link (outside any expander so
                    # reviewers can open the source in one click).
                    if row.get('original_url'):
                        st.markdown(f"🔗 **[원문 열람]({row['original_url']})**")

                    # Advanced editing — classification, tags, radar, expert
                    # commentary, AI review — hidden behind a nested expander.
                    # The primary review workflow (headline/summary/tip/확정)
                    # lives in the CNI state form above, not here.
                    with st.expander("🛠 고급 편집", expanded=False):
                        col_detail1, col_detail2 = st.columns([0.7, 0.3])

                        with col_detail1:
                            st.markdown("**📰 요약**")
                            st.write(row.get('summary', '요약 없음'))

                            if row.get('market_impact'):
                                st.markdown("**📈 시장 영향 분석**")
                                st.info(row['market_impact'])

                        with col_detail2:
                            st.markdown("**📋 분류 정보**")

                            # --- Editable classification section ---
                            edit_cls_key = f"edit_cls_{news_id}"
                            if not st.session_state.get(edit_cls_key):
                                # Display mode
                                cur_cat = row.get('industry_category') or ''
                                cur_imp = row.get('importance_score') or 0
                                st.write(f"- 산업: {get_korean_label(cur_cat)} (`{cur_cat}`)")
                                st.write(f"- 중요도: **{cur_imp:.2f}**")
                                st.write(f"- 유형: {row.get('content_type', '-')}")
                                st.write(f"- 감성: {row.get('sentiment', '-')}")
                                st.write(f"- 출처: {row.get('source', '-')}")
                                if st.button("✏️ 분류 수정", key=f"btn_edit_cls_{news_id}"):
                                    st.session_state[edit_cls_key] = True
                                    st.rerun()
                            else:
                                # Edit mode
                                from config.gics_taxonomy import GICS_SELECTED, GICS_EXTENSIONS
                                all_codes = {}
                                for code, data in GICS_SELECTED.items():
                                    all_codes[code] = f"{data[2]} ({code})"
                                for code, data in GICS_EXTENSIONS.items():
                                    all_codes[code] = f"{data[0]} ({code})"
                                code_list = list(all_codes.keys())
                                label_list = list(all_codes.values())

                                cur_cat = row.get('industry_category') or 'other'
                                cur_idx = code_list.index(cur_cat) if cur_cat in code_list else code_list.index('other')

                                new_cat = st.selectbox(
                                    "산업 분류",
                                    options=code_list,
                                    format_func=lambda x: all_codes[x],
                                    index=cur_idx,
                                    key=f"sel_cat_{news_id}"
                                )

                                cur_imp = row.get('importance_score') or 0.5
                                new_imp = st.slider(
                                    "중요도 점수",
                                    min_value=0.0, max_value=1.0,
                                    value=float(cur_imp), step=0.05,
                                    key=f"sl_imp_{news_id}"
                                )

                                content_types = ['policy', 'corporate', 'industry', 'market', 'opinion', 'news', 'data_release', 'research', 'macro']
                                cur_ct = row.get('content_type', 'news') or 'news'
                                ct_idx = content_types.index(cur_ct) if cur_ct in content_types else 0
                                new_ct = st.selectbox(
                                    "유형", content_types, index=ct_idx,
                                    key=f"sel_ct_{news_id}"
                                )

                                sentiments = ['positive', 'negative', 'neutral']
                                cur_sent = row.get('sentiment', 'neutral') or 'neutral'
                                sent_idx = sentiments.index(cur_sent) if cur_sent in sentiments else 2
                                new_sent = st.selectbox(
                                    "감성", sentiments, index=sent_idx,
                                    key=f"sel_sent_{news_id}"
                                )

                                col_save, col_cancel = st.columns(2)
                                with col_save:
                                    if st.button("💾 저장", key=f"btn_save_cls_{news_id}"):
                                        changed = {}
                                        if new_cat != cur_cat:
                                            changed['industry_category'] = new_cat
                                        if abs(new_imp - (row.get('importance_score') or 0.5)) > 0.01:
                                            changed['importance_score'] = new_imp
                                        if new_ct != cur_ct:
                                            changed['content_type'] = new_ct
                                        if new_sent != cur_sent:
                                            changed['sentiment'] = new_sent
                                        if changed:
                                            if update_news_classification(news_id, **changed):
                                                st.success("분류 수정 완료!")
                                                st.session_state.pop(edit_cls_key, None)
                                                st.rerun()
                                            else:
                                                st.error("저장 실패")
                                        else:
                                            st.info("변경 사항 없음")
                                with col_cancel:
                                    if st.button("취소", key=f"btn_cancel_cls_{news_id}"):
                                        st.session_state.pop(edit_cls_key, None)
                                        st.rerun()

                                st.write(f"- 출처: {row.get('source', '-')}")

                            if row.get('keywords'):
                                try:
                                    keywords = json.loads(row['keywords'])
                                    st.write(f"- 키워드: {', '.join(keywords)}")
                                except:
                                    st.write(f"- 키워드: {row['keywords']}")

                        # Score breakdown radar chart
                        if row.get('score_breakdown'):
                            try:
                                breakdown_data = json.loads(row['score_breakdown']) if isinstance(row['score_breakdown'], str) else row['score_breakdown']
                                if isinstance(breakdown_data, dict):
                                    scores = breakdown_data.get('breakdown', breakdown_data)

                                    # Parse boosters from score_explanation text
                                    boosters_parsed = []
                                    explanation = row.get('score_explanation', '') or ''
                                    booster_match = re.search(r'\[부스터:\s*(.+?)\]', explanation)
                                    if booster_match:
                                        for bm in re.finditer(r'(\w+)\(x([\d.]+)\)', booster_match.group(1)):
                                            boosters_parsed.append({"name": bm.group(1), "multiplier": float(bm.group(2))})

                                    st.markdown("---")
                                    col_radar, col_scores = st.columns([0.5, 0.5])

                                    with col_radar:
                                        st.markdown("**📊 8기준 점수 분석**")
                                        fig = create_score_radar_chart(scores)
                                        st.plotly_chart(fig, use_container_width=True, key=f"radar_{news_id}")

                                    with col_scores:
                                        st.markdown("**점수 상세**")
                                        for key, label in SCORE_AXIS_LABELS.items():
                                            score_val = scores.get(key, 0)
                                            if score_val >= 80:
                                                color = "🔴"
                                            elif score_val >= 60:
                                                color = "🟠"
                                            else:
                                                color = "⚪"
                                            st.write(f"{color} {label}: **{score_val}**")

                                        # Booster badges
                                        if boosters_parsed:
                                            st.markdown("**부스터 적용**")
                                            for b in boosters_parsed:
                                                badge_label = BOOSTER_LABELS.get(b['name'], f"🏷️ {b['name']}")
                                                st.markdown(f"{badge_label} (x{b['multiplier']})")
                            except (json.JSONDecodeError, TypeError):
                                pass

                        # Tags section
                        st.markdown("---")
                        st.markdown("**🏷️ 태그**")
                        current_tags = []
                        if row.get('tags'):
                            try:
                                current_tags = json.loads(row['tags'])
                            except:
                                pass

                        tag_input = st.text_input(
                            "태그 입력 (쉼표로 구분)",
                            value=", ".join(current_tags),
                            key=f"tags_{news_id}",
                            placeholder="예: 반도체, SMIC, 미중관계"
                        )

                        if st.button("태그 저장", key=f"save_tags_{news_id}"):
                            new_tags = [t.strip() for t in tag_input.split(",") if t.strip()]
                            if set_tags(news_id, new_tags):
                                st.success("태그가 저장되었습니다!")
                                st.rerun()

                        st.markdown("---")

                        # Expert comment section - Markdown based with Git
                        st.markdown("**📝 전문가 논평 (Markdown + Git)**")

                        md_review_manager = MarkdownReviewManager()

                        # Load existing review or DB comment
                        existing_md = md_review_manager.load_review(news_id)
                        existing_db_comment = row.get('expert_comment', '') or ''

                        # Determine what to show in editor
                        if existing_md:
                            # Extract just the comment part for editing
                            expert_comment = md_review_manager.extract_expert_comment(existing_md)
                            review_file_path = md_review_manager.get_review_path(news_id)
                            st.caption(f"📁 {review_file_path}")
                        else:
                            expert_comment = existing_db_comment

                        comment_key = f"comment_{news_id}"

                        expert_comment_input = st.text_area(
                            "Markdown 형식으로 논평을 입력하세요",
                            value=expert_comment,
                            height=150,
                            key=comment_key,
                            placeholder="## 핵심 분석\n- 포인트 1\n- 포인트 2\n\n## 투자 시사점\n..."
                        )

                        col_btn1, col_btn2, col_btn3 = st.columns([0.25, 0.25, 0.5])


                        with col_btn1:
                            if st.button("💾 저장 + Git", key=f"save_{news_id}"):
                                if expert_comment_input.strip():
                                    try:
                                        # DB 저장을 먼저 실행 (핵심)
                                        db_ok = save_expert_comment(news_id, expert_comment_input)
                                        if not db_ok:
                                            st.session_state["save_error_msg"] = f"DB 저장 실패 (뉴스 {news_id})"
                                            st.rerun()

                                        # Markdown 파일 + Git 커밋
                                        news_data = dict(row) if row is not None else None
                                        if news_data:
                                            result = md_review_manager.save_review(
                                                news_id=news_id,
                                                content=expert_comment_input,
                                                news=news_data,
                                                auto_commit=True
                                            )
                                        else:
                                            result = md_review_manager.save_expert_analysis(
                                                analysis_text=expert_comment_input,
                                                expert_name="중국 경제 전문가",
                                                title="외부 전문가 분석",
                                                auto_commit=True
                                            )

                                        news_title = (row.get('translated_title') or row.get('original_title') or '')[:30]
                                        git_msg = " + Git 커밋" if result.get("committed") else ""
                                        if has_original_content(news_id):
                                            st.session_state["save_success_msg"] = f"리뷰 저장 및 공개 완료{git_msg}: {news_title}... (리뷰 완료 탭에서 확인)"
                                        else:
                                            st.session_state["save_success_msg"] = f"리뷰 저장 완료 (⚠️ 원문 없음 - 비공개 상태){git_msg}: {news_title}..."
                                    except Exception as e:
                                        st.session_state["save_error_msg"] = f"저장 중 오류: {e}"

                                    st.rerun()
                                else:
                                    st.warning("논평을 입력해주세요.")

                        with col_btn2:
                            if st.button("📄 파일만 저장", key=f"save_file_{news_id}"):
                                if expert_comment_input.strip():
                                    try:
                                        db_ok = save_expert_comment(news_id, expert_comment_input)
                                        if not db_ok:
                                            st.session_state["save_error_msg"] = f"DB 저장 실패 (뉴스 {news_id})"
                                            st.rerun()

                                        news_data = dict(row) if row is not None else None
                                        if news_data:
                                            result = md_review_manager.save_review(
                                                news_id=news_id,
                                                content=expert_comment_input,
                                                news=news_data,
                                                auto_commit=False
                                            )
                                        else:
                                            result = md_review_manager.save_expert_analysis(
                                                analysis_text=expert_comment_input,
                                                expert_name="중국 경제 전문가",
                                                title="외부 전문가 분석",
                                                auto_commit=False
                                            )

                                        if has_original_content(news_id):
                                            st.session_state["save_success_msg"] = f"리뷰 저장 및 공개 완료: {result.get('file_path', '')} (리뷰 완료 탭에서 확인)"
                                        else:
                                            st.session_state["save_success_msg"] = f"리뷰 저장 완료 (⚠️ 원문 없음 - 비공개 상태): {result.get('file_path', '')}"
                                    except Exception as e:
                                        st.session_state["save_error_msg"] = f"저장 중 오류: {e}"

                                    st.rerun()
                                else:
                                    st.warning("논평을 입력해주세요.")

                        with col_btn3:
                            if st.button("🤖 AI 최종 리뷰 생성", key=f"ai_{news_id}"):
                                if not expert_comment_input.strip():
                                    st.warning("먼저 전문가 논평을 저장해주세요.")
                                else:
                                    with st.spinner("AI가 리뷰를 생성중입니다..."):
                                        result = generate_ai_final_review(news_id)
                                        if isinstance(result, dict):
                                            st.success("AI 최종 리뷰가 생성되었습니다!")
                                            st.rerun()
                                        else:
                                            st.error(result)

                            # Display AI final review if exists
                            if row.get('ai_final_review'):
                                st.markdown("---")
                                st.markdown("**🤖 AI 최종 리뷰**")
                                
                                if row.get('opinion_conflict'):
                                    st.warning("⚠️ AI와 전문가 의견에 차이가 있습니다.")
                                else:
                                    st.success("✅ AI와 전문가 의견이 대체로 일치합니다.")
                                
                                st.write(row['ai_final_review'])

                st.markdown("---")

    with tab2:
        st.subheader("⭐ 북마크된 뉴스")

        bookmarked_list = get_bookmarked_news(limit=50)

        if not bookmarked_list:
            st.info("북마크된 뉴스가 없습니다. 뉴스 카드의 ☆ 버튼을 클릭하여 북마크하세요.")
        else:
            for news in bookmarked_list:
                news_id = news['id']
                title = news.get('translated_title') or news.get('original_title', '제목 없음')
                importance = news.get('importance_score', 0)
                industry = get_korean_label(news.get('industry_category') or '')
                tags = []
                if news.get('tags'):
                    try:
                        tags = json.loads(news['tags'])
                    except:
                        pass

                with st.expander(f"⭐ {title}", expanded=False):
                    col1, col2 = st.columns([0.7, 0.3])

                    with col1:
                        st.markdown("**요약**")
                        st.write(news.get('summary', '요약 없음'))

                        if news.get('market_impact'):
                            st.markdown("**시장 영향**")
                            st.info(news['market_impact'])

                    with col2:
                        st.write(f"- 중요도: {importance:.2f}")
                        st.write(f"- 산업: {industry}")
                        st.write(f"- 출처: {news.get('source', '-')}")
                        if tags:
                            st.write(f"- 태그: {', '.join(tags)}")

                        if news.get('original_url'):
                            st.markdown(f"[원문 링크]({news['original_url']})")

                    if st.button("북마크 해제", key=f"unbookmark_{news_id}"):
                        toggle_bookmark(news_id)
                        st.rerun()

    with tab3:
        st.subheader("📂 Markdown 리뷰 파일")
        st.markdown("Git으로 버전 관리되는 Markdown 형식의 전문가 논평입니다.")

        md_manager = MarkdownReviewManager()
        md_reviews = md_manager.list_reviews(limit=30)

        if not md_reviews:
            st.info("아직 Markdown 리뷰가 없습니다. 'AI 추천 뉴스' 탭에서 논평을 작성하면 자동으로 생성됩니다.")
        else:
            # Group by date
            reviews_by_date = {}
            for review in md_reviews:
                date = review['date']
                if date not in reviews_by_date:
                    reviews_by_date[date] = []
                reviews_by_date[date].append(review)

            review_idx = 0
            for date, reviews in reviews_by_date.items():
                st.markdown(f"### 📅 {date}")

                for review in reviews:
                    review_idx += 1
                    with st.expander(f"📄 {review['title'][:60]}...", expanded=False):
                        # Show file path
                        st.caption(f"📁 `{review['file_path']}`")

                        # Load full content
                        full_content = md_manager.load_review(review['news_id'])
                        if full_content:
                            st.markdown(full_content)

                        # Edit button
                        col1, col2 = st.columns([0.3, 0.7])
                        with col1:
                            if st.button("✏️ 편집", key=f"edit_md_{review['news_id']}_{review_idx}"):
                                st.session_state[f"editing_{review['news_id']}"] = True
                                st.rerun()

                st.markdown("---")

    with tab4:
        st.subheader("📝 리뷰 완료 뉴스")

        conn = get_connection()
        reviewed_df = pd.read_sql_query("""
            SELECT n.id, n.translated_title, n.original_title, n.importance_score,
                   n.industry_category, n.source,
                   er.expert_comment, er.ai_final_review, er.opinion_conflict,
                   er.review_completed_at, er.publish_status
            FROM news n
            JOIN expert_reviews er ON n.id = er.news_id
            WHERE er.expert_comment IS NOT NULL
            ORDER BY er.review_completed_at DESC
            LIMIT 50
        """, conn)
        conn.close()

        # Display persistent feedback for tab4
        if st.session_state.get("tab4_success_msg"):
            st.success(st.session_state.pop("tab4_success_msg"))

        if reviewed_df.empty:
            st.info("아직 리뷰된 뉴스가 없습니다.")
        else:
            for idx, row in reviewed_df.iterrows():
                title = row['original_title'] or row['translated_title']
                conflict_icon = "⚠️" if row.get('opinion_conflict') else "✅"
                news_id = row['id']

                # Publish status icon
                pub_st = row.get('publish_status', '')
                if pub_st == 'published':
                    pub_icon = "📢"
                elif pub_st == 'draft':
                    pub_icon = "⏳"
                elif pub_st in ('discarded', 'rejected'):
                    pub_icon = "🗑"
                elif pub_st == 'approved':
                    pub_icon = "✅"
                else:
                    pub_icon = ""

                with st.expander(f"{conflict_icon} {pub_icon} {title}", expanded=False):
                    col1, col2 = st.columns(2)

                    with col1:
                        st.markdown("**전문가 코멘트**")
                        st.write(row.get('expert_comment', ''))

                    with col2:
                        st.markdown("**AI 최종 리뷰**")
                        st.write(row.get('ai_final_review', '아직 생성되지 않음'))

                    st.caption(f"리뷰 시간: {row.get('review_completed_at', '-')} | 게시상태: {pub_st or '-'}")

                    # 공개 취소 / 수정 버튼 (published 상태인 경우에만)
                    if pub_st == 'published':
                        edit_key = f"tab4_edit_mode_{news_id}"

                        if not st.session_state.get(edit_key):
                            col_discard, col_edit = st.columns([1, 1])
                            with col_discard:
                                if st.button("🗑 공개 취소 (폐기함으로)", key=f"tab4_discard_{news_id}"):
                                    if update_publish_status(news_id, 'discarded', '리뷰 완료 탭에서 공개 취소'):
                                        st.session_state["tab4_success_msg"] = f"#{news_id} 공개 취소 → 폐기함 이동"
                                        st.rerun()
                            with col_edit:
                                if st.button("✏️ 수정함", key=f"tab4_edit_btn_{news_id}"):
                                    st.session_state[edit_key] = True
                                    st.rerun()
                        else:
                            # 수정 폼
                            edited_title = st.text_input(
                                "제목 수정",
                                value=row.get('translated_title', '') or '',
                                key=f"tab4_edit_title_{news_id}",
                            )
                            edited_comment = st.text_area(
                                "리뷰 수정",
                                value=row.get('expert_comment', '') or '',
                                height=200,
                                key=f"tab4_edit_text_{news_id}",
                            )
                            col_save, col_cancel = st.columns([1, 1])
                            with col_save:
                                if st.button("💾 저장 및 게시", key=f"tab4_save_{news_id}"):
                                    if update_expert_comment(news_id, edited_comment, title=edited_title):
                                        st.session_state.pop(edit_key, None)
                                        st.session_state["tab4_success_msg"] = f"#{news_id} 수정 완료 → 게시 유지"
                                        st.rerun()
                            with col_cancel:
                                if st.button("취소", key=f"tab4_cancel_{news_id}"):
                                    st.session_state.pop(edit_key, None)
                                    st.rerun()

    with tab_approve:
        st.subheader("✅ 리뷰 승인 관리")

        # Status filter
        status_options = {
            'published': '📢 게시됨 (published)',
            'draft': '⏳ 승인대기 (draft)',
            'approved': '✅ 승인됨 (approved)',
            'discarded': '🗑 폐기함 (반려/공개취소)',
            'skipped': '🚫 비공개 뉴스 (skipped)',
        }
        selected_status = st.selectbox(
            "상태 필터",
            list(status_options.keys()),
            format_func=lambda x: status_options[x],
            key="approve_status_filter"
        )

        # Display persistent feedback
        if st.session_state.get("approve_success_msg"):
            st.success(st.session_state.pop("approve_success_msg"))
        if st.session_state.get("approve_error_msg"):
            st.error(st.session_state.pop("approve_error_msg"))

        # Skipped news: separate query (no expert_reviews join needed)
        if selected_status == 'skipped':
            conn_skip = get_connection()
            approve_df = pd.read_sql_query("""
                SELECT id, translated_title, original_title, importance_score,
                       industry_category, source, summary, published_at
                FROM news
                WHERE expert_review_status = 'skipped'
                ORDER BY updated_at DESC
                LIMIT 50
            """, conn_skip)
            conn_skip.close()
        else:
            approve_df = get_reviews_by_status(selected_status, limit=50)

        if approve_df.empty:
            st.info(f"'{status_options[selected_status]}' 상태의 리뷰가 없습니다.")
        else:
            st.caption(f"총 {len(approve_df)}건")

            # Bulk action area
            if selected_status == 'draft':
                st.markdown("#### 일괄 처리")
                bulk_col1, bulk_col2, bulk_col3 = st.columns([0.4, 0.3, 0.3])
                with bulk_col1:
                    bulk_ids = st.multiselect(
                        "일괄 처리할 뉴스 선택",
                        options=approve_df['id'].tolist(),
                        format_func=lambda x: f"#{x} - {(approve_df[approve_df['id']==x]['translated_title'].values[0] or approve_df[approve_df['id']==x]['original_title'].values[0] or '')[:40]}",
                        key="bulk_select"
                    )
                with bulk_col2:
                    if st.button("📢 일괄 승인+게시", key="bulk_publish", disabled=not bulk_ids):
                        count = bulk_update_publish_status(bulk_ids, 'published')
                        st.session_state["approve_success_msg"] = f"{count}건 게시 완료"
                        st.rerun()
                with bulk_col3:
                    if st.button("✅ 일괄 승인", key="bulk_approve", disabled=not bulk_ids):
                        count = bulk_update_publish_status(bulk_ids, 'approved')
                        st.session_state["approve_success_msg"] = f"{count}건 승인 완료"
                        st.rerun()

                # 일괄 처리 대상 중 한자 포함 제목 경고
                if bulk_ids:
                    chinese_bulk = [
                        bid for bid in bulk_ids
                        if not (approve_df[approve_df['id'] == bid]['card_headline'].values[0] or '')
                        and _has_chinese(approve_df[approve_df['id'] == bid]['translated_title'].values[0] or '')
                    ]
                    if chinese_bulk:
                        st.warning(f"⚠️ 선택 항목 중 {len(chinese_bulk)}건에 한자 포함 제목이 있습니다: {chinese_bulk}")

                st.markdown("---")

            # Skipped news: simple list with restore button
            if selected_status == 'skipped':
                for _, row in approve_df.iterrows():
                    news_id = row['id']
                    title = row['original_title'] or row['translated_title'] or '제목 없음'
                    importance = row.get('importance_score', 0) or 0
                    sk_col1, sk_col2 = st.columns([0.8, 0.2])
                    with sk_col1:
                        st.write(f"**#{news_id}** {title}")
                        st.caption(f"출처: {row.get('source', '-')} | 산업: {get_korean_label(row.get('industry_category') or '')} | 중요도: {importance:.2f}")
                    with sk_col2:
                        if st.button("↩ 선정으로 복원", key=f"restore_{news_id}"):
                            if restore_skipped_news(news_id):
                                st.session_state["approve_success_msg"] = f"#{news_id} 선정 목록으로 복원됨"
                                st.rerun()
                    st.markdown("---")

            # Individual review cards (for non-skipped statuses)
            if selected_status != 'skipped':
              for _, row in approve_df.iterrows():
                news_id = row['id']
                title = row['original_title'] or row['translated_title'] or '제목 없음'
                importance = row.get('importance_score', 0) or 0

                if importance >= 0.8:
                    imp_badge = "🔴"
                elif importance >= 0.6:
                    imp_badge = "🟠"
                elif importance >= 0.4:
                    imp_badge = "🟡"
                else:
                    imp_badge = "🟢"

                with st.expander(f"{imp_badge} #{news_id} {title}", expanded=False):
                    # News-review comparison view
                    col_news, col_review = st.columns(2)

                    with col_news:
                        st.markdown("**📰 뉴스 원문**")
                        st.markdown(f"**제목:** {title}")
                        st.caption(f"출처: {row.get('source', '-')} | 산업: {get_korean_label(row.get('industry_category') or '')} | 중요도: {importance:.2f}")

                        if row.get('summary'):
                            st.markdown("**요약:**")
                            st.write(row['summary'])

                        if row.get('original_content'):
                            content_preview = str(row['original_content'])[:500]
                            st.markdown("**원문 내용 (미리보기):**")
                            st.text(content_preview + ("..." if len(str(row['original_content'])) > 500 else ""))

                        if row.get('original_url'):
                            st.markdown(f"[원문 링크]({row['original_url']})")

                    with col_review:
                        st.markdown("**📝 전문가 리뷰**")
                        st.markdown(row.get('expert_comment', '리뷰 없음'))

                        if row.get('ai_final_review'):
                            st.markdown("---")
                            st.markdown("**🤖 AI 최종 리뷰**")
                            st.write(row['ai_final_review'])

                        if row.get('admin_note'):
                            st.markdown("---")
                            st.warning(f"**관리자 메모:** {row['admin_note']}")

                        st.caption(f"리뷰 완료: {row.get('review_completed_at', '-')} | 상태 변경: {row.get('publish_status_updated_at', '-')}")

                    # Action buttons
                    st.markdown("---")
                    action_cols = st.columns(5)

                    # 한자 포함 제목 경고 (card_headline 없고 translated_title에 한자 있을 때)
                    _card_hl = row.get('card_headline') or ''
                    _trans = row.get('translated_title') or ''
                    if not _card_hl and _has_chinese(_trans):
                        st.warning(
                            f"⚠️ 제목에 한자 포함: **{_trans[:60]}**  \n"
                            "게시 전 카드 헤드라인을 입력하거나 제목을 수정하세요."
                        )

                    if selected_status == 'draft':
                        with action_cols[0]:
                            if st.button("📢 승인+게시", key=f"ap_publish_{news_id}", type="primary"):
                                if update_publish_status(news_id, 'published'):
                                    st.session_state["approve_success_msg"] = f"#{news_id} 게시 완료"
                                    st.rerun()
                        with action_cols[1]:
                            if st.button("✅ 승인만", key=f"ap_approve_{news_id}"):
                                if update_publish_status(news_id, 'approved'):
                                    st.session_state["approve_success_msg"] = f"#{news_id} 승인 완료"
                                    st.rerun()
                        with action_cols[2]:
                            reject_note = st.text_input("반려 사유", key=f"ap_reject_note_{news_id}", placeholder="사유 입력")
                            if st.button("🗑 반려 (폐기함)", key=f"ap_reject_{news_id}"):
                                if update_publish_status(news_id, 'discarded', reject_note or '반려'):
                                    st.session_state["approve_success_msg"] = f"#{news_id} 반려 → 폐기함 이동"
                                    st.rerun()

                    elif selected_status == 'approved':
                        with action_cols[0]:
                            if st.button("📢 게시", key=f"ap_publish_{news_id}", type="primary"):
                                if update_publish_status(news_id, 'published'):
                                    st.session_state["approve_success_msg"] = f"#{news_id} 게시 완료"
                                    st.rerun()
                        with action_cols[1]:
                            if st.button("🗑 폐기함으로", key=f"ap_discard_{news_id}"):
                                if update_publish_status(news_id, 'discarded', '승인 후 폐기'):
                                    st.session_state["approve_success_msg"] = f"#{news_id} 폐기함 이동"
                                    st.rerun()

                    elif selected_status == 'published':
                        with action_cols[0]:
                            if st.button("🗑 공개 취소 (폐기함)", key=f"ap_unpublish_{news_id}"):
                                if update_publish_status(news_id, 'discarded', '공개 취소'):
                                    st.session_state["approve_success_msg"] = f"#{news_id} 공개 취소 → 폐기함 이동"
                                    st.rerun()

                    elif selected_status == 'discarded':
                        with action_cols[0]:
                            if st.button("↩ 복원 (draft)", key=f"ap_restore_{news_id}"):
                                if update_publish_status(news_id, 'draft'):
                                    st.session_state["approve_success_msg"] = f"#{news_id} 복원됨 (승인대기)"
                                    st.rerun()
                        with action_cols[1]:
                            if st.button("📢 복원+게시", key=f"ap_restore_pub_{news_id}", type="primary"):
                                if update_publish_status(news_id, 'published'):
                                    st.session_state["approve_success_msg"] = f"#{news_id} 복원 및 게시 완료"
                                    st.rerun()

    with tab5:
        st.subheader("🔔 알림")

        # Notification settings
        with st.expander("⚙️ 알림 설정", expanded=False):
            col_set1, col_set2 = st.columns(2)

            with col_set1:
                enabled = notification_manager.get_setting('notifications_enabled', 'true') == 'true'
                new_enabled = st.checkbox("알림 활성화", value=enabled)
                if new_enabled != enabled:
                    notification_manager.set_setting('notifications_enabled', 'true' if new_enabled else 'false')
                    st.rerun()

                threshold = float(notification_manager.get_setting('importance_threshold', '0.8'))
                new_threshold = st.slider("중요도 임계값", 0.0, 1.0, threshold, 0.1)
                if new_threshold != threshold:
                    notification_manager.set_setting('importance_threshold', str(new_threshold))

            with col_set2:
                notify_high = notification_manager.get_setting('notify_on_new_high_importance', 'true') == 'true'
                new_notify_high = st.checkbox("고중요도 뉴스 알림", value=notify_high)
                if new_notify_high != notify_high:
                    notification_manager.set_setting('notify_on_new_high_importance', 'true' if new_notify_high else 'false')

                notify_conflict = notification_manager.get_setting('notify_on_opinion_conflict', 'true') == 'true'
                new_notify_conflict = st.checkbox("의견 충돌 알림", value=notify_conflict)
                if new_notify_conflict != notify_conflict:
                    notification_manager.set_setting('notify_on_opinion_conflict', 'true' if new_notify_conflict else 'false')

        # Action buttons
        col_action1, col_action2 = st.columns([0.3, 0.7])
        with col_action1:
            if st.button("모두 읽음 표시"):
                notification_manager.mark_all_as_read()
                st.success("모든 알림을 읽음으로 표시했습니다.")
                st.rerun()

        st.markdown("---")

        # Notification list
        notifications = notification_manager.get_all_notifications(limit=50)

        if not notifications:
            st.info("알림이 없습니다.")
        else:
            for notif in notifications:
                notif_id = notif['id']
                is_read = notif.get('is_read', False)
                notif_type = notif.get('notification_type', '')
                title = notif.get('title', '알림')
                message = notif.get('message', '')
                created_at = notif.get('created_at', '')
                news_id = notif.get('news_id')

                # Type icon
                if notif_type == 'high_importance':
                    icon = "🔴"
                elif notif_type == 'opinion_conflict':
                    icon = "⚠️"
                else:
                    icon = "📢"

                # Style based on read status
                if is_read:
                    style = "opacity: 0.6;"
                else:
                    style = "font-weight: bold;"

                with st.container():
                    col1, col2, col3 = st.columns([0.7, 0.2, 0.1])

                    with col1:
                        st.markdown(f"<span style='{style}'>{icon} {title}</span>", unsafe_allow_html=True)
                        if message:
                            st.caption(message)

                    with col2:
                        st.caption(str(created_at)[:16] if created_at else '')

                    with col3:
                        if not is_read:
                            if st.button("✓", key=f"read_{notif_id}", help="읽음 표시"):
                                notification_manager.mark_as_read(notif_id)
                                st.rerun()

                    st.markdown("---")

    with tab6:
        st.subheader("📥 리포트 내보내기")
        st.markdown("분석된 뉴스를 Excel 또는 PDF 형식으로 내보냅니다.")

        col_export1, col_export2 = st.columns(2)

        with col_export1:
            st.markdown("### 📊 Excel 리포트")
            st.markdown("""
            Excel 리포트에 포함되는 내용:
            - **뉴스 요약**: 제목, 출처, 중요도, 요약, 시장영향
            - **전문가 리뷰**: 전문가 의견 및 AI 최종 리뷰
            - **통계**: 전체 통계 및 산업별 분석
            """)

            export_days_excel = st.slider("내보낼 기간 (일)", 1, 90, 7, key="excel_days")
            min_importance_excel = st.slider("최소 중요도", 0.0, 1.0, 0.0, 0.1, key="excel_importance")

            export_industry_excel = st.selectbox(
                "산업 필터",
                ["전체", "semiconductor", "ai", "new_energy", "bio", "aerospace", "quantum", "materials", "other"],
                key="excel_industry"
            )

            if st.button("📥 Excel 다운로드", key="download_excel"):
                with st.spinner("Excel 리포트 생성 중..."):
                    try:
                        exporter = ReportExporter()
                        df = exporter.get_report_data(
                            days=export_days_excel,
                            industry=export_industry_excel,
                            min_importance=min_importance_excel
                        )

                        if df.empty:
                            st.warning("내보낼 데이터가 없습니다.")
                        else:
                            excel_data = exporter.export_to_excel(df)
                            filename = f"china_news_report_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"

                            st.download_button(
                                label="💾 Excel 파일 저장",
                                data=excel_data,
                                file_name=filename,
                                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                            )
                            st.success(f"✅ {len(df)}개 뉴스가 포함된 리포트가 준비되었습니다.")
                    except Exception as e:
                        st.error(f"리포트 생성 실패: {e}")

        with col_export2:
            st.markdown("### 📄 PDF 리포트")
            st.markdown("""
            PDF 리포트에 포함되는 내용:
            - **요약 통계**: 전체 현황 및 핵심 지표
            - **주요 뉴스**: 중요도 상위 10개 뉴스 상세
            - **산업별 분석**: 산업 분류별 통계표
            """)

            export_days_pdf = st.slider("내보낼 기간 (일)", 1, 90, 7, key="pdf_days")
            min_importance_pdf = st.slider("최소 중요도", 0.0, 1.0, 0.0, 0.1, key="pdf_importance")

            export_industry_pdf = st.selectbox(
                "산업 필터",
                ["전체", "semiconductor", "ai", "new_energy", "bio", "aerospace", "quantum", "materials", "other"],
                key="pdf_industry"
            )

            if st.button("📥 PDF 다운로드", key="download_pdf"):
                with st.spinner("PDF 리포트 생성 중..."):
                    try:
                        exporter = ReportExporter()
                        df = exporter.get_report_data(
                            days=export_days_pdf,
                            industry=export_industry_pdf,
                            min_importance=min_importance_pdf
                        )

                        if df.empty:
                            st.warning("내보낼 데이터가 없습니다.")
                        else:
                            pdf_data = exporter.export_to_pdf(df)
                            filename = f"china_news_report_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf"

                            st.download_button(
                                label="💾 PDF 파일 저장",
                                data=pdf_data,
                                file_name=filename,
                                mime="application/pdf"
                            )
                            st.success(f"✅ {len(df)}개 뉴스가 포함된 리포트가 준비되었습니다.")
                    except Exception as e:
                        st.error(f"리포트 생성 실패: {e}")

        st.markdown("---")
        st.markdown("### 📈 빠른 리포트")

        col_quick1, col_quick2, col_quick3 = st.columns(3)

        with col_quick1:
            if st.button("📊 오늘 뉴스 (Excel)", key="quick_today"):
                with st.spinner("생성 중..."):
                    try:
                        exporter = ReportExporter()
                        df = exporter.get_report_data(days=1)
                        if not df.empty:
                            excel_data = exporter.export_to_excel(df)
                            st.download_button(
                                "💾 저장",
                                excel_data,
                                f"today_news_{datetime.now().strftime('%Y%m%d')}.xlsx",
                                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                key="quick_today_dl"
                            )
                        else:
                            st.info("오늘 수집된 뉴스가 없습니다.")
                    except Exception as e:
                        st.error(str(e))

        with col_quick2:
            if st.button("🔥 고중요도 (Excel)", key="quick_high"):
                with st.spinner("생성 중..."):
                    try:
                        exporter = ReportExporter()
                        df = exporter.get_report_data(days=30, min_importance=0.7)
                        if not df.empty:
                            excel_data = exporter.export_to_excel(df)
                            st.download_button(
                                "💾 저장",
                                excel_data,
                                f"high_importance_{datetime.now().strftime('%Y%m%d')}.xlsx",
                                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                key="quick_high_dl"
                            )
                        else:
                            st.info("고중요도 뉴스가 없습니다.")
                    except Exception as e:
                        st.error(str(e))

        with col_quick3:
            if st.button("📝 리뷰 완료 (Excel)", key="quick_reviewed"):
                with st.spinner("생성 중..."):
                    try:
                        exporter = ReportExporter()
                        df = exporter.get_report_data(days=90, include_reviews=True)
                        df = df[df['expert_comment'].notna()]
                        if not df.empty:
                            excel_data = exporter.export_to_excel(df)
                            st.download_button(
                                "💾 저장",
                                excel_data,
                                f"reviewed_news_{datetime.now().strftime('%Y%m%d')}.xlsx",
                                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                key="quick_reviewed_dl"
                            )
                        else:
                            st.info("리뷰 완료된 뉴스가 없습니다.")
                    except Exception as e:
                        st.error(str(e))

    with tab7:
        st.subheader("📊 카테고리별 뉴스 분석")

        # Fetch category data
        conn_cat = get_connection()
        cat_df = pd.read_sql_query("""
            SELECT
                COALESCE(industry_category, 'other') as category,
                COUNT(*) as count,
                ROUND(AVG(importance_score), 3) as avg_importance
            FROM news
            WHERE collected_at >= datetime('now', ? || ' days')
            GROUP BY COALESCE(industry_category, 'other')
            ORDER BY count DESC
        """, conn_cat, params=[f"-{days_range}"])
        conn_cat.close()

        if cat_df.empty:
            st.info("해당 기간에 뉴스가 없습니다.")
        else:
            cat_df['label'] = cat_df['category'].map(get_korean_label)

            col_donut, col_bar = st.columns(2)

            with col_donut:
                st.markdown("**뉴스 분포 (도넛 차트)**")
                fig_donut = go.Figure(data=[go.Pie(
                    labels=cat_df['label'],
                    values=cat_df['count'],
                    hole=0.45,
                    textinfo='label+percent',
                    textposition='outside',
                )])
                fig_donut.update_layout(
                    showlegend=False,
                    margin=dict(l=20, r=20, t=30, b=20),
                    height=380,
                )
                st.plotly_chart(fig_donut, use_container_width=True, key="cat_donut")

            with col_bar:
                st.markdown("**카테고리별 평균 중요도**")
                cat_sorted = cat_df.sort_values('avg_importance', ascending=True)
                colors = [
                    "rgba(220,53,69,0.8)" if v >= 0.8
                    else "rgba(255,152,0,0.8)" if v >= 0.6
                    else "rgba(158,158,158,0.6)"
                    for v in cat_sorted['avg_importance']
                ]
                fig_bar = go.Figure(data=[go.Bar(
                    x=cat_sorted['avg_importance'],
                    y=cat_sorted['label'],
                    orientation='h',
                    marker_color=colors,
                    text=[f"{v:.2f}" for v in cat_sorted['avg_importance']],
                    textposition='outside',
                )])
                fig_bar.update_layout(
                    xaxis=dict(range=[0, 1], title="평균 중요도"),
                    margin=dict(l=20, r=40, t=30, b=20),
                    height=380,
                )
                st.plotly_chart(fig_bar, use_container_width=True, key="cat_bar")

            # Interactive category filter
            st.markdown("---")
            st.markdown("**카테고리별 뉴스 보기**")
            selected_cat = st.selectbox(
                "카테고리 선택",
                cat_df['category'].tolist(),
                format_func=get_korean_label,
                key="cat_filter_select",
            )

            conn_filt = get_connection()
            filt_df = pd.read_sql_query("""
                SELECT translated_title, original_title, importance_score,
                       source, published_at
                FROM news
                WHERE COALESCE(industry_category, 'other') = ?
                  AND collected_at >= datetime('now', ? || ' days')
                ORDER BY importance_score DESC
                LIMIT 20
            """, conn_filt, params=[selected_cat, f"-{days_range}"])
            conn_filt.close()

            if filt_df.empty:
                st.info("해당 카테고리 뉴스가 없습니다.")
            else:
                for _, frow in filt_df.iterrows():
                    ftitle = frow['translated_title'] or frow['original_title']
                    fscore = frow['importance_score'] or 0
                    if fscore >= 0.8:
                        fc = "🔴"
                    elif fscore >= 0.6:
                        fc = "🟠"
                    else:
                        fc = "⚪"
                    st.write(f"{fc} **{ftitle}** ({fscore:.2f}) — {frow['source']}")

    with tab8:
        st.subheader("📡 소스별 뉴스 분포")

        # Source label map
        source_labels = {
            'people': '인민일보', 'ce': '경제일보',
            '36kr': '36Kr', 'cls': '재련사',
            'jiemian': '제면', 'yicai': '이차이징', 'sina_finance': '시나재경',
            '21jingji': '21세기경제', 'xinhua_finance': '신화재경',
            'beijing_gov': '베이징시', 'shanghai_gov': '상하이시',
            'shenzhen_gov': '선전시', 'bbtnews': 'BBT뉴스',
            'stdaily': '과기일보', 'cnstock': '중국증권보', 'sznews': '선전뉴스',
            'gov_cn': '중앙정부', 'ndrc': '발개위', 'mof': '재정부',
            'mofcom': '상무부', 'pboc': '인민은행',
        }

        # --- Bar chart: today's selected news by source ---
        conn_src = get_connection()
        src_df = pd.read_sql_query("""
            SELECT source, COUNT(*) as count
            FROM news
            WHERE collected_at >= datetime('now', ? || ' days')
            GROUP BY source
            ORDER BY count DESC
        """, conn_src, params=[f"-{days_range}"])

        # --- 7-day trend data ---
        trend_df = pd.read_sql_query("""
            SELECT source,
                   DATE(collected_at) as date,
                   COUNT(*) as count
            FROM news
            WHERE collected_at >= datetime('now', '-7 days')
            GROUP BY source, DATE(collected_at)
            ORDER BY date
        """, conn_src)
        conn_src.close()

        if src_df.empty:
            st.info("해당 기간에 뉴스가 없습니다.")
        else:
            src_df['label'] = src_df['source'].map(
                lambda s: source_labels.get(s, s)
            )
            src_df['priority'] = src_df['source'].map(
                lambda s: SOURCE_PRIORITY.get(s, 5)
            )

            # Priority-based color scale
            def priority_color(p):
                if p >= 10:
                    return "rgba(220, 53, 69, 0.85)"
                elif p >= 8:
                    return "rgba(255, 152, 0, 0.85)"
                elif p >= 6:
                    return "rgba(66, 133, 244, 0.85)"
                else:
                    return "rgba(158, 158, 158, 0.7)"

            src_sorted = src_df.sort_values('count', ascending=True)
            bar_colors = [priority_color(p) for p in src_sorted['priority']]

            st.markdown("**매체별 뉴스 건수**")
            fig_src = go.Figure(data=[go.Bar(
                x=src_sorted['count'],
                y=src_sorted['label'],
                orientation='h',
                marker_color=bar_colors,
                text=src_sorted['count'],
                textposition='outside',
            )])
            fig_src.update_layout(
                xaxis_title="건수",
                margin=dict(l=20, r=40, t=10, b=20),
                height=max(300, len(src_df) * 28),
            )
            st.plotly_chart(fig_src, use_container_width=True, key="src_bar")

            st.caption("색상: 🔴 우선순위 10+ | 🟠 8-9 | 🔵 6-7 | ⚪ 5 이하")

        # --- 7-day trend line chart ---
        if not trend_df.empty:
            st.markdown("---")
            st.markdown("**최근 7일 매체별 선정 빈도**")

            # Top 8 sources by total count for readability
            top_sources = trend_df.groupby('source')['count'].sum().nlargest(8).index.tolist()
            trend_top = trend_df[trend_df['source'].isin(top_sources)]

            fig_trend = go.Figure()
            for src in top_sources:
                sdata = trend_top[trend_top['source'] == src].sort_values('date')
                label = source_labels.get(src, src)
                fig_trend.add_trace(go.Scatter(
                    x=sdata['date'],
                    y=sdata['count'],
                    mode='lines+markers',
                    name=label,
                    line=dict(width=2),
                    marker=dict(size=5),
                ))
            fig_trend.update_layout(
                xaxis_title="날짜",
                yaxis_title="건수",
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                margin=dict(l=20, r=20, t=40, b=20),
                height=350,
            )
            st.plotly_chart(fig_trend, use_container_width=True, key="src_trend")


    # ── KG Tab ──
    with tab_kg:
        st.subheader("🧠 Knowledge Graph")

        try:
            from src.kg.query import get_graph_stats, search_entity, get_entity_relations, get_entity_events
            from src.kg.signal_engine import generate_signals, aggregate_signals
            from src.kg.trend_scanner import scan_trends

            # KG Stats
            kg_stats = get_graph_stats()
            col_a, col_b, col_c, col_d = st.columns(4)
            col_a.metric("엔티티", kg_stats.get("active_entities", 0))
            col_b.metric("이벤트", kg_stats.get("events", 0))
            col_c.metric("관계", kg_stats.get("relations", 0))
            col_d.metric("시그널", len(generate_signals()))

            st.markdown("---")

            # Entity Search
            kg_search = st.text_input("🔍 엔티티 검색", placeholder="BYD, 국무원, 반도체...", key="kg_search")
            if kg_search:
                found = search_entity(kg_search, limit=10)
                if found:
                    for ent in found:
                        with st.expander(f"{ent['canonical_name']} ({ent['entity_type']}) — mentions: {ent['mention_count']}"):
                            st.write(f"**중국어:** {ent.get('canonical_name_zh', '-')}")
                            st.write(f"**설명:** {ent.get('description', '-')}")

                            # Relations
                            rels = get_entity_relations(ent['kg_entity_id'])
                            if rels['outgoing'] or rels['incoming']:
                                st.write("**관계:**")
                                for r in rels['outgoing'][:5]:
                                    st.write(f"  → [{r['relation_type']}] {r['target_name']} ({r['target_type']})")
                                for r in rels['incoming'][:5]:
                                    st.write(f"  ← [{r['relation_type']}] {r['source_name']} ({r['source_type']})")

                            # Events
                            evts = get_entity_events(ent['kg_entity_id'])
                            if evts:
                                st.write(f"**이벤트 ({len(evts)}건):**")
                                for ev in evts[:5]:
                                    st.write(f"  • [{ev['magnitude']}] {ev['headline']}")

                            # Signals
                            sigs = generate_signals(ent['canonical_name'])
                            agg = aggregate_signals(sigs)
                            ent_agg = agg.get(ent['canonical_name'])
                            if ent_agg:
                                direction_color = {"POSITIVE": "🟢", "NEGATIVE": "🔴", "MIXED": "🟡", "NEUTRAL": "⚪"}
                                st.write(f"**시그널:** {direction_color.get(ent_agg['net_direction'], '⚪')} "
                                         f"{ent_agg['net_direction']} "
                                         f"(+{ent_agg['positive']} -{ent_agg['negative']} ={ent_agg['neutral']}, "
                                         f"conf: {ent_agg['avg_confidence']:.2f})")
                else:
                    st.info(f"'{kg_search}'에 해당하는 엔티티가 없습니다.")

            st.markdown("---")

            # Top Entities
            st.write("**핵심 엔티티 (연결 중심성 Top 10)**")
            top_connected = kg_stats.get("top_connected", [])
            if top_connected:
                top_df = pd.DataFrame(top_connected)
                top_df.columns = ["이름", "유형", "언급수", "연결수"]
                st.dataframe(top_df, use_container_width=True, hide_index=True)

            # Trend Summary
            st.markdown("---")
            st.write("**산업 트렌드 요약**")
            trends = scan_trends()
            tcol1, tcol2 = st.columns(2)
            with tcol1:
                st.write("🟢 **성장 산업**")
                for t in trends.get("growing", [])[:5]:
                    st.write(f"  • {t['industry']} (+{t['positive']} signals)")
            with tcol2:
                st.write("🔴 **리스크 산업**")
                for t in trends.get("at_risk", [])[:5]:
                    st.write(f"  • {t['industry']} (-{t['negative']} signals)")

        except Exception as e:
            st.error(f"KG 모듈 로드 실패: {e}")
            st.info("KG 테이블이 아직 생성되지 않았거나, 데이터가 없을 수 있습니다.")

    # ── CNI Translation Tab ──
    with tab_cni:
        st.subheader("📝 CNI 번역 관리")

        try:
            from src.cni.summary_store import (
                get_pending_translations, get_cni_stats, init_cni_tables,
            )
            from src.cni.translator import save_manual_translation
            from src.cni.cni_pipeline import run_post_translation

            init_cni_tables()
            cni_stats = get_cni_stats()

            # Stats
            sc1, sc2, sc3, sc4 = st.columns(4)
            sc1.metric("전체", cni_stats.get("total", 0))
            sc2.metric("번역 대기", cni_stats.get("pending", 0))
            sc3.metric("번역 완료", cni_stats.get("translated", 0))
            sc4.metric("후처리 완료", cni_stats.get("refined", 0))

            st.markdown("---")

            # Pending translations
            pending = get_pending_translations(10)
            if pending:
                st.write(f"**번역 대기 ({len(pending)}건)** — 중문 요약을 Papago로 번역 후 붙여넣기")

                for item in pending:
                    nid = item["news_id"]
                    with st.expander(f"#{nid} — {item.get('original_title', '')[:50]}"):
                        st.write(f"**출처:** {item.get('source', '-')}")
                        st.write(f"**중문 요약:**")
                        st.code(item.get("summary_zh", ""), language=None)

                        # Copy helper
                        st.caption("위 중문 요약을 복사 → Papago에서 번역 → 아래에 붙여넣기")

                        ko_input = st.text_area(
                            "한국어 번역 입력",
                            key=f"cni_ko_{nid}",
                            height=120,
                            placeholder="Papago 번역 결과를 여기에 붙여넣으세요..."
                        )

                        col_save, col_refine = st.columns(2)
                        with col_save:
                            if st.button("💾 번역 저장", key=f"cni_save_{nid}"):
                                if ko_input and ko_input.strip():
                                    result = save_manual_translation(nid, ko_input)
                                    if result.get("status") == "ok":
                                        st.success(f"#{nid} 번역 저장 완료")
                                        st.rerun()
                                    else:
                                        st.error(result.get("error", "저장 실패"))
                                else:
                                    st.warning("번역을 입력해주세요")

                        with col_refine:
                            if st.button("✨ 저장+후처리", key=f"cni_refine_{nid}"):
                                if ko_input and ko_input.strip():
                                    save_manual_translation(nid, ko_input)
                                    result = run_post_translation(nid)
                                    if result.get("status") == "ok":
                                        st.success(f"#{nid} 후처리 완료")
                                        st.rerun()
                                    else:
                                        st.error(result.get("error", "후처리 실패"))
                                else:
                                    st.warning("번역을 입력해주세요")
            else:
                st.info("번역 대기 항목이 없습니다. CNI 파이프라인을 먼저 실행하세요.")

        except Exception as e:
            st.error(f"CNI 모듈 로드 실패: {e}")
            st.info("CNI 테이블이 아직 생성되지 않았을 수 있습니다.")

    # ── Published News Tab (공개함) ──
    with tab_published:
        st.subheader("📢 공개된 뉴스 관리")

        if st.session_state.get("pub_success_msg"):
            st.success(st.session_state.pop("pub_success_msg"))

        try:
            from src.cni.pipeline_service import unpublish_news as _unpub_cni
            from src.database.models import get_connection as _pub_gc

            _pub_conn = _pub_gc()

            # CNI published — 최근 30건만 렌더 (전체 렌더 시 수백 건×폼위젯으로 rerun이 수십 초 걸림)
            _cni_pub = _pub_conn.execute("""
                SELECT n.id, n.card_headline, n.hansanguk_tip, n.pipeline_status, n.updated_at,
                       cs.summary_ko
                FROM news n
                LEFT JOIN cni_summaries cs ON n.id = cs.news_id
                WHERE n.pipeline_status = 'published'
                ORDER BY n.updated_at DESC
                LIMIT 30
            """).fetchall()
            _cni_pub_total = _pub_conn.execute(
                "SELECT COUNT(*) FROM news WHERE pipeline_status = 'published'"
            ).fetchone()[0]

            # Legacy published
            _leg_pub = _pub_conn.execute("""
                SELECT n.id, n.card_headline, n.translated_title,
                       er.publish_status, er.publish_status_updated_at
                FROM news n
                JOIN expert_reviews er ON n.id = er.news_id
                WHERE er.publish_status = 'published'
                ORDER BY er.publish_status_updated_at DESC
                LIMIT 30
            """).fetchall()

            _pub_conn.close()

            total_pub = _cni_pub_total + len(_leg_pub)
            st.metric("공개 뉴스 총", total_pub, f"CNI {_cni_pub_total} + Legacy {len(_leg_pub)}")
            st.markdown("---")

            # CNI Published (수정 가능)
            if _cni_pub:
                _more = f" — 최근 30건 표시 (총 {_cni_pub_total}건)" if _cni_pub_total > len(_cni_pub) else ""
                st.write(f"**📦 자동번역 공개 ({len(_cni_pub)}건){_more}**")
                for r in _cni_pub:
                    _nid = r['id']
                    _hl = r['card_headline'] or '(제목 없음)'
                    _ko = r['summary_ko'] or ''

                    with st.expander(f"#{_nid} — {_hl[:40]}"):
                        _edit_hl = st.text_input("헤드라인 수정", value=_hl, key=f"pub_hl_{_nid}")
                        _edit_ko = st.text_area("번역 수정", value=_ko, key=f"pub_ko_{_nid}", height=120)
                        _existing_tip = r['hansanguk_tip'] or ''
                        _edit_tip = st.text_area("💡 한상국의 팁", value=_existing_tip, key=f"pub_tip_{_nid}", height=80)

                        _pc1, _pc2, _pc3 = st.columns(3)
                        with _pc1:
                            if st.button("💾 수정 저장", key=f"pub_save_{_nid}"):
                                from src.cni.summary_store import update_translation as _pub_ut, update_refined as _pub_ur
                                _pub_ut(_nid, _edit_ko.strip())
                                _pub_ur(_nid, _edit_ko.strip())
                                _puc = _pub_gc()
                                _puc.execute("UPDATE news SET card_headline=? WHERE id=?", (_edit_hl[:72], _nid))
                                if _edit_tip and _edit_tip.strip():
                                    _puc.execute("UPDATE news SET hansanguk_tip=? WHERE id=?", (_edit_tip.strip()[:500], _nid))
                                else:
                                    _puc.execute("UPDATE news SET hansanguk_tip=NULL WHERE id=?", (_nid,))
                                _puc.commit()
                                _puc.close()
                                st.session_state["pub_success_msg"] = f"#{_nid} 수정 저장 (즉시 반영)"
                                st.rerun()
                        with _pc2:
                            if st.button("🔒 비공개", key=f"unpub_cni_{_nid}"):
                                _unpub_cni(_nid)
                                st.session_state["pub_success_msg"] = f"#{_nid} 비공개 전환"
                                st.rerun()

            st.markdown("---")

            # Legacy Published
            if _leg_pub:
                st.write(f"**📋 기존 리뷰 공개 (최근 {len(_leg_pub)}건)**")
                for r in _leg_pub:
                    _nid = r['id']
                    _hl = r['card_headline'] or r['translated_title'] or '(제목 없음)'
                    _col1, _col2 = st.columns([0.8, 0.2])
                    with _col1:
                        st.write(f"**#{_nid}** — {_hl[:50]}")
                    with _col2:
                        if st.button("🔒 비공개", key=f"unpub_leg_{_nid}"):
                            _uc = _pub_gc()
                            _uc.execute("""
                                UPDATE expert_reviews
                                SET publish_status = 'draft',
                                    publish_status_updated_at = CURRENT_TIMESTAMP
                                WHERE news_id = ?
                            """, (_nid,))
                            _uc.commit()
                            _uc.close()
                            st.session_state["pub_success_msg"] = f"#{_nid} 비공개 전환 완료"
                            st.rerun()

        except Exception as e:
            st.error(f"공개함 로드 실패: {e}")

    # ── 비공개함 Tab ──
    with tab_hidden:
        st.subheader("🔒 비공개 뉴스")

        if st.session_state.get("hidden_success_msg"):
            st.success(st.session_state.pop("hidden_success_msg"))

        try:
            from src.cni.pipeline_service import set_pipeline_status as _hidden_sps, publish_news as _hidden_pub
            from src.cni.summary_store import update_translation as _hidden_ut, update_refined as _hidden_ur
            from src.database.models import get_connection as _hidden_gc

            _hconn = _hidden_gc()

            # CNI unpublished + skipped — 최근 30건만 렌더 (전체 렌더 시 rerun 지연)
            _hidden_cni = _hconn.execute("""
                SELECT n.id, n.card_headline, n.pipeline_status, n.title_zh, n.summary_zh,
                       cs.summary_ko, cs.refined_ko
                FROM news n
                LEFT JOIN cni_summaries cs ON n.id = cs.news_id
                WHERE n.pipeline_status IN ('unpublished', 'skipped')
                ORDER BY n.updated_at DESC
                LIMIT 30
            """).fetchall()
            _hidden_cni_total = _hconn.execute(
                "SELECT COUNT(*) FROM news WHERE pipeline_status IN ('unpublished', 'skipped')"
            ).fetchone()[0]

            # Legacy draft/discarded
            _hidden_leg = _hconn.execute("""
                SELECT n.id, n.card_headline, n.translated_title,
                       er.publish_status, er.expert_comment
                FROM news n
                JOIN expert_reviews er ON n.id = er.news_id
                WHERE er.publish_status IN ('draft', 'discarded')
                ORDER BY er.publish_status_updated_at DESC
                LIMIT 20
            """).fetchall()

            _hconn.close()

            st.metric("비공개 뉴스", _hidden_cni_total + len(_hidden_leg))
            st.markdown("---")

            # CNI 비공개
            if _hidden_cni:
                _hmore = f" — 최근 30건 표시 (총 {_hidden_cni_total}건)" if _hidden_cni_total > len(_hidden_cni) else ""
                st.write(f"**📦 자동번역 비공개 ({len(_hidden_cni)}건){_hmore}**")
                for r in _hidden_cni:
                    _nid = r['id']
                    _hl = r['card_headline'] or r['title_zh'] or '(제목 없음)'
                    _ko = r['summary_ko'] or r['refined_ko'] or ''
                    _status = r['pipeline_status']

                    with st.expander(f"#{_nid} [{_status}] — {_hl[:40]}"):
                        # 수정 가능한 헤드라인
                        _new_hl = st.text_input("헤드라인 수정", value=_hl, key=f"hid_hl_{_nid}")
                        # 수정 가능한 번역
                        _new_ko = st.text_area("번역 수정", value=_ko, key=f"hid_ko_{_nid}", height=120)

                        _hc1, _hc2, _hc3 = st.columns(3)
                        with _hc1:
                            if st.button("📢 재공개", key=f"hid_repub_{_nid}", type="primary"):
                                if _new_ko and _new_ko.strip():
                                    _hidden_ut(_nid, _new_ko.strip())
                                    _hidden_ur(_nid, _new_ko.strip())
                                    _uc = _hidden_gc()
                                    _uc.execute("UPDATE news SET card_headline=? WHERE id=?", (_new_hl[:72], _nid))
                                    _uc.commit()
                                    _uc.close()
                                    _hidden_sps(_nid, "translated")
                                    _hidden_pub(_nid)
                                    st.session_state["hidden_success_msg"] = f"#{_nid} 재공개 완료"
                                    st.rerun()
                                else:
                                    st.warning("번역을 입력하세요")
                        with _hc2:
                            if st.button("💾 수정 저장", key=f"hid_save_{_nid}"):
                                _hidden_ut(_nid, _new_ko.strip() if _new_ko else '')
                                _uc = _hidden_gc()
                                _uc.execute("UPDATE news SET card_headline=? WHERE id=?", (_new_hl[:72], _nid))
                                _uc.commit()
                                _uc.close()
                                st.session_state["hidden_success_msg"] = f"#{_nid} 수정 저장 완료"
                                st.rerun()
                        with _hc3:
                            if st.button("🗑 삭제", key=f"hid_del_{_nid}"):
                                _hidden_sps(_nid, "skipped")
                                st.session_state["hidden_success_msg"] = f"#{_nid} 삭제 처리"
                                st.rerun()

            # Legacy 비공개
            if _hidden_leg:
                st.markdown("---")
                st.write(f"**📋 기존 리뷰 비공개 ({len(_hidden_leg)}건)**")
                for r in _hidden_leg:
                    _nid = r['id']
                    _hl = r['card_headline'] or r['translated_title'] or '(제목 없음)'
                    st.write(f"  #{_nid} [{r['publish_status']}] — {_hl[:50]}")

            if not _hidden_cni and not _hidden_leg:
                st.info("비공개 뉴스가 없습니다.")

        except Exception as e:
            st.error(f"비공개함 로드 실패: {e}")



if __name__ == "__main__":
    main()

"""Expert Dashboard - Streamlit UI for news review and commentary."""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime, timedelta
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.database.models import get_connection
from src.utils.report_exporter import ReportExporter
from src.utils.notifications import (
    NotificationManager, toggle_bookmark, set_tags, get_tags,
    get_all_tags, get_bookmarked_news
)
from src.utils.markdown_review import MarkdownReviewManager
from config.settings import ANTHROPIC_API_KEY, CLAUDE_MODEL

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
                 queued_only: bool = False) -> pd.DataFrame:
    """Get top news sorted by importance score."""
    conn = get_connection()

    query = """
        SELECT n.*,
               er.expert_comment,
               er.ai_final_review,
               er.opinion_conflict,
               er.review_completed_at
        FROM news n
        LEFT JOIN expert_reviews er ON n.id = er.news_id
        WHERE n.analyzed_at IS NOT NULL
    """
    params = []

    if queued_only:
        query += " AND n.expert_review_status = 'queued_today'"
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
    """Save expert comment and trigger AI final review."""
    conn = get_connection()
    cursor = conn.cursor()

    try:
        # Check if review exists
        cursor.execute("SELECT id FROM expert_reviews WHERE news_id = ?", (news_id,))
        existing = cursor.fetchone()

        now = datetime.now()

        if existing:
            cursor.execute("""
                UPDATE expert_reviews SET
                    expert_comment = ?,
                    review_completed_at = ?,
                    updated_at = ?
                WHERE news_id = ?
            """, (comment, now, now, news_id))
        else:
            cursor.execute("""
                INSERT INTO expert_reviews
                (news_id, expert_comment, review_started_at, review_completed_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (news_id, comment, now, now, now, now))

        conn.commit()
        return True
    except Exception as e:
        st.error(f"저장 실패: {e}")
        return False
    finally:
        conn.close()


def generate_ai_final_review(news_id: int) -> str:
    """Generate AI final review comparing expert and AI opinions."""
    news = get_news_detail(news_id)
    if not news:
        return "뉴스를 찾을 수 없습니다."

    if not news.get('expert_comment'):
        return "전문가 코멘트가 없습니다."

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

        prompt = f"""다음 뉴스에 대한 AI 분석과 전문가 의견을 비교하여 최종 리뷰를 작성해주세요.

## 뉴스 정보
- 제목: {news.get('translated_title', news.get('original_title', ''))}
- 요약: {news.get('summary', '')}
- AI 중요도 점수: {news.get('importance_score', 0):.2f}
- 산업 분류: {news.get('industry_category', '')}
- AI 시장 영향 분석: {news.get('market_impact', '')}

## 전문가 코멘트
{news.get('expert_comment', '')}

## 요청사항
1. AI 분석과 전문가 의견이 일치하는지, 충돌하는지 판단해주세요.
2. 충돌이 있다면 어떤 부분에서 차이가 있는지 구체적으로 설명해주세요.
3. 전문가 의견을 우선하되, AI의 관점도 참고할 수 있도록 정리해주세요.
4. 최종 투자/전략적 시사점을 제시해주세요.

응답은 다음 JSON 형식으로:
{{
    "opinion_conflict": true 또는 false,
    "conflict_summary": "충돌 요약 (충돌 시에만)",
    "expert_priority": "전문가 의견 핵심 요약",
    "ai_reference": "AI 분석 중 참고할 점",
    "final_insight": "최종 시사점 (3-5문장)"
}}"""

        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}]
        )

        result_text = response.content[0].text

        # Parse JSON
        if "```json" in result_text:
            json_str = result_text.split("```json")[1].split("```")[0]
        elif "```" in result_text:
            json_str = result_text.split("```")[1].split("```")[0]
        else:
            json_str = result_text

        result = json.loads(json_str.strip())

        # Update database
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE expert_reviews SET
                ai_final_review = ?,
                opinion_conflict = ?,
                expert_opinion_priority = ?,
                ai_opinion_reference = ?,
                updated_at = ?
            WHERE news_id = ?
        """, (
            result.get('final_insight', ''),
            result.get('opinion_conflict', False),
            result.get('expert_priority', ''),
            result.get('ai_reference', ''),
            datetime.now(),
            news_id
        ))
        conn.commit()
        conn.close()

        return result

    except Exception as e:
        return f"AI 리뷰 생성 실패: {e}"


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


def render_stat_cards(stats):
    """Render statistics as modern cards."""
    col1, col2, col3, col4, col5, col6 = st.columns(6)

    with col1:
        st.markdown(f"""
        <div class="stat-card">
            <p class="stat-number">{stats['total_news']}</p>
            <p class="stat-label">전체 뉴스</p>
        </div>
        """, unsafe_allow_html=True)

    with col2:
        st.markdown(f"""
        <div class="stat-card" style="background: linear-gradient(135deg, #00695c 0%, #004d40 100%);">
            <p class="stat-number">{stats['analyzed_news']}</p>
            <p class="stat-label">분석 완료</p>
        </div>
        """, unsafe_allow_html=True)

    with col3:
        st.markdown(f"""
        <div class="stat-card" style="background: linear-gradient(135deg, #5e35b1 0%, #4527a0 100%);">
            <p class="stat-number">{stats['reviewed_news']}</p>
            <p class="stat-label">전문가 리뷰</p>
        </div>
        """, unsafe_allow_html=True)

    with col4:
        st.markdown(f"""
        <div class="stat-card" style="background: linear-gradient(135deg, #d84315 0%, #bf360c 100%);">
            <p class="stat-number">{stats['conflicts']}</p>
            <p class="stat-label">의견 충돌</p>
        </div>
        """, unsafe_allow_html=True)

    with col5:
        st.markdown(f"""
        <div class="stat-card" style="background: linear-gradient(135deg, #0277bd 0%, #01579b 100%);">
            <p class="stat-number">{stats['today_news']}</p>
            <p class="stat-label">오늘 수집</p>
        </div>
        """, unsafe_allow_html=True)

    with col6:
        st.markdown(f"""
        <div class="stat-card" style="background: linear-gradient(135deg, #ff8f00 0%, #ff6f00 100%);">
            <p class="stat-number">{stats['bookmarked']}</p>
            <p class="stat-label">북마크</p>
        </div>
        """, unsafe_allow_html=True)


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

            title = row['translated_title'] or row['original_title'] or ''
            if len(title) > 50:
                title = title[:50] + "…"

            table_data.append({
                "": badge,
                "제목": title,
                "중요도": f"{imp:.2f}",
                "출처": row.get('source', '-'),
                "산업": row.get('industry_category', '-'),
                "리뷰": status,
            })

        st.dataframe(
            pd.DataFrame(table_data),
            use_container_width=True,
            hide_index=True,
            height=min(len(table_data) * 35 + 38, 400),
        )


def login_page():
    """관리자 로그인 페이지."""
    st.title("🔐 관리자 로그인")
    st.markdown("---")

    col1, col2, col3 = st.columns([1, 2, 1])

    with col2:
        user = st.text_input("ID")
        pw = st.text_input("PW", type="password")

        if st.button("로그인", use_container_width=True):
            if user == "skhan96" and pw == "kshan0816!!":
                st.session_state["login"] = True
                st.rerun()
            else:
                st.error("ID 또는 PW가 틀렸습니다")


def main():
    """Main Streamlit app."""
    st.set_page_config(
        page_title="한상국의 쉬운 중국경제뉴스 해설",
        page_icon="🇨🇳",
        layout="wide",
        initial_sidebar_state="expanded"
    )

    # 로그인 체크
    if "login" not in st.session_state or not st.session_state["login"]:
        login_page()
        st.stop()

    # Apply custom CSS
    apply_custom_css()

    # Render header banner
    render_header()

    # Get statistics first
    stats = get_statistics()

    # Render stat cards
    render_stat_cards(stats)
    st.markdown("<br>", unsafe_allow_html=True)

    # Today's selected news overview
    render_today_overview(stats)

    # Sidebar filters
    with st.sidebar:
        st.markdown("### 🎛️ 필터 설정")

        # Industry filter with Korean labels
        industry_labels = {
            "전체": "전체 산업",
            "semiconductor": "🔬 반도체",
            "ai": "🤖 인공지능",
            "new_energy": "⚡ 신에너지",
            "bio": "🧬 바이오",
            "aerospace": "🚀 항공우주",
            "quantum": "⚛️ 양자기술",
            "materials": "🧱 신소재",
            "low_carbon": "🌱 저탄소환경",
            "other": "📦 기타"
        }
        industry_options = list(industry_labels.keys())
        selected_industry = st.selectbox(
            "산업 분류",
            industry_options,
            format_func=lambda x: industry_labels[x]
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

    tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
        "🔥 AI 추천 뉴스", "⭐ 북마크", "📂 Markdown 리뷰",
        "📝 리뷰 완료", notification_label, "📥 리포트 내보내기",
        "📊 카테고리 분석"
    ])

    with tab1:
        st.subheader("📋 오늘의 선정 뉴스")

        df = get_top_news(
            limit=news_limit,
            industry=selected_industry,
            days=days_range,
            bookmarked_only=bookmarked_only,
            tag_filter=selected_tag,
            queued_only=True
        )

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

                    status_text = " | ".join(status_badges) if status_badges else "📝 리뷰대기"

                    # Title and metadata
                    title = row['translated_title'] or row['original_title']
                    is_bookmarked = row.get('is_bookmarked') or False
                    bookmark_icon = "⭐" if is_bookmarked else "☆"

                    col1, col2, col3, col4, col5 = st.columns([0.45, 0.18, 0.17, 0.1, 0.1])

                    with col1:
                        if has_review:
                            st.markdown(f"<span style='opacity:0.55'>✔ {title}</span>", unsafe_allow_html=True)
                        else:
                            st.markdown(f"**{title}**")
                    with col2:
                        st.caption(f"{badge} ({importance:.2f})")
                    with col3:
                        st.caption(status_text)
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
                                save_expert_comment(news_id, full_comment)
                                # Auto Git commit via MarkdownReviewManager
                                md_mgr = MarkdownReviewManager()
                                md_mgr.save_review(
                                    news_id=news_id,
                                    content=full_comment,
                                    news=dict(row),
                                    auto_commit=True,
                                )
                                st.success("저장 완료!")
                                st.rerun()

                    # Expandable details
                    with st.expander("상세 정보 및 리뷰", expanded=False):
                        # News details
                        col_detail1, col_detail2 = st.columns([0.7, 0.3])

                        with col_detail1:
                            st.markdown("**📰 요약**")
                            st.write(row.get('summary', '요약 없음'))

                            if row.get('market_impact'):
                                st.markdown("**📈 시장 영향 분석**")
                                st.info(row['market_impact'])

                        with col_detail2:
                            st.markdown("**📋 분류 정보**")
                            st.write(f"- 산업: {row.get('industry_category', '-')}")
                            st.write(f"- 유형: {row.get('content_type', '-')}")
                            st.write(f"- 감성: {row.get('sentiment', '-')}")
                            st.write(f"- 출처: {row.get('source', '-')}")

                            if row.get('keywords'):
                                try:
                                    keywords = json.loads(row['keywords'])
                                    st.write(f"- 키워드: {', '.join(keywords)}")
                                except:
                                    st.write(f"- 키워드: {row['keywords']}")

                            if row.get('original_url'):
                                st.markdown(f"[원문 링크]({row['original_url']})")

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
                                    # Prepare news data for template
                                    news_data = dict(row)

                                    # Save to Markdown file with Git commit
                                    result = md_review_manager.save_review(
                                        news_id=news_id,
                                        content=expert_comment_input,
                                        news=news_data,
                                        auto_commit=True
                                    )

                                    # Also save to DB for compatibility
                                    save_expert_comment(news_id, expert_comment_input)

                                    if result['committed']:
                                        st.success(f"✅ Git 커밋 완료!")
                                    else:
                                        st.warning(result['message'])
                                    st.rerun()
                                else:
                                    st.warning("논평을 입력해주세요.")

                        with col_btn2:
                            if st.button("📄 파일만 저장", key=f"save_file_{news_id}"):
                                if expert_comment_input.strip():
                                    news_data = dict(row)
                                    result = md_review_manager.save_review(
                                        news_id=news_id,
                                        content=expert_comment_input,
                                        news=news_data,
                                        auto_commit=False
                                    )
                                    save_expert_comment(news_id, expert_comment_input)
                                    st.success(f"📁 {result['file_path']}")
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
                industry = news.get('industry_category', '-')
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
                   er.review_completed_at
            FROM news n
            JOIN expert_reviews er ON n.id = er.news_id
            WHERE er.expert_comment IS NOT NULL
            ORDER BY er.review_completed_at DESC
            LIMIT 50
        """, conn)
        conn.close()

        if reviewed_df.empty:
            st.info("아직 리뷰된 뉴스가 없습니다.")
        else:
            for idx, row in reviewed_df.iterrows():
                title = row['translated_title'] or row['original_title']
                conflict_icon = "⚠️" if row.get('opinion_conflict') else "✅"

                with st.expander(f"{conflict_icon} {title}", expanded=False):
                    col1, col2 = st.columns(2)

                    with col1:
                        st.markdown("**전문가 코멘트**")
                        st.write(row.get('expert_comment', ''))

                    with col2:
                        st.markdown("**AI 최종 리뷰**")
                        st.write(row.get('ai_final_review', '아직 생성되지 않음'))

                    st.caption(f"리뷰 시간: {row.get('review_completed_at', '-')}")

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
            # Map category keys to Korean labels (without filter "전체")
            cat_label_map = {k: v for k, v in industry_labels.items() if k != "전체"}
            cat_df['label'] = cat_df['category'].map(
                lambda c: cat_label_map.get(c, f"📦 {c}")
            )

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
                format_func=lambda c: cat_label_map.get(c, f"📦 {c}"),
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


if __name__ == "__main__":
    main()

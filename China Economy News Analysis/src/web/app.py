"""Flask application for public news feed.

Provides user-facing web interface for expert-reviewed news.
Port: 8502 (separate from admin dashboard on 8501)
"""

from flask import Flask, render_template, abort, request
from markupsafe import Markup
from datetime import datetime, date, timedelta
from pathlib import Path
import sys
import bleach

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.api.public_feed import (
    get_published_news,
    get_published_news_count,
    get_news_by_id,
    get_published_news_by_date,
    get_available_dates,
    get_today_headlines,
)

app = Flask(
    __name__,
    template_folder=str(Path(__file__).parent / "templates"),
    static_folder=str(Path(__file__).parent / "static"),
)

# Configuration
app.config["ITEMS_PER_PAGE"] = 10


def group_news_by_date(news_list: list[dict]) -> dict[str, list[dict]]:
    """Group news items by date category (today/yesterday/earlier).

    Args:
        news_list: List of news dictionaries with 'date' field

    Returns:
        Dictionary with keys 'today', 'yesterday', 'earlier'
    """
    today = date.today()
    yesterday = today - timedelta(days=1)

    grouped = {"today": [], "yesterday": [], "earlier": []}

    for news in news_list:
        news_date_str = news.get("date")
        if not news_date_str:
            grouped["earlier"].append(news)
            continue

        try:
            # Parse datetime string to date
            if "T" in news_date_str:
                news_date = datetime.fromisoformat(news_date_str).date()
            else:
                news_date = datetime.strptime(news_date_str[:10], "%Y-%m-%d").date()

            if news_date == today:
                grouped["today"].append(news)
            elif news_date == yesterday:
                grouped["yesterday"].append(news)
            else:
                grouped["earlier"].append(news)
        except (ValueError, TypeError):
            grouped["earlier"].append(news)

    return grouped


@app.route("/")
def index():
    """Homepage: Today's news feed with date grouping."""
    page = request.args.get("page", 1, type=int)
    per_page = app.config["ITEMS_PER_PAGE"]
    offset = (page - 1) * per_page

    # Get news and total count
    news_list = get_published_news(limit=per_page, offset=offset)
    total_count = get_published_news_count()
    total_pages = (total_count + per_page - 1) // per_page

    # Group by date
    grouped_news = group_news_by_date(news_list)

    # Get today's headlines for summary card
    today_headlines = get_today_headlines()

    return render_template(
        "feed.html",
        grouped_news=grouped_news,
        today_headlines=today_headlines,
        current_page=page,
        total_pages=total_pages,
        total_count=total_count,
        today=date.today(),
    )


@app.route("/archive")
def archive():
    """Archive page: Browse news by date."""
    # Get selected date from query param
    date_str = request.args.get("date")
    available_dates = get_available_dates()

    selected_news = []
    selected_date = None

    if date_str:
        try:
            selected_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            selected_news = get_published_news_by_date(selected_date)
        except ValueError:
            pass

    return render_template(
        "archive.html",
        available_dates=available_dates,
        selected_date=selected_date,
        selected_news=selected_news,
        today=date.today(),
    )


@app.route("/news/<int:news_id>")
def news_detail(news_id: int):
    """Individual news detail page (shareable link)."""
    news = get_news_by_id(news_id)

    if not news:
        abort(404)

    return render_template(
        "news_detail.html",
        news=news,
        today=date.today(),
    )


@app.errorhandler(404)
def page_not_found(e):
    """Custom 404 page."""
    return render_template("404.html", today=date.today()), 404


@app.context_processor
def inject_globals():
    """Inject global template variables."""
    return {
        "site_title": "한상국의 쉬운 중국경제뉴스 해설",
        "current_year": datetime.now().year,
    }


@app.template_filter("format_date")
def format_date_filter(date_str: str) -> str:
    """Format date string for display."""
    if not date_str:
        return ""
    try:
        if "T" in date_str:
            dt = datetime.fromisoformat(date_str)
        else:
            dt = datetime.strptime(date_str[:10], "%Y-%m-%d")
        return dt.strftime("%Y년 %m월 %d일")
    except (ValueError, TypeError):
        return date_str


@app.template_filter("format_importance")
def format_importance_filter(score: float) -> dict:
    """Return importance badge info based on score."""
    if score is None:
        return {"label": "일반", "class": "importance-low"}
    if score >= 0.8:
        return {"label": "매우 중요", "class": "importance-critical"}
    elif score >= 0.6:
        return {"label": "중요", "class": "importance-high"}
    elif score >= 0.4:
        return {"label": "보통", "class": "importance-medium"}
    else:
        return {"label": "일반", "class": "importance-low"}


@app.template_filter("category_label")
def category_label_filter(category: str) -> str:
    """Convert category code to Korean label."""
    labels = {
        "semiconductor": "반도체",
        "ai": "AI/인공지능",
        "new_energy": "신에너지",
        "bio": "바이오",
        "aerospace": "항공우주",
        "quantum": "양자",
        "materials": "신소재",
        "tech": "테크",
        "policy": "정책",
        "corporate": "기업",
        "industry": "산업",
        "market": "시장",
        "other": "기타",
    }
    return labels.get(category, category or "기타")


# Allowed HTML tags and attributes for XSS prevention
ALLOWED_TAGS = [
    "p", "br", "strong", "b", "em", "i", "u", "s",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li", "blockquote", "pre", "code",
    "a", "span", "div", "table", "thead", "tbody", "tr", "th", "td",
]
ALLOWED_ATTRIBUTES = {
    "a": ["href", "title", "target", "rel"],
    "span": ["class"],
    "div": ["class"],
    "td": ["colspan", "rowspan"],
    "th": ["colspan", "rowspan"],
}


@app.template_filter("safe_html")
def safe_html_filter(content: str) -> Markup:
    """Sanitize HTML content to prevent XSS attacks.

    Only allows safe HTML tags and attributes.
    """
    if not content:
        return Markup("")

    cleaned = bleach.clean(
        content,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        strip=True,
    )
    # Convert newlines to <br> for plain text
    if "<" not in content:
        cleaned = cleaned.replace("\n", "<br>")

    return Markup(cleaned)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8502, debug=True)

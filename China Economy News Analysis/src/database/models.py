"""Database models and initialization."""

import sqlite3
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from config.settings import DATABASE_PATH


def get_connection() -> sqlite3.Connection:
    """Get database connection."""
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Initialize database with schema."""
    conn = get_connection()
    cursor = conn.cursor()

    # News table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source VARCHAR(100) NOT NULL,
            original_url TEXT NOT NULL UNIQUE,
            original_title TEXT NOT NULL,
            original_content TEXT,
            translated_title TEXT,
            summary TEXT,
            importance_score REAL DEFAULT 0.5,
            industry_category VARCHAR(50),
            content_type VARCHAR(50),
            sentiment VARCHAR(20),
            market_impact TEXT,
            keywords TEXT,
            related_news TEXT,
            published_at DATETIME,
            collected_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            analyzed_at DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Expert reviews table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS expert_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            news_id INTEGER NOT NULL,
            ai_comment TEXT,
            expert_comment TEXT,
            ai_final_review TEXT,
            opinion_conflict BOOLEAN DEFAULT FALSE,
            expert_opinion_priority TEXT,
            ai_opinion_reference TEXT,
            review_started_at DATETIME,
            review_completed_at DATETIME,
            review_duration_seconds INTEGER,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (news_id) REFERENCES news(id)
        )
    """)

    # Indexes
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_news_importance ON news(importance_score DESC)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_news_industry ON news(industry_category)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_news_published ON news(published_at DESC)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_news_source ON news(source)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_news_collected ON news(collected_at DESC)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_reviews_news ON expert_reviews(news_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_reviews_completed ON expert_reviews(review_completed_at DESC)")

    conn.commit()
    conn.close()
    print("Database initialized successfully.")


if __name__ == "__main__":
    init_db()

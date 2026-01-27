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
            is_bookmarked BOOLEAN DEFAULT FALSE,
            tags TEXT,
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

    # Notifications table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            news_id INTEGER,
            notification_type VARCHAR(50) NOT NULL,
            title TEXT NOT NULL,
            message TEXT,
            is_read BOOLEAN DEFAULT FALSE,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (news_id) REFERENCES news(id)
        )
    """)

    # Notification settings table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS notification_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            setting_key VARCHAR(100) UNIQUE NOT NULL,
            setting_value TEXT,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Insert default notification settings
    cursor.execute("""
        INSERT OR IGNORE INTO notification_settings (setting_key, setting_value)
        VALUES ('importance_threshold', '0.8'),
               ('notifications_enabled', 'true'),
               ('notify_on_new_high_importance', 'true'),
               ('notify_on_opinion_conflict', 'true')
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


def migrate_db():
    """Add new columns to existing database."""
    conn = get_connection()
    cursor = conn.cursor()

    # Check and add is_bookmarked column
    cursor.execute("PRAGMA table_info(news)")
    columns = [col[1] for col in cursor.fetchall()]

    if 'is_bookmarked' not in columns:
        cursor.execute("ALTER TABLE news ADD COLUMN is_bookmarked BOOLEAN DEFAULT FALSE")
        print("Added is_bookmarked column to news table")

    if 'tags' not in columns:
        cursor.execute("ALTER TABLE news ADD COLUMN tags TEXT")
        print("Added tags column to news table")

    # Create notifications tables if not exist
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            news_id INTEGER,
            notification_type VARCHAR(50) NOT NULL,
            title TEXT NOT NULL,
            message TEXT,
            is_read BOOLEAN DEFAULT FALSE,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (news_id) REFERENCES news(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS notification_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            setting_key VARCHAR(100) UNIQUE NOT NULL,
            setting_value TEXT,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Insert default settings
    cursor.execute("""
        INSERT OR IGNORE INTO notification_settings (setting_key, setting_value)
        VALUES ('importance_threshold', '0.8'),
               ('notifications_enabled', 'true'),
               ('notify_on_new_high_importance', 'true'),
               ('notify_on_opinion_conflict', 'true')
    """)

    # Create indexes
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_news_bookmarked ON news(is_bookmarked)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_notifications_read ON notifications(is_read)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_notifications_created ON notifications(created_at DESC)")

    conn.commit()
    conn.close()
    print("Database migration completed.")


if __name__ == "__main__":
    init_db()
    migrate_db()

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
            expert_review_status TEXT DEFAULT 'none',
            market_relevance_score REAL,
            uncertainty_score REAL,
            expert_explainability_score REAL,
            topic_vector TEXT,
            content_score REAL,
            score_breakdown TEXT,
            score_explanation TEXT,
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
            publish_status TEXT DEFAULT 'draft',
            admin_note TEXT,
            publish_status_updated_at DATETIME,
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
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_reviews_publish_status ON expert_reviews(publish_status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_news_content_score ON news(content_score DESC)")

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

    # Phase 5: Daily selection algorithm columns
    if 'expert_review_status' not in columns:
        cursor.execute("ALTER TABLE news ADD COLUMN expert_review_status TEXT DEFAULT 'none'")
        print("Added expert_review_status column to news table")

    if 'market_relevance_score' not in columns:
        cursor.execute("ALTER TABLE news ADD COLUMN market_relevance_score REAL")
        print("Added market_relevance_score column to news table")

    if 'uncertainty_score' not in columns:
        cursor.execute("ALTER TABLE news ADD COLUMN uncertainty_score REAL")
        print("Added uncertainty_score column to news table")

    if 'expert_explainability_score' not in columns:
        cursor.execute("ALTER TABLE news ADD COLUMN expert_explainability_score REAL")
        print("Added expert_explainability_score column to news table")

    if 'topic_vector' not in columns:
        cursor.execute("ALTER TABLE news ADD COLUMN topic_vector TEXT")
        print("Added topic_vector column to news table")

    # Content-Based Scoring 컬럼
    if 'content_score' not in columns:
        cursor.execute("ALTER TABLE news ADD COLUMN content_score REAL")
        print("Added content_score column to news table")

    if 'score_breakdown' not in columns:
        cursor.execute("ALTER TABLE news ADD COLUMN score_breakdown TEXT")
        print("Added score_breakdown (JSON) column to news table")

    if 'score_explanation' not in columns:
        cursor.execute("ALTER TABLE news ADD COLUMN score_explanation TEXT")
        print("Added score_explanation column to news table")

    # Card headline for mobile UI (max 18 Korean characters = 36 bytes)
    if 'card_headline' not in columns:
        cursor.execute("ALTER TABLE news ADD COLUMN card_headline VARCHAR(36)")
        print("Added card_headline column to news table")

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

    # Publish status columns for expert_reviews
    cursor.execute("PRAGMA table_info(expert_reviews)")
    er_columns = [col[1] for col in cursor.fetchall()]

    if 'publish_status' not in er_columns:
        cursor.execute("ALTER TABLE expert_reviews ADD COLUMN publish_status TEXT DEFAULT 'draft'")
        # Backfill existing reviews as 'published' for backward compatibility
        cursor.execute("UPDATE expert_reviews SET publish_status = 'published' WHERE publish_status IS NULL OR publish_status = 'draft'")
        print("Added publish_status column to expert_reviews table (existing rows backfilled as 'published')")

    if 'admin_note' not in er_columns:
        cursor.execute("ALTER TABLE expert_reviews ADD COLUMN admin_note TEXT")
        print("Added admin_note column to expert_reviews table")

    if 'publish_status_updated_at' not in er_columns:
        cursor.execute("ALTER TABLE expert_reviews ADD COLUMN publish_status_updated_at DATETIME")
        print("Added publish_status_updated_at column to expert_reviews table")

    # Create indexes
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_reviews_publish_status ON expert_reviews(publish_status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_news_bookmarked ON news(is_bookmarked)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_notifications_read ON notifications(is_read)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_notifications_created ON notifications(created_at DESC)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_news_expert_review_status ON news(expert_review_status)")

    # Trigger: block expert_reviews insert unless news is queued_today
    cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS trg_check_expert_review_status
        BEFORE INSERT ON expert_reviews
        FOR EACH ROW
        BEGIN
            SELECT
                RAISE(
                    ABORT,
                    'Expert comments can only be written when expert_review_status = queued_today'
                )
            WHERE NOT EXISTS (
                SELECT 1
                FROM news
                WHERE id = NEW.news_id
                  AND expert_review_status = 'queued_today'
            );
        END;
    """)

    # Trigger: auto-update news status to 'commented' after expert review insert
    cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS trg_set_expert_review_commented
        AFTER INSERT ON expert_reviews
        FOR EACH ROW
        BEGIN
            UPDATE news
            SET expert_review_status = 'commented',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = NEW.news_id;
        END;
    """)

    conn.commit()
    conn.close()
    print("Database migration completed.")


if __name__ == "__main__":
    init_db()
    migrate_db()

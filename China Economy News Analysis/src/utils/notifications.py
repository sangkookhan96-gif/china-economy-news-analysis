"""Notification utilities for important news alerts."""

from datetime import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.database.models import get_connection


class NotificationManager:
    """Manage notifications for important news."""

    def __init__(self):
        self.conn = None

    def _get_conn(self):
        if not self.conn:
            self.conn = get_connection()
        return self.conn

    def _close_conn(self):
        if self.conn:
            self.conn.close()
            self.conn = None

    def get_setting(self, key: str, default: str = None) -> str:
        """Get notification setting value."""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT setting_value FROM notification_settings WHERE setting_key = ?",
            (key,)
        )
        row = cursor.fetchone()
        return row[0] if row else default

    def set_setting(self, key: str, value: str) -> bool:
        """Update notification setting."""
        conn = self._get_conn()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                INSERT INTO notification_settings (setting_key, setting_value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(setting_key) DO UPDATE SET
                    setting_value = excluded.setting_value,
                    updated_at = excluded.updated_at
            """, (key, value, datetime.now()))
            conn.commit()
            return True
        except Exception as e:
            print(f"Error updating setting: {e}")
            return False

    def is_enabled(self) -> bool:
        """Check if notifications are enabled."""
        return self.get_setting('notifications_enabled', 'true') == 'true'

    def get_threshold(self) -> float:
        """Get importance threshold for notifications."""
        return float(self.get_setting('importance_threshold', '0.8'))

    def create_notification(self, news_id: int, notification_type: str,
                          title: str, message: str = None) -> int:
        """Create a new notification."""
        if not self.is_enabled():
            return None

        conn = self._get_conn()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                INSERT INTO notifications (news_id, notification_type, title, message, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (news_id, notification_type, title, message, datetime.now()))
            conn.commit()
            return cursor.lastrowid
        except Exception as e:
            print(f"Error creating notification: {e}")
            return None

    def check_and_notify_high_importance(self, news_id: int, importance_score: float,
                                         title: str) -> bool:
        """Check if news meets threshold and create notification."""
        if not self.is_enabled():
            return False

        threshold = self.get_threshold()
        notify_high = self.get_setting('notify_on_new_high_importance', 'true') == 'true'

        if notify_high and importance_score >= threshold:
            self.create_notification(
                news_id=news_id,
                notification_type='high_importance',
                title=f"[고중요도] {title[:50]}...",
                message=f"중요도 {importance_score:.2f} - 즉시 검토가 필요한 뉴스입니다."
            )
            return True
        return False

    def notify_opinion_conflict(self, news_id: int, title: str) -> bool:
        """Create notification for opinion conflict."""
        if not self.is_enabled():
            return False

        notify_conflict = self.get_setting('notify_on_opinion_conflict', 'true') == 'true'

        if notify_conflict:
            self.create_notification(
                news_id=news_id,
                notification_type='opinion_conflict',
                title=f"[의견충돌] {title[:50]}...",
                message="AI와 전문가 의견에 차이가 있습니다. 검토가 필요합니다."
            )
            return True
        return False

    def get_unread_notifications(self, limit: int = 20) -> list:
        """Get unread notifications."""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT n.*, news.translated_title, news.original_title
            FROM notifications n
            LEFT JOIN news ON n.news_id = news.id
            WHERE n.is_read = FALSE
            ORDER BY n.created_at DESC
            LIMIT ?
        """, (limit,))
        return [dict(row) for row in cursor.fetchall()]

    def get_all_notifications(self, limit: int = 50) -> list:
        """Get all notifications."""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT n.*, news.translated_title, news.original_title
            FROM notifications n
            LEFT JOIN news ON n.news_id = news.id
            ORDER BY n.created_at DESC
            LIMIT ?
        """, (limit,))
        return [dict(row) for row in cursor.fetchall()]

    def mark_as_read(self, notification_id: int) -> bool:
        """Mark notification as read."""
        conn = self._get_conn()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "UPDATE notifications SET is_read = TRUE WHERE id = ?",
                (notification_id,)
            )
            conn.commit()
            return True
        except Exception as e:
            print(f"Error marking notification as read: {e}")
            return False

    def mark_all_as_read(self) -> bool:
        """Mark all notifications as read."""
        conn = self._get_conn()
        cursor = conn.cursor()
        try:
            cursor.execute("UPDATE notifications SET is_read = TRUE")
            conn.commit()
            return True
        except Exception as e:
            print(f"Error marking all as read: {e}")
            return False

    def get_unread_count(self) -> int:
        """Get count of unread notifications."""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM notifications WHERE is_read = FALSE")
        return cursor.fetchone()[0]

    def delete_old_notifications(self, days: int = 30) -> int:
        """Delete notifications older than specified days."""
        conn = self._get_conn()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                DELETE FROM notifications
                WHERE created_at < datetime('now', ?)
            """, (f'-{days} days',))
            conn.commit()
            return cursor.rowcount
        except Exception as e:
            print(f"Error deleting old notifications: {e}")
            return 0


# Bookmark and tag functions
def toggle_bookmark(news_id: int) -> bool:
    """Toggle bookmark status for a news item."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT is_bookmarked FROM news WHERE id = ?", (news_id,))
        row = cursor.fetchone()
        if row:
            new_status = not (row[0] or False)
            cursor.execute(
                "UPDATE news SET is_bookmarked = ?, updated_at = ? WHERE id = ?",
                (new_status, datetime.now(), news_id)
            )
            conn.commit()
            return new_status
        return False
    finally:
        conn.close()


def set_tags(news_id: int, tags: list) -> bool:
    """Set tags for a news item."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        import json
        tags_json = json.dumps(tags, ensure_ascii=False)
        cursor.execute(
            "UPDATE news SET tags = ?, updated_at = ? WHERE id = ?",
            (tags_json, datetime.now(), news_id)
        )
        conn.commit()
        return True
    except Exception as e:
        print(f"Error setting tags: {e}")
        return False
    finally:
        conn.close()


def get_tags(news_id: int) -> list:
    """Get tags for a news item."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT tags FROM news WHERE id = ?", (news_id,))
        row = cursor.fetchone()
        if row and row[0]:
            import json
            return json.loads(row[0])
        return []
    finally:
        conn.close()


def get_all_tags() -> list:
    """Get all unique tags used across news items."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT DISTINCT tags FROM news WHERE tags IS NOT NULL AND tags != ''")
        all_tags = set()
        import json
        for row in cursor.fetchall():
            try:
                tags = json.loads(row[0])
                all_tags.update(tags)
            except:
                pass
        return sorted(list(all_tags))
    finally:
        conn.close()


def get_bookmarked_news(limit: int = 50) -> list:
    """Get all bookmarked news items."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT * FROM news
            WHERE is_bookmarked = TRUE
            ORDER BY updated_at DESC
            LIMIT ?
        """, (limit,))
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def get_news_by_tag(tag: str, limit: int = 50) -> list:
    """Get news items with a specific tag."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT * FROM news
            WHERE tags LIKE ?
            ORDER BY importance_score DESC
            LIMIT ?
        """, (f'%"{tag}"%', limit))
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


if __name__ == "__main__":
    # Test notification manager
    manager = NotificationManager()
    print(f"Notifications enabled: {manager.is_enabled()}")
    print(f"Importance threshold: {manager.get_threshold()}")
    print(f"Unread count: {manager.get_unread_count()}")

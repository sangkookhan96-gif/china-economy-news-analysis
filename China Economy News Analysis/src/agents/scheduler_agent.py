#!/usr/bin/env python3
"""Scheduler agent for automated news collection and analysis.

This agent runs on a schedule to:
1. Collect news from all enabled sources (every hour)
2. Analyze unanalyzed news with Claude AI
3. Generate daily summaries
"""

import logging
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

import schedule

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from config.settings import CRAWL_INTERVAL_HOURS, ANTHROPIC_API_KEY
from src.database.models import init_db, migrate_db, get_connection
from src.collector.crawler import NewsCrawler
from src.utils.backup import create_backup, cleanup_old_backups
from src.utils.notifications import NotificationManager
from src.agents.daily_news_selector import run_daily_selection
from src.analyzer.embeddings import backfill_topic_vectors

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("scheduler")


class SchedulerAgent:
    """Agent that schedules and runs automated tasks."""

    def __init__(self):
        self.crawler = NewsCrawler()
        self.analyzer = None  # Lazy load to avoid API key issues at startup
        self.notification_manager = NotificationManager()
        self.running = True
        self.stats = {
            "total_collected": 0,
            "total_analyzed": 0,
            "total_notifications": 0,
            "total_selected_for_review": 0,
            "last_crawl": None,
            "last_analysis": None,
            "last_daily_selection": None,
            "errors": 0,
        }

    def _get_analyzer(self):
        """Lazy load analyzer to handle API key availability."""
        if self.analyzer is None:
            if not ANTHROPIC_API_KEY:
                logger.warning("ANTHROPIC_API_KEY not set, analysis disabled")
                return None
            from src.analyzer.claude_analyzer import ClaudeAnalyzer
            self.analyzer = ClaudeAnalyzer()
        return self.analyzer

    def collect_news(self) -> dict:
        """Run news collection from all enabled sources."""
        logger.info("=" * 50)
        logger.info("Starting scheduled news collection...")

        try:
            results = self.crawler.crawl_all()
            self.stats["total_collected"] += results["new"]
            self.stats["last_crawl"] = datetime.now()

            logger.info(f"Collection complete: {results['total']} total, {results['new']} new")

            # Log per-source results
            for source, data in results["sources"].items():
                if data["new"] > 0:
                    logger.info(f"  - {source}: {data['new']} new articles")

            return results

        except Exception as e:
            logger.error(f"Collection failed: {e}")
            self.stats["errors"] += 1
            return {"total": 0, "new": 0, "sources": {}}

    def analyze_news(self, limit: int = 10) -> list:
        """Analyze unanalyzed news articles."""
        analyzer = self._get_analyzer()
        if not analyzer:
            return []

        logger.info(f"Starting AI analysis (limit: {limit})...")

        try:
            results = analyzer.analyze_unanalyzed(limit=limit)
            self.stats["total_analyzed"] += len(results)
            self.stats["last_analysis"] = datetime.now()

            # Log analysis results and create notifications for high importance
            for result in results:
                if "error" not in result:
                    title = result.get("translated_title", "")
                    score = result.get("importance_score", 0)
                    news_id = result.get("news_id")
                    logger.info(f"  - [{score:.2f}] {title[:40]}...")

                    # Create notification for high importance news
                    if news_id and self.notification_manager.check_and_notify_high_importance(
                        news_id=news_id,
                        importance_score=score,
                        title=title
                    ):
                        self.stats["total_notifications"] += 1
                        logger.info(f"    -> Notification created (high importance)")

            logger.info(f"Analysis complete: {len(results)} articles processed")
            return results

        except Exception as e:
            logger.error(f"Analysis failed: {e}")
            self.stats["errors"] += 1
            return []

    def run_hourly_task(self):
        """Combined hourly task: collect, enrich, then analyze."""
        self.collect_news()

        # Enrich content for articles missing full text
        self.enrich_content(limit=5)

        # Analyze up to 10 new articles per hour
        self.analyze_news(limit=10)

        # Print stats
        self._print_stats()

    def enrich_content(self, limit: int = 5):
        """Fetch full content for articles missing it."""
        logger.info(f"Enriching content (limit: {limit})...")

        try:
            enriched = self.crawler.enrich_news_content(limit=limit)
            logger.info(f"Content enriched: {enriched} articles")
        except Exception as e:
            logger.error(f"Content enrichment failed: {e}")
            self.stats["errors"] += 1

    def run_daily_backup(self):
        """Run daily database backup (runs at 23:00)."""
        logger.info("=" * 50)
        logger.info("Running daily backup...")

        try:
            backup_path = create_backup(compress=True)
            logger.info(f"Backup created: {backup_path}")

            # Cleanup old backups (keep 7 days)
            deleted = cleanup_old_backups(keep_days=7)
            if deleted > 0:
                logger.info(f"Cleaned up {deleted} old backup(s)")

        except Exception as e:
            logger.error(f"Backup failed: {e}")
            self.stats["errors"] += 1

    def run_daily_news_selection(self):
        """Select 10 news items for expert review (runs at 06:00)."""
        logger.info("=" * 50)
        logger.info("Running daily news selection for expert review...")

        try:
            # First, backfill any missing topic vectors
            logger.info("Backfilling topic vectors...")
            backfill_result = backfill_topic_vectors(limit=50)
            logger.info(f"Topic vectors: {backfill_result['success']} generated")

            # Run the selection algorithm
            result = run_daily_selection()

            self.stats["total_selected_for_review"] += result["selected_count"]
            self.stats["last_daily_selection"] = datetime.now()

            logger.info(f"Daily selection complete:")
            logger.info(f"  - Reset from previous queue: {result['reset_count']}")
            logger.info(f"  - Selected for review: {result['selected_count']}")
            logger.info(f"  - News IDs: {result['selected_ids']}")

            return result

        except Exception as e:
            logger.error(f"Daily selection failed: {e}")
            self.stats["errors"] += 1
            return {"selected_count": 0, "selected_ids": []}

    def run_daily_summary(self):
        """Generate daily summary (runs at midnight)."""
        logger.info("=" * 50)
        logger.info("Generating daily summary...")

        conn = get_connection()
        cursor = conn.cursor()

        # Get today's stats
        cursor.execute("""
            SELECT
                COUNT(*) as total,
                COUNT(CASE WHEN analyzed_at IS NOT NULL THEN 1 END) as analyzed,
                AVG(CASE WHEN importance_score > 0 THEN importance_score END) as avg_score
            FROM news
            WHERE DATE(collected_at) = DATE('now')
        """)
        row = cursor.fetchone()

        # Get top articles
        cursor.execute("""
            SELECT translated_title, importance_score, industry_category
            FROM news
            WHERE DATE(collected_at) = DATE('now')
                AND importance_score IS NOT NULL
            ORDER BY importance_score DESC
            LIMIT 5
        """)
        top_articles = cursor.fetchall()
        conn.close()

        logger.info(f"Today's stats:")
        logger.info(f"  - Total collected: {row['total']}")
        logger.info(f"  - Analyzed: {row['analyzed']}")
        logger.info(f"  - Avg importance: {row['avg_score']:.2f}" if row['avg_score'] else "  - Avg importance: N/A")

        if top_articles:
            logger.info("Top 5 articles by importance:")
            for article in top_articles:
                logger.info(f"  - [{article['importance_score']:.2f}] [{article['industry_category']}] {article['translated_title'][:50]}")

    def _print_stats(self):
        """Print current agent statistics."""
        logger.info("-" * 30)
        logger.info(f"Agent stats:")
        logger.info(f"  - Total collected: {self.stats['total_collected']}")
        logger.info(f"  - Total analyzed: {self.stats['total_analyzed']}")
        logger.info(f"  - Notifications sent: {self.stats['total_notifications']}")
        logger.info(f"  - Selected for review: {self.stats['total_selected_for_review']}")
        logger.info(f"  - Errors: {self.stats['errors']}")
        if self.stats['last_crawl']:
            logger.info(f"  - Last crawl: {self.stats['last_crawl'].strftime('%H:%M:%S')}")
        if self.stats['last_daily_selection']:
            logger.info(f"  - Last daily selection: {self.stats['last_daily_selection'].strftime('%H:%M:%S')}")

    def setup_schedule(self):
        """Configure the schedule for all tasks."""
        interval = CRAWL_INTERVAL_HOURS

        # Hourly collection and analysis
        schedule.every(interval).hours.do(self.run_hourly_task)

        # Daily backup at 23:00
        schedule.every().day.at("23:00").do(self.run_daily_backup)

        # Daily summary at midnight
        schedule.every().day.at("00:00").do(self.run_daily_summary)

        # Daily news selection for expert review at 06:00
        schedule.every().day.at("06:00").do(self.run_daily_news_selection)

        logger.info(f"Schedule configured:")
        logger.info(f"  - News collection: every {interval} hour(s)")
        logger.info(f"  - Daily backup: at 23:00")
        logger.info(f"  - Daily summary: at 00:00")
        logger.info(f"  - Daily news selection: at 06:00")

    def check_missed_daily_tasks(self):
        """Check if daily tasks were missed (e.g., after reboot) and run them."""
        now = datetime.now()

        # Check if daily news selection (06:00) was missed today
        if now.hour >= 6 and self.stats["last_daily_selection"] is None:
            # Check DB for today's queued items
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COUNT(*) as cnt FROM news
                WHERE expert_review_status = 'queued_today'
            """)
            queued = cursor.fetchone()["cnt"]
            conn.close()

            if queued == 0:
                logger.info("Missed daily news selection detected (06:00 task). Running now...")
                self.run_hourly_task()  # Collect and analyze first
                self.run_daily_news_selection()

    def run(self, run_immediately: bool = True):
        """Start the scheduler agent."""
        logger.info("=" * 50)
        logger.info("Starting Scheduler Agent")
        logger.info("=" * 50)

        # Initialize database and run migrations
        init_db()
        migrate_db()

        # Setup schedule
        self.setup_schedule()

        # Run immediately if requested
        if run_immediately:
            logger.info("Running initial collection...")
            self.run_hourly_task()

        # Check for missed daily tasks (e.g., after reboot past 06:00)
        self.check_missed_daily_tasks()

        # Main loop
        logger.info("Entering scheduler loop (Ctrl+C to stop)...")
        while self.running:
            schedule.run_pending()
            time.sleep(60)  # Check every minute

        logger.info("Scheduler agent stopped.")

    def stop(self):
        """Stop the scheduler agent gracefully."""
        logger.info("Stopping scheduler agent...")
        self.running = False


def signal_handler(signum, frame):
    """Handle shutdown signals."""
    logger.info(f"Received signal {signum}, shutting down...")
    global agent
    if agent:
        agent.stop()


agent = None


def main():
    """Main entry point."""
    global agent

    # Setup signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Parse arguments
    import argparse
    parser = argparse.ArgumentParser(description="News Scheduler Agent")
    parser.add_argument("--no-immediate", action="store_true",
                        help="Don't run collection immediately at startup")
    parser.add_argument("--once", action="store_true",
                        help="Run once and exit (don't enter scheduler loop)")
    parser.add_argument("--daily-selection", action="store_true",
                        help="Run daily news selection and exit")
    args = parser.parse_args()

    # Create and run agent
    agent = SchedulerAgent()

    if args.daily_selection:
        logger.info("Running daily news selection...")
        init_db()
        migrate_db()
        agent.run_daily_news_selection()
    elif args.once:
        logger.info("Running single collection cycle...")
        init_db()
        migrate_db()
        agent.run_hourly_task()
    else:
        agent.run(run_immediately=not args.no_immediate)


if __name__ == "__main__":
    main()

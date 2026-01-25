"""News collector module."""
from .sources import NEWS_SOURCES
from .crawler import NewsCrawler

__all__ = ["NEWS_SOURCES", "NewsCrawler"]

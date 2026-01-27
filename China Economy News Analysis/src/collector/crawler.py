"""News crawler implementation."""

import logging
import re
from datetime import datetime
from typing import Optional
from urllib.parse import urljoin

import feedparser
import requests
from bs4 import BeautifulSoup

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from config.settings import (
    REQUEST_HEADERS,
    REQUEST_TIMEOUT,
    MAX_NEWS_PER_SOURCE,
    INDUSTRY_KEYWORDS,
)
from src.collector.sources import get_enabled_sources
from src.database.models import get_connection

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class NewsCrawler:
    """News crawler for Chinese economic news sources."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(REQUEST_HEADERS)

    def fetch_url(self, url: str) -> Optional[str]:
        """Fetch URL content."""
        try:
            response = self.session.get(url, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            response.encoding = response.apparent_encoding or "utf-8"
            return response.text
        except requests.RequestException as e:
            logger.error(f"Failed to fetch {url}: {e}")
            return None

    def parse_rss(self, rss_url: str, source_key: str) -> list[dict]:
        """Parse RSS feed and return news items."""
        feed = feedparser.parse(rss_url)
        items = []

        for entry in feed.entries[:MAX_NEWS_PER_SOURCE]:
            item = {
                "source": source_key,
                "original_url": entry.get("link", ""),
                "original_title": entry.get("title", ""),
                "original_content": entry.get("summary", ""),
                "published_at": self._parse_date(entry.get("published")),
            }
            if item["original_url"] and item["original_title"]:
                items.append(item)

        return items

    def crawl_xinhua(self) -> list[dict]:
        """Crawl Xinhua News (신화통신)."""
        items = []
        url = "http://www.xinhuanet.com/fortune/"
        html = self.fetch_url(url)
        if not html:
            return items

        soup = BeautifulSoup(html, "lxml")
        # Find news links in the main content area
        for link in soup.select("a[href*='/fortune/']")[:MAX_NEWS_PER_SOURCE]:
            href = link.get("href", "")
            title = link.get_text(strip=True)

            if not href or not title or len(title) < 10:
                continue
            if not href.startswith("http"):
                href = urljoin(url, href)
            # Filter for article URLs (usually contain date pattern)
            if re.search(r"/\d{4}-\d{2}/\d{2}/", href):
                items.append({
                    "source": "xinhua",
                    "original_url": href,
                    "original_title": title,
                    "original_content": "",
                    "published_at": None,
                })

        return items

    def crawl_people(self) -> list[dict]:
        """Crawl People's Daily Finance (인민일보 재경)."""
        items = []
        url = "http://finance.people.com.cn/"
        html = self.fetch_url(url)
        if not html:
            return items

        soup = BeautifulSoup(html, "lxml")
        for link in soup.select("a[href*='people.com.cn']")[:MAX_NEWS_PER_SOURCE * 2]:
            href = link.get("href", "")
            title = link.get_text(strip=True)

            if not href or not title or len(title) < 10:
                continue
            if not href.startswith("http"):
                href = urljoin(url, href)
            # Match patterns like /n1/2025/0125/ or /n1/2024/1230/
            if re.search(r"/n\d+/\d{4}/\d{2,4}/", href) and ".htm" in href:
                items.append({
                    "source": "people",
                    "original_url": href,
                    "original_title": title,
                    "original_content": "",
                    "published_at": None,
                })

        return items

    def crawl_ce(self) -> list[dict]:
        """Crawl China Economic Daily (경제일보)."""
        items = []
        url = "http://www.ce.cn/"
        try:
            response = self.session.get(url, timeout=REQUEST_TIMEOUT)
            response.encoding = "utf-8"
            html = response.text
        except requests.RequestException as e:
            logger.error(f"Failed to fetch {url}: {e}")
            return items

        soup = BeautifulSoup(html, "lxml")
        for link in soup.select("a[href*='.ce.cn']")[:MAX_NEWS_PER_SOURCE * 2]:
            href = link.get("href", "")
            title = link.get_text(strip=True)

            if not href or not title or len(title) < 5:
                continue
            if not href.startswith("http"):
                href = urljoin(url, href)
            # Match patterns like /202601/t20260123_2723689.shtml
            if re.search(r"/\d{6}/t\d{8}_\d+\.shtml", href):
                items.append({
                    "source": "ce",
                    "original_url": href,
                    "original_title": title,
                    "original_content": "",
                    "published_at": None,
                })

        return items

    def crawl_stcn(self) -> list[dict]:
        """Crawl Securities Times (증권시보)."""
        items = []
        url = "https://www.stcn.com/"
        html = self.fetch_url(url)
        if not html:
            return items

        soup = BeautifulSoup(html, "lxml")
        for link in soup.select("a[href*='stcn.com']")[:MAX_NEWS_PER_SOURCE * 2]:
            href = link.get("href", "")
            title = link.get_text(strip=True)

            if not href or not title or len(title) < 10:
                continue
            if not href.startswith("http"):
                href = urljoin(url, href)
            # Match article URLs with date patterns
            if (re.search(r"/article/", href) or
                re.search(r"/\d{8}/", href) or
                re.search(r"/djjd\d+/", href)):
                items.append({
                    "source": "stcn",
                    "original_url": href,
                    "original_title": title,
                    "original_content": "",
                    "published_at": None,
                })

        return items

    def crawl_caixin(self) -> list[dict]:
        """Crawl Caixin (차이신) - Independent financial media."""
        items = []
        seen_urls = set()

        # Crawl multiple Caixin sections
        sections = [
            "https://finance.caixin.com/",
            "https://companies.caixin.com/",
            "https://www.caixin.com/business/",
        ]

        for section_url in sections:
            html = self.fetch_url(section_url)
            if not html:
                continue

            soup = BeautifulSoup(html, "lxml")

            for link in soup.select("a"):
                href = link.get("href", "")
                title = link.get_text(strip=True)

                if not href or not title or len(title) < 10:
                    continue
                if href in seen_urls:
                    continue

                # Match article URLs: /2026-01-26/xxxxx.html
                if re.search(r"/\d{4}-\d{2}-\d{2}/\d+\.html", href):
                    seen_urls.add(href)
                    items.append({
                        "source": "caixin",
                        "original_url": href,
                        "original_title": title,
                        "original_content": "",
                        "published_at": None,
                    })

                    if len(items) >= MAX_NEWS_PER_SOURCE:
                        return items

        return items

    def crawl_huxiu(self) -> list[dict]:
        """Crawl Huxiu (후시우) - Tech media."""
        items = []
        url = "https://www.huxiu.com/"
        html = self.fetch_url(url)
        if not html:
            return items

        soup = BeautifulSoup(html, "lxml")

        # Huxiu article links pattern
        for link in soup.select("a[href*='huxiu.com/article']")[:MAX_NEWS_PER_SOURCE * 2]:
            href = link.get("href", "")
            title = link.get_text(strip=True)

            if not href or not title or len(title) < 8:
                continue
            if not href.startswith("http"):
                href = urljoin(url, href)

            # Match article URLs: /article/xxxxx.html
            if re.search(r"/article/\d+", href):
                items.append({
                    "source": "huxiu",
                    "original_url": href,
                    "original_title": title,
                    "original_content": "",
                    "published_at": None,
                })

        return items

    def crawl_shanghai_gov(self) -> list[dict]:
        """Crawl Shanghai Government (상하이시 정부) - Policy announcements."""
        items = []
        seen_urls = set()
        base_url = "https://www.shanghai.gov.cn"

        # Multiple pages to crawl
        pages = [
            "/nw12344/index.html",  # 정보공개 (Recent Information)
            "/nw4411/index.html",   # 정책문건 (Policy Documents)
        ]

        for page in pages:
            url = base_url + page
            html = self.fetch_url(url)
            if not html:
                continue

            soup = BeautifulSoup(html, "lxml")

            # Find news list items
            for li in soup.select("ul.tadaty-list li, ul.list-date li"):
                link = li.select_one("a")
                if not link:
                    continue

                href = link.get("href", "")
                title = link.get("title") or link.get_text(strip=True)

                if not href or not title or len(title) < 8:
                    continue

                # Build full URL
                if not href.startswith("http"):
                    href = urljoin(base_url, href)

                # Skip duplicates
                if href in seen_urls:
                    continue
                seen_urls.add(href)

                # Match Shanghai gov article patterns
                if re.search(r"/nw\d+/\d{8}/", href) and ".html" in href:
                    # Parse date from URL if possible
                    date_match = re.search(r"/(\d{4})(\d{2})(\d{2})/", href)
                    published_at = None
                    if date_match:
                        try:
                            published_at = datetime(
                                int(date_match.group(1)),
                                int(date_match.group(2)),
                                int(date_match.group(3))
                            )
                        except ValueError:
                            pass

                    items.append({
                        "source": "shanghai_gov",
                        "original_url": href,
                        "original_title": title,
                        "original_content": "",
                        "published_at": published_at,
                    })

                    if len(items) >= MAX_NEWS_PER_SOURCE:
                        return items

        return items

    def crawl_shenzhen_gov(self) -> list[dict]:
        """Crawl Shenzhen Government (선전시 정부) - Industry and IT Bureau."""
        items = []
        seen_urls = set()
        base_url = "http://gxj.sz.gov.cn"

        # Main page has news listed
        html = self.fetch_url(base_url)
        if not html:
            return items

        soup = BeautifulSoup(html, "lxml")

        # Find news links with titles
        for link in soup.select("a[href*='content/post_']"):
            href = link.get("href", "")
            title = link.get("title") or link.get_text(strip=True)

            # Skip non-news links
            if not href or not title or len(title) < 8:
                continue
            if title in ["查看详情", "业务咨询"]:
                continue

            # Build full URL
            if not href.startswith("http"):
                href = urljoin(base_url, href)

            # Skip duplicates
            if href in seen_urls:
                continue
            seen_urls.add(href)

            # Match Shenzhen gov article patterns
            if re.search(r"/content/post_\d+\.html", href):
                items.append({
                    "source": "shenzhen_gov",
                    "original_url": href,
                    "original_title": title,
                    "original_content": "",
                    "published_at": None,
                })

                if len(items) >= MAX_NEWS_PER_SOURCE:
                    break

        # Also try the policy documents list page
        policy_url = "https://gxj.sz.gov.cn/xxgk/xxgkml/zcfgjzcjd/gfxwjcx/index.html"
        html = self.fetch_url(policy_url)
        if html:
            soup = BeautifulSoup(html, "lxml")
            for link in soup.select("a[href*='content/post_']"):
                href = link.get("href", "")
                title = link.get("title") or link.get_text(strip=True)

                if not href or not title or len(title) < 8:
                    continue
                if href in seen_urls:
                    continue

                if not href.startswith("http"):
                    href = urljoin(base_url, href)

                seen_urls.add(href)
                items.append({
                    "source": "shenzhen_gov",
                    "original_url": href,
                    "original_title": title,
                    "original_content": "",
                    "published_at": None,
                })

                if len(items) >= MAX_NEWS_PER_SOURCE:
                    break

        return items

    def crawl_beijing_gov(self) -> list[dict]:
        """Crawl Beijing Government (베이징시 정부) - Policy documents."""
        items = []
        seen_urls = set()
        base_url = "https://www.beijing.gov.cn"

        # Policy documents page
        policy_url = f"{base_url}/zhengce/zhengcefagui/index.html"
        html = self.fetch_url(policy_url)
        if not html:
            return items

        soup = BeautifulSoup(html, "lxml")

        # Find policy links in list items
        for li in soup.select("li"):
            link = li.select_one("a[href*='.html']")
            if not link:
                continue

            href = link.get("href", "")
            title = link.get("title") or link.get_text(strip=True)

            if not href or not title or len(title) < 10:
                continue

            # Build full URL
            if href.startswith("./"):
                href = f"{base_url}/zhengce/zhengcefagui/{href[2:]}"
            elif href.startswith("/"):
                href = base_url + href
            elif not href.startswith("http"):
                href = urljoin(policy_url, href)

            # Skip duplicates
            if href in seen_urls:
                continue
            seen_urls.add(href)

            # Match Beijing gov article patterns
            if re.search(r"/\d{6}/t\d{8}_\d+\.html", href):
                # Parse date from URL
                date_match = re.search(r"/(\d{4})(\d{2})/t(\d{4})(\d{2})(\d{2})_", href)
                published_at = None
                if date_match:
                    try:
                        published_at = datetime(
                            int(date_match.group(3)),
                            int(date_match.group(4)),
                            int(date_match.group(5))
                        )
                    except ValueError:
                        pass

                items.append({
                    "source": "beijing_gov",
                    "original_url": href,
                    "original_title": title,
                    "original_content": "",
                    "published_at": published_at,
                })

                if len(items) >= MAX_NEWS_PER_SOURCE:
                    break

        return items

    def fetch_article_content(self, url: str, source: str = "") -> Optional[str]:
        """Fetch full article content from URL.

        Args:
            url: Article URL
            source: Source key for site-specific parsing

        Returns:
            Article content text or None
        """
        html = self.fetch_url(url)
        if not html:
            return None

        soup = BeautifulSoup(html, "lxml")

        # Site-specific selectors
        site_selectors = {
            "people": ["div.rm_txt_con", "div.content", "div.article"],
            "ce": ["div.TRS_Editor", "div.content", "div.article"],
            "stcn": ["div.txt_content", "div.article-content", "article"],
            "caixin": ["div#Main_Content_Val", "div.article-content", "article"],
            "huxiu": ["div.article-content-wrap", "div.article__content", "article"],
            "36kr": ["div.article-content", "div.content", "article"],
            "shanghai_gov": ["div.Article_content", "div.article-con", "div.zwgk-text", "div.content"],
            "shenzhen_gov": ["div.news_cont_d_wrap", "div.zwgk-text", "div.article-content", "div.content"],
            "beijing_gov": ["div.view TRS_UEDITOR", "div.xl_news", "div.article-content", "div.content"],
        }

        # Get selectors for this source or use defaults
        selectors = site_selectors.get(source, [])
        selectors.extend([
            "div.article-content",
            "div.content",
            "div.article",
            "div#content",
            "article",
            "div.text",
            "div.detail",
        ])

        for selector in selectors:
            content_div = soup.select_one(selector)
            if content_div:
                # Remove unwanted elements
                for tag in content_div.find_all(["script", "style", "nav", "footer", "aside"]):
                    tag.decompose()
                text = content_div.get_text(separator="\n", strip=True)
                if len(text) > 100:
                    return text[:10000]  # Limit to 10k chars

        # Fallback: get all paragraphs
        paragraphs = soup.find_all("p")
        text = "\n".join(p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 20)
        return text[:10000] if len(text) > 100 else None

    def enrich_news_content(self, limit: int = 10) -> int:
        """Fetch full content for news items missing content.

        Args:
            limit: Maximum number of items to enrich

        Returns:
            Number of items enriched
        """
        conn = get_connection()
        cursor = conn.cursor()

        # Get news items without content
        cursor.execute("""
            SELECT id, source, original_url FROM news
            WHERE (original_content IS NULL OR original_content = '')
            ORDER BY collected_at DESC
            LIMIT ?
        """, (limit,))

        items = cursor.fetchall()
        enriched = 0

        for item in items:
            news_id, source, url = item["id"], item["source"], item["original_url"]
            logger.info(f"Fetching content for news {news_id}...")

            content = self.fetch_article_content(url, source)
            if content:
                cursor.execute("""
                    UPDATE news SET original_content = ?, updated_at = ?
                    WHERE id = ?
                """, (content, datetime.now(), news_id))
                enriched += 1
                logger.info(f"  Content fetched: {len(content)} chars")
            else:
                logger.warning(f"  Failed to fetch content")

        conn.commit()
        conn.close()
        return enriched

    def is_relevant_news(self, title: str, content: str = "") -> bool:
        """Check if news is relevant to target industries or economy."""
        text = f"{title} {content}"

        # Industry keywords (high priority)
        for keywords in INDUSTRY_KEYWORDS.values():
            for keyword in keywords:
                if keyword in text:
                    return True

        # General economy keywords (also relevant)
        economy_keywords = [
            "经济", "产业", "科技", "创新", "发展", "投资", "市场",
            "企业", "公司", "政策", "规划", "制造", "工业", "数字",
            "技术", "智能", "绿色", "高质量", "改革", "金融",
        ]
        for keyword in economy_keywords:
            if keyword in text:
                return True

        return False

    def _parse_date(self, date_str: Optional[str]) -> Optional[datetime]:
        """Parse date string to datetime."""
        if not date_str:
            return None
        try:
            from email.utils import parsedate_to_datetime
            return parsedate_to_datetime(date_str)
        except (ValueError, TypeError):
            return None

    def save_news(self, items: list[dict]) -> int:
        """Save news items to database, returns count of new items."""
        conn = get_connection()
        cursor = conn.cursor()
        new_count = 0

        for item in items:
            try:
                cursor.execute("""
                    INSERT OR IGNORE INTO news
                    (source, original_url, original_title, original_content, published_at, collected_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    item["source"],
                    item["original_url"],
                    item["original_title"],
                    item.get("original_content", ""),
                    item.get("published_at"),
                    datetime.now(),
                ))
                if cursor.rowcount > 0:
                    new_count += 1
            except Exception as e:
                logger.error(f"Failed to save news: {e}")

        conn.commit()
        conn.close()
        return new_count

    def crawl_all(self) -> dict:
        """Crawl all enabled sources."""
        results = {"total": 0, "new": 0, "sources": {}}
        sources = get_enabled_sources()

        for source_key, source_info in sources.items():
            logger.info(f"Crawling {source_info['name']}...")
            items = []

            # Use RSS if available
            if source_info.get("rss"):
                items = self.parse_rss(source_info["rss"], source_key)
            else:
                # Use specific crawler
                crawler_method = getattr(self, f"crawl_{source_key}", None)
                if crawler_method:
                    items = crawler_method()
                else:
                    logger.warning(f"No crawler implemented for {source_key}")

            # Filter relevant news
            items = [item for item in items if self.is_relevant_news(item["original_title"])]

            # Remove duplicates by URL
            seen_urls = set()
            unique_items = []
            for item in items:
                if item["original_url"] not in seen_urls:
                    seen_urls.add(item["original_url"])
                    unique_items.append(item)

            # Save to database
            new_count = self.save_news(unique_items)

            results["sources"][source_key] = {
                "collected": len(unique_items),
                "new": new_count,
            }
            results["total"] += len(unique_items)
            results["new"] += new_count

            logger.info(f"  {source_info['name_ko']}: {len(unique_items)}개 수집, {new_count}개 신규")

        return results


def main():
    """Run crawler."""
    from src.database.models import init_db

    # Initialize database
    init_db()

    # Run crawler
    crawler = NewsCrawler()
    results = crawler.crawl_all()

    print(f"\n총 수집: {results['total']}개")
    print(f"신규: {results['new']}개")


if __name__ == "__main__":
    main()

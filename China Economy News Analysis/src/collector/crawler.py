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

        # Non-news URL patterns to skip (department pages, org charts, etc.)
        shenzhen_skip_patterns = ["/jgzn/", "/nsjg/", "/zsjg/", "/ldjs/"]

        # Find news links with titles
        for link in soup.select("a[href*='content/post_']"):
            href = link.get("href", "")
            title = link.get("title") or link.get_text(strip=True)

            # Skip non-news links
            if not href or not title or len(title) < 8:
                continue
            if title in ["查看详情", "业务咨询"]:
                continue

            # Build full URL (force HTTP: HTTPS is broken on this server)
            if not href.startswith("http"):
                href = urljoin(base_url, href)
            href = href.replace("https://gxj.sz.gov.cn", "http://gxj.sz.gov.cn")

            # Skip department/org pages (not news)
            if any(pat in href for pat in shenzhen_skip_patterns):
                continue

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
        # Note: HTTPS is broken on this server (BAD_ECPOINT SSL error), use HTTP
        policy_url = "http://gxj.sz.gov.cn/xxgk/xxgkml/zcfgjzcjd/gfxwjcx/index.html"
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
                href = href.replace("https://gxj.sz.gov.cn", "http://gxj.sz.gov.cn")

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

    def crawl_cls(self) -> list[dict]:
        """Crawl CLS (차이롄셔 财联社)."""
        items = []
        url = "https://www.cls.cn/"
        html = self.fetch_url(url)
        if not html:
            return items

        soup = BeautifulSoup(html, "lxml")
        seen_urls = set()

        for link in soup.select("a[href*='/detail/']")[:MAX_NEWS_PER_SOURCE * 2]:
            href = link.get("href", "")
            title = link.get_text(strip=True)

            if not href or not title or len(title) < 10:
                continue
            if not href.startswith("http"):
                href = urljoin(url, href)
            if href in seen_urls:
                continue

            if re.search(r"/detail/\d+", href):
                seen_urls.add(href)
                items.append({
                    "source": "cls",
                    "original_url": href,
                    "original_title": title,
                    "original_content": "",
                    "published_at": None,
                })

                if len(items) >= MAX_NEWS_PER_SOURCE:
                    break

        return items

    def crawl_jiemian(self) -> list[dict]:
        """Crawl Jiemian News (지에미엔뉴스 界面新闻)."""
        items = []
        url = "https://www.jiemian.com/"
        html = self.fetch_url(url)
        if not html:
            return items

        soup = BeautifulSoup(html, "lxml")
        seen_urls = set()

        for link in soup.select("a[href*='/article/']")[:MAX_NEWS_PER_SOURCE * 2]:
            href = link.get("href", "")
            title = link.get_text(strip=True)

            if not href or not title or len(title) < 10:
                continue
            if not href.startswith("http"):
                href = urljoin(url, href)
            if href in seen_urls:
                continue

            if re.search(r"/article/\d+\.html", href):
                seen_urls.add(href)
                items.append({
                    "source": "jiemian",
                    "original_url": href,
                    "original_title": title,
                    "original_content": "",
                    "published_at": None,
                })

                if len(items) >= MAX_NEWS_PER_SOURCE:
                    break

        return items

    def crawl_yicai(self) -> list[dict]:
        """Crawl Yicai (디이차이징 第一财经)."""
        items = []
        url = "https://www.yicai.com/"
        html = self.fetch_url(url)
        if not html:
            return items

        soup = BeautifulSoup(html, "lxml")
        seen_urls = set()

        for link in soup.select("a[href*='/news/']")[:MAX_NEWS_PER_SOURCE * 2]:
            href = link.get("href", "")
            title = link.get_text(strip=True)

            if not href or not title or len(title) < 10:
                continue
            if not href.startswith("http"):
                href = urljoin(url, href)
            if href in seen_urls:
                continue

            if re.search(r"/news/\d+\.html", href):
                seen_urls.add(href)
                items.append({
                    "source": "yicai",
                    "original_url": href,
                    "original_title": title,
                    "original_content": "",
                    "published_at": None,
                })

                if len(items) >= MAX_NEWS_PER_SOURCE:
                    break

        return items

    def crawl_sina_finance(self) -> list[dict]:
        """Crawl Sina Finance (시나 파이낸스 新浪财经)."""
        items = []
        url = "https://finance.sina.com.cn/"
        html = self.fetch_url(url)
        if not html:
            return items

        soup = BeautifulSoup(html, "lxml")
        seen_urls = set()

        # Links can be absolute or relative paths with doc-xxx.shtml pattern
        for link in soup.select("a"):
            href = link.get("href", "")
            title = link.get_text(strip=True)

            if not href or not title or len(title) < 10:
                continue

            # Match doc-xxx.shtml pattern
            if not re.search(r"/doc-[a-z0-9]+\.shtml", href):
                continue

            if not href.startswith("http"):
                href = urljoin(url, href)
            if href in seen_urls:
                continue

            seen_urls.add(href)
            items.append({
                "source": "sina_finance",
                "original_url": href,
                "original_title": title,
                "original_content": "",
                "published_at": None,
            })

            if len(items) >= MAX_NEWS_PER_SOURCE:
                break

        return items

    def crawl_21jingji(self) -> list[dict]:
        """Crawl 21st Century Business Herald (21세기경제보도 21世纪经济报道)."""
        items = []
        url = "https://www.21jingji.com/"
        html = self.fetch_url(url)
        if not html:
            return items

        soup = BeautifulSoup(html, "lxml")
        seen_urls = set()

        for link in soup.select("a[href*='/article/']")[:MAX_NEWS_PER_SOURCE * 2]:
            href = link.get("href", "")
            title = link.get("title") or link.get_text(strip=True)

            if not href or not title or len(title) < 10:
                continue
            if not href.startswith("http"):
                href = urljoin(url, href)
            if href in seen_urls:
                continue

            # Match /article/YYYYMMDD/section/hash.html
            if re.search(r"/article/\d{8}/\w+/[a-f0-9]+\.html", href):
                seen_urls.add(href)
                # Parse date from URL
                date_match = re.search(r"/article/(\d{4})(\d{2})(\d{2})/", href)
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
                    "source": "21jingji",
                    "original_url": href,
                    "original_title": title,
                    "original_content": "",
                    "published_at": published_at,
                })

                if len(items) >= MAX_NEWS_PER_SOURCE:
                    break

        return items

    def crawl_xinhua_finance(self) -> list[dict]:
        """Crawl Xinhua Finance (신화파이낸스 新华财经)."""
        items = []
        url = "https://www.cnfin.com/"
        html = self.fetch_url(url)
        if not html:
            return items

        soup = BeautifulSoup(html, "lxml")
        seen_urls = set()

        # Links are protocol-relative: //www.cnfin.com/yw-lb/detail/...
        for link in soup.select("a"):
            href = link.get("href", "")
            title = link.get_text(strip=True)

            if not href or not title or len(title) < 10:
                continue

            # Match /detail/YYYYMMDD/id_1.html pattern
            if not re.search(r"/detail/\d{8}/\d+_1\.html", href):
                continue

            if href.startswith("//"):
                href = "https:" + href
            elif not href.startswith("http"):
                href = urljoin(url, href)
            if href in seen_urls:
                continue

            seen_urls.add(href)
            # Parse date from URL
            date_match = re.search(r"/detail/(\d{4})(\d{2})(\d{2})/", href)
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
                "source": "xinhua_finance",
                "original_url": href,
                "original_title": title,
                "original_content": "",
                "published_at": published_at,
            })

            if len(items) >= MAX_NEWS_PER_SOURCE:
                break

        return items

    # =================================================================
    # Week 5: Central Government Sources (중앙정부)
    # =================================================================

    def crawl_gov_cn(self) -> list[dict]:
        """Crawl 中国政府网 (국무원) - 최신 정책."""
        items = []
        seen_urls = set()
        base_url = "https://www.gov.cn"
        page_url = f"{base_url}/zhengce/zuixin/"

        html = self.fetch_url(page_url)
        if not html:
            return items

        soup = BeautifulSoup(html, "lxml")

        for link in soup.select("a"):
            href = link.get("href", "")
            title = link.get("title") or link.get_text(strip=True)

            if not href or not title or len(title) < 10:
                continue

            # 기사 URL 패턴: /content/YYYYMM/content_XXXXXXX.htm
            if not re.search(r"/content/\d{6}/content_\d+\.htm", href):
                continue

            if not href.startswith("http"):
                href = urljoin(page_url, href)
            if href in seen_urls:
                continue
            seen_urls.add(href)

            items.append({
                "source": "gov_cn",
                "original_url": href,
                "original_title": title,
                "original_content": "",
                "published_at": None,
            })

            if len(items) >= MAX_NEWS_PER_SOURCE:
                break

        return items

    def crawl_ndrc(self) -> list[dict]:
        """Crawl 国家发改委 (발개위) - 뉴스 발표 + 정책 발표."""
        items = []
        seen_urls = set()

        pages = [
            "https://www.ndrc.gov.cn/xwdt/xwfb/",    # 新闻发布 (뉴스 발표)
            "https://www.ndrc.gov.cn/xxgk/zcfb/",     # 政策发布 (정책 발표)
        ]

        for page_url in pages:
            html = self.fetch_url(page_url)
            if not html:
                continue

            soup = BeautifulSoup(html, "lxml")

            for link in soup.select("a"):
                href = link.get("href", "")
                title = link.get("title") or link.get_text(strip=True)

                if not href or not title or len(title) < 10:
                    continue

                # 기사 URL 패턴: ./YYYYMM/tYYYYMMDD_XXXXXXX.html (상대경로)
                # 또는 절대경로 /xwdt/xwfb/YYYYMM/tYYYYMMDD_XXXXXXX.html
                if not re.search(r"t\d{8}_\d+\.html", href):
                    continue

                # 상대경로를 page_url 기준으로 해석 (base_url이 아닌 page_url)
                if not href.startswith("http"):
                    href = urljoin(page_url, href)
                if href in seen_urls:
                    continue
                seen_urls.add(href)

                items.append({
                    "source": "ndrc",
                    "original_url": href,
                    "original_title": title,
                    "original_content": "",
                    "published_at": None,
                })

                if len(items) >= MAX_NEWS_PER_SOURCE:
                    return items

        return items

    def crawl_mof(self) -> list[dict]:
        """Crawl 财政部 (재정부) - 재정 뉴스."""
        items = []
        seen_urls = set()
        base_url = "https://www.mof.gov.cn"
        page_url = f"{base_url}/zhengwuxinxi/caizhengxinwen/"

        html = self.fetch_url(page_url)
        if not html:
            return items

        soup = BeautifulSoup(html, "lxml")

        for link in soup.select("a"):
            href = link.get("href", "")
            title = link.get("title") or link.get_text(strip=True)

            if not href or not title or len(title) < 10:
                continue

            # 기사 URL 패턴: /tYYYYMMDD_XXXXXXX.htm
            if not re.search(r"/t\d{8}_\d+\.htm", href):
                continue

            if not href.startswith("http"):
                href = urljoin(page_url, href)
            if href in seen_urls:
                continue
            seen_urls.add(href)

            items.append({
                "source": "mof",
                "original_url": href,
                "original_title": title,
                "original_content": "",
                "published_at": None,
            })

            if len(items) >= MAX_NEWS_PER_SOURCE:
                break

        return items

    def crawl_pboc(self) -> list[dict]:
        """Crawl 中国人民银行 (인민은행) - 정책 소통."""
        items = []
        seen_urls = set()
        base_url = "http://www.pbc.gov.cn"
        # 新闻发布 > 货币政策/金融市场
        page_url = f"{base_url}/goutongjiaoliu/113456/113469/index.html"

        html = self.fetch_url(page_url)
        if not html:
            return items

        soup = BeautifulSoup(html, "lxml")

        for link in soup.select("a"):
            href = link.get("href", "")
            title = link.get("title") or link.get_text(strip=True)

            if not href or not title or len(title) < 10:
                continue

            # 기사 URL 패턴: /XXXXXXXXXXXXXXXXXXX/index.html (19자리 이상 숫자)
            if not re.search(r"/\d{19,}/index\.html", href):
                continue

            if not href.startswith("http"):
                href = urljoin(page_url, href)
            if href in seen_urls:
                continue
            seen_urls.add(href)

            items.append({
                "source": "pboc",
                "original_url": href,
                "original_title": title,
                "original_content": "",
                "published_at": None,
            })

            if len(items) >= MAX_NEWS_PER_SOURCE:
                break

        return items

    def crawl_mofcom(self) -> list[dict]:
        """Crawl 商务部 (상무부) - 뉴스 발표 + 정책 해석."""
        items = []
        seen_urls = set()

        # 메인 페이지 + 인터뷰/발표 서브도메인
        pages = [
            "https://www.mofcom.gov.cn/",
            "http://interview.mofcom.gov.cn/",
        ]

        for page_url in pages:
            html = self.fetch_url(page_url)
            if not html:
                continue

            base = page_url.rstrip("/")
            soup = BeautifulSoup(html, "lxml")

            for link in soup.select("a"):
                href = link.get("href", "")
                title = link.get("title") or link.get_text(strip=True)

                if not href or not title or len(title) < 10:
                    continue

                # 기사 URL 패턴:
                # /art/YYYY/art_XXXXX (정책/공지)
                # /detail/YYYYMM/XXXXX.html (인터뷰/기자회견)
                is_article = (
                    re.search(r"/art/\d{4}/art_\w+", href) or
                    re.search(r"/detail/\d{6}/\w+\.html?", href)
                )
                if not is_article:
                    continue

                if not href.startswith("http"):
                    href = urljoin(base, href)
                if href in seen_urls:
                    continue
                seen_urls.add(href)

                items.append({
                    "source": "mofcom",
                    "original_url": href,
                    "original_title": title,
                    "original_content": "",
                    "published_at": None,
                })

                if len(items) >= MAX_NEWS_PER_SOURCE:
                    return items

        return items

    # =================================================================
    # Week 6: Local Media Sources (지방 언론)
    # =================================================================

    def crawl_bbtnews(self) -> list[dict]:
        """Crawl 北京商报 (베이징상보) - Beijing Business Today."""
        items = []
        seen_urls = set()

        # Main page + finance section
        pages = [
            "http://www.bbtnews.com.cn/",
            "http://www.bbtnews.com.cn/finance/",
        ]

        for page_url in pages:
            html = self.fetch_url(page_url)
            if not html:
                continue

            soup = BeautifulSoup(html, "lxml")

            for link in soup.select("a"):
                href = link.get("href", "")
                title = link.get_text(strip=True)

                if not href or not title or len(title) < 10:
                    continue

                # URL pattern: /YYYY/MMDD/######.shtml
                if not re.search(r"/\d{4}/\d{4}/\d+\.shtml", href):
                    continue

                if not href.startswith("http"):
                    href = urljoin(page_url, href)
                if href in seen_urls:
                    continue
                seen_urls.add(href)

                # Parse date from URL: /YYYY/MMDD/
                date_match = re.search(r"/(\d{4})/(\d{2})(\d{2})/", href)
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
                    "source": "bbtnews",
                    "original_url": href,
                    "original_title": title,
                    "original_content": "",
                    "published_at": published_at,
                })

                if len(items) >= MAX_NEWS_PER_SOURCE:
                    return items

        return items

    def crawl_stdaily(self) -> list[dict]:
        """Crawl 科技日报 (과학기술일보) - Science and Technology Daily."""
        items = []
        seen_urls = set()
        base_url = "http://www.stdaily.com"

        html = self.fetch_url(base_url)
        if not html:
            return items

        soup = BeautifulSoup(html, "lxml")

        for link in soup.select("a"):
            href = link.get("href", "")
            title = link.get_text(strip=True)

            if not href or not title or len(title) < 10:
                continue

            # URL pattern: /web/[section/]YYYY-MM/DD/content_######.html
            if not re.search(r"/content_\d+\.html", href):
                continue

            if not href.startswith("http"):
                href = urljoin(base_url, href)
            if href in seen_urls:
                continue
            seen_urls.add(href)

            # Parse date from URL: /YYYY-MM/DD/
            date_match = re.search(r"/(\d{4})-(\d{2})/(\d{2})/", href)
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
                "source": "stdaily",
                "original_url": href,
                "original_title": title,
                "original_content": "",
                "published_at": published_at,
            })

            if len(items) >= MAX_NEWS_PER_SOURCE:
                break

        return items

    def crawl_cnstock(self) -> list[dict]:
        """Crawl 上海证券报 (상하이증권보) - Shanghai Securities News.

        Extracts articles from __NEXT_DATA__ JSON embedded in the page,
        parsing relative time strings (e.g. '7小时前') into datetimes.
        """
        import json as _json
        from datetime import timedelta

        items = []
        base_url = "https://www.cnstock.com"

        html = self.fetch_url(f"{base_url}/channel/10005")
        if not html:
            return items

        # Extract articles from __NEXT_DATA__ JSON
        next_data_match = re.search(
            r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL
        )
        if not next_data_match:
            # Fallback to HTML parsing
            return self._crawl_cnstock_html(html, base_url)

        try:
            data = _json.loads(next_data_match.group(1))
            article_list = data["props"]["pageProps"]["data"]["pageInfo"]["list"]
        except (ValueError, KeyError):
            return self._crawl_cnstock_html(html, base_url)

        now = datetime.now()
        seen_ids = set()

        for card in article_list:
            # Collect article entries: some cards have childList, others are direct
            entries = card.get("childList", [])
            if not entries and card.get("contId"):
                entries = [card]

            for child in entries:
                cont_id = str(child.get("contId", ""))
                title = child.get("name", "").strip()
                pub_time_str = child.get("pubTime", "")

                if not cont_id or not title or len(title) < 10:
                    continue
                if cont_id in seen_ids:
                    continue
                seen_ids.add(cont_id)

                published_at = self._parse_cnstock_time(pub_time_str, now)

                items.append({
                    "source": "cnstock",
                    "original_url": f"{base_url}/commonDetail/{cont_id}",
                    "original_title": title,
                    "original_content": child.get("summary", ""),
                    "published_at": published_at,
                })

                if len(items) >= MAX_NEWS_PER_SOURCE:
                    return items

        return items

    def _crawl_cnstock_html(self, html: str, base_url: str) -> list[dict]:
        """Fallback HTML-based cnstock crawling."""
        items = []
        seen_urls = set()
        soup = BeautifulSoup(html, "lxml")

        for link in soup.select("a"):
            href = link.get("href", "")
            title = link.get_text(strip=True)
            if not href or not title or len(title) < 10:
                continue
            if not re.search(r"/commonDetail/\d+", href):
                continue
            if not href.startswith("http"):
                href = urljoin(base_url, href)
            if href in seen_urls:
                continue
            seen_urls.add(href)
            items.append({
                "source": "cnstock",
                "original_url": href,
                "original_title": title,
                "original_content": "",
                "published_at": None,
            })
            if len(items) >= MAX_NEWS_PER_SOURCE:
                break
        return items

    @staticmethod
    def _parse_cnstock_time(time_str: str, now: datetime) -> Optional[datetime]:
        """Parse cnstock relative time strings into datetime."""
        from datetime import timedelta

        if not time_str:
            return None

        # "Just now"
        if time_str.strip() == '刚刚':
            return now

        # Relative: '7小时前', '23分钟前', '1天前'
        m = re.match(r'(\d+)\s*分钟前', time_str)
        if m:
            return now - timedelta(minutes=int(m.group(1)))
        m = re.match(r'(\d+)\s*小时前', time_str)
        if m:
            return now - timedelta(hours=int(m.group(1)))
        m = re.match(r'(\d+)\s*天前', time_str)
        if m:
            return now - timedelta(days=int(m.group(1)))

        # Absolute: '2026-01-30' or '2026-01-30 09:35'
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return datetime.strptime(time_str.strip(), fmt)
            except ValueError:
                continue

        return None

    def crawl_sznews(self) -> list[dict]:
        """Crawl 深圳新闻网 (선전뉴스망) - covers 深圳商报 + 深圳晚报."""
        items = []
        seen_urls = set()
        base_url = "https://www.sznews.com"

        html = self.fetch_url(f"{base_url}/news/")
        if not html:
            return items

        soup = BeautifulSoup(html, "lxml")

        for link in soup.select("a"):
            href = link.get("href", "")
            title = link.get_text(strip=True)

            if not href or not title or len(title) < 10:
                continue

            # URL pattern: /news/content/YYYY-MM/DD/content_######.htm
            if not re.search(r"/content/\d{4}-\d{2}/\d{2}/content_\d+\.htm", href):
                continue

            if not href.startswith("http"):
                href = urljoin(base_url, href)
            if href in seen_urls:
                continue
            seen_urls.add(href)

            # Parse date from URL: /YYYY-MM/DD/
            date_match = re.search(r"/(\d{4})-(\d{2})/(\d{2})/", href)
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
                "source": "sznews",
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
            "cls": ["div.detail-content", "div.article-content", "article"],
            "jiemian": ["div.article-content", "div.article-main", "article"],
            "yicai": ["div.m-text", "div.article-content", "article"],
            "sina_finance": ["div.article-content-left", "div#artibody", "div.article"],
            "21jingji": ["div.article-content", "div.txtContent", "article"],
            "xinhua_finance": ["div.detail-content", "div.article-content", "article"],
            # Week 5 중앙정부
            "gov_cn": ["#UCAP-CONTENT", "div.pages_content", "div.article"],
            "ndrc": ["div.TRS_Editor", "div.article_con", "div.content"],
            "mof": ["div.TRS_Editor", "div.content", "article"],
            "pboc": ["div#zoom", "div.content", "article"],
            "mofcom": ["div.article-content", "div.content", "div.TRS_Editor"],
            # Week 6 지방 언론
            "bbtnews": ["div.article-content", "div.content", "article"],
            "stdaily": ["div.content_area", "div.article-content", "div.content", "article"],
            "cnstock": ["div.article-content", "div.content", "article"],
            "sznews": ["div.article-content", "div.content", "article"],
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
        if len(text) > 100:
            return text[:10000]

        # PDF 첨부파일 자동 감지 및 추출 (중앙정부 사이트에 흔함)
        try:
            from src.collector.pdf_extractor import find_pdf_links, extract_pdf_text
            pdf_links = find_pdf_links(html, url)
            if pdf_links:
                logger.info(f"  PDF {len(pdf_links)}개 감지: {url}")
                for pdf_url in pdf_links[:2]:  # 최대 2개 PDF만 시도
                    pdf_text = extract_pdf_text(pdf_url, dict(self.session.headers))
                    if pdf_text and len(pdf_text) > 100:
                        logger.info(f"  PDF 텍스트 추출 성공: {len(pdf_text)}자")
                        return pdf_text
        except Exception as e:
            logger.debug(f"PDF extraction skipped: {e}")

        return None

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
        """Check if news is relevant to target industries or economy.

        Uses a two-tier keyword system:
        - Strong keywords: 1 match is sufficient (specific economic terms)
        - Weak keywords: need 2+ matches (generic terms that appear in non-economic news)
        - Exclusion patterns: reject regardless of keyword matches
        """
        text = f"{title} {content}"

        # Exclusion: reject titles about crime, accidents, disasters, social topics
        exclude_patterns = [
            "死亡", "遇难", "火灾", "地震", "洪水", "暴雨", "塌落", "坍塌",
            "杀人", "犯罪", "被捕", "逮捕", "判刑", "判处", "刑事", "嫌疑人",
            "车祸", "事故", "失联", "溺水", "坠楼",
            "社保如何", "公积金如何", "如何办理", "如何领取",
            "体育", "娱乐", "选秀", "综艺", "明星",
        ]
        for pattern in exclude_patterns:
            if pattern in title:
                return False

        # Tier 1 — Strong keywords: 1 match = relevant
        # Industry-specific keywords
        for keywords in INDUSTRY_KEYWORDS.values():
            for keyword in keywords:
                if keyword in text:
                    return True

        # Strong economy keywords (unambiguously economic)
        strong_keywords = [
            "经济", "GDP", "产业", "金融", "财政", "货币", "利率",
            "投资", "融资", "上市", "IPO", "股价", "债券", "基金",
            "进出口", "贸易", "关税", "汇率", "外资", "外商",
            "制造业", "工业增加值", "PMI", "CPI", "PPI",
            "科创", "独角兽", "营收", "利润", "市值",
            "房地产", "楼市", "土地出让", "保障房", "住房", "保租房",
            "减税", "降费", "专项债", "财政赤字",
        ]
        for keyword in strong_keywords:
            if keyword in text:
                return True

        # Tier 2 — Weak keywords: need 2+ matches
        weak_keywords = [
            "发展", "市场", "企业", "公司", "政策", "规划",
            "创新", "科技", "技术", "数字", "智能", "绿色",
            "高质量", "改革", "工业", "制造",
        ]
        weak_count = sum(1 for keyword in weak_keywords if keyword in text)
        if weak_count >= 2:
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

    @staticmethod
    def _parse_date_from_url(url: str) -> Optional[datetime]:
        """Extract published date from URL patterns.

        Supports:
          - ndrc/mof: tYYYYMMDD_XXXXXXX.html
          - pboc: YYYYMMDDHHMMSS in path segment
          - sina_finance: /YYYY-MM-DD/
          - mofcom: /art/YYYY/art_ (year only, falls back to None)
          - gov_cn: /content_YYYY-MM/DD/ or tYYYYMMDD
        """
        patterns = [
            # tYYYYMMDD (ndrc, mof, gov_cn)
            (r't(\d{4})(\d{2})(\d{2})_', lambda m: datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))),
            # /YYYY-MM-DD/ (sina_finance)
            (r'/(\d{4})-(\d{2})-(\d{2})/', lambda m: datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))),
            # YYYYMMDD as 8-digit segment in path (pboc)
            (r'/(\d{4})(\d{2})(\d{2})\d{8,}/', lambda m: datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))),
            # /YYYYMM/ folder pattern (ndrc, mof fallback)
            (r'/(\d{4})(\d{2})/', lambda m: datetime(int(m.group(1)), int(m.group(2)), 1)),
        ]
        for pattern, builder in patterns:
            m = re.search(pattern, url)
            if m:
                try:
                    dt = builder(m)
                    if datetime(2020, 1, 1) <= dt <= datetime.now():
                        return dt
                except (ValueError, OverflowError):
                    continue
        return None

    def save_news(self, items: list[dict]) -> int:
        """Save news items to database, returns count of new items."""
        conn = get_connection()
        cursor = conn.cursor()
        new_count = 0

        for item in items:
            try:
                # Fallback: extract published_at from URL if not provided
                published_at = item.get("published_at")
                if not published_at:
                    published_at = self._parse_date_from_url(item["original_url"])

                cursor.execute("""
                    INSERT OR IGNORE INTO news
                    (source, original_url, original_title, original_content, published_at, collected_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    item["source"],
                    item["original_url"],
                    item["original_title"],
                    item.get("original_content", ""),
                    published_at,
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

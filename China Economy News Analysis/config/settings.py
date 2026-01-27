"""Application settings and configuration."""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Base paths
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# Database
DATABASE_PATH = os.getenv("DATABASE_PATH", str(DATA_DIR / "news.db"))
BACKUP_PATH = os.getenv("BACKUP_PATH", str(DATA_DIR / "backups"))

# Application
APP_ENV = os.getenv("APP_ENV", "development")
DEBUG = os.getenv("DEBUG", "true").lower() == "true"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Claude API
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "4096"))

# Crawler
CRAWL_INTERVAL_HOURS = int(os.getenv("CRAWL_INTERVAL_HOURS", "1"))
MAX_NEWS_PER_SOURCE = int(os.getenv("MAX_NEWS_PER_SOURCE", "20"))

# Request settings
REQUEST_TIMEOUT = 30
REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# Industry keywords for filtering
INDUSTRY_KEYWORDS = {
    "semiconductor": ["芯片", "半导体", "晶圆", "光刻", "封装测试", "集成电路", "GPU", "CPU"],
    "ai": ["人工智能", "AI", "机器学习", "深度学习", "大模型", "ChatGPT", "算力", "机器人", "GPT", "智能"],
    "new_energy": ["新能源", "电动汽车", "锂电池", "光伏", "风电", "储能", "氢能", "特斯拉", "比亚迪"],
    "bio": ["生物医药", "创新药", "基因", "疫苗", "医疗器械", "细胞治疗"],
    "aerospace": ["航空航天", "卫星", "火箭", "C919", "商飞", "北斗", "无人机"],
    "quantum": ["量子计算", "量子通信", "量子芯片"],
    "materials": ["新材料", "碳纤维", "稀土", "石墨烯"],
    "tech": ["互联网", "科技", "创业", "融资", "独角兽", "科创", "数据", "云计算", "5G", "6G"],
}

# Content type keywords
CONTENT_TYPE_KEYWORDS = {
    "policy": ["政策", "规划", "意见", "通知", "会议", "国务院", "发改委", "工信部"],
    "corporate": ["公司", "企业", "集团", "股份", "有限公司", "上市"],
    "industry": ["行业", "产业", "市场", "趋势", "发展"],
    "market": ["股价", "涨跌", "交易", "融资", "投资"],
    "opinion": ["分析", "评论", "观点", "预测", "展望"],
}

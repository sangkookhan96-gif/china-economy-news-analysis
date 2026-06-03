"""CNI Translator — Papago API 자동 번역 + 수동 번역 지원.

Papago API 연결 완료 (2026-04-02).
URL: https://papago.apigw.ntruss.com/nmt/v1/translation
"""

import os
import re
import hashlib
import logging
import requests
import sqlite3
from datetime import date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dotenv import load_dotenv
load_dotenv()

from src.cni.summary_store import (
    update_translation, get_pending_translations, get_summary,
)
from src.database.kg_models import get_kg_connection

logger = logging.getLogger("cni_translator")

# ── Papago API Config ──
PAPAGO_URL = "https://papago.apigw.ntruss.com/nmt/v1/translation"
PAPAGO_CLIENT_ID = os.getenv("PAPAGO_CLIENT_ID", "")
PAPAGO_CLIENT_SECRET = os.getenv("PAPAGO_CLIENT_SECRET", "")
DAILY_CHAR_LIMIT = 5000000  # Naver Papago 무료 한도: 일 500만 자


# ── API Quota ──

def _get_quota_conn():
    conn = get_kg_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cni_api_quota (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            api_name TEXT NOT NULL,
            usage_date TEXT NOT NULL,
            char_count INTEGER DEFAULT 0,
            call_count INTEGER DEFAULT 0,
            UNIQUE(api_name, usage_date)
        )
    """)
    conn.commit()
    return conn


def check_api_quota(char_count: int) -> bool:
    today = str(date.today())
    conn = _get_quota_conn()
    row = conn.execute(
        "SELECT char_count FROM cni_api_quota WHERE api_name='papago' AND usage_date=?",
        (today,)).fetchone()
    current = row[0] if row else 0
    conn.close()
    return (current + char_count) <= DAILY_CHAR_LIMIT


def record_api_usage(char_count: int):
    today = str(date.today())
    conn = _get_quota_conn()
    existing = conn.execute(
        "SELECT id FROM cni_api_quota WHERE api_name='papago' AND usage_date=?",
        (today,)).fetchone()
    if existing:
        conn.execute("UPDATE cni_api_quota SET char_count=char_count+?, call_count=call_count+1 WHERE id=?",
                     (char_count, existing[0]))
    else:
        conn.execute("INSERT INTO cni_api_quota (api_name, usage_date, char_count, call_count) VALUES ('papago',?,?,1)",
                     (today, char_count))
    conn.commit()
    conn.close()


def get_api_usage_today() -> dict:
    today = str(date.today())
    conn = _get_quota_conn()
    row = conn.execute(
        "SELECT char_count, call_count FROM cni_api_quota WHERE api_name='papago' AND usage_date=?",
        (today,)).fetchone()
    conn.close()
    if row:
        return {"chars": row[0], "calls": row[1], "limit": DAILY_CHAR_LIMIT, "remaining": DAILY_CHAR_LIMIT - row[0]}
    return {"chars": 0, "calls": 0, "limit": DAILY_CHAR_LIMIT, "remaining": DAILY_CHAR_LIMIT}


# ── Papago Translation ──

# ── Translation cache (동일 원문 재번역 차단 → 유료 호출 절감) ──

def _cache_lookup(text: str):
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()
    try:
        conn = get_kg_connection()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS papago_cache (
                zh_hash TEXT PRIMARY KEY, ko TEXT, char_count INTEGER,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        row = conn.execute("SELECT ko FROM papago_cache WHERE zh_hash=?", (h,)).fetchone()
        conn.close()
        return (h, row[0]) if row and row[0] else (h, None)
    except Exception as e:
        logger.warning(f"Papago cache lookup failed: {e}")
        return (h, None)


def _cache_store(h: str, ko: str, char_count: int):
    try:
        conn = get_kg_connection()
        conn.execute(
            "INSERT OR REPLACE INTO papago_cache (zh_hash, ko, char_count) VALUES (?,?,?)",
            (h, ko, char_count))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"Papago cache store failed: {e}")


def papago_translate(text: str) -> str:
    """Papago API 중→한 번역 (동일 원문 캐시 적용)."""
    if not text or not text.strip():
        return ""
    if not PAPAGO_CLIENT_ID or not PAPAGO_CLIENT_SECRET:
        logger.warning("Papago API keys not configured")
        return ""

    # 캐시 적중 시 API 호출/과금 없이 반환
    h, cached = _cache_lookup(text)
    if cached:
        logger.info(f"Papago cache HIT ({len(text)} chars saved)")
        return cached

    char_count = len(text)
    if not check_api_quota(char_count):
        logger.warning(f"Papago quota exceeded")
        return ""

    try:
        resp = requests.post(
            PAPAGO_URL,
            headers={
                "X-NCP-APIGW-API-KEY-ID": PAPAGO_CLIENT_ID,
                "X-NCP-APIGW-API-KEY": PAPAGO_CLIENT_SECRET,
            },
            data={"source": "zh-CN", "target": "ko", "text": text},
            timeout=15,
        )
        resp.raise_for_status()
        result = resp.json()["message"]["result"]["translatedText"]
        record_api_usage(char_count)
        if result:
            _cache_store(h, result, char_count)
        logger.info(f"Papago OK ({char_count} chars)")
        return result
    except Exception as e:
        logger.error(f"Papago API failed: {e}")
        return ""


# ── Public API ──

def save_manual_translation(news_id: int, ko_text: str) -> dict:
    """Save user's manual Korean translation."""
    if not ko_text or not ko_text.strip():
        return {"error": "Empty translation"}
    update_translation(news_id, ko_text.strip())
    return {"status": "ok", "news_id": news_id}


def get_translation_queue(limit: int = 20) -> list[dict]:
    """Get items awaiting translation."""
    return get_pending_translations(limit)


def zh_to_ko_auto(text: str) -> str:
    """Papago API 자동 번역 (활성화됨)."""
    return papago_translate(text)


if __name__ == "__main__":
    print("=== Papago Translation Test ===")
    tests = [
        "人工智能正在改变世界经济格局",
        "国务院发布半导体产业支持新政策，总投资3000亿元",
    ]
    for t in tests:
        result = papago_translate(t)
        print(f"ZH: {t}")
        print(f"KO: {result}")
        print()
    print(f"API Usage: {get_api_usage_today()}")

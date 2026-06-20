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
# NCP가 Papago를 신규 상품으로 이전하면서 엔드포인트가 바뀔 수 있어 env로 override 가능.
# (2026-06: 구 papago.apigw.ntruss.com URL이 신규 상품에서 401 → 신청 상품의 호출 URL로 교체 필요)
PAPAGO_URL = os.getenv("PAPAGO_URL", "https://papago.apigw.ntruss.com/nmt/v1/translation")
PAPAGO_CLIENT_ID = os.getenv("PAPAGO_CLIENT_ID", "")
PAPAGO_CLIENT_SECRET = os.getenv("PAPAGO_CLIENT_SECRET", "")

# ── Qwen(Ollama) 폴백 Config ──
# Papago 장애(401/쿼터/네트워크) 시 빈 문자열 대신 로컬 Qwen으로 zh→ko 번역해
# summary_ko 공백→공개 차단을 막는다. 품질은 Papago 우선이며, 폴백 결과는
# papago_cache에 저장하지 않아 Papago 복구 시 자동으로 Papago로 되돌아간다.
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
# 예산 보호용 일일 문자 한도. 유료 청구 중이므로 무료한도(500만)가 아니라
# 실사용(평소 1~2만자/일)을 충분히 덮으면서 폭주를 막는 값으로 제한.
# PAPAGO_DAILY_CHAR_LIMIT 환경변수로 조정(대량 백필 시 일시 상향). 초과 시
# check_api_quota가 호출을 차단하고, health_monitor가 80% 도달 시 텔레그램 경보.
DAILY_CHAR_LIMIT = int(os.getenv("PAPAGO_DAILY_CHAR_LIMIT", "100000"))


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
    # 쿼터 조회 실패(DB 락 등)는 번역을 막지 않는다 — best-effort, 실패 시 허용.
    today = str(date.today())
    try:
        conn = _get_quota_conn()
        row = conn.execute(
            "SELECT char_count FROM cni_api_quota WHERE api_name='papago' AND usage_date=?",
            (today,)).fetchone()
        current = row[0] if row else 0
        conn.close()
        return (current + char_count) <= DAILY_CHAR_LIMIT
    except Exception as e:
        logger.warning(f"Papago quota check failed(허용): {e}")
        return True


def record_api_usage(char_count: int):
    # 사용량 기록 실패(DB 락 등)는 번역을 실패시키지 않는다 — best-effort.
    today = str(date.today())
    try:
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
    except Exception as e:
        logger.warning(f"Papago usage record failed(무시): {e}")


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


def _qwen_translate(text: str) -> str:
    """Papago 장애 시 폴백 — 로컬 Qwen(Ollama)으로 zh→ko 번역.

    실패 시 빈 문자열 반환(폴백의 폴백 없음). 결과는 캐시에 저장하지 않아
    Papago 복구 시 자동으로 Papago 결과로 되돌아간다.
    """
    if not text or not text.strip():
        return ""
    prompt = (
        "다음 중국어를 자연스러운 한국어로 번역하세요. "
        "설명·따옴표·원문 병기 없이 번역문만 출력합니다.\n\n"
        f"중국어: {text}\n\n한국어:"
    )
    try:
        resp = requests.post(
            OLLAMA_URL,
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False,
                  "options": {"num_predict": 1024, "temperature": 0.2, "num_ctx": 2048}},
            timeout=90,
        )
        resp.raise_for_status()
        result = (resp.json().get("response") or "").strip()
        if result:
            logger.warning(f"Qwen fallback translation OK ({len(text)} chars)")
        return result
    except Exception as e:
        logger.error(f"Qwen fallback translation failed: {e}")
        return ""


def papago_translate(text: str) -> str:
    """Papago API 중→한 번역 (동일 원문 캐시 적용, 실패 시 Qwen 폴백)."""
    if not text or not text.strip():
        return ""
    if not PAPAGO_CLIENT_ID or not PAPAGO_CLIENT_SECRET:
        logger.warning("Papago API keys not configured → Qwen 폴백")
        return _qwen_translate(text)

    # 캐시 적중 시 API 호출/과금 없이 반환
    h, cached = _cache_lookup(text)
    if cached:
        logger.info(f"Papago cache HIT ({len(text)} chars saved)")
        return cached

    char_count = len(text)
    if not check_api_quota(char_count):
        logger.warning(f"Papago quota exceeded → Qwen 폴백")
        return _qwen_translate(text)

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
        # Papago 장애(401/네트워크 등) → 빈 값 대신 Qwen 폴백으로 공개 차단 방지
        logger.error(f"Papago API failed: {e} → Qwen 폴백")
        return _qwen_translate(text)


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

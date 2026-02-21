"""Telegram notification module for system alerts.

Sends alerts via Telegram Bot API when the system encounters failures.
Configure via environment variables:
    TELEGRAM_BOT_TOKEN  - Bot token from @BotFather
    TELEGRAM_CHAT_ID    - Target chat/group ID
"""

import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError
import json

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


def is_configured() -> bool:
    """Check if Telegram credentials are set."""
    return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)


def send_telegram(message: str) -> bool:
    """Send a message via Telegram Bot API.

    Returns True on success, False on failure (never raises).
    """
    if not is_configured():
        logger.warning("Telegram not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing)")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
    }).encode("utf-8")

    try:
        req = Request(url, data=payload, headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                logger.info("Telegram alert sent successfully")
                return True
            logger.error(f"Telegram API returned {resp.status}")
            return False
    except URLError as e:
        logger.error(f"Telegram send failed: {e}")
        return False
    except Exception as e:
        logger.error(f"Telegram unexpected error: {e}")
        return False


# --- Pre-built alert messages ---

def alert_scheduler_down(minutes_since: int):
    """Alert: scheduler has not run recently."""
    send_telegram(
        f"<b>[WARN] Scheduler 미작동</b>\n"
        f"최근 {minutes_since}분간 스케줄러 실행 기록 없음\n"
        f"시각: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"조치: <code>sudo systemctl restart news-scheduler</code>"
    )


def alert_edition_missing(edition: str):
    """Alert: edition selection did not run today."""
    send_telegram(
        f"<b>[WARN] {edition} 에디션 미선정</b>\n"
        f"오늘 {edition} 에디션 선정 뉴스 없음\n"
        f"시각: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"조치: <code>python3 manage.py select --edition {edition}</code>"
    )


def alert_analysis_low(count: int):
    """Alert: too few articles analyzed today."""
    send_telegram(
        f"<b>[WARN] 분석 부족</b>\n"
        f"오늘 분석 완료: {count}건 (최소 5건 미달)\n"
        f"시각: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"Ollama 상태를 확인하세요: <code>curl localhost:11434/api/tags</code>"
    )


def alert_recovery_failed(check_name: str, attempts: int):
    """Alert: auto-recovery failed after multiple attempts."""
    send_telegram(
        f"<b>[FAIL] 자동 복구 실패</b>\n"
        f"항목: {check_name}\n"
        f"연속 실패: {attempts}회\n"
        f"시각: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"수동 점검 필요: <code>python3 manage.py healthcheck</code>"
    )


def alert_custom(title: str, body: str):
    """Send a custom alert."""
    send_telegram(f"<b>{title}</b>\n{body}\n시각: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

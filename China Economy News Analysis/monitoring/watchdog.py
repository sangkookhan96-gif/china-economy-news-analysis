#!/usr/bin/env python3
"""Watchdog: 10분마다 실행되는 자동 복구 헬스체크.

점검 항목:
  1. 스케줄러: 최근 70분 이내 수집 기록 (hourly 주기 + 10분 여유)
  2. 에디션: 당일 기대 에디션 선정 여부 (복수 동시 체크)
  3. 분석: 오늘 5건 이상 분석 완료 (09:00 이후)

복구 전략:
  1회 실패  → 자동 재시작/재선정 시도
  2회+ 연속 → Telegram 알림
  3회+ 연속 → 심각 알림 추가
"""

import json
import logging
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from monitoring.notifier import (
    alert_scheduler_down,
    alert_edition_missing,
    alert_analysis_low,
    alert_recovery_failed,
)

LOG_DIR = PROJECT_DIR / "logs"
DB_PATH = PROJECT_DIR / "data" / "news.db"
STATE_FILE = PROJECT_DIR / "monitoring" / ".watchdog_state.json"

LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [watchdog] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("watchdog")


# ─── State persistence (연속 실패 횟수 추적) ─────────────────────────────────

def load_state() -> dict:
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text())
    except Exception:
        pass
    return {}


def save_state(state: dict):
    try:
        STATE_FILE.write_text(json.dumps(state, indent=2, default=str))
    except Exception as e:
        logger.error(f"State save failed: {e}")


def inc_fail(state: dict, key: str) -> int:
    state[key] = state.get(key, 0) + 1
    save_state(state)
    return state[key]


def clear_fail(state: dict, key: str):
    if state.pop(key, None) is not None:
        save_state(state)


# ─── DB helper ───────────────────────────────────────────────────────────────

def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


# ─── Health Checks ────────────────────────────────────────────────────────────

def check_scheduler(state: dict) -> bool:
    """최근 70분 이내 수집 기록 확인 (hourly 주기 + 10분 여유)."""
    try:
        conn = get_db()
        threshold = (datetime.now() - timedelta(minutes=70)).strftime("%Y-%m-%d %H:%M:%S")
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM news WHERE collected_at >= ?", (threshold,)
        ).fetchone()
        conn.close()
        count = row["cnt"]
    except Exception as e:
        logger.error(f"DB error in check_scheduler: {e}")
        return True  # DB 오류는 스케줄러 문제가 아님

    if count > 0:
        logger.info(f"Scheduler OK: {count}건 수집 (최근 70분)")
        clear_fail(state, "scheduler")
        return True

    fails = inc_fail(state, "scheduler")
    logger.warning(f"Scheduler FAIL #{fails}: 최근 70분간 수집 0건")

    if fails == 1:
        logger.info("자동 복구: 스케줄러 재시작 시도...")
        _restart_scheduler()
    else:
        alert_scheduler_down(70)
        if fails >= 3:
            alert_recovery_failed("scheduler", fails)

    return False


def check_editions(state: dict) -> bool:
    """당일 기대 에디션 선정 여부 확인 (복수 에디션 동시 체크)."""
    now = datetime.now()
    hour = now.hour
    today = now.strftime("%Y-%m-%d")

    # 에디션별 마감 + 1시간 이후부터 체크
    due = []
    if hour >= 8:    # morning: 07:00 → 08:00 이후 체크
        due.append("morning")
    if hour >= 15:   # afternoon: 14:00 → 15:00 이후 체크
        due.append("afternoon")
    if hour >= 21:   # evening: 20:00 → 21:00 이후 체크
        due.append("evening")

    if not due:
        return True

    all_ok = True
    try:
        conn = get_db()
        for edition in due:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM news WHERE edition=? AND DATE(updated_at)=?",
                (edition, today),
            ).fetchone()
            count = row["cnt"]
            key = f"edition_{edition}"

            if count > 0:
                logger.info(f"Edition {edition} OK: {count}건")
                clear_fail(state, key)
            else:
                fails = inc_fail(state, key)
                logger.warning(f"Edition {edition} FAIL #{fails}: 오늘 선정 0건")

                if fails == 1:
                    logger.info(f"자동 복구: {edition} 선정 재실행...")
                    _run_selection(edition)
                else:
                    alert_edition_missing(edition)
                    if fails >= 3:
                        alert_recovery_failed(f"edition_{edition}", fails)

                all_ok = False
        conn.close()
    except Exception as e:
        logger.error(f"DB error in check_editions: {e}")
        return True

    return all_ok


def check_analysis(state: dict) -> bool:
    """오늘 5건 이상 분석 완료 여부 (09:00 이후 체크)."""
    if datetime.now().hour < 9:
        return True

    try:
        conn = get_db()
        today = datetime.now().strftime("%Y-%m-%d")
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM news WHERE DATE(analyzed_at)=?", (today,)
        ).fetchone()
        conn.close()
        count = row["cnt"]
    except Exception as e:
        logger.error(f"DB error in check_analysis: {e}")
        return True

    if count >= 5:
        logger.info(f"Analysis OK: {count}건 분석 완료")
        clear_fail(state, "analysis")
        return True

    fails = inc_fail(state, "analysis")
    logger.warning(f"Analysis FAIL #{fails}: {count}/5건 (부족)")

    if fails >= 2:
        alert_analysis_low(count)

    return False


# ─── Recovery Actions ─────────────────────────────────────────────────────────

def _restart_scheduler():
    """스케줄러 재시작: systemd 우선, 실패 시 직접 프로세스 재시작."""
    # 1차: systemd restart
    try:
        result = subprocess.run(
            ["sudo", "systemctl", "restart", "news-scheduler"],
            capture_output=True, timeout=30,
        )
        if result.returncode == 0:
            logger.info("스케줄러 재시작 완료 (systemd)")
            return
    except Exception as e:
        logger.warning(f"systemctl restart 실패: {e}")

    # 2차: 직접 프로세스 재시작 (fallback)
    logger.info("fallback: 직접 스케줄러 재시작...")
    subprocess.run(["pkill", "-f", "scheduler_agent.py"], capture_output=True)
    time.sleep(2)
    log_path = LOG_DIR / "scheduler.log"
    with open(log_path, "a") as lf:
        subprocess.Popen(
            [sys.executable, "src/agents/scheduler_agent.py"],
            cwd=str(PROJECT_DIR),
            stdout=lf,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env={**os.environ, "HOME": "/home/jeozeohan"},
        )
    logger.info("스케줄러 재시작 완료 (직접 실행)")


def _run_selection(edition: str):
    """에디션 선정 직접 실행 (lookback 3일)."""
    log_path = LOG_DIR / f"daily_news_{edition}_watchdog.log"
    try:
        env = {
            **os.environ,
            "HOME": "/home/jeozeohan",
            "PYTHONPATH": "/home/jeozeohan/.local/lib/python3.10/site-packages",
        }
        result = subprocess.run(
            [
                sys.executable,
                "src/agents/daily_news_selector.py",
                "--edition", edition,
                "--lookback-days", "3",
            ],
            cwd=str(PROJECT_DIR),
            capture_output=True,
            timeout=180,
            text=True,
            env=env,
        )
        with open(log_path, "a") as f:
            f.write(f"\n[{datetime.now()}] watchdog 자동 복구: {edition}\n")
            if result.stdout:
                f.write(result.stdout)
            if result.stderr:
                f.write(result.stderr)

        if result.returncode == 0:
            logger.info(f"{edition} 선정 복구 성공")
        else:
            logger.error(f"{edition} 선정 복구 실패 (rc={result.returncode})")
    except subprocess.TimeoutExpired:
        logger.error(f"{edition} 선정 타임아웃 (180s)")
    except Exception as e:
        logger.error(f"{edition} 선정 오류: {e}")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    logger.info("=" * 60)
    logger.info(f"Watchdog 헬스체크 시작: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    state = load_state()

    checks = {
        "scheduler": check_scheduler(state),
        "editions":  check_editions(state),
        "analysis":  check_analysis(state),
    }

    ok = sum(checks.values())
    total = len(checks)
    failed = [k for k, v in checks.items() if not v]

    if ok == total:
        logger.info(f"결과: ALL OK ({ok}/{total})")
    else:
        logger.warning(f"결과: {ok}/{total} 통과 | 실패: {failed}")

    logger.info("=" * 60)


if __name__ == "__main__":
    main()

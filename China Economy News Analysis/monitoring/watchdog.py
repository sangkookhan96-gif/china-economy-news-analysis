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
import socket
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

# 신규 수집 0건을 장애로 보기까지의 허용 시간. 야간 저발행 구간과 긴 분석
# 사이클을 견디도록 70 → 180분으로 완화 (과민 재시작 방지).
SCHEDULER_STALE_MINUTES = 180


def _scheduler_process_alive() -> bool:
    """systemd 기준 스케줄러 프로세스가 살아있는지 확인."""
    try:
        r = subprocess.run(
            ["systemctl", "is-active", "news-scheduler"],
            capture_output=True, text=True, timeout=10,
        )
        return r.stdout.strip() == "active"
    except Exception as e:
        logger.warning(f"is-active 확인 실패: {e}")
        return True  # 판단 불가 시 살아있다고 간주 → 불필요한 재시작 방지


def check_scheduler(state: dict) -> bool:
    """최근 수집 기록 확인.

    핵심: '신규 0건'만으로 재시작하지 않는다. 프로세스가 살아있으면 야간
    저발행/긴 분석 사이클일 뿐이며, 재시작은 진행 중인 분석·CNI 작업을
    버리고 ~90초 LLM 호출에 블로킹돼 타임아웃만 유발한다. 따라서
    **프로세스가 실제로 죽었을 때만** 재시작한다.
    """
    try:
        conn = get_db()
        threshold = (datetime.now() - timedelta(minutes=SCHEDULER_STALE_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM news WHERE collected_at >= ?", (threshold,)
        ).fetchone()
        conn.close()
        count = row["cnt"]
    except Exception as e:
        logger.error(f"DB error in check_scheduler: {e}")
        return True  # DB 오류는 스케줄러 문제가 아님

    if count > 0:
        logger.info(f"Scheduler OK: {count}건 수집 (최근 {SCHEDULER_STALE_MINUTES}분)")
        clear_fail(state, "scheduler")
        return True

    alive = _scheduler_process_alive()
    fails = inc_fail(state, "scheduler")
    logger.warning(
        f"Scheduler FAIL #{fails}: 최근 {SCHEDULER_STALE_MINUTES}분 수집 0건 "
        f"(process_alive={alive})"
    )

    if not alive:
        # 프로세스가 실제로 죽은 경우에만 복구 재시작
        if fails == 1:
            logger.info("프로세스 다운 감지 → 스케줄러 재시작 시도...")
            _restart_scheduler()
        else:
            alert_scheduler_down(SCHEDULER_STALE_MINUTES)
            if fails >= 3:
                alert_recovery_failed("scheduler", fails)
    else:
        # 살아있는데 신규 0건 → 재시작 금지(작업 보호). 장기 지속 시에만 알림.
        logger.info("프로세스 정상 가동 중 → 재시작 생략 (신규 0건은 저발행/분석지연 가능성)")
        if fails >= 3:
            alert_scheduler_down(SCHEDULER_STALE_MINUTES)

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


def check_web_server(state: dict) -> bool:
    """포트 8502 응답 확인 → 실패 시 news-web.service 재시작."""
    try:
        sock = socket.create_connection(("127.0.0.1", 8502), timeout=5)
        sock.close()
        logger.info("Web server OK: port 8502 응답")
        clear_fail(state, "web_server")
        return True
    except OSError:
        pass

    fails = inc_fail(state, "web_server")
    logger.warning(f"Web server FAIL #{fails}: port 8502 응답 없음")

    # 자동 복구: systemd restart
    try:
        result = subprocess.run(
            ["sudo", "systemctl", "restart", "news-web"],
            capture_output=True, timeout=30,
        )
        if result.returncode == 0:
            logger.info("웹서버 재시작 완료 (systemd)")
        else:
            logger.error(f"웹서버 재시작 실패: {result.stderr.decode()}")
    except Exception as e:
        logger.error(f"웹서버 재시작 오류: {e}")

    if fails >= 2:
        from monitoring.notifier import send_telegram
        send_telegram(f"⚠️ 웹서버 다운 {fails}회 연속\nport 8502 응답 없음 — 자동 재시작 시도 중")

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
        # NOTE: python3.10 PYTHONPATH 주입 금지 — /usr/bin/python3는 3.12이며
        # 3.10-ABI lxml이 shadow되어 크롤/선정이 깨진다 (2026-05-28 장애 원인).
        env = {
            **os.environ,
            "HOME": "/home/jeozeohan",
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
        "scheduler":  check_scheduler(state),
        "editions":   check_editions(state),
        "analysis":   check_analysis(state),
        "web_server": check_web_server(state),
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

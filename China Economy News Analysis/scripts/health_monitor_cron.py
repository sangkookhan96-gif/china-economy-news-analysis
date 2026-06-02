"""일일 헬스 모니터 (cron용).

1) monitor_health.py 스냅샷을 logs/health_trend.log 에 누적
2) 당일 지표가 이상치면 Telegram 1줄 경보

이상치 기준: 당일 선정계 < 15(정상 ~30) | CNI 건당 중앙값 > 120s | 분석 건당 > 90s
"""
import os, sys, subprocess, sqlite3, statistics, glob, re
from datetime import datetime, timedelta

ROOT = "/home/jeozeohan/vibe_temp/China Economy News Analysis"
DB = os.path.join(ROOT, "data", "news.db")
TREND = os.path.join(ROOT, "logs", "health_trend.log")

# --- .env 로드(텔레그램 자격) ---
try:
    for line in open(os.path.join(ROOT, ".env")):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())
except Exception:
    pass

# --- 1) 스냅샷 누적 ---
snap = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "monitor_health.py"), "7"],
                      capture_output=True, text=True).stdout
with open(TREND, "a") as f:
    f.write(f"\n\n##### {datetime.now().strftime('%Y-%m-%d %H:%M')} #####\n{snap}")

# --- 2) 당일 이상치 판정 ---
con = sqlite3.connect(DB); con.row_factory = sqlite3.Row; c = con.cursor()
today = c.execute("SELECT max(date(collected_at)) d FROM news").fetchone()["d"]

seltot = c.execute(
    "SELECT count(*) FROM news WHERE date(updated_at)=? AND edition IS NOT NULL "
    "AND pipeline_status IN ('selected','translated','published')", (today,)).fetchone()[0]

# CNI 건당(perf 로그 당일)
pv = []
pf = os.path.join(ROOT, "logs", f"perf_{today.replace('-','')}.log")
if os.path.exists(pf):
    pv = [int(m.group(1)) for m in (re.search(r"total=(\d+)s", l) for l in open(pf)) if m]
cni_med = statistics.median(pv) if pv else 0

# 분석 건당(analyzed_at 간격 당일)
rows = [r[0] for r in c.execute(
    "SELECT analyzed_at FROM news WHERE date(analyzed_at)=? AND analyzed_at IS NOT NULL ORDER BY analyzed_at",
    (today,)).fetchall()]
ts = [datetime.fromisoformat(x) for x in rows]
gaps = [g for g in ((ts[i]-ts[i-1]).total_seconds() for i in range(1, len(ts))) if 0 < g < 300]
ana_med = statistics.median(gaps) if gaps else 0
con.close()

alerts = []
if seltot < 15:
    alerts.append(f"추천 선정계 {seltot}건(정상 ~30)")
if cni_med > 120:
    alerts.append(f"CNI 건당 {cni_med:.0f}s(>120)")
if ana_med > 90:
    alerts.append(f"분석 건당 {ana_med:.0f}s(>90)")

status = (f"⚠️ [헬스] {today} 이상: " + " / ".join(alerts)) if alerts else \
         f"✅ [헬스] {today} 정상 — 선정 {seltot}건, CNI {cni_med:.0f}s, 분석 {ana_med:.0f}s"
print(status)

if alerts and os.environ.get("MONITOR_DRY") != "1":
    try:
        sys.path.insert(0, ROOT)
        from monitoring.notifier import send_telegram
        send_telegram(status)
    except Exception as e:
        print(f"telegram 실패: {e}")

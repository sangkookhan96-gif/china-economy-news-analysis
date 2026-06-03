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

# --- 번역 품질: 당일 신규 항목의 4개 오류 ---
import re as _re
_PERSP = _re.compile(r"우리나라|우리\s*(정부|기업|업계|산업|시장|경제|군|측|회사|은행|국민)|자국|我国|我國|我们")
def _has_cjk(t): return bool(_re.search(r"[一-鿿]", _re.sub(r"\([一-鿿].*?\)", "", t or "")))
con2 = sqlite3.connect(DB); con2.row_factory = sqlite3.Row; cc = con2.cursor()
tvals = [r[0] for r in cc.execute(
    "SELECT summary FROM news WHERE summary IS NOT NULL AND summary!='' "
    "AND date(analyzed_at)=?", (today,)).fetchall()]
con2.close()
persp_err = sum(1 for v in tvals if _PERSP.search(v))
cjk_err = sum(1 for v in tvals if _has_cjk(v))
cjk_rate = (100 * cjk_err / len(tvals)) if tvals else 0

alerts = []
if seltot < 15:
    alerts.append(f"추천 선정계 {seltot}건(정상 ~30)")
if cni_med > 120:
    alerts.append(f"CNI 건당 {cni_med:.0f}s(>120)")
if ana_med > 90:
    alerts.append(f"분석 건당 {ana_med:.0f}s(>90)")
if persp_err > 0:
    alerts.append(f"⚠️정치-시점오류 {persp_err}건(우리나라/我国 — 0이어야 함)")
if cjk_rate > 5:
    alerts.append(f"중국어 잔류 {cjk_rate:.0f}%(>5%)")

status = (f"⚠️ [헬스] {today} 이상: " + " / ".join(alerts)) if alerts else \
         (f"✅ [헬스] {today} 정상 — 선정 {seltot}건, CNI {cni_med:.0f}s, 분석 {ana_med:.0f}s, "
          f"시점오류 {persp_err}건, 중국어 {cjk_rate:.0f}%")
print(status)

if alerts and os.environ.get("MONITOR_DRY") != "1":
    try:
        sys.path.insert(0, ROOT)
        from monitoring.notifier import send_telegram
        send_telegram(status)
    except Exception as e:
        print(f"telegram 실패: {e}")

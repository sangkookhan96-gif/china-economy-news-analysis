"""일일 헬스 스냅샷 — 추천 건수 / CNI 처리시간 / 분석시간 / 수집 / 재시작 추이.

사용: python3 scripts/monitor_health.py [days]   (기본 7일)
읽기 전용 (DB·로그 조회만, 변경 없음).
"""
import sys, os, glob, re, sqlite3, statistics
from datetime import datetime, timedelta

ROOT = "/home/jeozeohan/vibe_temp/China Economy News Analysis"
DB = os.path.join(ROOT, "data", "news.db")
DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 7

# 기준 '오늘'은 DB 최신 타임스탬프 기준(테스트 환경 날짜 안정성)
con = sqlite3.connect(DB); con.row_factory = sqlite3.Row; c = con.cursor()
maxd = c.execute("SELECT max(date(collected_at)) d FROM news").fetchone()["d"]
today = datetime.fromisoformat(maxd) if maxd else datetime.now()
dates = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(DAYS - 1, -1, -1)]

def q1(sql, *a):
    return c.execute(sql, a).fetchone()[0]

# --- perf 로그(건당 CNI 처리시간) 일자별 ---
perf_by_day = {}
for f in glob.glob(os.path.join(ROOT, "logs", "perf_*.log")):
    m = re.search(r"perf_(\d{8})\.log", f)
    if not m:
        continue
    d = f"{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:]}"
    vals = []
    try:
        for line in open(f):
            mm = re.search(r"total=(\d+)s", line)
            if mm:
                vals.append(int(mm.group(1)))
    except Exception:
        pass
    if vals:
        perf_by_day[d] = vals

print("=" * 92)
print(f" 헬스 추이  (기준일 {today.strftime('%Y-%m-%d')}, 최근 {DAYS}일)   생성 {datetime.now().strftime('%H:%M:%S')}")
print("=" * 92)
hdr = f"{'날짜':<12}{'수집':>6}{'분석':>6}│{'아침':>5}{'오후':>5}{'저녁':>5}{'선정계':>6}{'발행':>6}│{'CNI건당(중앙/평균/n)':>22}│{'분석건당':>9}"
print(hdr)
print("-" * 92)
for d in dates:
    coll = q1("SELECT count(*) FROM news WHERE date(collected_at)=?", d)
    ana = q1("SELECT count(*) FROM news WHERE date(analyzed_at)=?", d)
    eds = {}
    for ed in ("morning", "afternoon", "evening"):
        eds[ed] = q1("SELECT count(*) FROM news WHERE date(updated_at)=? AND edition=? AND pipeline_status IN ('selected','translated','published')", d, ed)
    seltot = q1("SELECT count(*) FROM news WHERE date(updated_at)=? AND pipeline_status IN ('selected','translated','published') AND edition IS NOT NULL", d)
    pub = q1("SELECT count(*) FROM news WHERE date(updated_at)=? AND pipeline_status='published'", d)
    # CNI 처리시간
    pv = perf_by_day.get(d, [])
    cni = f"{statistics.median(pv):.0f}/{sum(pv)/len(pv):.0f}/{len(pv)}" if pv else "-"
    # 분석 건당(analyzed_at 간격 중앙값, 300s 미만)
    rows = [r[0] for r in c.execute("SELECT analyzed_at FROM news WHERE date(analyzed_at)=? AND analyzed_at IS NOT NULL ORDER BY analyzed_at", (d,)).fetchall()]
    ts = [datetime.fromisoformat(x) for x in rows]
    gaps = [(ts[i] - ts[i-1]).total_seconds() for i in range(1, len(ts))]
    gaps = [g for g in gaps if 0 < g < 300]
    anat = f"{statistics.median(gaps):.0f}s" if gaps else "-"
    print(f"{d:<12}{coll:>6}{ana:>6}│{eds['morning']:>5}{eds['afternoon']:>5}{eds['evening']:>5}{seltot:>6}{pub:>6}│{cni:>22}│{anat:>9}")
con.close()
print("-" * 92)
print(" 정상 기대치: 선정계 ~10/판(×3판), CNI 건당 ~20~60s(7b), 분석 ~20~30s, 수집 일 수백건")

"""CNI 처리시간 개선 검증 — 수정 전 vs 후 perf 추이 비교.

수정 적용일(FIX_DATE) 기준으로 perf_*.log의 건당 처리시간(total=)을 분리 집계해
개선 여부를 판정한다. 며칠 후 실행하면 post 표본이 쌓여 효과가 수치로 확인된다.
읽기 전용.
"""
import glob, os, re, statistics

ROOT = "/home/jeozeohan/vibe_temp/China Economy News Analysis"
FIX_DATE = "20260603"   # 7b+한자제거+parallel2+분석컷오프 적용일

by_day = {}
for f in sorted(glob.glob(os.path.join(ROOT, "logs", "perf_*.log"))):
    m = re.search(r"perf_(\d{8})\.log", f)
    if not m:
        continue
    d = m.group(1)
    vals = [int(x.group(1)) for x in (re.search(r"total=(\d+)s", l) for l in open(f)) if x]
    if vals:
        by_day[d] = vals

if not by_day:
    print("perf 데이터 없음")
    raise SystemExit

print(f"{'날짜':<12}{'건수':>6}{'중앙(s)':>9}{'평균(s)':>9}{'최대(s)':>9}")
pre, post = [], []
for d in sorted(by_day):
    v = by_day[d]
    print(f"{d:<12}{len(v):>6}{statistics.median(v):>9.0f}{sum(v)/len(v):>9.0f}{max(v):>9}")
    (post if d >= FIX_DATE else pre).extend(v)

print("-" * 45)
def summ(label, v):
    if v:
        print(f"  {label}: n={len(v)} 중앙 {statistics.median(v):.0f}s 평균 {sum(v)/len(v):.0f}s")
    else:
        print(f"  {label}: (표본 없음)")
summ(f"수정 전(<{FIX_DATE})", pre)
summ(f"수정 후(>={FIX_DATE})", post)
if pre and post:
    pm, qm = statistics.median(pre), statistics.median(post)
    drop = 100 * (pm - qm) / pm if pm else 0
    verdict = "✅ 개선 확인" if qm < pm * 0.8 else ("≈ 유사" if qm < pm * 1.1 else "⚠️ 악화")
    print(f"\n  중앙값 {pm:.0f}s → {qm:.0f}s ({drop:+.0f}%)  → {verdict}")
    if len(post) < 30:
        print(f"  ※ post 표본 {len(post)}건으로 적음 — 며칠 더 누적 후 재확인 권장")

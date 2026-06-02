"""헤드라인 사용자 수정 분석 — card_headline_ai(AI 원본) vs card_headline(최종).

card_headline_ai는 CNI 생성 시점에 한 번 기록되고 전문가 편집은 card_headline만
바꾸므로, 둘이 다르면 그 차이가 곧 '사용자 수정'이다. (2026-06-03 이후 생성분부터
데이터 축적). 주기적으로 실행해 수정 패턴을 보고 프롬프트 개선에 반영한다.

사용: python3 scripts/analyze_headline_edits.py [days]   (기본 60)
읽기 전용.
"""
import sys, os, re, sqlite3, statistics, collections

ROOT = "/home/jeozeohan/vibe_temp/China Economy News Analysis"
DB = os.path.join(ROOT, "data", "news.db")
DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 60

c = sqlite3.connect(DB).cursor()
rows = c.execute(
    "SELECT card_headline_ai, card_headline FROM news "
    "WHERE card_headline_ai IS NOT NULL AND card_headline IS NOT NULL "
    "AND card_headline_ai != '' AND card_headline != '' "
    f"AND date(updated_at) >= date('now','-{DAYS} days')"
).fetchall()

pairs = [(a.strip(), f.strip()) for a, f in rows]
edited = [(a, f) for a, f in pairs if a != f]
n, ne = len(pairs), len(edited)
print(f"AI 원본 보존된 헤드라인: {n}건 | 사용자 수정됨: {ne}건 ({100*ne/n:.0f}%)" if n else
      "아직 card_headline_ai 데이터 없음 — 생성 파이프라인이 신규 항목을 처리하면 축적됩니다.")
if not edited:
    if n:
        print("수정된 쌍이 아직 없습니다 (전부 AI 원본 그대로 승인).")
    raise SystemExit

dl = [len(f) - len(a) for a, f in edited]
noun = re.compile(r'(다|로|며|을|를|에|이|가|의)$')
ai_verbend = sum(1 for a, _ in edited if noun.search(a))
fi_verbend = sum(1 for _, f in edited if noun.search(f))
print(f"\n길이 변화: 평균 {statistics.mean(dl):+.1f}자 (음수=사용자가 줄임)")
print(f"서술형/조사 종결: AI {ai_verbend}건 → 최종 {fi_verbend}건 (사용자가 명사종결로 고치는 경향 확인용)")

# 자주 추가/삭제된 토큰
added, removed = collections.Counter(), collections.Counter()
for a, f in edited:
    aw, fw = set(a.split()), set(f.split())
    for w in fw - aw:
        added[w] += 1
    for w in aw - fw:
        removed[w] += 1
print("\n사용자가 자주 추가한 표현:", [w for w, _ in added.most_common(12)])
print("사용자가 자주 제거한 표현:", [w for w, _ in removed.most_common(12)])
print("\n--- 수정 예시 (최대 10) ---")
for a, f in edited[:10]:
    print(f"  AI : {a}")
    print(f"  수정: {f}\n")

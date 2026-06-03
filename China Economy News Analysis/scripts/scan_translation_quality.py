"""번역 품질 베이스라인 스캔 — 4개 오류 클래스 발생률.

오류: ①정치-시점(우리나라/我国/자국/국내) ②중국어-only(한자비율) ③평어체(비경어체 종결)
④문장중단(미완결). 읽기 전용. 사용: python3 scripts/scan_translation_quality.py [days]
"""
import sys, os, re, sqlite3

ROOT = "/home/jeozeohan/vibe_temp/China Economy News Analysis"
DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 60
c = sqlite3.connect(os.path.join(ROOT, "data", "news.db")).cursor()

PERSPECTIVE = re.compile(r"우리나라|우리\s*(정부|기업|업계|산업|시장|경제|군|측|회사|은행|국민)|자국|我国|我國|我们")
# 평어체 = '다'로 끝나되 경어체 '~니다'(습니다/입니다 등)는 제외
CASUAL_END = re.compile(r"(?<!니)다[.\s]*$")
def has_cjk(t): return bool(re.search(r"[一-鿿]", re.sub(r"\([一-鿿].*?\)", "", t or "")))
def incomplete(t):
    t = (t or "").rstrip().rstrip("\"')')")
    return bool(t) and t[-1] not in ".。!?다요음함임됨"

FIELDS = [("news", "translated_title", False), ("news", "summary", True),
          ("news", "market_impact", True), ("news", "analysis_ko", True),
          ("cni_summaries", "summary_ko", True), ("cni_summaries", "refined_ko", True)]

print(f"=== 번역 품질 스캔 (최근 {DAYS}일) ===")
print(f"{'필드':<26}{'표본':>6}{'시점오류':>8}{'중국어':>7}{'평어체':>7}{'중단':>6}")
for tbl, col, is_sentence in FIELDS:
    try:
        if tbl == "news":
            rows = c.execute(f"SELECT {col} FROM news WHERE {col} IS NOT NULL AND {col}!='' AND date(updated_at)>=date('now','-{DAYS} days')").fetchall()
        else:
            rows = c.execute(f"SELECT {col} FROM cni_summaries WHERE {col} IS NOT NULL AND {col}!=''").fetchall()
    except Exception as e:
        print(f"{tbl}.{col:<20} ERR {e}"); continue
    vals = [r[0] for r in rows]
    n = len(vals)
    if not n:
        print(f"{tbl}.{col:<20}{0:>6}"); continue
    persp = sum(1 for v in vals if PERSPECTIVE.search(v))
    cjk = sum(1 for v in vals if has_cjk(v))
    casual = sum(1 for v in vals if is_sentence and CASUAL_END.search(v)) if is_sentence else 0
    inc = sum(1 for v in vals if is_sentence and incomplete(v)) if is_sentence else 0
    print(f"{tbl+'.'+col:<26}{n:>6}{persp:>8}{cjk:>7}{casual:>7}{inc:>6}")
print("\n(시점오류=우리나라/我国 등, 중국어=괄호병기 외 한자, 평어체=비경어 종결, 중단=미완결 — 문장형 필드만)")

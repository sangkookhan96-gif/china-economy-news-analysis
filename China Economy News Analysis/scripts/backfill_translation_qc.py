"""기존 발행분에 통합 QC(run_qc) 1회 소급 적용.

기본 dry-run(변경 미반영, 요약/샘플 출력). 실제 반영은 --apply.
범위: pipeline_status='published' 뉴스의 translated_title/summary/market_impact +
해당 news_id의 cni_summaries.summary_ko/refined_ko.
"""
import sys, os, sqlite3, collections

ROOT = "/home/jeozeohan/vibe_temp/China Economy News Analysis"
sys.path.insert(0, ROOT)
from src.cni.translation_qc import run_qc

APPLY = "--apply" in sys.argv
LIMIT = next((int(a.split("=")[1]) for a in sys.argv if a.startswith("--limit=")), 0)

con = sqlite3.connect(os.path.join(ROOT, "data", "news.db"), timeout=30)
con.row_factory = sqlite3.Row
cur = con.cursor()
_done = 0  # 증분 commit 카운터 (타임아웃/중단 안전)

NEWS_FIELDS = [("translated_title", "translated_title"), ("summary", "summary"),
               ("market_impact", "market_impact")]
ids = [r["id"] for r in cur.execute(
    "SELECT id FROM news WHERE pipeline_status='published'").fetchall()]
if LIMIT:
    ids = ids[:LIMIT]
print(f"대상 발행 뉴스: {len(ids)}건  (mode={'APPLY' if APPLY else 'DRY-RUN'})")

stats = collections.Counter()
samples = []
for nid in ids:
    row = cur.execute("SELECT translated_title, summary, market_impact FROM news WHERE id=?", (nid,)).fetchone()
    for col, field in NEWS_FIELDS:
        val = row[col]
        if not val:
            continue
        new, iss = run_qc(val, field, nid)
        if new != val:
            for i in iss:
                stats[i.split(":")[0]] += 1
            stats[f"_{col}_changed"] += 1
            if len(samples) < 8:
                samples.append((nid, col, val[:60], new[:60], iss))
            if APPLY:
                cur.execute(f"UPDATE news SET {col}=? WHERE id=?", (new, nid))
    # cni_summaries
    for col in ("summary_ko", "refined_ko"):
        r2 = cur.execute(f"SELECT {col} FROM cni_summaries WHERE news_id=?", (nid,)).fetchone()
        if not r2 or not r2[0]:
            continue
        new, iss = run_qc(r2[0], "summary_ko", nid)
        if new != r2[0]:
            for i in iss:
                stats[i.split(":")[0]] += 1
            stats[f"_{col}_changed"] += 1
            if APPLY:
                cur.execute(f"UPDATE cni_summaries SET {col}=? WHERE news_id=?", (new, nid))
    # 증분 commit — 중단돼도 진행분 보존 + 멱등 재실행 가능
    if APPLY:
        _done += 1
        if _done % 50 == 0:
            con.commit()

if APPLY:
    con.commit()
con.close()

print("\n=== 교정 통계 ===")
for k, v in sorted(stats.items(), key=lambda x: -x[1]):
    print(f"  {k}: {v}")
print("\n=== 샘플 (최대 8) ===")
for nid, col, a, b, iss in samples:
    print(f"  #{nid} {col} {iss}")
    print(f"    전: {a}")
    print(f"    후: {b}")
print(f"\n{'✅ 반영 완료' if APPLY else 'ℹ️ dry-run — 반영하려면 --apply'}")

"""지난 N일(기본 30) 발행분에서 '널리 아는' 엔티티의 중국어 병기를 제거한다.

원칙: 시진핑(习近平)·국무원(国务院)·중국(中国) 등 익숙한 표기는 병기해도 이해도
향상이 없으므로 괄호 병기를 떼어낸다. 식별에 도움 되는 병기(닝더스다이(宁德时代,
CATL) 등)는 그대로 둔다. WELL_KNOWN_ZH(formatter)에 정의된 정확한 zh만 대상.

기본 dry-run. 적용은 --apply. 사용:
  python3 scripts/strip_wellknown_annotations.py --days 30 [--show 8] [--apply]
"""
import sys, os, re, sqlite3, argparse

ROOT = "/home/jeozeohan/vibe_temp/China Economy News Analysis"
sys.path.insert(0, ROOT)
from src.utils.proper_noun_formatter import WELL_KNOWN_ZH

ap = argparse.ArgumentParser()
ap.add_argument("--days", type=int, default=30)
ap.add_argument("--show", type=int, default=8)
ap.add_argument("--apply", action="store_true")
args = ap.parse_args()

# "(zh)" 또는 "(zh, English)" 병기 제거 (괄호 안에 다른 괄호 없음 가정)
_zh_alt = "|".join(sorted((re.escape(z) for z in WELL_KNOWN_ZH), key=len, reverse=True))
STRIP_RE = re.compile(r"\s?\(\s*(?:" + _zh_alt + r")\s*(?:,[^()]*)?\)")

def strip(text):
    if not text:
        return text, False
    new = STRIP_RE.sub("", text)
    return new, new != text

con = sqlite3.connect(os.path.join(ROOT, "data", "news.db"), timeout=30)
con.row_factory = sqlite3.Row
cur = con.cursor()

ids = [r["id"] for r in cur.execute(
    f"""SELECT DISTINCT n.id FROM news n
        LEFT JOIN expert_reviews er ON er.news_id=n.id AND er.publish_status='published'
        LEFT JOIN cni_summaries cs ON cs.news_id=n.id
        WHERE n.updated_at >= datetime('now','-{int(args.days)} days')
          AND (er.id IS NOT NULL OR (n.pipeline_status='published' AND cs.summary_ko IS NOT NULL))
    """).fetchall()]
print(f"대상 발행 뉴스(최근 {args.days}일): {len(ids)}건  mode={'APPLY' if args.apply else 'DRY-RUN'}")

changed = 0; shown = 0; per = {}
for nid in ids:
    diffs = []
    # cni_summaries
    r = cur.execute("SELECT summary_ko, refined_ko FROM cni_summaries WHERE news_id=? ORDER BY id DESC LIMIT 1", (nid,)).fetchone()
    if r:
        for col in ("summary_ko", "refined_ko"):
            nv, ch = strip(r[col])
            if ch:
                diffs.append(("cni_summaries", col, r[col], nv))
                if args.apply:
                    cur.execute(f"UPDATE cni_summaries SET {col}=? WHERE news_id=?", (nv, nid))
    # expert_reviews (공개)
    r2 = cur.execute("SELECT id, expert_comment, ai_comment, ai_final_review FROM expert_reviews WHERE news_id=? AND publish_status='published' ORDER BY id DESC LIMIT 1", (nid,)).fetchone()
    if r2:
        for col in ("expert_comment", "ai_comment", "ai_final_review"):
            nv, ch = strip(r2[col])
            if ch:
                diffs.append(("expert_reviews", col, r2[col], nv))
                if args.apply:
                    cur.execute(f"UPDATE expert_reviews SET {col}=? WHERE id=?", (nv, r2["id"]))
    # news 본문 필드
    r3 = cur.execute("SELECT translated_title, summary, market_impact FROM news WHERE id=?", (nid,)).fetchone()
    for col in ("translated_title", "summary", "market_impact"):
        nv, ch = strip(r3[col])
        if ch:
            diffs.append(("news", col, r3[col], nv))
            if args.apply:
                cur.execute(f"UPDATE news SET {col}=? WHERE id=?", (nv, nid))
    if not diffs:
        continue
    changed += 1
    for t, c, *_ in diffs:
        per[f"{t}.{c}"] = per.get(f"{t}.{c}", 0) + 1
    if not args.apply and shown < args.show:
        print(f"\n--- news id={nid} ---")
        for t, c, b, a in diffs[:3]:
            print(f"  [{t}.{c}]\n    전: ...{b[max(0,b.find('('))-25:b.find('(')+20]}...")
            print(f"    후: (병기 제거됨)")
        shown += 1

if args.apply:
    con.commit()
con.close()
print(f"\n변경 뉴스: {changed}건 | 필드별: {per}")
print("✅ 적용 완료" if args.apply else "ℹ️ dry-run — 적용하려면 --apply")

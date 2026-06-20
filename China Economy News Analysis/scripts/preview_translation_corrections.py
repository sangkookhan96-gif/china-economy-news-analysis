"""번역 교정 미리보기 (dry-run) — DB·발행 텍스트를 변경하지 않는다.

최근 N일간 '공개된' 뉴스의 요약(공개 지면 텍스트 = COALESCE(refined_ko, summary_ko))에
시제·시점 QC 게이트를 돌려 '무엇이 어떻게 교정될지'만 출력한다.
실제 수정/적재는 하지 않는다(run_qc record=False, allow_papago=False).
"""
import sys
sys.path.insert(0, "/home/jeozeohan/vibe_temp/China Economy News Analysis")
from src.database.models import get_connection
from src.cni.translation_qc import run_qc, _compact_diff

DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 2

conn = get_connection()
cur = conn.cursor()
cur.execute(f"""
    SELECT cs.news_id,
           COALESCE(NULLIF(cs.refined_ko, ''), cs.summary_ko) AS pub_text,
           cs.published_at,
           COALESCE(NULLIF(n.card_headline, ''), n.translated_title, n.original_title) AS title
    FROM cni_summaries cs
    JOIN news n ON n.id = cs.news_id
    WHERE cs.published_at IS NOT NULL
      AND datetime(cs.published_at) >= datetime('now', '-{DAYS} days')
      AND COALESCE(NULLIF(cs.refined_ko, ''), cs.summary_ko) IS NOT NULL
    ORDER BY cs.published_at DESC
""")
rows = cur.fetchall()
conn.close()

total_news = len(rows)
hit_news = 0
total_fixes = 0
print(f"=== 번역 교정 미리보기 (최근 {DAYS}일 공개 뉴스 {total_news}건, dry-run) ===\n")

for r in rows:
    nid, text, pub, title = r["news_id"], r["pub_text"], r["published_at"], r["title"]
    if not text:
        continue
    out, issues = run_qc(text, "summary_ko", nid, allow_papago=False, record=False)
    meaningful = [i for i in issues if i.startswith(("tense", "perspective", "cn_self", "political"))]
    if not meaningful or out == text:
        continue
    hit_news += 1
    frags = _compact_diff(text, out)
    total_fixes += len(frags)
    print(f"[#{nid}] 🕒 {str(pub)[:16]}  {str(title)[:40]}")
    print(f"      유형: {', '.join(meaningful)}")
    for b, a in frags:
        print(f"      오류: {b!r}  →  수정: {a!r}")
    print()

print(f"--- 요약: 공개 {total_news}건 중 {hit_news}건에서 교정 후보 {total_fixes}개 발견 (DB 변경 없음) ---")

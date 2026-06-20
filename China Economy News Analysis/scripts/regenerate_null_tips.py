"""팁이 NULL/빈 공개 뉴스의 팁을 재생성 (WAL+busy_timeout 락 수정 후 재시도).

이전 백필이 SQLite 락 경합으로 다수를 NULL 처리한 것을 복구한다.
build_quality_tip 실패(품질 미달) 시에는 NULL 유지(빈 '💡' 발행 방지).
"""
import sys
import time

sys.path.insert(0, "/home/jeozeohan/vibe_temp/China Economy News Analysis")
from src.database.models import get_connection
from src.cni.generate_cni_fields import build_quality_tip, _tip_has_cjk_residue

conn = get_connection()
c = conn.cursor()
c.execute("""SELECT n.id, n.title_zh, n.summary_zh, n.original_title
             FROM news n JOIN cni_summaries cs ON cs.news_id = n.id
             WHERE cs.published_at IS NOT NULL
               AND (n.hansanguk_tip IS NULL OR n.hansanguk_tip = '')
               AND n.summary_zh IS NOT NULL""")
rows = c.fetchall()
print(f"NULL 팁 재생성 대상: {len(rows)}건", flush=True)

upd = still_null = 0
t0 = time.time()
for i, r in enumerate(rows):
    nid = r["id"]
    title = r["title_zh"] or r["original_title"] or ""
    summary = r["summary_zh"] or ""
    try:
        tip = build_quality_tip(nid, title, summary, source_zh=f"{title} {summary}", retries=2)
    except Exception as e:
        print(f"  #{nid} 예외: {e}", flush=True)
        tip = None
    if tip and not _tip_has_cjk_residue(tip) and len(tip.replace("💡", "").strip()) >= 35:
        conn.execute("UPDATE news SET hansanguk_tip=? WHERE id=?", (tip[:500], nid))
        upd += 1
    else:
        still_null += 1
    if (i + 1) % 10 == 0:
        conn.commit()
        el = time.time() - t0
        print(f"  {i+1}/{len(rows)} (생성{upd} 미생성{still_null}) "
              f"{el:.0f}s, ~{el/(i+1)*(len(rows)-i-1):.0f}s 남음", flush=True)

conn.commit()
conn.close()
print(f"완료: 생성 {upd}, 미생성(NULL유지) {still_null} / 총 {len(rows)} "
      f"({time.time()-t0:.0f}s)", flush=True)

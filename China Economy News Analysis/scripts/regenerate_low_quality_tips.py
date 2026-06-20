"""저품질 한상국 팁 재생성 백필.

대상(공개 뉴스): 빈 팁(💡만)·짧음·한자잔류·사전식 일반론.
새 함의중심 프롬프트 + 품질 게이트로 재생성. 실패 시:
  - 원본이 빈/한자잔류/짧음 → NULL로 정리(깨진 팁 제거)
  - 원본이 사전식 → 원본 유지(정의라도 있는 게 나음)
"""
import sys
import time

sys.path.insert(0, "/home/jeozeohan/vibe_temp/China Economy News Analysis")
from src.database.models import get_connection
from src.cni.generate_cni_fields import (
    build_quality_tip, _tip_quality_ok, _tip_has_cjk_residue,
    _TIP_DEF_OPEN, _TIP_DICT_END,
)

conn = get_connection()
c = conn.cursor()
c.execute("""SELECT DISTINCT n.id, n.title_zh, n.summary_zh, n.original_title, n.hansanguk_tip
             FROM news n JOIN cni_summaries cs ON cs.news_id = n.id
             WHERE cs.published_at IS NOT NULL""")
rows = c.fetchall()

target = []
for r in rows:
    tip = r["hansanguk_tip"]
    if not tip:
        continue
    body = tip.replace("💡", "").strip()
    ok, _ = _tip_quality_ok(tip)
    is_dict = bool(_TIP_DEF_OPEN.search(body)) and bool(_TIP_DICT_END.search(body))
    if (not ok) or is_dict:
        target.append(r)

print(f"재생성 대상: {len(target)}건", flush=True)
upd = cleared = kept = 0
t0 = time.time()
for i, r in enumerate(target):
    nid = r["id"]
    title = r["title_zh"] or r["original_title"] or ""
    summary = r["summary_zh"] or ""
    old = r["hansanguk_tip"]
    try:
        new = build_quality_tip(nid, title, summary, source_zh=f"{title} {summary}", retries=2)
    except Exception as e:
        print(f"  #{nid} 예외: {e}", flush=True)
        new = None

    if new and not _tip_has_cjk_residue(new) and len(new.replace("💡", "").strip()) >= 35:
        conn.execute("UPDATE news SET hansanguk_tip=? WHERE id=?", (new[:500], nid))
        upd += 1
    else:
        oldreason = _tip_quality_ok(old)[1]
        if oldreason in ("empty", "too_short", "cjk_residue"):
            conn.execute("UPDATE news SET hansanguk_tip=NULL WHERE id=?", (nid,))
            cleared += 1
        else:
            kept += 1  # 사전식 원본 유지

    if (i + 1) % 10 == 0:
        conn.commit()
        el = time.time() - t0
        print(f"  {i+1}/{len(target)} (갱신{upd} 정리{cleared} 유지{kept}) "
              f"{el:.0f}s, ~{el/(i+1)*(len(target)-i-1):.0f}s 남음", flush=True)

conn.commit()
conn.close()
print(f"완료: 갱신 {upd}, NULL정리 {cleared}, 원본유지 {kept} / 총 {len(target)} "
      f"({time.time()-t0:.0f}s)", flush=True)

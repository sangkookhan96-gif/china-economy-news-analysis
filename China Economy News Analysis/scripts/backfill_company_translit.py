"""신규 등재 기업(음차 교정) 대상 공개분 백필 — 행 단위 커밋(락 장기점유 방지).

format_proper_nouns가 부이위안/무희 등 오음차를 정본으로 교정. 해당 기업이 원문에
등장하는 공개 뉴스만 처리해 범위를 최소화한다(전체 1094건 단일 트랜잭션 금지 — 2026-06-16 교훈).
"""
import sys
import time

sys.path.insert(0, "/home/jeozeohan/vibe_temp/China Economy News Analysis")
from src.database.models import get_connection
from scripts.backfill_proper_nouns import _rewrite_field, PUBLISHED_NEWS_FIELDS, CNI_FIELDS

TARGETS = ["领益", "生益", "佰维", "粤芯", "蓝思", "广发", "东吴", "闻泰", "江淮", "中天"]

conn = get_connection()
c = conn.cursor()
c.execute("""SELECT DISTINCT n.id FROM news n JOIN cni_summaries cs ON cs.news_id = n.id
             WHERE cs.published_at IS NOT NULL""")
ids = [r["id"] for r in c.fetchall()]

# 대상 필터: 원문에 타깃 기업명 포함
def _has_target(nid):
    r = c.execute("SELECT COALESCE(original_title,'')||COALESCE(original_content,'') s FROM news WHERE id=?",
                  (nid,)).fetchone()
    return r and any(t in r["s"] for t in TARGETS)

targets = [nid for nid in ids if _has_target(nid)]
print(f"타깃 기업 언급 공개분: {len(targets)}건", flush=True)

upd = 0
t0 = time.time()
for i, nid in enumerate(targets):
    news = c.execute("""SELECT id, original_title, original_content, translated_title,
                        summary, market_impact, hansanguk_tip FROM news WHERE id=?""", (nid,)).fetchone()
    cni = c.execute("SELECT id, summary_ko, refined_ko FROM cni_summaries WHERE news_id=? ORDER BY id DESC LIMIT 1",
                    (nid,)).fetchone()
    source = f"{news['original_title'] or ''}\n{news['original_content'] or ''}"

    nu = {}
    nchanged = False
    for f in PUBLISHED_NEWS_FIELDS:
        nv, ch = _rewrite_field(news[f], source)
        nu[f] = nv
        nchanged = nchanged or ch
    cu = {}
    cchanged = False
    if cni:
        for f in CNI_FIELDS:
            nv, ch = _rewrite_field(cni[f], source)
            cu[f] = nv
            cchanged = cchanged or ch

    if nchanged:
        conn.execute("UPDATE news SET translated_title=?, summary=?, market_impact=?, hansanguk_tip=? WHERE id=?",
                     (nu["translated_title"], nu["summary"], nu["market_impact"], nu["hansanguk_tip"], nid))
    if cni and cchanged:
        conn.execute("UPDATE cni_summaries SET summary_ko=?, refined_ko=? WHERE id=?",
                     (cu["summary_ko"], cu["refined_ko"], cni["id"]))
    if nchanged or cchanged:
        conn.commit()   # 행 단위 커밋 — 락을 즉시 해제(대시보드 버튼 보호)
        upd += 1
    time.sleep(0.05)    # throttle

conn.close()
print(f"완료: {upd}/{len(targets)} 갱신 ({time.time()-t0:.0f}s)", flush=True)

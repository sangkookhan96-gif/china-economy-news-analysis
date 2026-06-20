"""공개 뉴스 제목 검증 + 수정 제안 리스트 생성 (DB 미수정 — 승인 전 리스트업 전용).

새 제목 작성 원칙(build_headline)으로 모든 공개 뉴스의 제안 제목을 산출해 파일로 출력.
출력: reviews/headline_fix_list_<날짜>.tsv  (id / 현재(len) / 원제 / 제안(len) / 사유)
읽기전용: published 내용 미변경, translation_corrections 미기록(news_id=None → record=False).
"""
import sys
import time

sys.path.insert(0, "/home/jeozeohan/vibe_temp/China Economy News Analysis")
from src.database.models import get_connection
from src.cni.generate_cni_fields import build_headline, MOBILE_HEADLINE_LEN

OUT = sys.argv[1] if len(sys.argv) > 1 else "reviews/headline_fix_list.tsv"

conn = get_connection()
c = conn.cursor()
c.execute("""SELECT n.id, n.original_title ot, n.card_headline ch, n.summary_zh sz,
                    cs.published_at pub
             FROM news n JOIN cni_summaries cs ON cs.news_id = n.id
             WHERE cs.published_at IS NOT NULL AND n.card_headline IS NOT NULL
             ORDER BY cs.published_at DESC""")
rows = c.fetchall()
conn.close()

print(f"검증 대상 공개 헤드라인: {len(rows)}건", flush=True)
n_toolong = n_diff = 0
t0 = time.time()
with open(OUT, "w", encoding="utf-8") as f:
    f.write("news_id\t현재제목\t현재길이\t원제(중)\t제안제목\t제안길이\t사유\n")
    for i, r in enumerate(rows):
        cur = r["ch"] or ""
        try:
            prop = build_headline(r["ot"], r["sz"] or "", "", news_id=None, current=cur) or ""
        except Exception as e:
            prop = f"(생성실패: {e})"
        reasons = []
        if len(cur) > MOBILE_HEADLINE_LEN:
            reasons.append(f"길이초과({len(cur)}>{MOBILE_HEADLINE_LEN})")
            n_toolong += 1
        if prop and prop != cur:
            reasons.append("원제번역과상이")
            n_diff += 1
        reason = ", ".join(reasons) or "-"
        f.write(f"{r['id']}\t{cur}\t{len(cur)}\t{(r['ot'] or '')[:50]}\t{prop}\t{len(prop)}\t{reason}\n")
        if (i + 1) % 50 == 0:
            el = time.time() - t0
            print(f"  {i+1}/{len(rows)} ({el:.0f}s, ~{el/(i+1)*(len(rows)-i-1):.0f}s 남음)", flush=True)

print(f"완료: {len(rows)}건 검증 / 길이초과 {n_toolong} / 원제와상이 {n_diff} → {OUT} ({time.time()-t0:.0f}s)",
      flush=True)

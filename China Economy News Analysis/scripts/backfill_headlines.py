"""길이초과(>24자) 공개 헤드라인 백필 — 새 제목 원칙 적용.

흐름: 결정적 build_headline(원제 번역·사실1개·24/36) 우선. 결과가 '불완전'(액션·수치로
끝나지 않고 중간 잘림)이면 → Qwen으로 한-사실 24자(불가 36) 재작성. 그래도 불완전하면
결정적 결과 유지. 안전: 행단위 커밋·throttle.
"""
import sys
import re
import time

sys.path.insert(0, "/home/jeozeohan/vibe_temp/China Economy News Analysis")
from src.database.models import get_connection
from src.cni.generate_cni_fields import (
    build_headline, _ensure_korean, _fit_headline, _call_ollama,
    MOBILE_HEADLINE_LEN, HEADLINE_RELAX_LEN, _INCOMPLETE_ENDINGS,
)
from src.utils.proper_noun_formatter import format_proper_nouns

# 종결이 조사·연결어미·관형어이면 '중간 잘림'(불완전). 명사·동작명사·수치 종결은 완결로 간주.
_DANGLING_DET = ("새로운", "대형", "최초", "주요", "신규", "관련", "거대", "전체", "첫", "새",
                 "향후", "각종", "대규모", "주력", "핵심", "기존", "일부", "다수", "여러", "주된")


def _complete(h):
    h = (h or "").rstrip().rstrip("\"'").rstrip("!?！？.。")
    if not h or len(h) < 6:
        return False
    if any(h.endswith(e) for e in _INCOMPLETE_ENDINGS):   # 조사/연결어미 종결
        return False
    if any(h.endswith(d) for d in _DANGLING_DET):          # 관형어(명사 기대) 종결
        return False
    return True


def _qwen_refine(current, news_id=None):
    P = ("다음 한국어 뉴스 제목을 더 짧고 명료하게 다시 써라.\n"
         "규칙: 핵심 사실 1개만(두 가지 사실 금지), 24자 이내(꼭 필요할 때만 최대 36자), "
         "한국어만, 수치는 그대로 유지, 문장이 잘리지 않는 완결된 제목.\n"
         f"원제목: {current}\n새 제목:")
    raw = _call_ollama(P, num_predict=50, temperature=0.2)
    if not raw:
        return None
    h = raw.strip().split("\n")[0].strip().strip("\"'").strip()
    h = re.sub(r"^\d+[\.\)]\s*", "", h)
    h = _ensure_korean(h, "headline", news_id or 0)
    try:
        h = format_proper_nouns(h, current or "", max_annotations=0)
    except Exception:
        pass
    return _fit_headline(h)


def main():
    conn = get_connection()
    c = conn.cursor()
    c.execute("""SELECT n.id, n.original_title ot, n.summary_zh sz, n.card_headline ch,
                        COALESCE(NULLIF(cs.refined_ko,''), cs.summary_ko) sko
                 FROM news n JOIN cni_summaries cs ON cs.news_id = n.id
                 WHERE cs.published_at IS NOT NULL AND n.card_headline IS NOT NULL
                   AND LENGTH(n.card_headline) > ?""", (MOBILE_HEADLINE_LEN,))
    rows = c.fetchall()
    print(f"길이초과 백필 대상: {len(rows)}건", flush=True)

    upd = used_qwen = kept = 0
    t0 = time.time()
    for i, r in enumerate(rows):
        cur = r["ch"]
        det = build_headline(r["ot"], r["sz"] or "", r["sko"] or "", news_id=None, current=cur) or ""
        if _complete(det):
            new = det
        else:                                   # 한계 케이스 → Qwen 한-사실 재작성
            q = _qwen_refine(cur, None)
            used_qwen += 1
            new = q if (q and _complete(q) and len(q) <= HEADLINE_RELAX_LEN) else det
        if new and new != cur and 5 <= len(new) <= HEADLINE_RELAX_LEN:
            conn.execute("UPDATE news SET card_headline=? WHERE id=?", (new[:72], r["id"]))
            conn.commit()                       # 행단위 커밋(락 보호)
            upd += 1
        else:
            kept += 1
        time.sleep(0.03)
        if (i + 1) % 50 == 0:
            el = time.time() - t0
            print(f"  {i+1}/{len(rows)} (갱신{upd} Qwen{used_qwen} 유지{kept}) "
                  f"{el:.0f}s, ~{el/(i+1)*(len(rows)-i-1):.0f}s 남음", flush=True)
    conn.close()
    print(f"완료: 갱신 {upd} / Qwen재작성 {used_qwen} / 유지 {kept} / 총 {len(rows)} "
          f"({time.time()-t0:.0f}s)", flush=True)


if __name__ == "__main__":
    main()

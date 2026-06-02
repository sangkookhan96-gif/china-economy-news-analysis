"""14b vs 7b 품질·속도 비교 (비파괴: DB 미기록).

실제 CNI 프로덕션 프롬프트(SUMMARY_PROMPT, TIP_PROMPT)와 _call_ollama를 그대로
사용해 summary_zh / tip 을 두 모델로 생성하고 시간을 측정한다.
"""
import sys, time
sys.path.insert(0, "/home/jeozeohan/vibe_temp/China Economy News Analysis")

from src.database.models import get_connection
from src.cni.generate_cni_fields import (
    SUMMARY_PROMPT, TIP_PROMPT, _call_ollama, _clean_summary,
    _extract_numbers, _ensure_tip_complete,
    TIMEOUT_SUMMARY, TIMEOUT_TIP,
)

MODELS = ["qwen2.5:14b", "qwen2.5:7b"]
N = 2

conn = get_connection()
rows = conn.execute(
    "SELECT id, original_title, original_content FROM news "
    "WHERE original_content IS NOT NULL AND length(original_content) > 400 "
    "ORDER BY collected_at DESC LIMIT ?", (N,)
).fetchall()
conn.close()

for r in rows:
    nid = r["id"]; title = r["original_title"] or ""
    text = (r["original_content"] or "")[:1500]
    nums = _extract_numbers(text)
    sp = SUMMARY_PROMPT.format(
        extracted_data=", ".join(nums[:5]) if nums else "(없음)", text=text)
    print("\n" + "=" * 78)
    print(f"NEWS #{nid}  {title[:50]}")
    print("=" * 78)
    for m in MODELS:
        t0 = time.time()
        summ = _clean_summary(_call_ollama(sp, model=m, timeout=TIMEOUT_SUMMARY, num_predict=400))
        t_s = time.time() - t0
        tp = TIP_PROMPT.format(title=title, summary=(summ or "")[:300])
        t1 = time.time()
        tip = _ensure_tip_complete(_call_ollama(tp, model=m, timeout=TIMEOUT_TIP, num_predict=260, temperature=0.3))
        t_t = time.time() - t1
        print(f"\n--- [{m}]  summary={t_s:.0f}s  tip={t_t:.0f}s  (합계 {t_s+t_t:.0f}s) ---")
        print(f"  요약({len(summ or '')}자): {summ}")
        print(f"  팁({len(tip or '')}자): {tip}")

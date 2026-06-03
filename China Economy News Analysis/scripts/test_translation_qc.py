"""translation_qc 회귀 테스트 — 4개 오류 교정 + 정상문/제목 무변경."""
import sys
sys.path.insert(0, "/home/jeozeohan/vibe_temp/China Economy News Analysis")
from src.cni.translation_qc import run_qc

CASES = [
    # (input, field, expect_issue_substr, expect_not_contains)
    ("우리나라 경제는 빠르게 성장하고 있다.", "summary", "perspective", "우리나라"),
    ("我国 정부가 신정책을 발표했다.", "summary", "cn_self", "我国"),
    ("우리 정부는 3000억 위안을 투자한다", "market_impact", "perspective", "우리 정부"),
    ("중국 정부가 신정책을 발표했습니다.", "summary", None, None),          # 정상 무변경
    ("국무원, 반도체 산업 지원 신정책 발표", "translated_title", None, None),  # 제목 무변경
    ("자국 기업들이 반도체에 투자한다", "summary", "perspective", "자국"),
]

fails = 0
for txt, fld, want_issue, not_contains in CASES:
    out, iss = run_qc(txt, fld, 1)
    ok = True
    if want_issue and not any(want_issue in i for i in iss):
        ok = False
    if want_issue is None and iss:
        ok = False
    if not_contains and not_contains in out:
        ok = False
    # 평어체: 문장형이고 마침표로 끝나면 ~니다 종결 기대
    print(f"{'PASS' if ok else 'FAIL'} [{fld}] {txt!r} -> {out!r} {iss}")
    if not ok:
        fails += 1

print(f"\n{'ALL PASS ✅' if fails == 0 else f'{fails} FAIL ❌'}")
sys.exit(1 if fails else 0)

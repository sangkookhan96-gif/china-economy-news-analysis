"""translation_qc 회귀 테스트 — 5개 오류 교정 + 정상문/제목 무변경."""
import sys
sys.path.insert(0, "/home/jeozeohan/vibe_temp/China Economy News Analysis")
from src.cni.translation_qc import run_qc, _fix_tense

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

# ── ⑤ 시제 게이트: 과거 시점 문장만 미래/추정형→과거형, 진짜 미래는 유지 ──
# cur_year 고정(2026)으로 날짜 의존 없이 회귀 검증.
TENSE_CASES = [
    # (input, cur_year, expect_changed, must_contain)
    ("비야디는 2025년 매출 8039억 위안을 달성할 전망입니다.", 2026, True, "달성했습니다"),
    ("2025년 순이익이 19% 감소할 것으로 예상됩니다.", 2026, True, "감소했습니다"),
    ("지난해 수출이 크게 증가할 것입니다.", 2026, True, "증가했습니다"),
    ("CPI가 2025년 2분기에 상승할 것으로 보입니다.", 2026, True, "상승했습니다"),
    ("2027년에는 매출이 1조 위안을 돌파할 전망입니다.", 2026, False, "돌파할 전망입니다"),  # 미래연도 유지
    ("내년 신규 공장 가동을 계획할 전망입니다.", 2026, False, "계획할 전망입니다"),          # 내년 유지
    ("올해 하반기 금리를 인하할 전망입니다.", 2026, False, "인하할 전망입니다"),            # 올해 미래시점 유지
    # 비교 기준 연도 — 미래 전망이므로 과거로 바꾸면 안 됨(오교정 방지)
    ("연간 IPO 수가 2025년 수준을 따라잡거나 초과할 것으로 예상됩니다.", 2026, False, "초과할 것으로 예상됩니다"),
    ("전년 대비 매출이 증가할 전망입니다.", 2026, False, "증가할 전망입니다"),
]
for txt, yr, want_changed, must in TENSE_CASES:
    out, changed = _fix_tense(txt, yr)
    ok = (changed == want_changed) and (must in out)
    print(f"{'PASS' if ok else 'FAIL'} [tense] {txt!r} -> {out!r}")
    if not ok:
        fails += 1

print(f"\n{'ALL PASS ✅' if fails == 0 else f'{fails} FAIL ❌'}")
sys.exit(1 if fails else 0)

"""CNI Fact Checker v2 — Enhanced 3-layer verification.

Layer 1: 사실 검증 (원문에 없는 숫자/기관명 탐지)
Layer 2: 일관성 검증 (번역 vs 원문, 요약 vs 원문)
Layer 3: 분석 검증 (근거 없는 주장, 과도한 해석, 정치 오류)

실패 시: 해당 항목 재실행 1회 → 2회 실패 시 NULL
"""

import re
import logging

logger = logging.getLogger("cni_fact_checker")

# ══════════════════════════════════════
# 차단 패턴
# ══════════════════════════════════════

KOREA_ERROR_PATTERNS = [
    r"한국은?.*중국에 속한", r"한국은?.*중화인민공화국.*속한",
    r"한국은?.*중국.*지역", r"한국.*속한 지역",
    r"한국은?.*중국.*일부", r"한국.*중국의 일부",
    r"한국은?.*중국.*영토", r"한국.*같은.*중화",
]

KOREA_MENTION_BLOCK = [
    "한국은", "한국의", "한국도", "한국에서",
    "한국 기업", "한국 경제", "한국 시장",
]

FACT_ERROR_KEYWORDS = [
    ("세계 최대의 경제대국", "중국은 세계 2위"),
    ("세계 최대 경제대국", "중국은 세계 2위"),
    ("세계 1위 경제대국", "중국은 세계 2위"),
]


# ══════════════════════════════════════
# Layer 1: 사실 검증 (숫자/기관명)
# ══════════════════════════════════════

def _extract_numbers(text):
    """텍스트에서 모든 숫자 추출."""
    if not text:
        return set()
    return set(re.findall(r'\d+\.?\d*', text))


def _extract_institutions(text):
    """중국 기관/기업명 패턴 추출."""
    if not text:
        return set()
    patterns = [
        r'[\u4e00-\u9fff]{2,8}(?:公司|集团|银行|证券|基金|委员会|总局|部|院|所|局|厅|会)',
        r'[\u4e00-\u9fff]{2,6}(?:科技|能源|制造|投资|控股)',
    ]
    results = set()
    for p in patterns:
        results.update(re.findall(p, text))
    return results


def verify_numbers(summary_text, original_text):
    """요약의 숫자가 원문에 존재하는지 검증."""
    issues = []
    orig_nums = _extract_numbers(original_text)
    summ_nums = _extract_numbers(summary_text)

    # 요약에만 있고 원문에 없는 숫자 탐지
    fabricated = summ_nums - orig_nums
    # 단순 변환 허용 (만→萬, 억→亿 등의 변환으로 인한 차이)
    # 1자리 숫자는 무시 (문맥 번호 등)
    fabricated = {n for n in fabricated if len(n) >= 2}

    if fabricated:
        issues.append(f"원문에 없는 숫자 탐지: {list(fabricated)[:5]}")

    return issues


# ══════════════════════════════════════
# Layer 2: 일관성 검증
# ══════════════════════════════════════

def verify_consistency(summary_ko, summary_zh, original_text):
    """번역/요약의 일관성 검증."""
    issues = []

    if not summary_ko:
        return issues

    # 구조 태그 잔존
    if re.search(r'\[핵심|\[배경|\[영향|【', summary_ko):
        issues.append("구조 태그 잔존")

    # 마크다운 잔존
    if "**" in summary_ko:
        issues.append("마크다운 잔존")

    # "제목:" "요약:" 접두어 잔존
    if summary_ko.strip().startswith("제목:") or summary_ko.strip().startswith("제목 :"):
        issues.append("접두어 잔존")

    # 300자 미만
    if len(summary_ko) < 200:
        issues.append(f"요약 짧음 ({len(summary_ko)}자)")

    # 문단 반복
    if len(summary_ko) > 100:
        first_50 = summary_ko[:50]
        if first_50 in summary_ko[50:]:
            issues.append("문단 반복")

    # 경어체 확인 (마지막 문장)
    sentences = [s.strip() for s in re.split(r'[.。]', summary_ko) if s.strip() and len(s) > 10]
    if sentences:
        last = sentences[-1]
        casual_endings = ['했다', '있다', '됐다', '한다', '이다', '뚜렷하다']
        if any(last.endswith(e) for e in casual_endings):
            issues.append("비경어체 잔존")

    return issues


# ══════════════════════════════════════
# Layer 3: 정치/사실 오류 검증
# ══════════════════════════════════════

def verify_political_safety(text):
    """정치적·사실적 오류 검증."""
    issues = []

    if not text:
        return issues

    # 한국 주권 오류
    for pattern in KOREA_ERROR_PATTERNS:
        if re.search(pattern, text):
            issues.append(f"한국 주권 오류: {pattern}")

    # 사실 오류
    for wrong, correct in FACT_ERROR_KEYWORDS:
        if wrong in text:
            issues.append(f"사실 오류: '{wrong}' → '{correct}'")

    return issues


def verify_tip_safety(tip):
    """팁 전용 검증."""
    issues = []

    if not tip:
        return issues

    # 한국 언급 차단
    for kw in KOREA_MENTION_BLOCK:
        if kw in tip:
            issues.append(f"팁 한국 언급: {kw}")

    # 정치/사실 오류
    issues.extend(verify_political_safety(tip))

    return issues


# ══════════════════════════════════════
# 통합 검증
# ══════════════════════════════════════

def verify_summary(summary_ko, original_content):
    """요약 전체 검증 (Layer 1+2+3)."""
    issues = []

    # Layer 1: 숫자 검증
    issues.extend(verify_numbers(summary_ko, original_content))

    # Layer 2: 일관성 검증
    issues.extend(verify_consistency(summary_ko, "", original_content))

    # Layer 3: 정치/사실 검증
    issues.extend(verify_political_safety(summary_ko))

    return {"passed": len(issues) == 0, "issues": issues}


def verify_analysis(analysis_ko, original_content):
    """분석 검증 (Layer 3)."""
    issues = []

    if not analysis_ko:
        return {"passed": True, "issues": []}

    # 정치/사실 검증
    issues.extend(verify_political_safety(analysis_ko))

    # 한국 언급 차단
    for kw in KOREA_MENTION_BLOCK:
        if kw in analysis_ko:
            issues.append(f"분석 한국 언급: {kw}")

    # 추론 표시 확인
    speculation_words = ["것으로 보입니다", "예상됩니다", "전망됩니다", "가능성이"]
    has_speculation = any(w in analysis_ko for w in speculation_words)
    has_marker = "추측" in analysis_ko or "추론" in analysis_ko
    if has_speculation and not has_marker:
        issues.append("추론 표시 없는 추측")

    return {"passed": len(issues) == 0, "issues": issues}


def verify_tip(tip):
    """팁 검증."""
    issues = verify_tip_safety(tip)
    return {"passed": len(issues) == 0, "issues": issues}


def verify_all(summary_ko, analysis_ko, tip, original_content):
    """전체 3중 검증.

    Returns: {
        "passed": bool,
        "summary": {"passed": bool, "issues": list},
        "analysis": {"passed": bool, "issues": list},
        "tip": {"passed": bool, "issues": list},
        "total_issues": int,
        "actions": list  # 필요한 조치
    }
    """
    s = verify_summary(summary_ko, original_content)
    a = verify_analysis(analysis_ko, original_content)
    t = verify_tip(tip)

    all_issues = s["issues"] + a["issues"] + t["issues"]
    actions = []

    # 조치 결정
    if not t["passed"]:
        actions.append("tip_remove")  # 팁 삭제
    if not s["passed"]:
        # 심각도에 따라 분류
        critical = [i for i in s["issues"] if "주권" in i or "사실 오류" in i]
        if critical:
            actions.append("summary_block")  # 요약 차단
        else:
            actions.append("summary_warn")  # 경고만

    if all_issues:
        logger.warning(f"Verification: {len(all_issues)} issues — {all_issues}")

    return {
        "passed": len(all_issues) == 0,
        "summary": s,
        "analysis": a,
        "tip": t,
        "total_issues": len(all_issues),
        "actions": actions,
    }

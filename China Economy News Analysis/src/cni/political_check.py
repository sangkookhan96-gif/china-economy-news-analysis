"""CNI Political Sensitivity Check.

Filters politically sensitive expressions for Korean readers.
Neutralizes propaganda-style language to objective reporting tone.
"""

import re
import logging

logger = logging.getLogger("cni_political")

# High sensitivity: may cause diplomatic/political issues
SENSITIVE_HIGH = [
    "대만 통일", "台湾统一", "하나의 중국", "一个中国",
    "시진핑 사상", "习近平思想", "시진핑 신시대",
    "중화민족 위대한 부흥", "中华民族伟大复兴",
    "미제국주의", "패권주의", "제국주의",
    "대만은 중국의 일부", "台湾是中国的一部分",
    # 한국 주권 오류
    "한국은 중화인민공화국에 속한",
    "한국은 중국에 속한",
    "한국은 중국의 일부",
]

# Medium sensitivity: propaganda tone, needs neutralization
SENSITIVE_MEDIUM = [
    "일대일로", "一带一路",
    "중국몽", "中国梦",
    "인류운명공동체", "人类命运共同体",
    "사회주의 핵심 가치관", "社会主义核心价值观",
    "당의 영도", "党的领导",
    "인민을 위한", "为人民服务",
    "위대한 승리", "伟大胜利",
    "역사적 성취", "历史性成就",
]

# Auto-neutralization map: propaganda → neutral
NEUTRALIZE_MAP = {
    "위대한 부흥": "경제 발전 목표",
    "당의 영도 하에": "정부 주도로",
    "당의 지도 하에": "정부 주도로",
    "사회주의 현대화": "현대화",
    "중국 특색 사회주의": "중국식 경제 체제",
    "인민을 위한 봉사": "공공 서비스",
    "위대한 승리를 거두": "성과를 달성하",
    "역사적 성취를 이루": "주요 성과를 달성하",
    "전면적 소강사회": "중산층 사회",
    "쌍순환 신발전 구도": "내수-수출 이중 경제 전략",
    "신질생산력": "새로운 품질의 생산력",
}


def check_sensitivity(text: str) -> tuple:
    """Check political sensitivity of text.

    Returns:
        (score: float 0~1, reasons: list of str)
        score >= 0.7: HIGH — 사용자 수동 확인 필요
        score >= 0.3: MEDIUM — 자동 중립화 적용
        score < 0.3: LOW — 통과
    """
    if not text:
        return 0.0, []

    score = 0.0
    reasons = []

    for kw in SENSITIVE_HIGH:
        if kw in text:
            score = max(score, 0.8)
            reasons.append(f"HIGH: '{kw}'")

    for kw in SENSITIVE_MEDIUM:
        if kw in text:
            score = max(score, 0.4)
            reasons.append(f"MEDIUM: '{kw}'")

    return score, reasons


def neutralize_text(text: str) -> str:
    """Neutralize propaganda-style expressions to objective tone."""
    if not text:
        return text
    result = text
    for propaganda, neutral in NEUTRALIZE_MAP.items():
        result = result.replace(propaganda, neutral)
    return result


def check_and_neutralize(text: str) -> tuple:
    """Check sensitivity and auto-neutralize if needed.

    Returns:
        (cleaned_text, score, reasons)
    """
    score, reasons = check_sensitivity(text)

    if score >= 0.7:
        # HIGH: neutralize + flag for manual review
        cleaned = neutralize_text(text)
        logger.warning(f"Political HIGH ({score:.1f}): {reasons}")
        return cleaned, score, reasons
    elif score >= 0.3:
        # MEDIUM: auto-neutralize
        cleaned = neutralize_text(text)
        logger.info(f"Political MEDIUM ({score:.1f}): {reasons} — auto-neutralized")
        return cleaned, score, reasons
    else:
        return text, score, reasons

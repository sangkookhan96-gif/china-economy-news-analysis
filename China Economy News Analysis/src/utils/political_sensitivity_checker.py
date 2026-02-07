"""Political Sensitivity Checker for China Economy News.

Detects and corrects politically sensitive expressions that could cause
diplomatic issues with China, Japan, or Korea.

CRITICAL: This module prevents content that could trigger:
- Official diplomatic protests
- Platform blocking in China
- Legal action from governments
- Reputation damage

Severity Levels:
- CRITICAL: Must be blocked/corrected immediately (diplomatic incident risk)
- HIGH: Should be corrected before publication (strong political sensitivity)
- MEDIUM: Review recommended (potential misunderstanding)
- LOW: Informational only (minor style issue)
"""

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Severity(Enum):
    """Severity level of political sensitivity."""
    CRITICAL = "CRITICAL"  # 🚨 즉시 차단/수정 필수
    HIGH = "HIGH"          # ⛔ 게시 전 반드시 수정
    MEDIUM = "MEDIUM"      # ⚠️ 검토 권장
    LOW = "LOW"            # ℹ️ 참고 사항


@dataclass
class SensitivityIssue:
    """A detected sensitivity issue."""
    severity: Severity
    category: str
    pattern: str
    match: str
    context: str
    description: str
    recommendation: str
    auto_fix: Optional[tuple[str, str]] = None  # (from, to)


@dataclass
class CheckResult:
    """Result of sensitivity check."""
    original: str
    corrected: str
    issues: list[SensitivityIssue] = field(default_factory=list)
    has_critical: bool = False
    has_high: bool = False
    blocked: bool = False


# =============================================================================
# 🚨 CRITICAL: 즉시 차단/수정 필수 (외교 문제 직결)
# =============================================================================

CRITICAL_PATTERNS = [
    # 대만 주권 관련 (중국 정부 최대 민감 사안)
    {
        "pattern": r"대만\s*(국가|나라|국\b)",
        "category": "대만 주권",
        "description": "대만을 국가로 표현 - 중국 정부 강력 반발 예상",
        "recommendation": "'대만 지역' 또는 '대만'으로 수정",
        "auto_fix": ("대만 국가", "대만 지역"),
    },
    {
        "pattern": r"(타이완|台湾|臺灣)\s*(국가|나라|국\b)",
        "category": "대만 주권",
        "description": "타이완을 국가로 표현",
        "recommendation": "'대만 지역' 또는 '대만'으로 수정",
    },
    {
        "pattern": r"대만\s*대통령",
        "category": "대만 주권",
        "description": "'대만 대통령' 표현 - 중국은 '총통' 인정 안함",
        "recommendation": "'대만 총통' 또는 '대만 지도자'로 수정",
        "auto_fix": ("대만 대통령", "대만 총통"),
    },
    {
        "pattern": r"중화민국\s*(정부|대통령|국가)",
        "category": "대만 주권",
        "description": "중화민국 정부 표현 - 중국 정부 인정 안함",
        "recommendation": "'대만 당국'으로 수정",
    },
    {
        "pattern": r"대만\s*(독립|분리)",
        "category": "대만 주권",
        "description": "대만 독립 지지로 해석될 수 있음",
        "recommendation": "문맥 확인 후 중립적 표현으로 수정",
    },
    {
        "pattern": r"하나의\s*중국.*부정|하나의\s*중국.*반대",
        "category": "대만 주권",
        "description": "'하나의 중국' 원칙 부정",
        "recommendation": "표현 삭제 또는 중립적 서술로 변경",
    },

    # 티베트/신장 독립 관련
    {
        "pattern": r"티베트\s*(독립|국가|나라|분리)",
        "category": "티베트 주권",
        "description": "티베트를 독립 국가로 표현 - 중국 정부 강력 반발",
        "recommendation": "'티베트 자치구' 또는 '시짱'으로 수정",
    },
    {
        "pattern": r"(신장|위구르)\s*(독립|국가|나라|분리)",
        "category": "신장 주권",
        "description": "신장/위구르를 독립 국가로 표현",
        "recommendation": "'신장 위구르 자치구'로 수정",
    },
    {
        "pattern": r"동투르키스탄",
        "category": "신장 주권",
        "description": "'동투르키스탄' - 분리독립 용어",
        "recommendation": "'신장' 또는 '신장 위구르 자치구'로 수정",
        "auto_fix": ("동투르키스탄", "신장"),
    },

    # 홍콩 독립 관련
    {
        "pattern": r"홍콩\s*(독립|국가|나라|분리)",
        "category": "홍콩 주권",
        "description": "홍콩을 독립 국가로 표현",
        "recommendation": "'홍콩 특별행정구'로 수정",
    },

    # 천안문 사태 관련
    {
        "pattern": r"천안문\s*(학살|대학살|민주화\s*탄압)",
        "category": "역사 민감",
        "description": "천안문 사태 표현 - 중국 내 금기 주제",
        "recommendation": "표현 삭제 또는 '1989년 천안문 사건'으로 중립화",
    },
    {
        "pattern": r"6[·\.\s]*4\s*(사건|민주화|시위)",
        "category": "역사 민감",
        "description": "6.4 사건 - 천안문 사태 암시",
        "recommendation": "문맥 확인 후 삭제 또는 수정",
    },
]

# =============================================================================
# ⛔ HIGH: 게시 전 반드시 수정 (높은 정치적 민감도)
# =============================================================================

HIGH_PATTERNS = [
    # 영토 분쟁 관련
    {
        "pattern": r"센카쿠\s*제도",
        "category": "영토 분쟁",
        "description": "'센카쿠'는 일본 명칭 - 중국은 '댜오위다오'",
        "recommendation": "'센카쿠(댜오위다오)' 병기 권장",
    },
    {
        "pattern": r"댜오위다오|钓鱼岛",
        "category": "영토 분쟁",
        "description": "'댜오위다오'는 중국 명칭 - 일본은 '센카쿠'",
        "recommendation": "'댜오위다오(센카쿠)' 병기 권장",
    },
    {
        "pattern": r"다케시마|竹島",
        "category": "영토 분쟁",
        "description": "'다케시마'는 일본 명칭 - 한국은 '독도'",
        "recommendation": "'다케시마' 삭제, '독도'로 수정",
        "auto_fix": ("다케시마", "독도"),
    },
    {
        "pattern": r"독도.*일본\s*(영토|영유권|고유)",
        "category": "영토 분쟁",
        "description": "독도에 대한 일본 영유권 주장 표현",
        "recommendation": "표현 삭제 또는 중립적 서술",
    },

    # 역사 문제
    {
        "pattern": r"난징\s*대학살.*부정|난징\s*학살.*없",
        "category": "역사 민감",
        "description": "난징대학살 부정 표현 - 중국 정부 강력 반발",
        "recommendation": "표현 삭제",
    },
    {
        "pattern": r"위안부.*자발|위안부.*매춘",
        "category": "역사 민감",
        "description": "위안부 피해 부정 표현 - 한국 정부 반발",
        "recommendation": "표현 삭제",
    },
    {
        "pattern": r"야스쿠니\s*신사\s*(참배\s*지지|찬양)",
        "category": "역사 민감",
        "description": "야스쿠니 신사 참배 지지 표현",
        "recommendation": "표현 삭제 또는 중립적 서술",
    },

    # 체제 비판
    {
        "pattern": r"공산당\s*(독재|붕괴|몰락|타도)",
        "category": "체제 비판",
        "description": "중국 공산당 직접 비판 - 중국 내 차단 위험",
        "recommendation": "표현 삭제",
    },
    {
        "pattern": r"시진핑.*(독재|실정|무능|타도)",
        "category": "체제 비판",
        "description": "시진핑 주석 직접 비판 - 중국 내 차단 위험",
        "recommendation": "표현 삭제",
    },

    # 위구르 인권 문제
    {
        "pattern": r"위구르\s*(강제\s*수용|집단\s*학살|제노사이드)",
        "category": "인권 민감",
        "description": "위구르 인권 문제 강한 표현 - 중국 반발",
        "recommendation": "중립적 표현으로 수정 권장",
    },
]

# =============================================================================
# ⚠️ MEDIUM: 검토 권장 (잠재적 오해 가능)
# =============================================================================

MEDIUM_PATTERNS = [
    # 관점 혼란
    {
        "pattern": r"우리로서는",
        "category": "관점 혼란",
        "description": "'우리'가 한국인지 중국인지 불명확",
        "recommendation": "'한국으로서는'으로 명확화",
        "auto_fix": ("우리로서는", "한국으로서는"),
    },
    {
        "pattern": r"우리\s*입장에서",
        "category": "관점 혼란",
        "description": "'우리'가 한국인지 중국인지 불명확",
        "recommendation": "'한국 입장에서'로 명확화",
        "auto_fix": ("우리 입장에서", "한국 입장에서"),
    },
    {
        "pattern": r"우리\s*정부가",
        "category": "관점 혼란",
        "description": "'우리 정부'가 한국인지 중국인지 불명확",
        "recommendation": "'한국 정부가' 또는 '중국 정부가'로 명확화",
    },
    {
        "pattern": r"우리\s*기업이",
        "category": "관점 혼란",
        "description": "'우리 기업'이 한국인지 중국인지 불명확",
        "recommendation": "'한국 기업이' 또는 '중국 기업이'로 명확화",
    },

    # 민감한 비교
    {
        "pattern": r"중국.*후진|중국.*낙후|중국.*미개",
        "category": "국가 비하",
        "description": "중국 비하 표현",
        "recommendation": "객관적 표현으로 수정",
    },
    {
        "pattern": r"일본.*전범|일본.*침략.*미화",
        "category": "역사 민감",
        "description": "일본 역사 관련 강한 표현",
        "recommendation": "문맥 확인 후 중립화 검토",
    },
]

# =============================================================================
# ℹ️ LOW: 참고 사항 (경미한 스타일 문제)
# =============================================================================

LOW_PATTERNS = [
    {
        "pattern": r"대만\s*정부",
        "category": "대만 표현",
        "description": "'대만 정부' - 중국은 '대만 당국' 선호",
        "recommendation": "'대만 당국' 사용 권장 (필수 아님)",
    },
    {
        "pattern": r"달라이\s*라마",
        "category": "티베트 관련",
        "description": "달라이 라마 언급 - 문맥 확인 권장",
        "recommendation": "중립적 서술 확인",
    },
]


def check_sensitivity(text: str) -> CheckResult:
    """Check text for political sensitivity issues.

    Args:
        text: Text to check (title, content, or review)

    Returns:
        CheckResult with issues found and corrected text
    """
    if not text:
        return CheckResult(original="", corrected="", issues=[], blocked=False)

    issues = []
    corrected = text

    # Check all pattern groups
    all_patterns = [
        (CRITICAL_PATTERNS, Severity.CRITICAL),
        (HIGH_PATTERNS, Severity.HIGH),
        (MEDIUM_PATTERNS, Severity.MEDIUM),
        (LOW_PATTERNS, Severity.LOW),
    ]

    for patterns, severity in all_patterns:
        for p in patterns:
            regex = re.compile(p["pattern"], re.IGNORECASE)
            for match in regex.finditer(text):
                # Extract context
                start = max(0, match.start() - 30)
                end = min(len(text), match.end() + 30)
                context = text[start:end]

                issue = SensitivityIssue(
                    severity=severity,
                    category=p["category"],
                    pattern=p["pattern"],
                    match=match.group(),
                    context=context,
                    description=p["description"],
                    recommendation=p["recommendation"],
                    auto_fix=p.get("auto_fix"),
                )
                issues.append(issue)

                # Apply auto-fix if available
                if issue.auto_fix:
                    corrected = corrected.replace(issue.auto_fix[0], issue.auto_fix[1])

    # Determine if blocked
    has_critical = any(i.severity == Severity.CRITICAL for i in issues)
    has_high = any(i.severity == Severity.HIGH for i in issues)

    return CheckResult(
        original=text,
        corrected=corrected,
        issues=issues,
        has_critical=has_critical,
        has_high=has_high,
        blocked=has_critical,  # Block if any CRITICAL issue
    )


def get_severity_icon(severity: Severity) -> str:
    """Get emoji icon for severity level."""
    icons = {
        Severity.CRITICAL: "🚨",
        Severity.HIGH: "⛔",
        Severity.MEDIUM: "⚠️",
        Severity.LOW: "ℹ️",
    }
    return icons.get(severity, "")


def format_report(result: CheckResult) -> str:
    """Format check result as a human-readable report."""
    if not result.issues:
        return "✅ 정치적 민감 표현이 발견되지 않았습니다."

    lines = []

    if result.blocked:
        lines.append("=" * 60)
        lines.append("🚨🚨🚨 게시 차단: CRITICAL 수준 문제 발견 🚨🚨🚨")
        lines.append("=" * 60)
    elif result.has_high:
        lines.append("⛔ 게시 전 수정 필요: HIGH 수준 문제 발견")
        lines.append("-" * 60)

    # Group by severity
    by_severity = {}
    for issue in result.issues:
        sev = issue.severity
        if sev not in by_severity:
            by_severity[sev] = []
        by_severity[sev].append(issue)

    for severity in [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW]:
        if severity in by_severity:
            icon = get_severity_icon(severity)
            lines.append(f"\n{icon} [{severity.value}] {len(by_severity[severity])}건:")
            for issue in by_severity[severity]:
                lines.append(f"  - {issue.description}")
                lines.append(f"    발견: \"{issue.match}\"")
                lines.append(f"    문맥: ...{issue.context}...")
                lines.append(f"    권장: {issue.recommendation}")
                if issue.auto_fix:
                    lines.append(f"    자동수정: {issue.auto_fix[0]} → {issue.auto_fix[1]}")
                lines.append("")

    if result.corrected != result.original:
        lines.append("-" * 60)
        lines.append("자동 수정 적용됨:")
        lines.append(f"  수정 전: {result.original[:100]}...")
        lines.append(f"  수정 후: {result.corrected[:100]}...")

    return "\n".join(lines)


# =============================================================================
# 데이터베이스 통합 함수
# =============================================================================

def check_news(news_id: int) -> CheckResult:
    """Check a specific news item for sensitivity issues.

    Args:
        news_id: News ID

    Returns:
        CheckResult for the news item
    """
    from src.database.models import get_connection

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT n.translated_title, er.expert_comment
        FROM news n
        LEFT JOIN expert_reviews er ON n.id = er.news_id
        WHERE n.id = ?
    """, (news_id,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        return CheckResult(original="", corrected="", issues=[])

    # Check both title and review
    title = row['translated_title'] or ""
    review = row['expert_comment'] or ""
    full_text = f"{title}\n{review}"

    return check_sensitivity(full_text)


def scan_published_news() -> list[dict]:
    """Scan all published news for sensitivity issues.

    Returns:
        List of news items with issues
    """
    from src.database.models import get_connection

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT n.id, n.translated_title, er.expert_comment
        FROM news n
        JOIN expert_reviews er ON n.id = er.news_id
        WHERE er.publish_status = 'published'
          AND er.expert_comment IS NOT NULL
    """)
    rows = cursor.fetchall()
    conn.close()

    results = []
    for row in rows:
        full_text = f"{row['translated_title'] or ''}\n{row['expert_comment'] or ''}"
        result = check_sensitivity(full_text)

        if result.issues:
            results.append({
                'news_id': row['id'],
                'title': row['translated_title'][:50] if row['translated_title'] else "",
                'result': result,
            })

    return results


def fix_published_news(dry_run: bool = True) -> list[dict]:
    """Fix all auto-fixable issues in published news.

    Args:
        dry_run: If True, only report without making changes

    Returns:
        List of fixed items
    """
    from src.database.models import get_connection

    items_with_issues = scan_published_news()
    fixed = []

    if dry_run:
        return [
            {
                'news_id': item['news_id'],
                'title': item['title'],
                'issues': len(item['result'].issues),
                'blocked': item['result'].blocked,
            }
            for item in items_with_issues
            if item['result'].corrected != item['result'].original
        ]

    conn = get_connection()
    cursor = conn.cursor()

    for item in items_with_issues:
        result = item['result']
        if result.corrected != result.original and not result.blocked:
            # Apply fixes to expert_comment
            cursor.execute("""
                SELECT expert_comment FROM expert_reviews WHERE news_id = ?
            """, (item['news_id'],))
            row = cursor.fetchone()

            if row:
                original = row['expert_comment']
                corrected = original

                # Apply all auto-fixes
                for issue in result.issues:
                    if issue.auto_fix:
                        corrected = corrected.replace(issue.auto_fix[0], issue.auto_fix[1])

                if corrected != original:
                    cursor.execute("""
                        UPDATE expert_reviews
                        SET expert_comment = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE news_id = ?
                    """, (corrected, item['news_id']))

                    fixed.append({
                        'news_id': item['news_id'],
                        'title': item['title'],
                        'changes': [i.auto_fix for i in result.issues if i.auto_fix],
                    })

    conn.commit()
    conn.close()

    return fixed


def validate_before_publish(news_id: int) -> tuple[bool, str]:
    """Validate news before publishing.

    Args:
        news_id: News ID to validate

    Returns:
        Tuple of (can_publish, report)
    """
    result = check_news(news_id)

    if result.blocked:
        return False, format_report(result)
    elif result.has_high:
        return False, format_report(result)
    else:
        return True, format_report(result) if result.issues else "✅ 게시 가능"

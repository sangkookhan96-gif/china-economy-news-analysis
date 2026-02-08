"""Title translation validator for Chinese news.

Detects and corrects translation issues where Chinese perspective
expressions (like "우리", "우리나라") should be replaced with explicit
country references for Korean readers.

Example:
    "우리나라 반도체 수출 급증" → "중국 반도체 수출 급증"
    "우리 정부 새 정책 발표" → "중국 정부 새 정책 발표"
"""

import re
from dataclasses import dataclass
from typing import Optional

# Patterns that need correction (Chinese perspective → explicit reference)
# Format: (pattern, replacement, description)
CORRECTION_RULES = [
    # 우리나라 + 조사 계열 (조사 포함 패턴 우선)
    (r"우리나라는", "중국은", "우리나라는 → 중국은"),
    (r"우리나라가", "중국이", "우리나라가 → 중국이"),
    (r"우리나라의", "중국의", "우리나라의 → 중국의"),
    (r"우리나라를", "중국을", "우리나라를 → 중국을"),
    (r"우리나라에", "중국에", "우리나라에 → 중국에"),
    (r"우리나라와", "중국과", "우리나라와 → 중국과"),
    (r"우리나라도", "중국도", "우리나라도 → 중국도"),
    (r"우리나라", "중국", "우리나라 → 중국"),
    (r"우리 나라", "중국", "우리 나라 → 중국"),

    # 우리 + 주체 계열
    (r"우리 정부", "중국 정부", "우리 정부 → 중국 정부"),
    (r"우리 기업", "중국 기업", "우리 기업 → 중국 기업"),
    (r"우리 업계", "중국 업계", "우리 업계 → 중국 업계"),
    (r"우리 산업", "중국 산업", "우리 산업 → 중국 산업"),
    (r"우리 시장", "중국 시장", "우리 시장 → 중국 시장"),
    (r"우리 경제", "중국 경제", "우리 경제 → 중국 경제"),
    (r"우리 군", "중국군", "우리 군 → 중국군"),
    (r"우리 측", "중국 측", "우리 측 → 중국 측"),
    (r"우리 회사", "중국 회사", "우리 회사 → 중국 회사"),
    (r"우리 은행", "중국 은행", "우리 은행 → 중국 은행"),
    (r"우리 기술", "중국 기술", "우리 기술 → 중국 기술"),
    (r"우리 제품", "중국 제품", "우리 제품 → 중국 제품"),
    (r"우리 국민", "중국 국민", "우리 국민 → 중국 국민"),
    (r"우리 사회", "중국 사회", "우리 사회 → 중국 사회"),

    # 국내 계열 (중국 뉴스에서 국내 = 중국)
    (r"국내(?=\s*(?:기업|시장|산업|경제|업계|정부|은행))", "중국", "국내 → 중국"),

    # 자국 계열
    (r"자국", "중국", "자국 → 중국"),

    # 단독 "우리"는 문맥에 따라 처리 (주의 필요)
    # 예: "우리가 먼저" → 문맥에 따라 다름
]

# Standalone "우리" patterns that need careful handling
AMBIGUOUS_PATTERNS = [
    (r"(?<![가-힣])우리(?![가-힣\s]*(?:나라|정부|기업|업계|산업|시장|경제|군|측|회사|은행|기술|제품|국민|사회))",
     "⚠️ '우리' 표현 검토 필요"),
]


@dataclass
class ValidationResult:
    """Result of title validation."""
    original: str
    corrected: str
    has_issues: bool
    corrections: list[tuple[str, str, str]]  # (before, after, rule_desc)
    warnings: list[str]


def validate_title(title: str) -> ValidationResult:
    """Validate and correct a translated news title.

    Args:
        title: The translated news title (Korean)

    Returns:
        ValidationResult with corrected title and list of corrections made
    """
    if not title:
        return ValidationResult(
            original="",
            corrected="",
            has_issues=False,
            corrections=[],
            warnings=[]
        )

    corrected = title
    corrections = []
    warnings = []

    # Apply correction rules
    for pattern, replacement, description in CORRECTION_RULES:
        regex = re.compile(pattern)
        matches = regex.findall(corrected)
        if matches:
            for match in matches:
                corrections.append((match, replacement, description))
            corrected = regex.sub(replacement, corrected)

    # Check for ambiguous patterns
    for pattern, warning_msg in AMBIGUOUS_PATTERNS:
        regex = re.compile(pattern)
        if regex.search(corrected):
            warnings.append(warning_msg)

    has_issues = len(corrections) > 0 or len(warnings) > 0

    return ValidationResult(
        original=title,
        corrected=corrected,
        has_issues=has_issues,
        corrections=corrections,
        warnings=warnings
    )


def correct_title(title: str) -> str:
    """Correct a translated title and return the fixed version.

    Args:
        title: The translated news title

    Returns:
        Corrected title
    """
    result = validate_title(title)
    return result.corrected


def has_translation_issues(title: str) -> bool:
    """Check if a title has translation issues.

    Args:
        title: The translated news title

    Returns:
        True if issues were found
    """
    result = validate_title(title)
    return result.has_issues


def get_issue_report(title: str) -> Optional[str]:
    """Get a human-readable report of issues found.

    Args:
        title: The translated news title

    Returns:
        Report string or None if no issues
    """
    result = validate_title(title)

    if not result.has_issues:
        return None

    lines = ["[번역 검토 필요]"]
    lines.append(f"원문: {result.original}")

    if result.corrections:
        lines.append(f"수정: {result.corrected}")
        lines.append("변경사항:")
        for before, after, desc in result.corrections:
            lines.append(f"  - {desc}")

    if result.warnings:
        lines.append("경고:")
        for warning in result.warnings:
            lines.append(f"  - {warning}")

    return "\n".join(lines)


def batch_validate(titles: list[str]) -> list[ValidationResult]:
    """Validate multiple titles at once.

    Args:
        titles: List of translated news titles

    Returns:
        List of ValidationResult objects
    """
    return [validate_title(title) for title in titles]


def batch_correct(titles: list[str]) -> list[str]:
    """Correct multiple titles at once.

    Args:
        titles: List of translated news titles

    Returns:
        List of corrected titles
    """
    return [correct_title(title) for title in titles]


# Database integration functions
def correct_title_in_db(news_id: int) -> Optional[str]:
    """Correct a news title in the database.

    Args:
        news_id: The news ID

    Returns:
        Corrected title or None if not found/no changes
    """
    from src.database.models import get_connection

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT translated_title FROM news WHERE id = ?",
        (news_id,)
    )
    row = cursor.fetchone()

    if not row or not row['translated_title']:
        conn.close()
        return None

    original = row['translated_title']
    result = validate_title(original)

    if result.has_issues and result.corrected != original:
        cursor.execute(
            "UPDATE news SET translated_title = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (result.corrected, news_id)
        )
        conn.commit()
        conn.close()
        return result.corrected

    conn.close()
    return None


def scan_all_titles() -> list[dict]:
    """Scan all translated titles in database for issues.

    Returns:
        List of dicts with news_id, original, corrected, issues
    """
    from src.database.models import get_connection

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT id, translated_title FROM news WHERE translated_title IS NOT NULL"
    )
    rows = cursor.fetchall()
    conn.close()

    issues_found = []

    for row in rows:
        result = validate_title(row['translated_title'])
        if result.has_issues:
            issues_found.append({
                'news_id': row['id'],
                'original': result.original,
                'corrected': result.corrected,
                'corrections': result.corrections,
                'warnings': result.warnings,
            })

    return issues_found


def fix_all_titles(dry_run: bool = True) -> list[dict]:
    """Fix all translated titles with issues.

    Args:
        dry_run: If True, only report what would be fixed without changing DB

    Returns:
        List of dicts with news_id and changes made
    """
    from src.database.models import get_connection

    issues = scan_all_titles()

    if dry_run:
        return issues

    conn = get_connection()
    cursor = conn.cursor()

    fixed = []
    for issue in issues:
        if issue['corrections']:  # Only fix if there are actual corrections
            cursor.execute(
                "UPDATE news SET translated_title = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (issue['corrected'], issue['news_id'])
            )
            fixed.append(issue)

    conn.commit()
    conn.close()

    return fixed

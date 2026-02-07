"""Post-processor for translated news titles.

Applies rule-based corrections to improve translation quality:
1. Dictionary-based term replacement (names, agencies, terms)
2. Awkward expression cleanup
3. Perspective correction (우리나라 → 중국)
4. Length optimization
"""

import re
from typing import Optional
from dataclasses import dataclass

from src.utils.chinese_dictionary import get_all_mappings
from src.utils.title_validator import correct_title as fix_perspective


# =============================================================================
# 어색한 표현 교정 패턴
# =============================================================================

AWKWARD_PATTERNS = [
    # 중복/장황한 표현
    (r"에 대한 에 대한", "에 대한"),
    (r"에 대해 에 대해", "에 대해"),
    (r"을 위한 을 위한", "을 위한"),
    (r"에 관한 에 관한", "에 관한"),

    # 불필요한 수식
    (r"^【[^】]+】\s*", ""),  # 앞쪽 【】 제거
    (r"^\[[^\]]+\]\s*", ""),  # 앞쪽 [] 제거 (영문)
    (r"\s*\|\s*$", ""),  # 끝쪽 | 제거
    (r"\.{3,}$", ""),  # 끝쪽 ... 제거

    # 직역 어색 표현 → 자연스러운 표현
    (r"준비된 요리", "예제요리"),
    (r"사전 제조된 요리", "예제요리"),
    (r"미리 만들어진 요리", "예제요리"),
    (r"두 번의 세션", "양회"),
    (r"두 세션", "양회"),
    (r"투 세션", "양회"),
    (r"국가 발전 및 개혁 위원회", "발개위"),
    (r"산업 및 정보 기술부", "공신부"),
    (r"산업정보기술부", "공신부"),

    # 영문 잔류 정리
    (r"\s+Co\.,?\s*Ltd\.?", ""),
    (r"\s+Inc\.?", ""),
    (r"\s+Corp\.?", ""),
    (r"\s+Group", " 그룹"),

    # 조사 정리
    (r"의의\s", "의 "),
    (r"를를\s", "를 "),
    (r"을을\s", "을 "),

    # 숫자 표기 통일
    (r"(\d),(\d{3})", r"\1\2"),  # 1,000 → 1000 (한국식)
    (r"(\d+)\s*억\s*위안", r"\1억 위안"),
    (r"(\d+)\s*만\s*위안", r"\1만 위안"),

    # 불필요한 접미사
    (r"입니다\.?$", ""),
    (r"합니다\.?$", ""),
    (r"됩니다\.?$", ""),
    (r"습니다\.?$", ""),
    (r"있습니다\.?$", ""),
]

# 제목 시작 불필요 표현
REMOVE_PREFIXES = [
    "속보:",
    "긴급:",
    "[속보]",
    "[긴급]",
    "[단독]",
    "[종합]",
    "장중 필독 |",
    "장중 필독|",
    "[데이터 검토]",
    "[금융가 발행]",
]


@dataclass
class PostProcessResult:
    """Result of post-processing."""
    original: str
    processed: str
    changes: list[str]


def apply_dictionary(text: str) -> tuple[str, list[str]]:
    """Apply dictionary replacements.

    Args:
        text: Input text

    Returns:
        Tuple of (processed text, list of changes made)
    """
    changes = []
    mappings = get_all_mappings()

    # Sort by length (longest first) to avoid partial replacements
    sorted_terms = sorted(mappings.keys(), key=len, reverse=True)

    for term in sorted_terms:
        if term in text:
            replacement = mappings[term]
            if term != replacement:  # Avoid no-op replacements
                text = text.replace(term, replacement)
                changes.append(f"{term} → {replacement}")

    return text, changes


def apply_awkward_patterns(text: str) -> tuple[str, list[str]]:
    """Apply awkward expression corrections.

    Args:
        text: Input text

    Returns:
        Tuple of (processed text, list of changes made)
    """
    changes = []

    for pattern, replacement in AWKWARD_PATTERNS:
        regex = re.compile(pattern)
        if regex.search(text):
            new_text = regex.sub(replacement, text)
            if new_text != text:
                changes.append(f"패턴 교정: {pattern[:20]}...")
                text = new_text

    return text, changes


def remove_prefixes(text: str) -> tuple[str, list[str]]:
    """Remove unnecessary prefixes.

    Args:
        text: Input text

    Returns:
        Tuple of (processed text, list of changes made)
    """
    changes = []

    for prefix in REMOVE_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
            changes.append(f"접두어 제거: {prefix}")

    return text, changes


def cleanup_whitespace(text: str) -> str:
    """Clean up whitespace issues."""
    # Multiple spaces to single
    text = re.sub(r"\s+", " ", text)
    # Trim
    text = text.strip()
    # Remove space before punctuation
    text = re.sub(r"\s+([,，.。:：;；!！?？])", r"\1", text)
    return text


def postprocess_title(title: str) -> PostProcessResult:
    """Apply all post-processing steps to a title.

    Args:
        title: Translated news title

    Returns:
        PostProcessResult with processed title and changes
    """
    if not title:
        return PostProcessResult(original="", processed="", changes=[])

    original = title
    all_changes = []

    # Step 1: Remove prefixes
    title, changes = remove_prefixes(title)
    all_changes.extend(changes)

    # Step 2: Apply dictionary replacements
    title, changes = apply_dictionary(title)
    all_changes.extend(changes)

    # Step 3: Fix perspective (우리나라 → 중국)
    fixed = fix_perspective(title)
    if fixed != title:
        all_changes.append("관점 표현 교정")
        title = fixed

    # Step 4: Apply awkward pattern corrections
    title, changes = apply_awkward_patterns(title)
    all_changes.extend(changes)

    # Step 5: Cleanup whitespace
    title = cleanup_whitespace(title)

    return PostProcessResult(
        original=original,
        processed=title,
        changes=all_changes
    )


def postprocess(title: str) -> str:
    """Simple wrapper that returns only the processed title.

    Args:
        title: Translated news title

    Returns:
        Processed title
    """
    result = postprocess_title(title)
    return result.processed


# =============================================================================
# 데이터베이스 통합 함수
# =============================================================================

def postprocess_in_db(news_id: int) -> Optional[str]:
    """Post-process a title in the database.

    Args:
        news_id: News ID

    Returns:
        Processed title or None if no changes
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
    result = postprocess_title(original)

    if result.changes and result.processed != original:
        cursor.execute(
            "UPDATE news SET translated_title = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (result.processed, news_id)
        )
        conn.commit()
        conn.close()
        return result.processed

    conn.close()
    return None


def scan_all_for_postprocess() -> list[dict]:
    """Scan all titles and identify those that need post-processing.

    Returns:
        List of dicts with news_id, original, processed, changes
    """
    from src.database.models import get_connection

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT id, translated_title FROM news WHERE translated_title IS NOT NULL"
    )
    rows = cursor.fetchall()
    conn.close()

    needs_processing = []

    for row in rows:
        result = postprocess_title(row['translated_title'])
        if result.changes:
            needs_processing.append({
                'news_id': row['id'],
                'original': result.original,
                'processed': result.processed,
                'changes': result.changes,
            })

    return needs_processing


def postprocess_all(dry_run: bool = True) -> list[dict]:
    """Post-process all titles that need it.

    Args:
        dry_run: If True, only report without making changes

    Returns:
        List of processed items
    """
    from src.database.models import get_connection

    items = scan_all_for_postprocess()

    if dry_run or not items:
        return items

    conn = get_connection()
    cursor = conn.cursor()

    for item in items:
        cursor.execute(
            "UPDATE news SET translated_title = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (item['processed'], item['news_id'])
        )

    conn.commit()
    conn.close()

    return items

"""First-occurrence dual-script rendering for Korean news summaries.

Given a Korean text (summary / title / market impact) and the original Chinese
source text, replace the first Korean mention of each proper noun with a
dual-script form such as:

    시진핑 국가주석(习近平)
    닝더스다이(宁德时代, CATL)
    국무원(国务院)

Only the first occurrence per entity is rewritten. Subsequent mentions stay as
the plain Korean transliteration for readability. Entities are only considered
when their Chinese form actually appears in the original source, so we never
hallucinate affiliations.
"""

from __future__ import annotations

import re
from typing import Iterable

from src.utils.proper_nouns import all_entries


_KOREAN_CHAR = re.compile(r"[가-힣]")


def _render(info: dict, zh: str, kind: str) -> str:
    """Render a dual-script form for one entity."""
    ko = info["ko"]
    en = info.get("en")
    paren_parts: list[str] = [zh]
    if en and en != zh:
        paren_parts.append(en)
    paren = f"({', '.join(paren_parts)})"
    if kind == "person" and info.get("title_ko"):
        return f"{ko} {info['title_ko']}{paren}"
    return f"{ko}{paren}"


def _already_formatted(text: str, info: dict, zh: str) -> bool:
    """Return True if text already contains an inline dual-script mention."""
    ko = info["ko"]
    # If the Chinese key already appears next to the Korean form, assume the
    # LLM produced a dual-script mention and leave it alone.
    return zh in text and ko in text and f"{ko}(" in text


def format_proper_nouns(text: str | None, source_zh: str | None,
                        max_annotations: int | None = None) -> str | None:
    """Rewrite first Korean occurrence of each known entity with dual script.

    Args:
        text: Korean text to rewrite (summary, translated_title, market_impact).
        source_zh: Original Chinese text (title + content). Used to decide which
            entities are actually present in the source article.
        max_annotations: cap the number of entities annotated (None = unlimited).
            Candidates are processed longest/most-specific first, so the cap
            keeps the hardest-to-read full names (e.g. 닝더스다이, 중국인민은행)
            and drops generic short ones. Used for the public feed (2-3/item).

    Returns:
        The rewritten text, or the input unchanged when nothing applies. None
        inputs pass through untouched.
    """
    if not text or not source_zh:
        return text

    candidates: list[tuple[str, dict, str]] = []
    for zh, info, kind in all_entries():
        if zh not in source_zh:
            continue
        ko = info["ko"]
        if not ko or ko not in text:
            continue
        if _already_formatted(text, info, zh):
            continue
        candidates.append((zh, info, kind))

    # Longest Korean form first ("핑안보험" before "핑안"); on ties, prefer the
    # most specific Chinese form ("中国人民银行" before "央行").
    candidates.sort(key=lambda c: (len(c[1]["ko"]), len(c[0])), reverse=True)

    result = text
    used_ko: set[str] = set()
    # Token-based masking: each applied replacement is stored as an opaque
    # sentinel so subsequent regex passes can't re-match any character inside
    # an already-inserted annotation. This prevents substring collisions like
    # "광둥성(广东省)" being re-matched by the shorter "광둥" rule.
    tokens: list[str] = []
    for zh, info, kind in candidates:
        ko = info["ko"]
        if ko in used_ko:
            continue
        # Skip when: preceded by Korean (word-mid), already inside an existing
        # "(…)" or "（…）" parenthetical, or immediately followed by "(" of an
        # already-annotated form.
        pattern = re.compile(
            r"(?<![가-힣(（])" + re.escape(ko) + r"(?![)）])(?!\()"
        )
        replacement = _render(info, zh, kind)

        def _sub(_m, rep=replacement):
            tokens.append(rep)
            return f"\x01PN{len(tokens) - 1}\x01"

        new_result, n = pattern.subn(_sub, result, count=1)
        if n == 0:
            continue
        result = new_result
        used_ko.add(ko)
        if max_annotations and len(used_ko) >= max_annotations:
            break

    for i, tok in enumerate(tokens):
        result = result.replace(f"\x01PN{i}\x01", tok)
    return result


def format_fields(
    fields: dict[str, str | None],
    source_zh: str | None,
) -> dict[str, str | None]:
    """Apply formatter to multiple fields sharing one Chinese source."""
    return {k: format_proper_nouns(v, source_zh) for k, v in fields.items()}

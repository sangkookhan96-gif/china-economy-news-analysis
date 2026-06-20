"""CNI Quality Scorer — Rule-based evaluation framework.

Scores headline, summary, and tip quality on 0~5 scale per dimension.
No LLM required — uses regex, length checks, keyword matching.
"""

import re
import logging
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"
logger = logging.getLogger("quality_scorer")

# ── Vague words (headline) ──
VAGUE_WORDS_ZH = [
    "新突破", "新发展", "新进展", "新成效", "新局面",
    "推进", "加强", "深化", "持续", "不断", "进一步",
    "有关", "相关", "若干", "一些",
]

# ── Artifact patterns (summary) ──
SUMMARY_ARTIFACTS = [
    r"^Here is the summary",
    r"^Here is a summary",
    r"^Here's the",
    r"^\*\*中国经济新闻摘要\*\*",
    r"^\*\*Chinese Economy",
    r"^中国经济新闻摘要",
    r"^Summary:",
    r"^摘要[：:]",
]

# ── Entity patterns ──
ENTITY_PATTERNS = [
    r"[\d,.]+[%％]",             # percentage
    r"[\d,.]+[亿万]",            # amount
    r"[\d,.]+美元|[\d,.]+元",    # currency
    r"[A-Z]{2,}",               # acronyms (TSMC, GDP, AI)
]


def score_headline(title_zh: str, original_title: str = "", summary_zh: str = "") -> dict:
    """Score headline quality (0~5 per dimension, total 0~20).

    Dimensions:
    - who_what: Contains subject + action
    - no_vague: No vague/filler words
    - length: Within 25 chars
    - readability: Not a copy of original title
    """
    title_zh = (title_zh or "").strip()
    original_title = (original_title or "").strip()
    scores = {}

    # 1. WHO + WHAT (entity + verb)
    has_entity = bool(re.search(r'[\u4e00-\u9fff]{2,}', title_zh))  # Chinese entity
    has_number = bool(re.search(r'\d', title_zh))
    has_action = bool(re.search(r'[发布|推出|达|增|降|破|超|获|涨|跌|达成|实现|突破|完成|启动|落地|布局|收购|融资|上市|签约]', title_zh))
    who_what = 0
    if has_entity:
        who_what += 2
    if has_action:
        who_what += 2
    if has_number:
        who_what += 1
    scores["who_what"] = min(who_what, 5)

    # 2. No vague words
    vague_count = sum(1 for w in VAGUE_WORDS_ZH if w in title_zh)
    scores["no_vague"] = max(5 - vague_count * 2, 0)

    # 3. Length <= 25 chars
    tlen = len(title_zh)
    if tlen <= 25:
        scores["length"] = 5
    elif tlen <= 28:
        scores["length"] = 3
    elif tlen <= 32:
        scores["length"] = 1
    else:
        scores["length"] = 0

    # 4. Readability (not a copy of original)
    if not title_zh:
        scores["readability"] = 0
    elif original_title and title_zh == original_title:
        scores["readability"] = 1  # exact copy
    elif original_title and title_zh in original_title:
        scores["readability"] = 2  # substring
    else:
        scores["readability"] = 5

    scores["total"] = sum(scores.values())
    scores["max"] = 20
    scores["pct"] = round(scores["total"] / 20 * 100)
    return scores


def score_summary(summary_zh: str, original_content: str = "") -> dict:
    """Score summary quality (0~5 per dimension, total 0~20).

    Dimensions:
    - length: >= 300 chars
    - content: Contains core event + background + impact structure
    - entities: Contains specific entities (numbers, organizations, policies)
    - no_artifacts: No meta-descriptions, markdown, English preamble
    """
    summary_zh = (summary_zh or "").strip()
    scores = {}

    # 1. Length
    slen = len(summary_zh)
    if slen >= 400:
        scores["length"] = 5
    elif slen >= 300:
        scores["length"] = 4
    elif slen >= 200:
        scores["length"] = 2
    elif slen >= 100:
        scores["length"] = 1
    else:
        scores["length"] = 0

    # 2. Content structure (sentence count as proxy for depth)
    sentences = re.split(r'[。！？.!?]', summary_zh)
    sentences = [s for s in sentences if len(s.strip()) > 5]
    sent_count = len(sentences)
    if sent_count >= 6:
        scores["content"] = 5
    elif sent_count >= 4:
        scores["content"] = 4
    elif sent_count >= 3:
        scores["content"] = 3
    elif sent_count >= 2:
        scores["content"] = 2
    else:
        scores["content"] = 1

    # 3. Entities (numbers, percentages, org names, acronyms)
    entity_count = 0
    for pat in ENTITY_PATTERNS:
        entity_count += len(re.findall(pat, summary_zh))
    if entity_count >= 5:
        scores["entities"] = 5
    elif entity_count >= 3:
        scores["entities"] = 4
    elif entity_count >= 2:
        scores["entities"] = 3
    elif entity_count >= 1:
        scores["entities"] = 2
    else:
        scores["entities"] = 0

    # 4. No artifacts
    artifact_found = 0
    for pat in SUMMARY_ARTIFACTS:
        if re.search(pat, summary_zh, re.MULTILINE | re.IGNORECASE):
            artifact_found += 1
    if "**" in summary_zh:
        artifact_found += 1
    if artifact_found == 0:
        scores["no_artifacts"] = 5
    elif artifact_found == 1:
        scores["no_artifacts"] = 2
    else:
        scores["no_artifacts"] = 0

    scores["total"] = sum(scores.values())
    scores["max"] = 20
    scores["pct"] = round(scores["total"] / 20 * 100)
    return scores


def score_tip(tip: str, summary_ko: str = "") -> dict:
    """Score tip quality (0~5 per dimension, total 0~15).

    Dimensions:
    - no_repeat: Does not repeat summary
    - why_matters: Explains "why this matters"
    - implication: Contains market/policy implication
    """
    tip = (tip or "").strip()
    summary_ko = (summary_ko or "").strip()
    scores = {}

    if not tip:
        return {"no_repeat": 0, "why_matters": 0, "implication": 0,
                "total": 0, "max": 15, "pct": 0}

    # 1. No repeat (check overlap with summary)
    if summary_ko and len(tip) > 20:
        # Check if >50% of tip words appear in summary
        tip_words = set(tip.split())
        summary_words = set(summary_ko.split())
        overlap = len(tip_words & summary_words) / max(len(tip_words), 1)
        if overlap > 0.6:
            scores["no_repeat"] = 1
        elif overlap > 0.4:
            scores["no_repeat"] = 3
        else:
            scores["no_repeat"] = 5
    else:
        scores["no_repeat"] = 3  # can't check

    # 2. Why it matters (explanation keywords)
    why_keywords = ["의미", "중요", "배경", "이유", "때문", "영향", "결과", "효과", "변화"]
    why_count = sum(1 for k in why_keywords if k in tip)
    scores["why_matters"] = min(why_count * 2, 5)

    # 3. Market/policy implication
    impl_keywords = ["시장", "투자", "산업", "정책", "경제", "성장", "하락", "상승",
                      "전망", "리스크", "기회", "수출", "수입", "무역", "금리", "환율"]
    impl_count = sum(1 for k in impl_keywords if k in tip)
    scores["implication"] = min(impl_count * 2, 5)

    scores["total"] = sum(scores.values())
    scores["max"] = 15
    scores["pct"] = round(scores["total"] / 15 * 100)
    return scores


def score_all(title_zh="", summary_zh="", tip="",
              original_title="", original_content="", summary_ko="") -> dict:
    """Score all components and return combined result."""
    hl = score_headline(title_zh, original_title, summary_zh)
    sm = score_summary(summary_zh, original_content)
    tp = score_tip(tip, summary_ko)

    total = hl["total"] + sm["total"] + tp["total"]
    max_total = hl["max"] + sm["max"] + tp["max"]

    return {
        "headline": hl,
        "summary": sm,
        "tip": tp,
        "total": total,
        "max": max_total,
        "pct": round(total / max_total * 100) if max_total > 0 else 0,
        "grade": "A" if total >= 44 else "B" if total >= 33 else "C" if total >= 22 else "D",
    }

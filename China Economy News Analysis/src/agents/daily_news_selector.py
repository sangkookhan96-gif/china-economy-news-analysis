"""Daily news selection algorithm for expert review queue.

Selects exactly 10 news items daily based on scoring and diversity constraints.
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Optional
import numpy as np

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.database.models import get_connection

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Category quotas: (min, max)
CATEGORY_QUOTAS = {
    "policy/macro": (3, 4),
    "market/finance": (2, 3),
    "industry/company": (2, 3),
    "risk/other": (1, 2),
}

# Max items per source
MAX_PER_SOURCE = 2


def derive_category(content_type: Optional[str]) -> str:
    """Derive category from content_type field."""
    if content_type == "policy":
        return "policy/macro"
    elif content_type == "market":
        return "market/finance"
    elif content_type in ("corporate", "industry"):
        return "industry/company"
    else:
        return "risk/other"


def derive_macro_impact_score(market_impact: Optional[str]) -> float:
    """Derive numeric macro impact score from market_impact text.

    Uses keyword heuristics to estimate impact level.
    """
    if not market_impact:
        return 0.5

    text = market_impact.lower()
    score = 0.5

    # High impact indicators
    high_impact = ["significant", "major", "substantial", "strong", "high",
                   "중대", "큰", "강한", "상당한", "대폭", "급등", "급락",
                   "重大", "显著", "强烈", "大幅", "剧烈"]

    # Medium impact indicators
    medium_impact = ["moderate", "some", "certain", "noticeable",
                     "어느 정도", "일부", "보통", "다소",
                     "一定", "部分", "中等", "适度"]

    # Low impact indicators
    low_impact = ["minimal", "limited", "slight", "minor", "little",
                  "제한적", "미미", "적은", "경미",
                  "有限", "轻微", "小幅", "略"]

    for word in high_impact:
        if word in text:
            score = max(score, 0.8)
            break

    for word in medium_impact:
        if word in text:
            score = max(score, 0.6)

    for word in low_impact:
        if word in text:
            score = min(score, 0.3)

    return score


def cosine_similarity(vec1: list, vec2: list) -> float:
    """Calculate cosine similarity between two vectors."""
    if not vec1 or not vec2:
        return 0.0

    a = np.array(vec1)
    b = np.array(vec2)

    dot_product = np.dot(a, b)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)

    if norm_a == 0 or norm_b == 0:
        return 0.0

    return dot_product / (norm_a * norm_b)


def compute_reading_time(content_length: int) -> float:
    """Compute reading time in minutes from content length."""
    return (content_length / 1000) * 0.8


def calculate_base_score(
    importance_score: float,
    macro_impact_score: float,
    market_relevance_score: float,
    uncertainty_score: float,
    expert_explainability_score: float
) -> float:
    """Calculate base score using weighted sum."""
    return (
        0.45 * importance_score +
        0.25 * macro_impact_score +
        0.15 * market_relevance_score +
        0.10 * uncertainty_score +
        0.05 * expert_explainability_score
    )


def apply_penalties(
    base_score: float,
    reading_time: float,
    topic_similarity: float
) -> float:
    """Apply penalties to base score."""
    score = base_score

    # Topic similarity penalty
    if topic_similarity > 0.82:
        score -= 0.15

    # Reading time penalties
    if reading_time > 6:
        score -= 0.05
    elif reading_time < 2:
        score -= 0.08

    return max(0.0, score)


def get_eligible_candidates(conn) -> list:
    """Fetch all eligible news candidates for selection."""
    cursor = conn.cursor()

    # Calculate 48 hours ago
    cutoff_time = datetime.now() - timedelta(hours=48)

    cursor.execute("""
        SELECT
            id,
            source,
            content_type,
            importance_score,
            market_impact,
            market_relevance_score,
            uncertainty_score,
            expert_explainability_score,
            topic_vector,
            original_content,
            LENGTH(original_content) as content_length,
            published_at
        FROM news
        WHERE
            analyzed_at IS NOT NULL
            AND (expert_review_status = 'none' OR expert_review_status IS NULL)
            AND expert_review_status != 'skipped'  -- permanently exclude skipped items
            AND importance_score >= 0.65
            AND published_at >= ?
            AND LENGTH(original_content) >= 600
        ORDER BY importance_score DESC
    """, (cutoff_time.strftime("%Y-%m-%d %H:%M:%S"),))

    candidates = []
    for row in cursor.fetchall():
        topic_vector = None
        if row["topic_vector"]:
            try:
                topic_vector = json.loads(row["topic_vector"])
            except json.JSONDecodeError:
                pass

        candidates.append({
            "id": row["id"],
            "source": row["source"],
            "category": derive_category(row["content_type"]),
            "importance_score": row["importance_score"] or 0.5,
            "macro_impact_score": derive_macro_impact_score(row["market_impact"]),
            "market_relevance_score": row["market_relevance_score"] if row["market_relevance_score"] is not None else 0.5,
            "uncertainty_score": row["uncertainty_score"] if row["uncertainty_score"] is not None else 0.5,
            "expert_explainability_score": row["expert_explainability_score"] if row["expert_explainability_score"] is not None else 0.5,
            "topic_vector": topic_vector,
            "content_length": row["content_length"] or 0,
            "reading_time": compute_reading_time(row["content_length"] or 0),
        })

    return candidates


def compute_max_similarity(candidate: dict, selected: list) -> float:
    """Compute maximum topic similarity between candidate and selected items."""
    if not candidate["topic_vector"] or not selected:
        return 0.0

    max_sim = 0.0
    for item in selected:
        if item["topic_vector"]:
            sim = cosine_similarity(candidate["topic_vector"], item["topic_vector"])
            max_sim = max(max_sim, sim)

    return max_sim


def select_daily_news(target_count: int = 10) -> list:
    """Select exactly target_count news items for daily expert review.

    Applies scoring, penalties, and diversity constraints.

    Returns:
        List of selected news IDs.
    """
    conn = get_connection()

    try:
        candidates = get_eligible_candidates(conn)

        if not candidates:
            logger.warning("No eligible candidates found for daily selection")
            return []

        logger.info(f"Found {len(candidates)} eligible candidates")

        # Calculate initial scores (without topic similarity penalty)
        for c in candidates:
            c["base_score"] = calculate_base_score(
                c["importance_score"],
                c["macro_impact_score"],
                c["market_relevance_score"],
                c["uncertainty_score"],
                c["expert_explainability_score"]
            )
            # Initial score with reading time penalty only
            c["score"] = apply_penalties(
                c["base_score"],
                c["reading_time"],
                topic_similarity=0.0  # Will be updated during selection
            )

        # Sort by initial score
        candidates.sort(key=lambda x: x["score"], reverse=True)

        # Selection with constraints
        selected = []
        source_counts = {}
        category_counts = {cat: 0 for cat in CATEGORY_QUOTAS}

        def can_add_to_category(category: str) -> bool:
            """Check if we can add more items to this category."""
            current = category_counts[category]
            min_quota, max_quota = CATEGORY_QUOTAS[category]
            return current < max_quota

        def category_needs_more(category: str) -> bool:
            """Check if category needs more items to meet minimum."""
            current = category_counts[category]
            min_quota, _ = CATEGORY_QUOTAS[category]
            return current < min_quota

        def can_add_from_source(source: str) -> bool:
            """Check if we can add more items from this source."""
            return source_counts.get(source, 0) < MAX_PER_SOURCE

        # First pass: select high-scoring items respecting constraints
        remaining = candidates.copy()

        while len(selected) < target_count and remaining:
            best_candidate = None
            best_idx = -1
            best_adjusted_score = -1

            for idx, candidate in enumerate(remaining):
                # Check source constraint
                if not can_add_from_source(candidate["source"]):
                    continue

                # Check category constraint
                if not can_add_to_category(candidate["category"]):
                    continue

                # Calculate topic similarity penalty
                max_sim = compute_max_similarity(candidate, selected)
                adjusted_score = apply_penalties(
                    candidate["base_score"],
                    candidate["reading_time"],
                    topic_similarity=max_sim
                )

                # Prioritize categories that need to meet minimum quota
                needs_priority = category_needs_more(candidate["category"])
                priority_boost = 0.1 if needs_priority else 0.0

                final_score = adjusted_score + priority_boost

                if final_score > best_adjusted_score:
                    best_adjusted_score = final_score
                    best_candidate = candidate
                    best_idx = idx

            if best_candidate is None:
                # No more valid candidates
                break

            # Add selected candidate
            selected.append(best_candidate)
            source_counts[best_candidate["source"]] = source_counts.get(best_candidate["source"], 0) + 1
            category_counts[best_candidate["category"]] += 1
            remaining.pop(best_idx)

            logger.debug(f"Selected news {best_candidate['id']} (score: {best_adjusted_score:.3f}, "
                        f"category: {best_candidate['category']}, source: {best_candidate['source']})")

        # Log selection summary
        logger.info(f"Selected {len(selected)} news items")
        logger.info(f"Category distribution: {category_counts}")
        logger.info(f"Source distribution: {source_counts}")

        return [item["id"] for item in selected]

    finally:
        conn.close()


def update_selected_status(news_ids: list) -> int:
    """Update expert_review_status to 'queued_today' for selected items.

    Returns:
        Number of items updated.
    """
    if not news_ids:
        return 0

    conn = get_connection()
    cursor = conn.cursor()

    try:
        placeholders = ",".join("?" * len(news_ids))
        cursor.execute(f"""
            UPDATE news
            SET expert_review_status = 'queued_today',
                updated_at = ?
            WHERE id IN ({placeholders})
        """, [datetime.now()] + news_ids)

        conn.commit()
        updated = cursor.rowcount
        logger.info(f"Updated {updated} news items to 'queued_today' status")
        return updated

    finally:
        conn.close()


def reset_previous_queue() -> int:
    """Reset yesterday's 'queued_today' items that weren't reviewed.

    Items that were 'queued_today' but not reviewed become 'none' again
    so they can be considered for future selection.

    Returns:
        Number of items reset.
    """
    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            UPDATE news
            SET expert_review_status = 'none',
                updated_at = ?
            WHERE expert_review_status = 'queued_today'
        """, (datetime.now(),))

        conn.commit()
        reset_count = cursor.rowcount

        if reset_count > 0:
            logger.info(f"Reset {reset_count} items from previous queue")

        return reset_count

    finally:
        conn.close()


def run_daily_selection() -> dict:
    """Run the complete daily selection process.

    1. Reset previous day's queue
    2. Select new items
    3. Update their status

    Returns:
        Summary dict with selection results.
    """
    logger.info("Starting daily news selection")

    # Reset previous queue
    reset_count = reset_previous_queue()

    # Select new items
    selected_ids = select_daily_news(target_count=10)

    # Update status
    updated_count = update_selected_status(selected_ids)

    result = {
        "reset_count": reset_count,
        "selected_count": len(selected_ids),
        "updated_count": updated_count,
        "selected_ids": selected_ids,
        "timestamp": datetime.now().isoformat(),
    }

    logger.info(f"Daily selection complete: {result}")
    return result


def main():
    """Run daily selection from command line."""
    result = run_daily_selection()
    print(f"Daily selection completed:")
    print(f"  - Reset from previous queue: {result['reset_count']}")
    print(f"  - Selected: {result['selected_count']}")
    print(f"  - Updated to queued_today: {result['updated_count']}")
    print(f"  - News IDs: {result['selected_ids']}")


if __name__ == "__main__":
    main()

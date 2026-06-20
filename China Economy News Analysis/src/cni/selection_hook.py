"""CNI Selection Hook.

Runs after daily_news_selector:
1. queued_today 뉴스를 확인
2. 원문 충분(200자+)한 뉴스 → pipeline_status='selected' + summary_zh 자동 생성
3. 저작권 텍스트/원문 부족 뉴스 → 기존 legacy 방식 유지 (사용자가 직접 판단)

저작권 뉴스를 스킵하거나 삭제하지 않음.
"""

import logging
from datetime import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.database.models import get_connection
from src.cni.pipeline_service import set_pipeline_selected

LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "selection_hook.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
    force=True,
)
logger = logging.getLogger("selection_hook")

# Junk content patterns — not real article content
JUNK_PATTERNS = [
    "中经社官网",
    "版权声明合作伙伴",
    "版权所有 Copyright",
    "客服邮箱",
    "All Rights Reserved",
]

MIN_CONTENT_LENGTH = 200


def _has_real_content(content: str) -> bool:
    """Check if original_content is real article (not copyright boilerplate)."""
    if not content or len(content.strip()) < MIN_CONTENT_LENGTH:
        for pattern in JUNK_PATTERNS:
            if pattern in (content or ""):
                return False
        if len(content or "") < MIN_CONTENT_LENGTH:
            return False
    return True


def run_selection_hook():
    """Bridge queued_today → CNI pipeline.

    원문 충분한 뉴스: pipeline_status='selected' → summary_zh 자동 생성
    원문 부족/저작권: 기존 legacy 유지 (사용자가 직접 챗GPT 등으로 리뷰)
    """
    conn = get_connection()

    rows = conn.execute("""
        SELECT id, original_title, original_content, LENGTH(original_content) as clen
        FROM news
        WHERE expert_review_status = 'queued_today'
          AND pipeline_status IS NULL
    """).fetchall()
    conn.close()

    logger.info(f"=== Selection Hook: {len(rows)} queued_today news ===")

    if not rows:
        logger.info("No new queued_today news to process.")
        return {"cni": 0, "legacy": 0}

    cni_ids = []
    legacy_ids = []

    for r in rows:
        content = r["original_content"] or ""
        title = (r["original_title"] or "")[:40]

        if _has_real_content(content):
            cni_ids.append(r["id"])
            logger.info(f"  CNI #{r['id']}: {title}... ({r['clen']}자)")
        else:
            legacy_ids.append(r["id"])
            logger.info(f"  LEGACY #{r['id']}: {title}... ({r['clen']}자) — 사용자 직접 판단")

    # CNI 대상: pipeline_status = 'selected' 설정
    if cni_ids:
        count = set_pipeline_selected(cni_ids)
        logger.info(f"  → CNI selected: {count}건")

        # 강화 알고리즘: 데이터추출 → 구조화요약 → 검증 → 헤드라인 → Papago 자동번역
        logger.info("  Generating CNI fields (enhanced + Papago)...")
        from src.cni.generate_cni_fields import run as generate_fields
        generate_fields(limit=len(cni_ids), enable_papago=True)

    # Legacy 대상: 그대로 유지 (queued_today, pipeline_status=NULL)
    if legacy_ids:
        logger.info(f"  → Legacy 유지: {len(legacy_ids)}건 (사용자 직접 리뷰)")

    result = {"cni": len(cni_ids), "legacy": len(legacy_ids)}
    logger.info(f"=== Hook complete: CNI {result['cni']}건, Legacy {result['legacy']}건 ===")
    return result


if __name__ == "__main__":
    run_selection_hook()

"""CNI Edition Cycle — Single unified pipeline for each edition.

Runs as one atomic cycle:
  select → filter → summary → headline → papago → tip → ready_for_review

Designed for 3x daily execution: 07:00, 13:00, 20:00 KST
Target: 10 news in ≤12 minutes
"""

import time
import logging
import subprocess
import requests
import re
from datetime import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.database.models import get_connection
from src.cni.generate_cni_fields import (
    generate_enhanced, _deduplicate_text, _trim_to_complete_sentence,
)
from src.cni.pipeline_service import set_pipeline_selected, set_pipeline_status, publish_news
from src.cni.summary_store import update_translation, update_refined, init_cni_tables
from src.cni.postprocess import (
    remove_structure_tags, replace_company_names, fix_currency,
    explain_terms, ensure_polite_korean, fix_headline_terms,
)
from src.cni.political_check import check_and_neutralize

LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / f"edition_cycle_{datetime.now().strftime('%Y%m%d')}.log",
                            encoding="utf-8"),
        logging.StreamHandler(),
    ],
    force=True,
)
logger = logging.getLogger("edition_cycle")

# Config
STAGE_TIMEOUT = 90  # seconds per stage
MAX_NEWS_PER_EDITION = 10
JUNK_PATTERNS = ["中经社官网", "版权声明", "客服邮箱"]
MIN_CONTENT_LENGTH = 200


def _ensure_ollama():
    """Ensure Ollama is running."""
    try:
        r = requests.get("http://localhost:11434/api/tags", timeout=5)
        if r.status_code == 200:
            return True
    except:
        pass
    logger.warning("Ollama not running, starting...")
    subprocess.Popen(["ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(8)
    try:
        r = requests.get("http://localhost:11434/api/tags", timeout=10)
        return r.status_code == 200
    except:
        return False


def _is_processable(content):
    """Check if news has enough content for CNI auto-processing.

    선정 알고리즘 결과는 모두 존중 — 대시보드에서 전부 표시.
    여기서는 자동번역 처리 가능 여부만 판단.
    """
    content = content or ""
    # 본문 200자 이상이면 자동 처리 가능
    if len(content) >= 200 and not any(kw in content for kw in JUNK_PATTERNS):
        return True
    return False


def run_edition_cycle(edition: str = None, max_news: int = MAX_NEWS_PER_EDITION):
    """Run one complete edition cycle.

    Args:
        edition: 'morning', 'afternoon', 'evening' or None (auto-detect)
        max_news: Maximum news to process per cycle
    """
    cycle_start = time.time()
    init_cni_tables()

    if not _ensure_ollama():
        logger.error("FATAL: Ollama unavailable. Aborting cycle.")
        return {"error": "Ollama unavailable"}

    # Auto-detect edition
    if not edition:
        hour = datetime.now().hour
        if hour < 10:
            edition = "morning"
        elif hour < 17:
            edition = "afternoon"
        else:
            edition = "evening"

    logger.info(f"{'='*60}")
    logger.info(f"  EDITION CYCLE: {edition}")
    logger.info(f"  Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    logger.info(f"{'='*60}")

    stats = {
        "edition": edition, "total": 0, "selected": 0, "translated": 0,
        "junk": 0, "legacy": 0, "failed": 0, "tips": 0,
        "stage_times": {},
    }

    # ═══════════════════════════════════
    # Stage 1: Select (뉴스 선정)
    # ═══════════════════════════════════
    t1 = time.time()
    logger.info("[1/6] Selecting news...")

    conn = get_connection()
    # queued_today + pipeline 미진입 뉴스
    candidates = conn.execute("""
        SELECT id, original_title, original_content, LENGTH(original_content) as clen
        FROM news
        WHERE expert_review_status = 'queued_today'
          AND pipeline_status IS NULL
          AND original_content IS NOT NULL
        ORDER BY importance_score DESC
    """).fetchall()
    conn.close()

    stats["total"] = len(candidates)
    stats["stage_times"]["select"] = time.time() - t1
    logger.info(f"  Candidates: {len(candidates)}건")

    # ═══════════════════════════════════
    # Stage 2: Filter (부적합 제거)
    # ═══════════════════════════════════
    t2 = time.time()
    logger.info("[2/6] Filtering...")

    valid_ids = []
    manual_ids = []  # 자동처리 불가 → 사용자 직접 처리
    for r in candidates:
        content = r["original_content"] or ""
        if _is_processable(content):
            valid_ids.append(r["id"])
        else:
            manual_ids.append(r["id"])
            stats["legacy"] += 1

    # Limit to max_news
    valid_ids = valid_ids[:max_news]
    stats["selected"] = len(valid_ids)
    stats["stage_times"]["filter"] = time.time() - t2
    logger.info(f"  Auto-processable: {len(valid_ids)}건, Manual: {len(manual_ids)}건")

    if not valid_ids:
        logger.info("  No auto-processable news. Manual review needed.")
        _report(stats, cycle_start)
        return stats

    # Set pipeline_status = 'selected'
    set_pipeline_selected(valid_ids)

    # ═══════════════════════════════════
    # Stage 3-6: Process each news
    # ═══════════════════════════════════
    for i, nid in enumerate(valid_ids, 1):
        news_start = time.time()
        conn = get_connection()
        row = conn.execute(
            "SELECT original_title, original_content FROM news WHERE id=?", (nid,)
        ).fetchone()
        conn.close()

        logger.info(f"[{i}/{len(valid_ids)}] #{nid}: {(row['original_title'] or '')[:40]}...")

        try:
            # Stage 3: Generate (summary_zh + title_zh + tip)
            result = generate_enhanced(
                nid, row["original_title"], row["original_content"],
                enable_papago=True)

            if not result:
                stats["failed"] += 1
                logger.warning(f"  Generation failed")
                continue

            # Save to DB
            conn = get_connection()
            conn.execute("""
                UPDATE news SET summary_zh=?, title_zh=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
            """, (result["summary_zh"], result["title_zh"], nid))

            if result.get("card_headline"):
                conn.execute("UPDATE news SET card_headline=? WHERE id=?",
                             (result["card_headline"][:72], nid))

            if result.get("hansanguk_tip"):
                conn.execute("UPDATE news SET hansanguk_tip=? WHERE id=?",
                             (result["hansanguk_tip"][:500], nid))
                stats["tips"] += 1

            conn.commit()
            conn.close()

            # Save Korean translation
            if result.get("summary_ko"):
                update_translation(nid, result["summary_ko"])
                update_refined(nid, result["summary_ko"])

                # 근거 문장 추출 (원문에서)
                try:
                    from src.cni.evidence_extractor import extract_evidence
                    evidence = extract_evidence(
                        result["summary_ko"], row["original_content"])
                    if evidence:
                        _econn = get_connection()
                        _econn.execute("UPDATE news SET evidence_sentences=? WHERE id=?",
                                       (evidence[:500], nid))
                        _econn.commit()
                        _econn.close()
                except Exception:
                    pass  # 비차단

                # 분석 생성 (별도 프로세스)
                try:
                    from src.cni.news_analyzer import analyze_news
                    analysis = analyze_news(nid, row["original_content"],
                                            result.get("summary_zh", ""))
                    if analysis.get("analysis_ko"):
                        _aconn = get_connection()
                        _aconn.execute("UPDATE news SET analysis_ko=? WHERE id=?",
                                       (analysis["analysis_ko"][:1000], nid))
                        _aconn.commit()
                        _aconn.close()
                except Exception:
                    pass  # 비차단

                # Check current status — don't downgrade
                _conn = get_connection()
                _cur = _conn.execute("SELECT pipeline_status FROM news WHERE id=?", (nid,)).fetchone()
                _conn.close()
                if _cur and _cur["pipeline_status"] != "published":
                    set_pipeline_status(nid, "translated")

                # Clear from recommendation box
                _conn = get_connection()
                _conn.execute("""
                    UPDATE news SET expert_review_status='commented'
                    WHERE id=? AND expert_review_status='queued_today'
                      AND pipeline_status IN ('translated', 'published')
                """, (nid,))
                _conn.commit()
                _conn.close()

                stats["translated"] += 1

            # ── 검증 레이어 (3중 검증) ──
            try:
                from src.cni.fact_checker import verify_all
                verification = verify_all(
                    summary_ko=result.get("summary_ko", ""),
                    analysis_ko="",
                    tip=result.get("hansanguk_tip", ""),
                    original_content=row["original_content"] or "",
                )
                if not verification["passed"]:
                    actions = verification.get("actions", [])
                    logger.warning(f"  VERIFICATION FAIL: {verification['total_issues']} issues, actions={actions}")

                    conn2 = get_connection()

                    # 팁 문제 → 즉시 삭제
                    if "tip_remove" in actions:
                        conn2.execute("UPDATE news SET hansanguk_tip=NULL WHERE id=?", (nid,))
                        logger.warning(f"  ACTION: tip removed")

                    # 요약 치명 오류 → 차단 (translated로 전환 안 함)
                    if "summary_block" in actions:
                        conn2.execute("UPDATE news SET pipeline_status='selected' WHERE id=?", (nid,))
                        logger.warning(f"  ACTION: summary blocked, reverted to selected")
                        stats["failed"] += 1
                        stats["translated"] -= 1

                    # 경고 수준 → 로그만
                    if "summary_warn" in actions:
                        logger.warning(f"  ACTION: summary warning — {verification['summary']['issues']}")

                    conn2.commit()
                    conn2.close()
                else:
                    logger.info(f"  Verification: PASS")
            except Exception as ve:
                logger.warning(f"  Verification skipped: {ve}")

            dur = time.time() - news_start
            logger.info(f"  OK ({dur:.0f}s) HL:{result.get('card_headline','')[:25]} "
                        f"Tip:{'Y' if result.get('hansanguk_tip') else 'N'}")

        except Exception as e:
            stats["failed"] += 1
            logger.error(f"  ERROR: {e}")

    _report(stats, cycle_start)
    return stats


def _report(stats, cycle_start):
    """Print cycle report."""
    duration = time.time() - cycle_start

    report = f"""
{'='*60}
  EDITION CYCLE REPORT
  Edition: {stats['edition']}
  Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}
  Duration: {duration:.0f}s ({duration/60:.1f}min)
{'='*60}
  Total candidates:   {stats['total']}
  Selected:           {stats['selected']}
  Translated (Papago): {stats['translated']}
  Tips generated:     {stats['tips']}
  Junk filtered:      {stats['junk']}
  Legacy (manual):    {stats['legacy']}
  Failed:             {stats['failed']}

  Avg per news:       {duration/max(stats['selected'],1):.0f}s
  Target (12min/10):  {'✅ PASS' if duration < 720 or stats['selected'] == 0 else '⚠️ OVER'}
{'='*60}
"""
    logger.info(report)
    print(report)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="CNI Edition Cycle")
    parser.add_argument("--edition", choices=["morning", "afternoon", "evening"])
    parser.add_argument("--max", type=int, default=MAX_NEWS_PER_EDITION)
    args = parser.parse_args()
    run_edition_cycle(edition=args.edition, max_news=args.max)

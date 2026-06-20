"""CNI System Stabilizer — 7-step platform stability check.

Usage:
    python3 src/cni/system_stabilizer.py
    python3 src/cni/system_stabilizer.py --fix  (auto-fix issues)
"""

import sqlite3
import re
import os
import glob
import logging
import requests
from datetime import datetime, date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.database.models import get_connection
from src.database.kg_models import get_kg_connection

LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "system_stabilizer.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
    force=True,
)
logger = logging.getLogger("stabilizer")


class StabilizationReport:
    def __init__(self):
        self.checks = []
        self.warnings = []
        self.errors = []
        self.fixes = []

    def ok(self, msg):
        self.checks.append(("✅", msg))

    def warn(self, msg):
        self.warnings.append(msg)
        self.checks.append(("⚠️", msg))

    def error(self, msg):
        self.errors.append(msg)
        self.checks.append(("❌", msg))

    def fixed(self, msg):
        self.fixes.append(msg)
        self.checks.append(("🔧", msg))

    def summary(self):
        total = len(self.checks)
        ok = sum(1 for s, _ in self.checks if s == "✅")
        warn = len(self.warnings)
        err = len(self.errors)
        fix = len(self.fixes)
        return total, ok, warn, err, fix


def step1_pipeline_status(report, auto_fix=False):
    """[1] 상태 불일치 점검 + 자동 복구."""
    logger.info("[1/7] Pipeline status conflicts...")
    conn = get_connection()

    # published인데 summary_ko 없음
    rows = conn.execute("""
        SELECT n.id FROM news n
        LEFT JOIN cni_summaries cs ON n.id = cs.news_id
        WHERE n.pipeline_status = 'published'
          AND (cs.summary_ko IS NULL OR cs.summary_ko = '')
    """).fetchall()
    if rows:
        ids = [r[0] for r in rows]
        report.warn(f"published인데 summary_ko 없음: {ids}")
        if auto_fix:
            for nid in ids:
                conn.execute("UPDATE news SET pipeline_status='translated' WHERE id=?", (nid,))
                report.fixed(f"#{nid}: published→translated (summary_ko 없음)")
            conn.commit()
    else:
        report.ok("published 뉴스 전원 summary_ko 보유")

    # translated인데 card_headline 없음
    rows = conn.execute("""
        SELECT id FROM news
        WHERE pipeline_status = 'translated' AND (card_headline IS NULL OR card_headline = '')
    """).fetchall()
    if rows:
        ids = [r[0] for r in rows]
        report.warn(f"translated인데 card_headline 없음: {ids}")
    else:
        report.ok("translated 뉴스 전원 card_headline 보유")

    # queued_today인데 unpublished (추천함에 비공개가 혼재)
    rows = conn.execute("""
        SELECT id FROM news
        WHERE expert_review_status = 'queued_today' AND pipeline_status = 'unpublished'
    """).fetchall()
    if rows:
        ids = [r[0] for r in rows]
        report.warn(f"queued_today+unpublished 혼재: {ids}")
    else:
        report.ok("추천함에 비공개 뉴스 혼재 없음")

    # published_at 없는 published
    rows = conn.execute("""
        SELECT n.id FROM news n
        JOIN cni_summaries cs ON n.id = cs.news_id
        WHERE n.pipeline_status = 'published' AND cs.published_at IS NULL
    """).fetchall()
    if rows:
        ids = [r[0] for r in rows]
        report.warn(f"published인데 published_at 없음: {ids}")
        if auto_fix:
            for nid in ids:
                conn.execute(
                    "UPDATE cni_summaries SET published_at=datetime('now') WHERE news_id=?", (nid,))
                report.fixed(f"#{nid}: published_at 보정")
            conn.commit()
    else:
        report.ok("published 뉴스 전원 published_at 보유")

    conn.close()


def step2_translation_quality(report, auto_fix=False):
    """[2] 번역 품질 검증 (fact_checker 통합)."""
    logger.info("[2/7] Translation quality (3-layer verification)...")
    conn = get_connection()

    rows = conn.execute("""
        SELECT n.id, n.card_headline, n.hansanguk_tip, n.original_content,
               cs.summary_ko
        FROM news n JOIN cni_summaries cs ON n.id = cs.news_id
        WHERE cs.summary_ko IS NOT NULL AND n.pipeline_status IN ('translated','published')
    """).fetchall()

    from src.cni.fact_checker import verify_all
    critical = 0
    warnings = 0

    for r in rows:
        v = verify_all(
            summary_ko=r["summary_ko"] or "",
            analysis_ko="",
            tip=r["hansanguk_tip"] or "",
            original_content=r["original_content"] or "",
        )
        if not v["passed"]:
            actions = v.get("actions", [])
            if "summary_block" in actions or "tip_remove" in actions:
                critical += 1
                report.error(f"#{r['id']}: 치명 오류 — {v['summary']['issues'] + v['tip']['issues']}")
                if auto_fix and "tip_remove" in actions:
                    conn.execute("UPDATE news SET hansanguk_tip=NULL WHERE id=?", (r["id"],))
                    report.fixed(f"#{r['id']}: 팁 자동 삭제")
            else:
                warnings += 1

    if auto_fix:
        conn.commit()
    conn.close()

    if critical == 0 and warnings == 0:
        report.ok(f"번역 품질 전원 통과 ({len(rows)}건, 3중 검증)")
    elif critical > 0:
        report.error(f"치명 오류 {critical}건 발견")
    else:
        report.warn(f"경고 {warnings}건 (치명 0)")

    # 기존 호환용 — 추가 체크
    issues = {"chinese_hl": 0, "chinese_ko": 0, "tags": 0, "casual": 0,
              "currency": 0, "short": 0, "markdown": 0}
    for r in rows:
        hl = r["card_headline"] or ""
        ko = r["summary_ko"] or ""
        if re.search(r'[\u4e00-\u9fff]', hl):
            issues["chinese_hl"] += 1
        if re.search(r'\[핵심|\[배경|【', ko):
            issues["tags"] += 1
        if any(c in ko for c in ['했다.', '있다.', '됐다.', '한다.']):
            issues["casual"] += 1
        if '억 원' in ko or '조 원' in ko:
            issues["currency"] += 1
        if len(ko) < 200:
            issues["short"] += 1
        if '**' in ko:
            issues["markdown"] += 1

    total_issues = sum(issues.values())
    if total_issues == 0:
        report.ok(f"번역 품질 전원 통과 ({len(rows)}건)")
    else:
        for k, v in issues.items():
            if v > 0:
                report.warn(f"번역 품질 {k}: {v}건")

    conn.close()


def step3_dashboard_integrity(report):
    """[3] 대시보드 데이터 정합성."""
    logger.info("[3/7] Dashboard integrity...")
    conn = get_connection()

    # 추천함에 부적합 뉴스
    junk = conn.execute("""
        SELECT COUNT(*) FROM news
        WHERE expert_review_status = 'queued_today'
          AND LENGTH(original_content) < 200
          AND (original_content LIKE '%中经社官网%' OR original_content LIKE '%版权声明%')
    """).fetchone()[0]

    if junk > 0:
        report.warn(f"추천함에 부적합 뉴스 {junk}건 노출")
    else:
        report.ok("추천함 부적합 뉴스 없음")

    # 공개함 데이터
    pub_cni = conn.execute("SELECT COUNT(*) FROM news WHERE pipeline_status='published'").fetchone()[0]
    pub_leg = conn.execute("SELECT COUNT(*) FROM expert_reviews WHERE publish_status='published'").fetchone()[0]
    report.ok(f"공개함: CNI {pub_cni}건 + Legacy {pub_leg}건")

    # 비공개함 데이터
    unpub = conn.execute("SELECT COUNT(*) FROM news WHERE pipeline_status='unpublished'").fetchone()[0]
    skipped = conn.execute("SELECT COUNT(*) FROM news WHERE pipeline_status='skipped'").fetchone()[0]
    report.ok(f"비공개함: unpublished {unpub}건 + skipped {skipped}건")

    conn.close()


def step4_guardrails(report):
    """[4] 가드레일 점검."""
    logger.info("[4/7] Guardrails...")

    # Ollama
    try:
        r = requests.get("http://localhost:11434/api/tags", timeout=5)
        models = [m["name"] for m in r.json().get("models", [])]
        report.ok(f"Ollama OK: {models}")
    except Exception as e:
        report.error(f"Ollama 연결 실패: {e}")

    # Papago
    try:
        from src.cni.translator import papago_translate, get_api_usage_today
        result = papago_translate("测试")
        usage = get_api_usage_today()
        if result:
            report.ok(f"Papago OK: 잔여 {usage['remaining']}/{usage['limit']}자")
        else:
            report.warn("Papago 번역 실패 (키 또는 쿼터 문제)")
    except Exception as e:
        report.error(f"Papago 연결 실패: {e}")

    # Flask
    try:
        r = requests.get("http://localhost:8502", timeout=5)
        if r.status_code == 200:
            report.ok("Flask (8502) OK")
        else:
            report.error(f"Flask HTTP {r.status_code}")
    except:
        report.error("Flask (8502) 연결 실패")

    # Streamlit
    try:
        r = requests.get("http://localhost:8501", timeout=5)
        if r.status_code == 200:
            report.ok("Streamlit (8501) OK")
        else:
            report.error(f"Streamlit HTTP {r.status_code}")
    except:
        report.error("Streamlit (8501) 연결 실패")


def step5_error_log_analysis(report):
    """[5] 오늘 오류 로그 분석."""
    logger.info("[5/7] Error log analysis...")

    today = date.today().isoformat().replace("-", "")
    error_counts = {"timeout": 0, "json": 0, "db_lock": 0, "ollama": 0, "papago": 0, "other": 0}

    log_files = glob.glob(str(LOG_DIR / f"*{today}*.log")) + glob.glob(str(LOG_DIR / "*.log"))
    checked = 0

    for lf in log_files:
        try:
            with open(lf, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if today.replace("2026", "2026-") not in line and "2026-04" not in line:
                        continue
                    if "ERROR" in line or "error" in line.lower():
                        checked += 1
                        ll = line.lower()
                        if "timeout" in ll:
                            error_counts["timeout"] += 1
                        elif "json" in ll:
                            error_counts["json"] += 1
                        elif "locked" in ll:
                            error_counts["db_lock"] += 1
                        elif "ollama" in ll:
                            error_counts["ollama"] += 1
                        elif "papago" in ll:
                            error_counts["papago"] += 1
                        else:
                            error_counts["other"] += 1
        except:
            pass

    total_errors = sum(error_counts.values())
    if total_errors == 0:
        report.ok("오늘 오류 0건")
    else:
        for k, v in error_counts.items():
            if v > 0:
                report.warn(f"오류 {k}: {v}건")


def step6_browser_accessibility(report):
    """[6] 브라우저 접근성 검증."""
    logger.info("[6/7] Browser accessibility...")

    try:
        from src.api.public_feed import get_news_by_id
        conn = get_connection()
        pub_ids = [r[0] for r in conn.execute(
            "SELECT id FROM news WHERE pipeline_status='published'"
        ).fetchall()]
        conn.close()

        if not pub_ids:
            report.ok("CNI published 0건 (검증 대상 없음)")
            return

        accessible = 0
        for nid in pub_ids:
            if get_news_by_id(nid):
                accessible += 1

        if accessible == len(pub_ids):
            report.ok(f"브라우저 접근 {accessible}/{len(pub_ids)}건 전원 OK")
        else:
            report.error(f"브라우저 접근 실패: {accessible}/{len(pub_ids)}건")
    except Exception as e:
        report.error(f"접근성 검증 실패: {e}")


def step7_comprehensive(report):
    """[7] 종합 시스템 상태."""
    logger.info("[7/7] Comprehensive check...")
    conn = get_connection()

    # KG 상태
    try:
        kg_ent = conn.execute("SELECT COUNT(*) FROM kg_entities WHERE status='active'").fetchone()[0]
        kg_evt = conn.execute("SELECT COUNT(*) FROM kg_events").fetchone()[0]
        kg_rel = conn.execute("SELECT COUNT(*) FROM kg_relations").fetchone()[0]
        report.ok(f"KG: entities={kg_ent} events={kg_evt} relations={kg_rel}")
    except:
        report.warn("KG 테이블 접근 불가")

    # CNI 상태
    cni_total = conn.execute("SELECT COUNT(*) FROM cni_summaries").fetchone()[0]
    cni_ko = conn.execute("SELECT COUNT(*) FROM cni_summaries WHERE summary_ko IS NOT NULL").fetchone()[0]
    report.ok(f"CNI summaries: {cni_total}건 (번역완료: {cni_ko}건)")

    # 전체 뉴스
    total = conn.execute("SELECT COUNT(*) FROM news").fetchone()[0]
    report.ok(f"전체 뉴스: {total}건")

    conn.close()


def run_stabilization(auto_fix=False):
    """Run full 7-step stabilization."""
    report = StabilizationReport()

    logger.info("=" * 60)
    logger.info("  SYSTEM STABILIZATION")
    logger.info(f"  Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    logger.info(f"  Auto-fix: {auto_fix}")
    logger.info("=" * 60)

    step1_pipeline_status(report, auto_fix)
    step2_translation_quality(report)
    step3_dashboard_integrity(report)
    step4_guardrails(report)
    step5_error_log_analysis(report)
    step6_browser_accessibility(report)
    step7_comprehensive(report)

    # Report
    total, ok, warn, err, fix = report.summary()

    output = f"""
{'='*60}
  STABILIZATION REPORT
  Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}
  Auto-fix: {auto_fix}
{'='*60}

"""
    for symbol, msg in report.checks:
        output += f"  {symbol} {msg}\n"

    verdict = "STABLE" if err == 0 else "UNSTABLE"
    output += f"""
  --- Summary ---
  Total checks: {total}
  OK: {ok} | Warnings: {warn} | Errors: {err} | Fixes: {fix}

  Verdict: {verdict}
{'='*60}
"""

    print(output)
    logger.info(output)

    with open(LOG_DIR / "system_stabilizer.log", "a", encoding="utf-8") as f:
        f.write(output)

    return err == 0


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--fix", action="store_true", help="Auto-fix issues")
    args = parser.parse_args()
    stable = run_stabilization(auto_fix=args.fix)
    sys.exit(0 if stable else 1)

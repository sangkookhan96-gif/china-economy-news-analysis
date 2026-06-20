"""CQ Executor — CQ를 Neo4j에서 실행하고 그래프를 자동 보완.

흐름: CQ → Cypher 실행 → 결과 판단 → 부족 시 노드/관계 MERGE → 로그 기록.
비동기 전용 — 뉴스 파이프라인 무영향.
"""

import hashlib
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.kg.cq_generator import generate_cqs, cq_to_cyphers
from src.kg.neo4j_adapter import (
    is_available, session, merge_entity, merge_relation,
    merge_news, TYPE_TO_LABEL, get_graph_stats
)

LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / f"cq_executor_{datetime.now().strftime('%Y%m%d')}.log",
                            encoding="utf-8"),
        logging.StreamHandler(),
    ],
    force=True,
)
logger = logging.getLogger("kg.cq_exec")

# CQ 로그 파일
CQ_LOG = LOG_DIR / f"cq_results_{datetime.now().strftime('%Y%m%d')}.log"

# 그래프 보완 임계값
MIN_WEIGHT = 0.3        # 이 이하면 관계 강화
WEIGHT_BOOST = 0.3      # 강화 시 추가 weight
NEW_RELATION_WEIGHT = 0.5  # 신규 관계 초기 weight


def execute_cq(news_id: int, cq: dict) -> dict:
    """단일 CQ를 Neo4j에서 실행하고 결과 반환.

    Returns:
        {"cq": str, "type": str, "queries_run": int,
         "results_found": int, "nodes_added": int, "relations_added": int}
    """
    stats = {
        "cq": cq["question"][:80],
        "type": cq["type"],
        "queries_run": 0,
        "results_found": 0,
        "nodes_added": 0,
        "relations_added": 0,
    }

    if not is_available():
        stats["error"] = "neo4j_unavailable"
        return stats

    cyphers = cq_to_cyphers(cq)

    for q in cyphers:
        with session() as s:
            if s is None:
                continue
            try:
                result = s.run(q["cypher"], **q["params"])
                records = result.data()
                stats["queries_run"] += 1
                stats["results_found"] += len(records)

                if len(records) > 0:
                    logger.info(f"    CQ hit: {q['entity']} → {len(records)} results")
                else:
                    # ── 그래프 보완: 결과 없음 → 엔티티 + 관계 추론 생성 ──
                    added = _expand_graph(cq, q["entity"])
                    stats["nodes_added"] += added["nodes"]
                    stats["relations_added"] += added["relations"]

            except Exception as e:
                logger.warning(f"    Cypher error: {e}")

    return stats


def _expand_graph(cq: dict, entity_name: str) -> dict:
    """CQ 결과가 없을 때 그래프를 보완.

    CQ 유형에 따라 적절한 노드와 관계를 MERGE.
    """
    added = {"nodes": 0, "relations": 0}
    cq_type = cq["type"]
    entities = cq.get("entities", [])

    # 1. 언급된 모든 엔티티를 노드로 보장
    entity_ids = {}
    for ent in entities:
        etype = _infer_entity_type(ent, cq_type)
        eid = f"KG-ENT-{etype}-{hashlib.md5(ent.encode()).hexdigest()[:8]}"
        label = TYPE_TO_LABEL.get(etype, "Company")

        if merge_entity(eid, ent, etype, ent):
            added["nodes"] += 1
            entity_ids[ent] = (eid, label)
            logger.info(f"    +Node: {label}({ent})")

    # 2. CQ 유형별 관계 추론
    if cq_type == "POLICY" and len(entity_ids) >= 2:
        # 첫 엔티티 → 나머지에 AFFECTS 관계
        items = list(entity_ids.items())
        src_name, (src_id, src_label) = items[0]
        for tgt_name, (tgt_id, tgt_label) in items[1:]:
            if merge_relation(src_id, tgt_id, "AFFECTS", src_label, tgt_label,
                             weight=NEW_RELATION_WEIGHT):
                added["relations"] += 1
                logger.info(f"    +Rel: {src_name} -[AFFECTS]-> {tgt_name}")

    elif cq_type == "INDUSTRY" and len(entity_ids) >= 2:
        # 기업 → 산업 BELONGS_TO
        items = list(entity_ids.items())
        for j in range(1, len(items)):
            src_name, (src_id, src_label) = items[j]
            tgt_name, (tgt_id, tgt_label) = items[0]
            if merge_relation(src_id, tgt_id, "BELONGS_TO", src_label, tgt_label,
                             weight=NEW_RELATION_WEIGHT):
                added["relations"] += 1
                logger.info(f"    +Rel: {src_name} -[BELONGS_TO]-> {tgt_name}")

    elif cq_type == "COMPANY" and len(entity_ids) >= 2:
        # 기업 간 RELATED_TO
        items = list(entity_ids.items())
        for j in range(1, len(items)):
            src_name, (src_id, src_label) = items[0]
            tgt_name, (tgt_id, tgt_label) = items[j]
            if merge_relation(src_id, tgt_id, "RELATED_TO", src_label, tgt_label,
                             weight=NEW_RELATION_WEIGHT):
                added["relations"] += 1
                logger.info(f"    +Rel: {src_name} -[RELATED_TO]-> {tgt_name}")

    elif cq_type == "KOREA_IMPACT" and len(entity_ids) >= 2:
        # 영향 경로 IMPACTS
        items = list(entity_ids.items())
        src_name, (src_id, src_label) = items[0]
        for tgt_name, (tgt_id, tgt_label) in items[1:]:
            if merge_relation(src_id, tgt_id, "IMPACTS", src_label, tgt_label,
                             weight=NEW_RELATION_WEIGHT):
                added["relations"] += 1
                logger.info(f"    +Rel: {src_name} -[IMPACTS]-> {tgt_name}")

    return added


def _infer_entity_type(name: str, cq_type: str) -> str:
    """엔티티 이름과 CQ 유형에서 엔티티 타입 추론."""
    n = name.lower()
    if any(kw in n for kw in ["정책", "법", "규제", "조례", "계획", "政策", "法"]):
        return "POL"
    if any(kw in n for kw in ["산업", "시장", "분야", "业", "行业"]):
        return "IND"
    if any(kw in n for kw in ["중국", "한국", "미국", "일본", "EU", "中国", "韩国"]):
        return "GEO"
    if cq_type == "POLICY":
        return "POL"
    if cq_type == "INDUSTRY":
        return "IND"
    return "COM"


def _log_cq_result(news_id: int, stats: dict):
    """CQ 실행 결과 로그."""
    try:
        with open(CQ_LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat()}|#{news_id}|"
                    f"type={stats['type']}|"
                    f"queries={stats['queries_run']}|"
                    f"results={stats['results_found']}|"
                    f"+nodes={stats['nodes_added']}|"
                    f"+rels={stats['relations_added']}|"
                    f"cq={stats['cq']}\n")
    except Exception:
        pass


# ══════════════════════════════════════
# Public API
# ══════════════════════════════════════

def process_news_cqs(news_id: int, title: str, summary: str) -> dict:
    """뉴스 1건에 대해 CQ 생성 → 실행 → 보완 전체 프로세스.

    Args:
        news_id: 뉴스 ID
        title: 원문 제목
        summary: 요약 (한국어 또는 중국어)

    Returns:
        {"cqs_generated": int, "cqs_executed": int,
         "total_results": int, "nodes_added": int, "relations_added": int}
    """
    if not is_available():
        return {"error": "neo4j_unavailable"}

    t0 = time.time()

    # 1. CQ 생성
    cqs = generate_cqs(title, summary)
    logger.info(f"  CQ generated: {len(cqs)}")

    if not cqs:
        return {"cqs_generated": 0}

    # 2. News 노드 보장
    merge_news(news_id, title)

    # 3. CQ 순차 실행
    totals = {
        "cqs_generated": len(cqs),
        "cqs_executed": 0,
        "total_results": 0,
        "nodes_added": 0,
        "relations_added": 0,
    }

    for i, cq in enumerate(cqs, 1):
        logger.info(f"  CQ[{i}/{len(cqs)}] [{cq['type']}] {cq['question'][:50]}...")
        stats = execute_cq(news_id, cq)
        totals["cqs_executed"] += 1
        totals["total_results"] += stats["results_found"]
        totals["nodes_added"] += stats["nodes_added"]
        totals["relations_added"] += stats["relations_added"]
        _log_cq_result(news_id, stats)

    dur = time.time() - t0
    totals["duration_sec"] = round(dur, 1)
    logger.info(f"  CQ complete: {totals['cqs_executed']} CQs, "
                f"+{totals['nodes_added']} nodes, +{totals['relations_added']} rels, "
                f"{dur:.0f}s")

    return totals


# ══════════════════════════════════════
# CLI
# ══════════════════════════════════════

if __name__ == "__main__":
    import argparse
    from src.database.models import get_connection

    parser = argparse.ArgumentParser(description="CQ Executor")
    parser.add_argument("--news-id", type=int, help="단일 뉴스 ID")
    parser.add_argument("--batch", type=int, default=0, help="최근 N건 일괄 처리")
    parser.add_argument("--stats", action="store_true", help="그래프 통계")
    args = parser.parse_args()

    if args.stats:
        stats = get_graph_stats()
        print(f"Neo4j: {stats}")

    elif args.news_id:
        conn = get_connection()
        r = conn.execute("SELECT original_title, summary_zh, summary FROM news WHERE id=?",
                         (args.news_id,)).fetchone()
        conn.close()
        if r:
            result = process_news_cqs(args.news_id, r["original_title"] or "",
                                       r["summary"] or r["summary_zh"] or "")
            print(f"Result: {result}")

    elif args.batch > 0:
        conn = get_connection()
        rows = conn.execute("""
            SELECT DISTINCT n.id, n.original_title, n.summary_zh, n.summary
            FROM news n
            LEFT JOIN expert_reviews er ON n.id = er.news_id
            WHERE (n.pipeline_status = 'published' OR er.publish_status = 'published')
            ORDER BY n.updated_at DESC LIMIT ?
        """, (args.batch,)).fetchall()
        conn.close()

        for i, r in enumerate(rows, 1):
            logger.info(f"[{i}/{len(rows)}] #{r['id']}: {(r['original_title'] or '')[:40]}")
            result = process_news_cqs(r["id"], r["original_title"] or "",
                                       r["summary"] or r["summary_zh"] or "")
            logger.info(f"  → {result}")

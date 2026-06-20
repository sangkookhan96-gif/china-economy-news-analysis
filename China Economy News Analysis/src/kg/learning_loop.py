"""CNI Learning Loop — 자기 진화형 지식 플랫폼 오케스트레이터.

전체 순환 구조:
  뉴스 → 요약 → CQ → 그래프확장 → 팁(그래프기반) → 헤드라인 → 사용자노출
  → 사용자행동 → reward → weight업데이트 → 다음뉴스 개선

모듈 구성:
  [실시간 경로] generate_cni_fields → 사용자 응답 (변경 없음)
  [비동기 경로] CQ → 그래프확장 → weight학습 (백그라운드)

비침투 원칙: 비동기 경로 실패 시 실시간 경로 100% 정상 작동.
"""

import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / f"learning_loop_{datetime.now().strftime('%Y%m%d')}.log",
                            encoding="utf-8"),
        logging.StreamHandler(),
    ],
    force=True,
)
logger = logging.getLogger("learning_loop")


# ══════════════════════════════════════
# 1. 뉴스 처리 + 그래프 학습 통합
# ══════════════════════════════════════

def process_news_with_learning(news_id: int, title: str, summary: str,
                                summary_ko: str = "") -> dict:
    """뉴스 1건에 대해 비동기 학습 파이프라인 실행.

    실시간 파이프라인(generate_enhanced)과 별도로 실행.
    실패해도 사용자 출력에 영향 없음.

    흐름:
      1. CQ 생성 (Qwen)
      2. CQ → Neo4j 조회 + 그래프 확장
      3. 트렌딩 weight 기반 CQ 우선순위 조정
      4. 결과 로그

    Args:
        news_id: 뉴스 ID
        title: 원문 제목
        summary: 요약 (중국어 또는 한국어)
        summary_ko: 한국어 요약 (있으면 우선 사용)

    Returns:
        {"cqs": int, "nodes_added": int, "relations_added": int,
         "graph_hits": int, "duration": float}
    """
    t0 = time.time()
    result = {
        "news_id": news_id,
        "cqs": 0, "nodes_added": 0, "relations_added": 0,
        "graph_hits": 0, "duration": 0,
    }

    try:
        # ── Step 1: 트렌딩 엔티티 조회 (weight 기반 우선순위) ──
        trending = []
        try:
            from src.kg.reward_engine import get_trending_entities
            trending = get_trending_entities(5)
            if trending:
                trending_names = [t["entity"] for t in trending]
                logger.info(f"  Trending: {trending_names[:3]}")
        except Exception:
            pass

        # ── Step 2: CQ 생성 + 실행 + 그래프 확장 ──
        try:
            from src.kg.cq_executor import process_news_cqs
            cq_result = process_news_cqs(news_id, title, summary_ko or summary)
            result["cqs"] = cq_result.get("cqs_executed", 0)
            result["nodes_added"] = cq_result.get("nodes_added", 0)
            result["relations_added"] = cq_result.get("relations_added", 0)
            result["graph_hits"] = cq_result.get("total_results", 0)
        except Exception as e:
            logger.warning(f"  CQ processing failed (non-blocking): {e}")

    except Exception as e:
        logger.error(f"  Learning loop error: {e}")

    result["duration"] = round(time.time() - t0, 1)
    _log_result(result)
    return result


# ══════════════════════════════════════
# 2. 일일 학습 배치 (cron)
# ══════════════════════════════════════

def run_daily_learning(limit: int = 50) -> dict:
    """일일 학습 배치: reward 처리 + decay + 미처리 뉴스 CQ.

    권장 실행: cron 01:00 (사용자 트래픽 최저 시간)

    흐름:
      1. 사용자 행동 → reward 계산 → weight 업데이트
      2. weight decay 적용
      3. 최근 미처리 뉴스에 CQ 실행
      4. 트렌딩 리포트 생성
    """
    logger.info(f"{'='*60}")
    logger.info(f"  DAILY LEARNING BATCH START")
    logger.info(f"{'='*60}")

    t0 = time.time()
    stats = {"reward_processed": 0, "decay": 0, "cq_processed": 0,
             "nodes_added": 0, "relations_added": 0}

    # ── Step 1: Reward 처리 ──
    try:
        from src.kg.reward_engine import process_pending_rewards
        reward_result = process_pending_rewards(days=1)
        stats["reward_processed"] = reward_result.get("processed", 0)
        logger.info(f"  [1/4] Rewards: {reward_result}")
    except Exception as e:
        logger.warning(f"  [1/4] Reward failed: {e}")

    # ── Step 2: Weight Decay ──
    try:
        from src.kg.reward_engine import apply_daily_decay
        decay_result = apply_daily_decay()
        stats["decay"] = decay_result.get("decayed", 0)
        logger.info(f"  [2/4] Decay: {decay_result}")
    except Exception as e:
        logger.warning(f"  [2/4] Decay failed: {e}")

    # ── Step 3: 미처리 뉴스 CQ ──
    try:
        from src.database.models import get_connection
        conn = get_connection()
        rows = conn.execute("""
            SELECT DISTINCT n.id, n.original_title, n.summary_zh, n.summary
            FROM news n
            LEFT JOIN expert_reviews er ON n.id = er.news_id
            WHERE (n.pipeline_status = 'published' OR er.publish_status = 'published')
              AND n.original_content IS NOT NULL
            ORDER BY n.updated_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
        conn.close()

        for i, r in enumerate(rows, 1):
            nid = r["id"]
            logger.info(f"  [3/4] CQ [{i}/{len(rows)}] #{nid}")
            lr = process_news_with_learning(
                nid, r["original_title"] or "",
                r["summary"] or r["summary_zh"] or ""
            )
            stats["cq_processed"] += 1
            stats["nodes_added"] += lr.get("nodes_added", 0)
            stats["relations_added"] += lr.get("relations_added", 0)

    except Exception as e:
        logger.warning(f"  [3/4] CQ batch failed: {e}")

    # ── Step 4: 트렌딩 리포트 ──
    try:
        from src.kg.reward_engine import get_trending_entities
        from src.kg.neo4j_adapter import get_graph_stats
        trending = get_trending_entities(10)
        graph = get_graph_stats()

        report = f"""
  [4/4] DAILY REPORT
  ─────────────────────
  Rewards processed: {stats['reward_processed']}
  Relations decayed: {stats['decay']}
  CQ processed: {stats['cq_processed']}
  Nodes added: {stats['nodes_added']}
  Relations added: {stats['relations_added']}

  Graph: {graph.get('nodes',0)} nodes, {graph.get('relations',0)} relations
  Trending: {[t['entity'] for t in trending[:5]]}
  Duration: {time.time()-t0:.0f}s
"""
        logger.info(report)
    except Exception as e:
        logger.warning(f"  [4/4] Report failed: {e}")

    duration = time.time() - t0
    stats["duration"] = round(duration, 1)

    logger.info(f"{'='*60}")
    logger.info(f"  DAILY LEARNING COMPLETE: {duration:.0f}s")
    logger.info(f"{'='*60}")

    return stats


# ══════════════════════════════════════
# 3. 실시간 뉴스 처리 후 비동기 학습 트리거
# ══════════════════════════════════════

def trigger_async_learning(news_id: int, title: str, summary: str,
                            summary_ko: str = ""):
    """뉴스 처리 완료 후 비동기로 학습 파이프라인 실행.

    process_queue에서 호출. 실패해도 무시.
    """
    try:
        import threading
        t = threading.Thread(
            target=process_news_with_learning,
            args=(news_id, title, summary, summary_ko),
            daemon=True
        )
        t.start()
        logger.info(f"  Async learning triggered for #{news_id}")
    except Exception as e:
        logger.warning(f"  Async learning trigger failed: {e}")


# ══════════════════════════════════════
# 4. 학습 상태 조회
# ══════════════════════════════════════

def get_learning_status() -> dict:
    """현재 학습 루프 상태 요약."""
    status = {"neo4j": False, "graph_nodes": 0, "graph_relations": 0,
              "user_events_today": 0, "trending": []}

    try:
        from src.kg.neo4j_adapter import is_available, get_graph_stats
        status["neo4j"] = is_available()
        if status["neo4j"]:
            g = get_graph_stats()
            status["graph_nodes"] = g.get("nodes", 0)
            status["graph_relations"] = g.get("relations", 0)
    except Exception:
        pass

    try:
        from src.database.models import get_connection
        conn = get_connection()
        today = datetime.now().strftime("%Y-%m-%d")
        r = conn.execute(
            "SELECT COUNT(*) FROM user_events WHERE created_at >= ?", (today,)
        ).fetchone()
        status["user_events_today"] = r[0] if r else 0
        conn.close()
    except Exception:
        pass

    try:
        from src.kg.reward_engine import get_trending_entities
        status["trending"] = [t["entity"] for t in get_trending_entities(5)]
    except Exception:
        pass

    return status


# ══════════════════════════════════════
# CLI
# ══════════════════════════════════════

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="CNI Learning Loop")
    parser.add_argument("--daily", action="store_true", help="일일 학습 배치 실행")
    parser.add_argument("--news", type=int, help="단일 뉴스 학습")
    parser.add_argument("--status", action="store_true", help="학습 상태 조회")
    parser.add_argument("--limit", type=int, default=50, help="배치 처리 건수")
    args = parser.parse_args()

    if args.daily:
        run_daily_learning(limit=args.limit)
    elif args.news:
        from src.database.models import get_connection
        conn = get_connection()
        r = conn.execute("SELECT original_title, summary_zh, summary FROM news WHERE id=?",
                         (args.news,)).fetchone()
        conn.close()
        if r:
            result = process_news_with_learning(
                args.news, r["original_title"] or "",
                r["summary"] or r["summary_zh"] or ""
            )
            print(f"Result: {result}")
    elif args.status:
        s = get_learning_status()
        print(f"Neo4j: {'ON' if s['neo4j'] else 'OFF'}")
        print(f"Graph: {s['graph_nodes']} nodes, {s['graph_relations']} relations")
        print(f"User events today: {s['user_events_today']}")
        print(f"Trending: {s['trending']}")

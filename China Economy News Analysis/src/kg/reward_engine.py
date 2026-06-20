"""Reward Engine — 사용자 행동 → 보상 계산 → Neo4j weight 업데이트.

사용자 행동 루프:
  클릭/조회/체류/공유/저장 → reward 점수 → Neo4j 관계 weight 강화
  시간 경과 → weight decay (감쇠)

비동기 전용 — 사용자 응답 경로에 영향 없음.
"""

import logging
import time
import math
from datetime import datetime, timedelta
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.database.models import get_connection
from src.kg.neo4j_adapter import is_available, session

logger = logging.getLogger("kg.reward")

LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"
REWARD_LOG = LOG_DIR / f"reward_{datetime.now().strftime('%Y%m%d')}.log"


# ══════════════════════════════════════
# 1. 사용자 행동 데이터 구조
# ══════════════════════════════════════

# DB 테이블: user_events
CREATE_USER_EVENTS_SQL = """
CREATE TABLE IF NOT EXISTS user_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    news_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    value REAL DEFAULT 1.0,
    user_agent TEXT,
    ip_address TEXT,
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (news_id) REFERENCES news(id)
);
CREATE INDEX IF NOT EXISTS idx_ue_news ON user_events(news_id);
CREATE INDEX IF NOT EXISTS idx_ue_type ON user_events(event_type);
CREATE INDEX IF NOT EXISTS idx_ue_date ON user_events(created_at);
"""

# 보상 가중치
REWARD_WEIGHTS = {
    "click": 1.0,        # 클릭
    "read_25": 0.5,      # 25% 스크롤
    "read_50": 1.0,      # 50% 스크롤
    "read_75": 1.5,      # 75% 스크롤
    "completion": 2.0,   # 끝까지 읽음
    "dwell_30s": 1.0,    # 30초 이상 체류
    "dwell_60s": 2.0,    # 60초 이상 체류
    "share": 3.0,        # 공유
    "save": 3.0,         # 저장/북마크
}

# 노이즈 필터링 기준
NOISE_FILTERS = {
    "min_dwell_sec": 3,          # 3초 미만 = 우연 클릭
    "max_events_per_ip_hour": 50, # IP당 시간당 50건 초과 = 봇
    "bot_user_agents": [          # 봇 UA 패턴
        "bot", "crawler", "spider", "Googlebot", "bingbot",
        "Baiduspider", "YandexBot", "Slurp",
    ],
}

# Weight decay: 하루 5% 감쇠
DECAY_RATE = 0.05
DECAY_INTERVAL_DAYS = 1


def init_tables():
    """user_events 테이블 생성."""
    conn = get_connection()
    conn.executescript(CREATE_USER_EVENTS_SQL)
    conn.commit()
    conn.close()
    logger.info("user_events table initialized")


# ══════════════════════════════════════
# 2. 이벤트 수집
# ══════════════════════════════════════

def record_event(news_id: int, event_type: str, value: float = 1.0,
                 user_agent: str = "", ip_address: str = "") -> bool:
    """사용자 행동 이벤트 기록.

    Args:
        news_id: 뉴스 ID
        event_type: click, completion, share, save, dwell_30s, etc.
        value: 이벤트 값 (체류시간 등)
        user_agent: UA 문자열
        ip_address: IP 주소
    """
    # 노이즈 필터링
    if _is_noise(event_type, value, user_agent, ip_address):
        return False

    try:
        conn = get_connection()
        conn.execute(
            "INSERT INTO user_events (news_id, event_type, value, user_agent, ip_address) "
            "VALUES (?, ?, ?, ?, ?)",
            (news_id, event_type, value, user_agent, ip_address)
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"record_event failed: {e}")
        return False


def _is_noise(event_type: str, value: float, ua: str, ip: str) -> bool:
    """노이즈 필터링."""
    # 봇 UA
    ua_lower = ua.lower()
    if any(bot in ua_lower for bot in NOISE_FILTERS["bot_user_agents"]):
        return True

    # 우연 클릭 (체류 3초 미만)
    if event_type == "click" and value < NOISE_FILTERS["min_dwell_sec"]:
        return True

    # IP 폭주
    if ip:
        try:
            conn = get_connection()
            one_hour_ago = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
            count = conn.execute(
                "SELECT COUNT(*) FROM user_events WHERE ip_address=? AND created_at>?",
                (ip, one_hour_ago)
            ).fetchone()[0]
            conn.close()
            if count > NOISE_FILTERS["max_events_per_ip_hour"]:
                return True
        except Exception:
            pass

    return False


# ══════════════════════════════════════
# 3. 보상 함수
# ══════════════════════════════════════

def calculate_reward(news_id: int, days: int = 7) -> float:
    """뉴스의 최근 N일간 총 보상 점수 계산.

    reward = Σ (event_weight × time_decay)
    time_decay = exp(-DECAY_RATE × days_since_event)
    """
    try:
        conn = get_connection()
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        events = conn.execute(
            "SELECT event_type, value, created_at FROM user_events "
            "WHERE news_id=? AND created_at>=?",
            (news_id, since)
        ).fetchall()
        conn.close()
    except Exception:
        return 0.0

    total_reward = 0.0
    now = datetime.now()

    for ev in events:
        etype = ev["event_type"]
        weight = REWARD_WEIGHTS.get(etype, 0.5)

        # 시간 감쇠
        try:
            ev_time = datetime.strptime(ev["created_at"][:19], "%Y-%m-%d %H:%M:%S")
            days_ago = (now - ev_time).total_seconds() / 86400
            decay = math.exp(-DECAY_RATE * days_ago)
        except Exception:
            decay = 0.5

        total_reward += weight * decay

    return round(total_reward, 3)


# ══════════════════════════════════════
# 4. Neo4j weight 업데이트
# ══════════════════════════════════════

def update_graph_weights(news_id: int, reward: float) -> dict:
    """뉴스의 reward를 Neo4j 관계 weight에 반영.

    뉴스와 연결된 모든 MENTIONS 관계의 weight를 reward 비례로 증가.
    """
    if not is_available() or reward <= 0:
        return {"updated": 0}

    # weight 증가량: reward를 0.01~0.5 범위로 정규화
    delta = min(0.5, max(0.01, reward * 0.05))

    updated = 0
    with session() as s:
        if s is None:
            return {"updated": 0}
        try:
            result = s.run(
                "MATCH (n:News {news_id: $nid})-[r:MENTIONS]->(e) "
                "SET r.weight = COALESCE(r.weight, 0) + $delta, "
                "    r.reward_updated = datetime() "
                "RETURN count(r) AS cnt",
                nid=news_id, delta=delta
            )
            updated = result.single()["cnt"]

            # 연관 엔티티 간 관계도 강화
            s.run(
                "MATCH (n:News {news_id: $nid})-[:MENTIONS]->(e1) "
                "MATCH (e1)-[r]->(e2) "
                "WHERE type(r) IN ['IMPACTS', 'AFFECTS', 'BELONGS_TO', 'RELATED_TO'] "
                "SET r.weight = COALESCE(r.weight, 0) + $delta * 0.5, "
                "    r.reward_updated = datetime()",
                nid=news_id, delta=delta
            )
        except Exception as e:
            logger.error(f"weight update failed: {e}")

    return {"updated": updated, "delta": delta, "reward": reward}


# ══════════════════════════════════════
# 5. Weight Decay (일일 배치)
# ══════════════════════════════════════

def apply_daily_decay() -> dict:
    """모든 관계의 weight에 일일 감쇠 적용.

    weight *= (1 - DECAY_RATE)
    최소 weight: 0.1 (완전 소멸 방지)
    """
    if not is_available():
        return {"decayed": 0}

    with session() as s:
        if s is None:
            return {"decayed": 0}
        try:
            result = s.run(
                "MATCH ()-[r]->() "
                "WHERE r.weight IS NOT NULL AND r.weight > 0.1 "
                "SET r.weight = CASE "
                "  WHEN r.weight * (1 - $rate) < 0.1 THEN 0.1 "
                "  ELSE r.weight * (1 - $rate) "
                "END "
                "RETURN count(r) AS cnt",
                rate=DECAY_RATE
            )
            cnt = result.single()["cnt"]
            logger.info(f"Daily decay applied: {cnt} relations")
            return {"decayed": cnt}
        except Exception as e:
            logger.error(f"decay failed: {e}")
            return {"decayed": 0, "error": str(e)}


# ══════════════════════════════════════
# 6. 배치 보상 처리
# ══════════════════════════════════════

def process_pending_rewards(days: int = 1) -> dict:
    """최근 N일간 이벤트가 있는 뉴스의 보상을 일괄 계산하고 weight 업데이트.

    권장: 일일 1회 배치 실행 (cron 00:30)
    """
    init_tables()

    try:
        conn = get_connection()
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        news_ids = conn.execute(
            "SELECT DISTINCT news_id FROM user_events WHERE created_at>=?",
            (since,)
        ).fetchall()
        conn.close()
    except Exception:
        return {"processed": 0}

    stats = {"processed": 0, "updated": 0, "total_reward": 0}

    for row in news_ids:
        nid = row["news_id"]
        reward = calculate_reward(nid, days=7)
        if reward > 0:
            result = update_graph_weights(nid, reward)
            stats["processed"] += 1
            stats["updated"] += result.get("updated", 0)
            stats["total_reward"] += reward

            # 로그
            try:
                with open(REWARD_LOG, "a", encoding="utf-8") as f:
                    f.write(f"{datetime.now().isoformat()}|#{nid}|"
                            f"reward={reward}|delta={result.get('delta',0)}|"
                            f"updated={result.get('updated',0)}\n")
            except Exception:
                pass

    logger.info(f"Rewards processed: {stats}")
    return stats


# ══════════════════════════════════════
# 7. 학습 반영 — 고 weight 엔티티 조회
# ══════════════════════════════════════

def get_trending_entities(limit: int = 10) -> list[dict]:
    """사용자 반응이 높은(weight 높은) 엔티티 TOP N.

    뉴스 선정/CQ 우선순위에 활용.
    """
    if not is_available():
        return []

    with session() as s:
        if s is None:
            return []
        try:
            result = s.run(
                "MATCH (n:News)-[r:MENTIONS]->(e) "
                "WHERE r.weight IS NOT NULL "
                "RETURN e.name AS entity, labels(e)[0] AS type, "
                "       sum(r.weight) AS total_weight, count(n) AS news_count "
                "ORDER BY total_weight DESC LIMIT $limit",
                limit=limit
            )
            return [dict(r) for r in result]
        except Exception:
            return []


def get_high_weight_paths(entity_name: str, min_weight: float = 1.0) -> list[dict]:
    """특정 엔티티의 고 weight 경로 조회 (팁 생성 시 우선 사용)."""
    if not is_available():
        return []

    with session() as s:
        if s is None:
            return []
        try:
            result = s.run(
                "MATCH (a)-[r]->(b) "
                "WHERE (a.name CONTAINS $name OR b.name CONTAINS $name) "
                "  AND r.weight >= $min_w "
                "RETURN a.name AS source, type(r) AS rel, b.name AS target, "
                "       r.weight AS weight "
                "ORDER BY r.weight DESC LIMIT 5",
                name=entity_name, min_w=min_weight
            )
            return [dict(r) for r in result]
        except Exception:
            return []


# ══════════════════════════════════════
# CLI
# ══════════════════════════════════════

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Reward Engine")
    parser.add_argument("--init", action="store_true", help="테이블 초기화")
    parser.add_argument("--process", action="store_true", help="보상 일괄 처리")
    parser.add_argument("--decay", action="store_true", help="일일 decay 적용")
    parser.add_argument("--trending", action="store_true", help="트렌딩 엔티티")
    parser.add_argument("--simulate", type=int, help="시뮬레이션: 뉴스 ID에 가상 이벤트 생성")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    if args.init:
        init_tables()
        print("Tables initialized")

    elif args.simulate:
        init_tables()
        nid = args.simulate
        # 가상 이벤트 생성
        for etype in ["click", "dwell_30s", "completion", "save"]:
            record_event(nid, etype, 1.0, "test-agent", "127.0.0.1")
        reward = calculate_reward(nid)
        result = update_graph_weights(nid, reward)
        print(f"Simulated: reward={reward}, {result}")

    elif args.process:
        init_tables()
        result = process_pending_rewards()
        print(f"Processed: {result}")

    elif args.decay:
        result = apply_daily_decay()
        print(f"Decay: {result}")

    elif args.trending:
        entities = get_trending_entities(10)
        for e in entities:
            print(f"  {e['entity']} ({e['type']}): weight={e['total_weight']:.2f}, news={e['news_count']}")

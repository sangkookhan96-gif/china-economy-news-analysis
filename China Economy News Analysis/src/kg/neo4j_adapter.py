"""Neo4j Knowledge Graph Adapter for CNI Platform.

비동기 백그라운드 전용 — 사용자 대면 파이프라인에 영향 없음.
Neo4j 장애 시 graceful degradation (SQLite fallback).
"""

import logging
import os
import time
from typing import Optional
from contextlib import contextmanager

logger = logging.getLogger("kg.neo4j")

# 환경변수로 재정의 가능(인스턴스 분리/포트 변경 대비). 기본은 기존 7687.
NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
# 인증: NEO4J_PASSWORD가 설정돼 있으면 (user, pass)로 접속, 없으면 무인증(현행 호환).
# → 구 인스턴스 인증을 켜는 순간 env만 주면 바로 붙고, 켜기 전엔 기존대로 동작.
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
_NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD")
NEO4J_TIMEOUT = 5  # 초과 시 그래프 조회 포기

_driver = None


def _get_driver():
    """Lazy singleton driver."""
    global _driver
    if _driver is None:
        try:
            from neo4j import GraphDatabase
            auth = (NEO4J_USER, _NEO4J_PASSWORD) if _NEO4J_PASSWORD else None
            _driver = GraphDatabase.driver(NEO4J_URI, auth=auth)
            _driver.verify_connectivity()
            logger.info(f"Neo4j connected ({NEO4J_URI}, auth={'on' if auth else 'off'})")
        except Exception as e:
            logger.warning(f"Neo4j unavailable: {e}")
            _driver = None
    return _driver


def is_available() -> bool:
    """Neo4j 사용 가능 여부 (5초 timeout)."""
    try:
        d = _get_driver()
        if d is None:
            return False
        d.verify_connectivity()
        return True
    except Exception:
        return False


@contextmanager
def session():
    """Safe session context — Neo4j 불가 시 None 반환."""
    d = _get_driver()
    if d is None:
        yield None
        return
    try:
        s = d.session()
        yield s
        s.close()
    except Exception as e:
        logger.error(f"Neo4j session error: {e}")
        yield None


# ══════════════════════════════════════
# Schema Initialization
# ══════════════════════════════════════

SCHEMA_CONSTRAINTS = [
    "CREATE CONSTRAINT IF NOT EXISTS FOR (c:Company) REQUIRE c.entity_id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (i:Industry) REQUIRE i.entity_id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (p:Policy) REQUIRE p.entity_id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (e:Event) REQUIRE e.event_id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (g:Country) REQUIRE g.entity_id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (pe:Person) REQUIRE pe.entity_id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:News) REQUIRE n.news_id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (ind:Indicator) REQUIRE ind.entity_id IS UNIQUE",
]

SCHEMA_INDEXES = [
    "CREATE INDEX IF NOT EXISTS FOR (c:Company) ON (c.name)",
    "CREATE INDEX IF NOT EXISTS FOR (i:Industry) ON (i.name)",
    "CREATE INDEX IF NOT EXISTS FOR (p:Policy) ON (p.name)",
    "CREATE INDEX IF NOT EXISTS FOR (e:Event) ON (e.event_type)",
    "CREATE INDEX IF NOT EXISTS FOR (n:News) ON (n.published_at)",
]


def init_schema():
    """Create constraints and indexes."""
    with session() as s:
        if s is None:
            logger.warning("Neo4j unavailable — schema init skipped")
            return False
        for cypher in SCHEMA_CONSTRAINTS + SCHEMA_INDEXES:
            try:
                s.run(cypher)
            except Exception as e:
                logger.warning(f"Schema: {e}")
        logger.info("Neo4j schema initialized")
        return True


# ══════════════════════════════════════
# Entity Type → Node Label Mapping
# ══════════════════════════════════════

TYPE_TO_LABEL = {
    "COM": "Company",
    "ORG": "Company",   # 기관도 Company 라벨 (조직 통합)
    "IND": "Industry",
    "POL": "Policy",
    "GEO": "Country",
    "PER": "Person",
    "IDX": "Indicator",
    "FIN": "Indicator",
}


# ══════════════════════════════════════
# MERGE Operations (중복 방지)
# ══════════════════════════════════════

def merge_entity(entity_id: str, name: str, entity_type: str,
                 name_zh: str = "", **props) -> bool:
    """MERGE 기반 엔티티 저장/업데이트."""
    label = TYPE_TO_LABEL.get(entity_type, "Entity")
    with session() as s:
        if s is None:
            return False
        try:
            s.run(
                f"MERGE (e:{label} {{entity_id: $eid}}) "
                f"ON CREATE SET e.name = $name, e.name_zh = $name_zh, "
                f"  e.entity_type = $etype, e.created_at = datetime(), e.mention_count = 1 "
                f"ON MATCH SET e.mention_count = e.mention_count + 1, "
                f"  e.updated_at = datetime()",
                eid=entity_id, name=name, name_zh=name_zh, etype=entity_type
            )
            return True
        except Exception as e:
            logger.error(f"merge_entity failed: {e}")
            return False


def merge_event(event_id: str, event_type: str, headline: str,
                magnitude: str = "", date: str = "", **props) -> bool:
    """MERGE 기반 이벤트 저장."""
    with session() as s:
        if s is None:
            return False
        try:
            s.run(
                "MERGE (ev:Event {event_id: $eid}) "
                "ON CREATE SET ev.event_type = $etype, ev.headline = $hl, "
                "  ev.magnitude = $mag, ev.date = $date, ev.created_at = datetime() "
                "ON MATCH SET ev.updated_at = datetime()",
                eid=event_id, etype=event_type, hl=headline,
                mag=magnitude, date=date
            )
            return True
        except Exception as e:
            logger.error(f"merge_event failed: {e}")
            return False


def merge_news(news_id: int, title: str, source: str = "",
               published_at: str = "") -> bool:
    """MERGE 기반 뉴스 노드 저장."""
    with session() as s:
        if s is None:
            return False
        try:
            s.run(
                "MERGE (n:News {news_id: $nid}) "
                "ON CREATE SET n.title = $title, n.source = $source, "
                "  n.published_at = $pub, n.created_at = datetime()",
                nid=news_id, title=title, source=source, pub=published_at
            )
            return True
        except Exception as e:
            logger.error(f"merge_news failed: {e}")
            return False


def merge_relation(source_id: str, target_id: str, relation_type: str,
                   source_label: str = "Company", target_label: str = "Company",
                   weight: float = 1.0, **props) -> bool:
    """MERGE 기반 관계 저장."""
    with session() as s:
        if s is None:
            return False
        try:
            cypher = (
                f"MATCH (a:{source_label} {{entity_id: $sid}}) "
                f"MATCH (b:{target_label} {{entity_id: $tid}}) "
                f"MERGE (a)-[r:{relation_type}]->(b) "
                f"ON CREATE SET r.weight = $w, r.created_at = datetime() "
                f"ON MATCH SET r.weight = r.weight + $w, r.updated_at = datetime()"
            )
            s.run(cypher, sid=source_id, tid=target_id, w=weight)
            return True
        except Exception as e:
            logger.error(f"merge_relation failed: {e}")
            return False


def link_news_entity(news_id: int, entity_id: str, entity_label: str,
                     role: str = "mention", confidence: float = 0.8) -> bool:
    """뉴스-엔티티 MENTIONS 관계."""
    with session() as s:
        if s is None:
            return False
        try:
            s.run(
                f"MATCH (n:News {{news_id: $nid}}) "
                f"MATCH (e:{entity_label} {{entity_id: $eid}}) "
                f"MERGE (n)-[r:MENTIONS]->(e) "
                f"ON CREATE SET r.role = $role, r.confidence = $conf",
                nid=news_id, eid=entity_id, role=role, conf=confidence
            )
            return True
        except Exception as e:
            logger.error(f"link_news_entity failed: {e}")
            return False


# ══════════════════════════════════════
# ExtractionResult → Neo4j 변환
# ══════════════════════════════════════

def save_extraction_result(news_id: int, title: str, result) -> dict:
    """ExtractionResult (validator.py) → Neo4j 일괄 저장.

    Args:
        news_id: 뉴스 ID
        title: 뉴스 제목
        result: ExtractionResult (entities, events, relations 포함)

    Returns:
        {"entities": int, "events": int, "relations": int, "ok": bool}
    """
    if not is_available():
        return {"entities": 0, "events": 0, "relations": 0, "ok": False, "reason": "neo4j_unavailable"}

    stats = {"entities": 0, "events": 0, "relations": 0}
    data = result.parsed_data or {}

    # 1. News 노드
    merge_news(news_id, title)

    # 2. Entities — ID가 없으면 name 기반 생성
    import hashlib
    entity_id_map = {}  # name → generated_id (관계 매핑용)

    for ent in (data.get("entities") or []):
        name = ent.get("name", "")
        etype = ent.get("type", "COM")
        name_zh = ent.get("name_zh", name)
        eid = ent.get("id") or f"KG-ENT-{etype}-{hashlib.md5(name.encode()).hexdigest()[:8]}"
        label = TYPE_TO_LABEL.get(etype, "Company")
        entity_id_map[name] = (eid, label)

        if merge_entity(eid, name, etype, name_zh):
            stats["entities"] += 1
            link_news_entity(news_id, eid, label, role="mention",
                           confidence=ent.get("confidence", 0.8))

    # 3. Events
    for evt in (data.get("events") or []):
        evtype = evt.get("type", "UNKNOWN")
        headline = evt.get("headline", "")
        evid = evt.get("id") or f"KG-EVT-{evtype}-{hashlib.md5(headline.encode()).hexdigest()[:8]}"
        if merge_event(evid, evtype, headline,
                       evt.get("magnitude", ""), evt.get("date", "")):
            stats["events"] += 1

    # 4. Relations — source/target를 name으로 매핑
    for rel in (data.get("relations") or []):
        src_name = rel.get("source", "")
        tgt_name = rel.get("target", "")
        rtype = rel.get("type", "RELATED_TO").replace(" ", "_").upper()

        src_info = entity_id_map.get(src_name)
        tgt_info = entity_id_map.get(tgt_name)

        if src_info and tgt_info:
            if merge_relation(src_info[0], tgt_info[0], rtype,
                             src_info[1], tgt_info[1],
                             weight=rel.get("confidence", 0.7)):
                stats["relations"] += 1

    return {**stats, "ok": True}


# ══════════════════════════════════════
# Query — 팁 생성 보조용 (읽기 전용)
# ══════════════════════════════════════

def get_entity_context(entity_name: str, limit: int = 5) -> list[dict]:
    """엔티티 관련 컨텍스트 조회 (팁 보강용).

    5초 timeout — 초과 시 빈 리스트 반환.
    """
    with session() as s:
        if s is None:
            return []
        try:
            result = s.run(
                "MATCH (e)-[r]-(related) "
                "WHERE e.name CONTAINS $name OR e.name_zh CONTAINS $name "
                "RETURN type(r) AS rel, related.name AS name, r.weight AS weight "
                "ORDER BY r.weight DESC LIMIT $limit",
                name=entity_name, limit=limit
            )
            return [dict(record) for record in result]
        except Exception:
            return []


def get_industry_trends(industry_name: str, limit: int = 3) -> list[dict]:
    """산업 관련 최근 이벤트 (팁 보강용)."""
    with session() as s:
        if s is None:
            return []
        try:
            result = s.run(
                "MATCH (i:Industry)<-[:AFFECTS]-(p:Policy) "
                "WHERE i.name CONTAINS $name "
                "RETURN p.name AS policy, p.entity_type AS type "
                "LIMIT $limit",
                name=industry_name, limit=limit
            )
            return [dict(record) for record in result]
        except Exception:
            return []


def get_graph_stats() -> dict:
    """그래프 전체 통계."""
    with session() as s:
        if s is None:
            return {"available": False}
        try:
            nodes = s.run("MATCH (n) RETURN count(n) AS cnt").single()["cnt"]
            rels = s.run("MATCH ()-[r]->() RETURN count(r) AS cnt").single()["cnt"]
            labels = s.run(
                "CALL db.labels() YIELD label "
                "RETURN label, count{(n) WHERE label IN labels(n)} AS cnt"
            )
            label_counts = {r["label"]: r["cnt"] for r in labels}
            return {"available": True, "nodes": nodes, "relations": rels,
                    "labels": label_counts}
        except Exception as e:
            return {"available": True, "error": str(e)}


def close():
    """드라이버 종료."""
    global _driver
    if _driver:
        _driver.close()
        _driver = None

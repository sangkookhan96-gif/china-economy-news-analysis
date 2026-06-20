"""KG Query Layer.

Provides structured query functions for KG exploration.
Supports: entity search, relation traversal, event lookup, industry trends.
"""

import sqlite3
from datetime import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from src.database.kg_models import get_kg_connection


def search_entity(name: str, entity_type: str = None, limit: int = 20) -> list[dict]:
    """Search entities by name (partial match)."""
    conn = get_kg_connection()
    sql = """
        SELECT kg_entity_id, canonical_name, canonical_name_zh, entity_type,
               description, mention_count, aliases, first_seen_date, last_seen_date
        FROM kg_entities
        WHERE (canonical_name LIKE ? OR canonical_name_zh LIKE ? OR aliases LIKE ?)
          AND status = 'active'
    """
    params = [f"%{name}%", f"%{name}%", f"%{name}%"]
    if entity_type:
        sql += " AND entity_type = ?"
        params.append(entity_type)
    sql += " ORDER BY mention_count DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_entity_relations(entity_id: str) -> dict:
    """Get all relations involving an entity (as source or target)."""
    conn = get_kg_connection()
    outgoing = conn.execute("""
        SELECT r.kg_relation_id, r.relation_type, r.confidence, r.evidence,
               t.canonical_name as target_name, t.entity_type as target_type
        FROM kg_relations r
        JOIN kg_entities t ON r.target_id = t.kg_entity_id
        WHERE r.source_id = ? AND r.status = 'active'
        ORDER BY r.confidence DESC
    """, (entity_id,)).fetchall()

    incoming = conn.execute("""
        SELECT r.kg_relation_id, r.relation_type, r.confidence, r.evidence,
               s.canonical_name as source_name, s.entity_type as source_type
        FROM kg_relations r
        JOIN kg_entities s ON r.source_id = s.kg_entity_id
        WHERE r.target_id = ? AND r.status = 'active'
        ORDER BY r.confidence DESC
    """, (entity_id,)).fetchall()

    conn.close()
    return {
        "outgoing": [dict(r) for r in outgoing],
        "incoming": [dict(r) for r in incoming],
    }


def get_entity_events(entity_id: str) -> list[dict]:
    """Get all events linked to an entity via news-entity and news-event maps."""
    conn = get_kg_connection()
    rows = conn.execute("""
        SELECT DISTINCT e.kg_event_id, e.event_type, e.headline, e.event_date,
               e.magnitude, e.actors, e.targets
        FROM kg_events e
        JOIN kg_news_event_map nem ON e.kg_event_id = nem.kg_event_id
        JOIN kg_news_entity_map nenm ON nem.news_id = nenm.news_id
        WHERE nenm.kg_entity_id = ?
        ORDER BY e.event_date DESC NULLS LAST, e.created_at DESC
    """, (entity_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_industry_entities(industry_name: str) -> list[dict]:
    """Get entities belonging to an industry via BELONGS_TO relations."""
    conn = get_kg_connection()

    # Find industry entity
    ind = conn.execute("""
        SELECT kg_entity_id FROM kg_entities
        WHERE (canonical_name LIKE ? OR canonical_name_zh LIKE ?)
          AND entity_type = 'IND' AND status = 'active'
    """, (f"%{industry_name}%", f"%{industry_name}%")).fetchall()

    if not ind:
        conn.close()
        return []

    ind_ids = [r["kg_entity_id"] for r in ind]
    placeholders = ",".join("?" * len(ind_ids))

    rows = conn.execute(f"""
        SELECT e.kg_entity_id, e.canonical_name, e.entity_type,
               e.mention_count, r.relation_type
        FROM kg_relations r
        JOIN kg_entities e ON r.source_id = e.kg_entity_id
        WHERE r.target_id IN ({placeholders})
          AND r.relation_type IN ('BELONGS_TO', 'RELATED_TO', 'SUPPLIES_TO')
          AND r.status = 'active'
        ORDER BY e.mention_count DESC
    """, ind_ids).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_industry_events(industry_name: str, limit: int = 20) -> list[dict]:
    """Get events related to an industry."""
    conn = get_kg_connection()

    rows = conn.execute("""
        SELECT DISTINCT e.kg_event_id, e.event_type, e.headline, e.event_date,
               e.magnitude
        FROM kg_events e
        JOIN kg_news_event_map nem ON e.kg_event_id = nem.kg_event_id
        JOIN kg_news_entity_map nenm ON nem.news_id = nenm.news_id
        JOIN kg_entities ent ON nenm.kg_entity_id = ent.kg_entity_id
        WHERE (ent.canonical_name LIKE ? OR ent.canonical_name_zh LIKE ?
               OR ent.entity_type = 'IND' AND ent.canonical_name LIKE ?)
        ORDER BY e.event_date DESC NULLS LAST
        LIMIT ?
    """, (f"%{industry_name}%", f"%{industry_name}%",
          f"%{industry_name}%", limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_top_entities(entity_type: str = None, limit: int = 20) -> list[dict]:
    """Get top entities by mention count."""
    conn = get_kg_connection()
    sql = """
        SELECT kg_entity_id, canonical_name, canonical_name_zh, entity_type,
               mention_count, description
        FROM kg_entities WHERE status = 'active'
    """
    params = []
    if entity_type:
        sql += " AND entity_type = ?"
        params.append(entity_type)
    sql += " ORDER BY mention_count DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_graph_stats() -> dict:
    """Get overall KG statistics."""
    conn = get_kg_connection()
    stats = {}

    for table, key in [("kg_entities", "entities"), ("kg_events", "events"),
                       ("kg_relations", "relations")]:
        stats[key] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    stats["active_entities"] = conn.execute(
        "SELECT COUNT(*) FROM kg_entities WHERE status='active'").fetchone()[0]

    stats["entity_types"] = {r[0]: r[1] for r in conn.execute(
        "SELECT entity_type, COUNT(*) FROM kg_entities GROUP BY entity_type"
    ).fetchall()}

    stats["event_types"] = {r[0]: r[1] for r in conn.execute(
        "SELECT event_type, COUNT(*) FROM kg_events GROUP BY event_type ORDER BY COUNT(*) DESC"
    ).fetchall()}

    stats["relation_types"] = {r[0]: r[1] for r in conn.execute(
        "SELECT relation_type, COUNT(*) FROM kg_relations GROUP BY relation_type ORDER BY COUNT(*) DESC"
    ).fetchall()}

    # Degree centrality (top 10)
    stats["top_connected"] = [dict(r) for r in conn.execute("""
        SELECT e.canonical_name, e.entity_type, e.mention_count,
               (SELECT COUNT(*) FROM kg_relations WHERE source_id = e.kg_entity_id OR target_id = e.kg_entity_id) as degree
        FROM kg_entities e
        WHERE e.status = 'active'
        ORDER BY degree DESC LIMIT 10
    """).fetchall()]

    conn.close()
    return stats


def get_policy_impact_chains(limit: int = 10) -> list[dict]:
    """Find policy → industry/company impact chains."""
    conn = get_kg_connection()
    rows = conn.execute("""
        SELECT
            s.canonical_name as policy_name,
            r.relation_type,
            t.canonical_name as target_name,
            t.entity_type as target_type,
            r.evidence
        FROM kg_relations r
        JOIN kg_entities s ON r.source_id = s.kg_entity_id
        JOIN kg_entities t ON r.target_id = t.kg_entity_id
        WHERE (s.entity_type IN ('POL', 'ORG') AND r.relation_type IN ('REGULATES', 'IMPACTS', 'ANNOUNCES', 'TRIGGERS'))
        ORDER BY s.mention_count DESC, r.confidence DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

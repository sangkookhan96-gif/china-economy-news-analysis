"""Knowledge Graph database models and initialization.

CEKG (China Economy Knowledge Graph) SQLite schema.
All tables use kg_ prefix for isolation from existing system.
All IDs use KG- prefix to avoid collision with existing integer IDs.
"""

import sqlite3
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from config.settings import DATABASE_PATH


def get_kg_connection() -> sqlite3.Connection:
    """Get database connection for KG operations.

    busy_timeout 30s — Papago 캐시·쿼터 쓰기가 라이브 파이프라인과 경합 시
    즉시 'database is locked' 대신 대기(2026-06-15 락 사고 대응). DB는 WAL 모드.
    """
    conn = sqlite3.connect(DATABASE_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_kg_db():
    """Create all KG tables in existing SQLite database."""
    conn = get_kg_connection()
    cursor = conn.cursor()

    # ── 1. Entity Table ──
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS kg_entities (
            kg_entity_id    TEXT PRIMARY KEY,
            canonical_name  TEXT NOT NULL,
            canonical_name_zh TEXT,
            aliases         TEXT,
            entity_type     TEXT NOT NULL
                CHECK(entity_type IN ('ORG','COM','PER','POL','IND','GEO','IDX','FIN')),
            description     TEXT,
            first_seen_date TEXT,
            last_seen_date  TEXT,
            mention_count   INTEGER DEFAULT 1,
            status          TEXT DEFAULT 'active'
                CHECK(status IN ('active','merged','deprecated')),
            merged_into     TEXT,
            created_at      TEXT DEFAULT (datetime('now')),
            updated_at      TEXT DEFAULT (datetime('now'))
        )
    """)

    # ── 2. Event Table ──
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS kg_events (
            kg_event_id     TEXT PRIMARY KEY,
            event_type      TEXT NOT NULL,
            headline        TEXT NOT NULL,
            event_date      TEXT,
            date_precision  TEXT DEFAULT 'exact'
                CHECK(date_precision IN ('exact','month','quarter','year')),
            actors          TEXT,
            targets         TEXT,
            indicators      TEXT,
            magnitude       TEXT DEFAULT 'moderate'
                CHECK(magnitude IN ('minor','moderate','major','critical')),
            source_news_id  INTEGER,
            source_url      TEXT,
            expert_judgment TEXT,
            supersedes      TEXT,
            created_at      TEXT DEFAULT (datetime('now')),
            updated_at      TEXT DEFAULT (datetime('now'))
        )
    """)

    # ── 3. Relation Table ──
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS kg_relations (
            kg_relation_id  TEXT PRIMARY KEY,
            source_id       TEXT NOT NULL,
            target_id       TEXT NOT NULL,
            relation_type   TEXT NOT NULL
                CHECK(relation_type IN (
                    'REGULATES','ANNOUNCES','TRIGGERS','IMPACTS',
                    'BELONGS_TO','LOCATED_IN','COMPETES_WITH','SUPPLIES_TO',
                    'MEASURES','SUCCEEDS','CONTRADICTS','SUPERSEDES'
                )),
            confidence      REAL DEFAULT 0.5,
            strength        TEXT DEFAULT 'medium'
                CHECK(strength IN ('weak','medium','strong')),
            direction       TEXT,
            evidence        TEXT,
            source_event_id TEXT,
            hop_count       INTEGER DEFAULT 0,
            status          TEXT DEFAULT 'active'
                CHECK(status IN ('active','review','rejected','archived')),
            created_at      TEXT DEFAULT (datetime('now')),
            updated_at      TEXT DEFAULT (datetime('now'))
        )
    """)

    # ── 4. News-Entity Mapping (links news articles to KG entities) ──
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS kg_news_entity_map (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            news_id         INTEGER NOT NULL,
            kg_entity_id    TEXT NOT NULL,
            role            TEXT DEFAULT 'mention'
                CHECK(role IN ('actor','target','mention')),
            extraction_confidence REAL DEFAULT 0.5,
            created_at      TEXT DEFAULT (datetime('now'))
        )
    """)

    # ── 5. News-Event Mapping (links news articles to KG events) ──
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS kg_news_event_map (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            news_id         INTEGER NOT NULL,
            kg_event_id     TEXT NOT NULL,
            is_primary      BOOLEAN DEFAULT TRUE,
            created_at      TEXT DEFAULT (datetime('now'))
        )
    """)

    # ── 6. Entity Importance (tier scoring) ──
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS kg_entity_importance (
            kg_entity_id    TEXT PRIMARY KEY,
            importance_score REAL DEFAULT 0.0,
            tier            TEXT CHECK(tier IN ('T1','T2','T3','T4','T5')),
            degree_centrality INTEGER DEFAULT 0,
            event_count     INTEGER DEFAULT 0,
            last_calculated TEXT,
            created_at      TEXT DEFAULT (datetime('now')),
            updated_at      TEXT DEFAULT (datetime('now'))
        )
    """)

    # ── 7. Graph Health Snapshot (daily metrics) ──
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS kg_graph_health (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_date   TEXT NOT NULL,
            total_entities  INTEGER DEFAULT 0,
            total_events    INTEGER DEFAULT 0,
            total_relations INTEGER DEFAULT 0,
            active_entities INTEGER DEFAULT 0,
            orphan_entities INTEGER DEFAULT 0,
            avg_degree      REAL DEFAULT 0.0,
            density         REAL DEFAULT 0.0,
            auto_generation_rate REAL,
            review_queue_size    INTEGER DEFAULT 0,
            created_at      TEXT DEFAULT (datetime('now'))
        )
    """)

    # ── 8. Extraction Log (audit trail for pipeline runs) ──
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS kg_extraction_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            news_id         INTEGER NOT NULL,
            entities_extracted INTEGER DEFAULT 0,
            events_extracted   INTEGER DEFAULT 0,
            relations_extracted INTEGER DEFAULT 0,
            extraction_method  TEXT DEFAULT 'claude',
            duration_ms     INTEGER,
            error_message   TEXT,
            created_at      TEXT DEFAULT (datetime('now'))
        )
    """)

    # ── 9. Conflict Log ──
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS kg_conflict_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            conflict_type   TEXT NOT NULL,
            source_id       TEXT,
            target_id       TEXT,
            detail          TEXT,
            resolution      TEXT,
            created_at      TEXT DEFAULT (datetime('now'))
        )
    """)

    # ── Indexes ──
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_kg_entities_type ON kg_entities(entity_type)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_kg_entities_status ON kg_entities(status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_kg_entities_name ON kg_entities(canonical_name)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_kg_events_type ON kg_events(event_type)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_kg_events_date ON kg_events(event_date DESC)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_kg_relations_source ON kg_relations(source_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_kg_relations_target ON kg_relations(target_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_kg_relations_type ON kg_relations(relation_type)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_kg_relations_status ON kg_relations(status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_kg_news_entity_map_news ON kg_news_entity_map(news_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_kg_news_entity_map_entity ON kg_news_entity_map(kg_entity_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_kg_news_event_map_news ON kg_news_event_map(news_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_kg_news_event_map_event ON kg_news_event_map(kg_event_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_kg_extraction_log_news ON kg_extraction_log(news_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_kg_graph_health_date ON kg_graph_health(snapshot_date)")

    conn.commit()
    conn.close()
    print("KG database schema initialized successfully.")


def verify_kg_tables():
    """Verify all KG tables exist and print their schemas."""
    conn = get_kg_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT name FROM sqlite_master
        WHERE type='table' AND name LIKE 'kg_%'
        ORDER BY name
    """)
    tables = [row[0] for row in cursor.fetchall()]

    print(f"\n{'='*60}")
    print(f"  KG Tables: {len(tables)}개")
    print(f"{'='*60}")

    for table in tables:
        cursor.execute(f"PRAGMA table_info({table})")
        cols = cursor.fetchall()
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        count = cursor.fetchone()[0]
        print(f"\n📋 {table} ({count} rows)")
        print(f"   {'Column':<28} {'Type':<12} {'Nullable':<8} {'Default'}")
        print(f"   {'-'*70}")
        for col in cols:
            nullable = "NULL" if not col[3] else "NOT NULL"
            default = col[4] or ""
            pk = " [PK]" if col[5] else ""
            print(f"   {col[1]:<28} {col[2] or 'TEXT':<12} {nullable:<8} {default}{pk}")

    # Check existing tables are unaffected
    cursor.execute("""
        SELECT name FROM sqlite_master
        WHERE type='table' AND name NOT LIKE 'kg_%' AND name NOT LIKE 'sqlite_%'
        ORDER BY name
    """)
    existing = [row[0] for row in cursor.fetchall()]
    print(f"\n{'='*60}")
    print(f"  기존 테이블 (변경 없음): {existing}")
    print(f"{'='*60}")

    conn.close()
    return tables


if __name__ == "__main__":
    init_kg_db()
    verify_kg_tables()

"""KG Entity Normalizer.

Merges duplicate entities, fixes type errors, rewires relations.
"""

import sqlite3
import json
import logging
from datetime import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from src.database.kg_models import get_kg_connection

LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
log_file = LOG_DIR / "kg_normalization.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(),
    ],
    force=True,
)
logger = logging.getLogger("kg_norm")

# ══════════════════════════════════════════
# [1] Canonical Entity Dictionary
# ══════════════════════════════════════════

# Format: canonical_id → {canonical_name, entity_type, aliases: [old_ids]}
# old_ids will be merged INTO canonical_id

MERGE_RULES = {
    # ── GEO: China ──
    "KG-ENT-GEO-0b5c377d": {  # 중국 (mentions:34, keep this)
        "canonical_name": "중국",
        "canonical_name_zh": "中国",
        "entity_type": "GEO",
        "merge_from": [
            "KG-ENT-GEO-c13dceab",   # 中国
            "KG-ENT-GEO-aa418558",   # 중화인민공화국
            "KG-ENT-GEO-ae54a5c0",   # China
            "KG-ENT-GEO-375c02a7",   # 중국 본토
        ],
    },
    # ── COM: BYD ──
    "KG-ENT-COM-fa5cc085": {  # BYD (mentions:3, keep)
        "canonical_name": "BYD",
        "canonical_name_zh": "比亚迪",
        "entity_type": "COM",
        "merge_from": [
            "KG-ENT-COM-06762162",   # 비야디
            "KG-ENT-COM-f8cb8ce7",   # 비아디
        ],
    },
    # ── ORG: PBOC ──
    "KG-ENT-ORG-95496ec3": {  # 인민은행 (mentions:3, keep)
        "canonical_name": "인민은행",
        "canonical_name_zh": "中国人民银行",
        "entity_type": "ORG",
        "merge_from": [
            "KG-ENT-ORG-32316c7a",   # 중국인민은행
            "KG-ENT-ORG-c2317d06",   # 중국 인민은행
            "KG-ENT-ORG-c0f0ab41",   # People's Bank of China
            "KG-ENT-ORG-a54c4243",   # 중국 중앙은행
            "KG-ENT-ORG-d064e5e3",   # PBOC
            "KG-ENT-ORG-8b99768c",   # 중국은행 (mistakenly mapped to PBOC)
        ],
    },
    # ── ORG: 국무원 ──
    "KG-ENT-ORG-e7c618bd": {  # 국무원 (mentions:14)
        "canonical_name": "국무원",
        "canonical_name_zh": "国务院",
        "entity_type": "ORG",
        "merge_from": [
            "KG-ENT-ORG-5fe4337f",   # 国무원
        ],
    },
    # ── PER: 시진핑 ──
    "KG-ENT-PER-7af41ca1": {  # 시진핑 (mentions:4)
        "canonical_name": "시진핑",
        "canonical_name_zh": "习近平",
        "entity_type": "PER",
        "merge_from": [
            "KG-ENT-PER-1fa2bf8c",   # 习近平
        ],
    },
    # ── ORG: 증감회 (CSRC) ──
    "KG-ENT-ORG-56ccebe1": {  # 证监会
        "canonical_name": "증감회",
        "canonical_name_zh": "中国证券监督管理委员会",
        "entity_type": "ORG",
        "merge_from": [
            "KG-ENT-ORG-38aa0219",   # 中国증권감독관리위원회
            "KG-ENT-ORG-b9c76b69",   # 中国证券监督委员会
            "KG-ENT-ORG-9a06bcec",   # 中国证券监管管理委员会
        ],
    },
    # ── ORG: 중국 정부 ──
    "KG-ENT-ORG-fb6861d6": {  # 중국 정부 (mentions:2)
        "canonical_name": "중국 정부",
        "canonical_name_zh": "中国政府",
        "entity_type": "ORG",
        "merge_from": [
            "KG-ENT-ORG-fe661337",   # 中国政府
            "KG-ENT-ORG-88a19c11",   # 中華人民共和国政府
            "KG-ENT-ORG-353c4b06",   # Chinese government
        ],
    },
    # ── ORG: CCTV ──
    "KG-ENT-ORG-8f0658e5": {  # CCTV
        "canonical_name": "CCTV",
        "canonical_name_zh": "中国中央电视台",
        "entity_type": "ORG",
        "merge_from": [
            "KG-ENT-ORG-ed0c759d",   # China Central Television
        ],
    },
    # ── ORG: 금융감독총국 ──
    "KG-ENT-ORG-af8568b9": {  # 中国金管局
        "canonical_name": "금융감독총국",
        "canonical_name_zh": "国家金融监督管理总局",
        "entity_type": "ORG",
        "merge_from": [
            "KG-ENT-ORG-00a46635",   # 国家金融监督管理总局
        ],
    },
    # ── ORG: 중국청년보 ──
    "KG-ENT-ORG-99e5308d": {
        "canonical_name": "중국청년보",
        "canonical_name_zh": "中国青年报",
        "entity_type": "ORG",
        "merge_from": [
            "KG-ENT-ORG-df051c03",   # 中国青年报
        ],
    },
}

# ══════════════════════════════════════════
# [4] Type fixes — entities with wrong type
# ══════════════════════════════════════════

TYPE_FIXES = {
    # Games wrongly classified as IND → delete (not relevant to economy KG)
    "DELETE": [
        "KG-ENT-IND-897e8b3a",   # Delta Force
        "KG-ENT-IND-590b0d1f",   # Honor of Kings
        "KG-ENT-IND-e237de4a",   # Peacekeeper Elite
        "KG-ENT-IND-e0f0c1e5",   # Valorant
        "KG-ENT-IND-46531109",   # PUBG MOBILE
        "KG-ENT-IND-4206e0e1",   # Wuthering Waves
    ],
    # Wrong name_zh mapping
    "FIX": [
        # Nvidia was mapped with 比亚迪 as name_zh — fix it
        ("KG-ENT-COM-2a21e256", {"canonical_name": "Nvidia", "canonical_name_zh": "英伟达", "entity_type": "COM"}),
        # 재정부 wrongly mapped to 中国人民银行
        ("KG-ENT-ORG-439aef78", {"canonical_name": "재정부", "canonical_name_zh": "财政部", "entity_type": "ORG"}),
    ],
}


def merge_entities(conn):
    """Execute entity merges: rewire relations, maps, then mark old as merged."""
    total_merged = 0

    for canonical_id, rule in MERGE_RULES.items():
        # Check canonical exists
        canon = conn.execute(
            "SELECT kg_entity_id FROM kg_entities WHERE kg_entity_id = ?",
            (canonical_id,)).fetchone()

        if not canon:
            logger.warning(f"  Canonical {canonical_id} not found, skipping")
            continue

        merge_ids = rule["merge_from"]
        # Filter to only existing IDs
        placeholders = ",".join("?" * len(merge_ids))
        existing = [r[0] for r in conn.execute(
            f"SELECT kg_entity_id FROM kg_entities WHERE kg_entity_id IN ({placeholders})",
            merge_ids).fetchall()]

        if not existing:
            continue

        logger.info(f"  Merging {len(existing)} entities into {canonical_id} ({rule['canonical_name']})")

        ph = ",".join("?" * len(existing))

        # 1. Rewire relations (source_id)
        conn.execute(f"""
            UPDATE kg_relations SET source_id = ?
            WHERE source_id IN ({ph})
        """, [canonical_id] + existing)

        # 2. Rewire relations (target_id)
        conn.execute(f"""
            UPDATE kg_relations SET target_id = ?
            WHERE target_id IN ({ph})
        """, [canonical_id] + existing)

        # 3. Rewire news_entity_map
        conn.execute(f"""
            UPDATE kg_news_entity_map SET kg_entity_id = ?
            WHERE kg_entity_id IN ({ph})
        """, [canonical_id] + existing)

        # 4. Rewire events (actors/targets JSON)
        events = conn.execute("SELECT kg_event_id, actors, targets FROM kg_events").fetchall()
        for evt in events:
            changed = False
            actors = json.loads(evt["actors"] or "[]")
            targets = json.loads(evt["targets"] or "[]")
            new_actors = [canonical_id if a in existing else a for a in actors]
            new_targets = [canonical_id if t in existing else t for t in targets]
            if new_actors != actors or new_targets != targets:
                conn.execute("""
                    UPDATE kg_events SET actors = ?, targets = ?
                    WHERE kg_event_id = ?
                """, (json.dumps(new_actors, ensure_ascii=False),
                      json.dumps(new_targets, ensure_ascii=False),
                      evt["kg_event_id"]))

        # 5. Sum mention_count
        total_mentions = conn.execute(f"""
            SELECT COALESCE(SUM(mention_count), 0) FROM kg_entities
            WHERE kg_entity_id IN ({ph})
        """, existing).fetchone()[0]

        # 6. Update canonical entity
        conn.execute("""
            UPDATE kg_entities
            SET canonical_name = ?,
                canonical_name_zh = ?,
                entity_type = ?,
                mention_count = mention_count + ?,
                updated_at = datetime('now')
            WHERE kg_entity_id = ?
        """, (rule["canonical_name"], rule["canonical_name_zh"],
              rule["entity_type"], total_mentions, canonical_id))

        # 7. Mark old entities as merged
        conn.execute(f"""
            UPDATE kg_entities
            SET status = 'merged', merged_into = ?, updated_at = datetime('now')
            WHERE kg_entity_id IN ({ph})
        """, [canonical_id] + existing)

        total_merged += len(existing)

    return total_merged


def fix_types(conn):
    """Fix entity type errors and delete irrelevant entities."""
    deleted = 0
    fixed = 0

    # Delete game/product entities wrongly classified as IND
    for eid in TYPE_FIXES["DELETE"]:
        # Remove related relations
        conn.execute("DELETE FROM kg_relations WHERE source_id = ? OR target_id = ?", (eid, eid))
        conn.execute("DELETE FROM kg_news_entity_map WHERE kg_entity_id = ?", (eid,))
        conn.execute("DELETE FROM kg_entities WHERE kg_entity_id = ?", (eid,))
        deleted += 1
        logger.info(f"  Deleted: {eid}")

    # Fix wrong attributes
    for eid, fixes in TYPE_FIXES["FIX"]:
        sets = ", ".join(f"{k} = ?" for k in fixes.keys())
        vals = list(fixes.values()) + [eid]
        conn.execute(f"UPDATE kg_entities SET {sets}, updated_at = datetime('now') WHERE kg_entity_id = ?", vals)
        fixed += 1
        logger.info(f"  Fixed: {eid} → {fixes}")

    return deleted, fixed


def remove_orphan_relations(conn):
    """Remove relations where source or target no longer exists as active."""
    result = conn.execute("""
        DELETE FROM kg_relations
        WHERE source_id NOT IN (SELECT kg_entity_id FROM kg_entities WHERE status = 'active')
           OR target_id NOT IN (SELECT kg_entity_id FROM kg_entities WHERE status = 'active')
    """)
    return result.rowcount


def remove_self_relations(conn):
    """Remove relations where source == target (caused by merging)."""
    result = conn.execute("DELETE FROM kg_relations WHERE source_id = target_id")
    return result.rowcount


def remove_duplicate_relations(conn):
    """Remove duplicate relations (same source, target, type) keeping highest confidence."""
    result = conn.execute("""
        DELETE FROM kg_relations
        WHERE rowid NOT IN (
            SELECT MIN(rowid) FROM kg_relations
            GROUP BY source_id, target_id, relation_type
        )
    """)
    return result.rowcount


def run_normalization():
    """Execute full normalization pipeline."""
    conn = get_kg_connection()
    logger.info("=== KG NORMALIZATION START ===")

    # Before stats
    before = {}
    for t in ["kg_entities", "kg_events", "kg_relations"]:
        before[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    before["active"] = conn.execute(
        "SELECT COUNT(*) FROM kg_entities WHERE status='active'").fetchone()[0]

    logger.info(f"Before: entities={before['kg_entities']} (active={before['active']}), "
                f"events={before['kg_events']}, relations={before['kg_relations']}")

    # [1][3] Merge duplicate entities
    logger.info("\n--- Phase 1: Entity Merge ---")
    merged = merge_entities(conn)
    logger.info(f"  Merged: {merged} entities")

    # [4] Fix type errors
    logger.info("\n--- Phase 2: Type Fixes ---")
    deleted, fixed = fix_types(conn)
    logger.info(f"  Deleted: {deleted}, Fixed: {fixed}")

    # [3] Clean up relations
    logger.info("\n--- Phase 3: Relation Cleanup ---")
    orphans = remove_orphan_relations(conn)
    logger.info(f"  Orphan relations removed: {orphans}")
    selfs = remove_self_relations(conn)
    logger.info(f"  Self-relations removed: {selfs}")
    dupes = remove_duplicate_relations(conn)
    logger.info(f"  Duplicate relations removed: {dupes}")

    conn.commit()

    # After stats
    after = {}
    for t in ["kg_entities", "kg_events", "kg_relations"]:
        after[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    after["active"] = conn.execute(
        "SELECT COUNT(*) FROM kg_entities WHERE status='active'").fetchone()[0]
    after["merged"] = conn.execute(
        "SELECT COUNT(*) FROM kg_entities WHERE status='merged'").fetchone()[0]

    # Top entities after
    top = conn.execute("""
        SELECT canonical_name, entity_type, mention_count,
            (SELECT COUNT(*) FROM kg_relations WHERE source_id = e.kg_entity_id OR target_id = e.kg_entity_id) as degree
        FROM kg_entities e WHERE status='active'
        ORDER BY degree DESC LIMIT 10
    """).fetchall()

    conn.close()

    # Report
    reduction = (before["kg_entities"] - after["active"]) / before["kg_entities"] * 100

    report = f"""
{'='*60}
  KG NORMALIZATION REPORT
  Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}
{'='*60}

  --- Before ---
  Entities:     {before['kg_entities']} (active: {before['active']})
  Events:       {before['kg_events']}
  Relations:    {before['kg_relations']}

  --- Operations ---
  Entities merged:          {merged}
  Entities deleted (bad type): {deleted}
  Entities fixed (type):    {fixed}
  Orphan relations removed: {orphans}
  Self-relations removed:   {selfs}
  Duplicate relations removed: {dupes}

  --- After ---
  Entities:     {after['kg_entities']} (active: {after['active']}, merged: {after['merged']})
  Events:       {after['kg_events']}
  Relations:    {after['kg_relations']}

  --- Quality Check ---
  Entity reduction:     {reduction:.0f}% [target: >= 20%]  {'PASS' if reduction >= 20 else 'FAIL'}

  --- Top 10 Entities (after normalization) ---"""

    for i, r in enumerate(top, 1):
        report += f"\n  {i:2d}. {r['canonical_name']} ({r['entity_type']}) — degree: {r['degree']}, mentions: {r['mention_count']}"

    report += f"\n{'='*60}"

    print(report)
    logger.info(report)
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(report)

    return reduction >= 20


if __name__ == "__main__":
    passed = run_normalization()
    sys.exit(0 if passed else 1)

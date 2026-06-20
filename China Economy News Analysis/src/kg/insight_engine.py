"""KG Insight Engine.

Generates human-readable insights from KG data using:
1. Event clustering by industry/company
2. Graph centrality analysis
3. Trend extraction
4. LLM-powered insight summarization
"""

import json
import logging
import time
import requests
from datetime import datetime
from collections import Counter, defaultdict
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.database.kg_models import get_kg_connection
from src.kg.query import (
    get_graph_stats, get_top_entities, get_industry_events,
    get_industry_entities, get_policy_impact_chains,
    search_entity, get_entity_relations, get_entity_events,
)

# ── Config ──
OLLAMA_URL = "http://localhost:11434/api/generate"
INSIGHT_MODEL = "qwen2.5:14b"

LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
log_file = LOG_DIR / "kg_insight_engine.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(),
    ],
    force=True,
)
logger = logging.getLogger("kg_insight")


# ── 1. Event Clustering ──

def cluster_events_by_industry() -> dict:
    """Group events by industry via entity-news-event mapping."""
    conn = get_kg_connection()

    rows = conn.execute("""
        SELECT ent.canonical_name as industry, ent.entity_type,
               e.event_type, e.headline, e.magnitude, e.event_date
        FROM kg_events e
        JOIN kg_news_event_map nem ON e.kg_event_id = nem.kg_event_id
        JOIN kg_news_entity_map nenm ON nem.news_id = nenm.news_id
        JOIN kg_entities ent ON nenm.kg_entity_id = ent.kg_entity_id
        WHERE ent.entity_type = 'IND'
        ORDER BY ent.canonical_name, e.event_date DESC NULLS LAST
    """).fetchall()
    conn.close()

    clusters = defaultdict(list)
    for r in rows:
        clusters[r["industry"]].append({
            "event_type": r["event_type"],
            "headline": r["headline"],
            "magnitude": r["magnitude"],
            "date": r["event_date"],
        })
    return dict(clusters)


def cluster_events_by_company(limit: int = 15) -> dict:
    """Group events by top companies."""
    conn = get_kg_connection()

    rows = conn.execute("""
        SELECT ent.canonical_name as company,
               e.event_type, e.headline, e.magnitude, e.event_date
        FROM kg_events e
        JOIN kg_news_event_map nem ON e.kg_event_id = nem.kg_event_id
        JOIN kg_news_entity_map nenm ON nem.news_id = nenm.news_id
        JOIN kg_entities ent ON nenm.kg_entity_id = ent.kg_entity_id
        WHERE ent.entity_type = 'COM'
        ORDER BY ent.mention_count DESC, e.event_date DESC NULLS LAST
    """).fetchall()
    conn.close()

    clusters = defaultdict(list)
    for r in rows:
        clusters[r["company"]].append({
            "event_type": r["event_type"],
            "headline": r["headline"],
            "magnitude": r["magnitude"],
            "date": r["date"],
        })

    # Return top N by event count
    sorted_clusters = sorted(clusters.items(), key=lambda x: -len(x[1]))
    return dict(sorted_clusters[:limit])


# ── 2. Graph Centrality Analysis ──

def get_centrality_ranking(top_n: int = 15) -> list[dict]:
    """Rank entities by degree centrality (connections)."""
    conn = get_kg_connection()
    rows = conn.execute("""
        SELECT e.kg_entity_id, e.canonical_name, e.entity_type, e.mention_count,
            (SELECT COUNT(*) FROM kg_relations r
             WHERE r.source_id = e.kg_entity_id OR r.target_id = e.kg_entity_id) as degree
        FROM kg_entities e
        WHERE e.status = 'active'
        ORDER BY degree DESC
        LIMIT ?
    """, (top_n,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_high_impact_events(limit: int = 10) -> list[dict]:
    """Get events with highest magnitude or most entity connections."""
    conn = get_kg_connection()
    rows = conn.execute("""
        SELECT e.kg_event_id, e.event_type, e.headline, e.magnitude, e.event_date,
               (SELECT COUNT(DISTINCT nenm.kg_entity_id)
                FROM kg_news_event_map nem
                JOIN kg_news_entity_map nenm ON nem.news_id = nenm.news_id
                WHERE nem.kg_event_id = e.kg_event_id) as entity_reach
        FROM kg_events e
        ORDER BY
            CASE e.magnitude
                WHEN 'critical' THEN 4 WHEN 'major' THEN 3
                WHEN 'moderate' THEN 2 WHEN 'minor' THEN 1 ELSE 0
            END DESC,
            entity_reach DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── 3. Trend Extraction ──

def extract_trends() -> dict:
    """Extract key trends from KG data."""
    conn = get_kg_connection()

    # Event type distribution
    event_dist = {r[0]: r[1] for r in conn.execute(
        "SELECT event_type, COUNT(*) FROM kg_events GROUP BY event_type ORDER BY COUNT(*) DESC"
    ).fetchall()}

    # Magnitude distribution
    mag_dist = {r[0]: r[1] for r in conn.execute(
        "SELECT magnitude, COUNT(*) FROM kg_events GROUP BY magnitude ORDER BY COUNT(*) DESC"
    ).fetchall()}

    # Policy patterns (most regulated industries)
    policy_targets = conn.execute("""
        SELECT t.canonical_name, t.entity_type, COUNT(*) as reg_count
        FROM kg_relations r
        JOIN kg_entities s ON r.source_id = s.kg_entity_id
        JOIN kg_entities t ON r.target_id = t.kg_entity_id
        WHERE r.relation_type IN ('REGULATES', 'IMPACTS', 'ANNOUNCES')
          AND s.entity_type IN ('ORG', 'POL')
        GROUP BY t.kg_entity_id
        ORDER BY reg_count DESC LIMIT 10
    """).fetchall()

    # Most active companies
    active_companies = conn.execute("""
        SELECT e.canonical_name, e.mention_count,
               (SELECT COUNT(*) FROM kg_news_entity_map WHERE kg_entity_id = e.kg_entity_id) as news_count
        FROM kg_entities e
        WHERE e.entity_type = 'COM' AND e.status = 'active'
        ORDER BY news_count DESC LIMIT 10
    """).fetchall()

    # Industry event counts
    industry_activity = conn.execute("""
        SELECT ent.canonical_name,
               COUNT(DISTINCT nem.kg_event_id) as event_count
        FROM kg_entities ent
        JOIN kg_news_entity_map nenm ON ent.kg_entity_id = nenm.kg_entity_id
        JOIN kg_news_event_map nem ON nenm.news_id = nem.news_id
        WHERE ent.entity_type = 'IND'
        GROUP BY ent.kg_entity_id
        ORDER BY event_count DESC LIMIT 10
    """).fetchall()

    conn.close()

    return {
        "event_distribution": event_dist,
        "magnitude_distribution": mag_dist,
        "policy_targets": [dict(r) for r in policy_targets],
        "active_companies": [dict(r) for r in active_companies],
        "industry_activity": [dict(r) for r in industry_activity],
    }


# ── 4. LLM Insight Summarization ──

INSIGHT_PROMPT = """You are a Chinese economy analyst. Based on the knowledge graph data below, generate insights in Korean.

## KG Statistics
- Total entities: {total_entities} (Companies: {com_count}, Organizations: {org_count}, Industries: {ind_count})
- Total events: {total_events}
- Total relations: {total_relations}

## Top Event Types
{event_types}

## Most Regulated/Impacted Targets (policy → target)
{policy_targets}

## Most Active Companies
{active_companies}

## Most Active Industries
{industry_activity}

## High Impact Events
{high_impact_events}

## Top Connected Entities (by graph degree)
{centrality}

## Task
Based on the above KG data, generate a report in Korean with:

1. 핵심 트렌드 (Top 3~5 trends with brief explanation of WHY it matters)
2. 주요 기업 분석 (Key companies and their role in the trends)
3. 정책-산업 영향 흐름 (How government policies are affecting industries)
4. 이벤트 흐름 요약 (Summary of major event patterns)

Format each section clearly. Be specific and data-driven — reference actual entities and events from the data.
Output in Korean."""


def generate_llm_insights(trends: dict, stats: dict, centrality: list,
                           high_impact: list, policy_chains: list) -> str:
    """Generate LLM-powered insight summary."""

    event_types_str = "\n".join(
        f"- {k}: {v}건" for k, v in list(trends["event_distribution"].items())[:8])

    policy_str = "\n".join(
        f"- {r['canonical_name']} ({r['entity_type']}): 규제/영향 {r['reg_count']}회"
        for r in trends["policy_targets"][:8])

    company_str = "\n".join(
        f"- {r['canonical_name']}: 뉴스 {r['news_count']}건"
        for r in trends["active_companies"][:8])

    industry_str = "\n".join(
        f"- {r['canonical_name']}: 이벤트 {r['event_count']}건"
        for r in trends["industry_activity"][:8])

    impact_str = "\n".join(
        f"- {r['headline']} ({r['event_type']}, {r['magnitude']})"
        for r in high_impact[:8])

    centrality_str = "\n".join(
        f"- {r['canonical_name']} ({r['entity_type']}): degree={r['degree']}"
        for r in centrality[:8])

    prompt = INSIGHT_PROMPT.format(
        total_entities=stats["entities"],
        com_count=stats["entity_types"].get("COM", 0),
        org_count=stats["entity_types"].get("ORG", 0),
        ind_count=stats["entity_types"].get("IND", 0),
        total_events=stats["events"],
        total_relations=stats["relations"],
        event_types=event_types_str,
        policy_targets=policy_str,
        active_companies=company_str,
        industry_activity=industry_str,
        high_impact_events=impact_str,
        centrality=centrality_str,
    )

    try:
        resp = requests.post(
            OLLAMA_URL,
            json={"model": INSIGHT_MODEL, "prompt": prompt,
                  "stream": False,
                  "options": {"temperature": 0.3, "num_predict": 3000}},
            timeout=300,
        )
        resp.raise_for_status()
        return resp.json().get("response", "")
    except Exception as e:
        logger.error(f"LLM insight generation failed: {e}")
        return ""


# ── 5. Industry Deep Dive ──

def industry_deep_dive(industry_name: str) -> dict:
    """Generate comprehensive analysis for a specific industry."""
    entities = search_entity(industry_name, entity_type="IND")
    if not entities:
        return {"error": f"Industry '{industry_name}' not found"}

    ind = entities[0]
    ind_id = ind["kg_entity_id"]

    members = get_industry_entities(industry_name)
    events = get_industry_events(industry_name, limit=20)
    relations = get_entity_relations(ind_id)

    # Event type breakdown
    evt_types = Counter(e["event_type"] for e in events)

    return {
        "industry": ind,
        "members": members,
        "events": events,
        "event_type_breakdown": dict(evt_types),
        "relations": relations,
        "total_events": len(events),
        "total_members": len(members),
    }


# ── Main: Full Insight Report ──

def generate_full_report(industries: list[str] = None) -> str:
    """Generate complete KG insight report."""
    logger.info("=== Generating KG Insight Report ===")
    start = time.time()

    # Gather data
    stats = get_graph_stats()
    trends = extract_trends()
    centrality = get_centrality_ranking(15)
    high_impact = get_high_impact_events(10)
    policy_chains = get_policy_impact_chains(15)

    # Build structured report
    report = []
    report.append("=" * 60)
    report.append("  KG INSIGHT REPORT")
    report.append(f"  Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    report.append(f"  Data: {stats['entities']} entities, {stats['events']} events, {stats['relations']} relations")
    report.append("=" * 60)

    # Section 1: Graph Overview
    report.append("\n## 1. 그래프 현황\n")
    report.append(f"  엔티티: {stats['entities']}개 (활성: {stats['active_entities']})")
    for etype, cnt in sorted(stats["entity_types"].items(), key=lambda x: -x[1]):
        report.append(f"    {etype}: {cnt}")
    report.append(f"\n  이벤트: {stats['events']}개")
    for etype, cnt in list(stats["event_types"].items())[:8]:
        report.append(f"    {etype}: {cnt}")
    report.append(f"\n  관계: {stats['relations']}개")
    for rtype, cnt in list(stats["relation_types"].items()):
        report.append(f"    {rtype}: {cnt}")

    # Section 2: Centrality
    report.append("\n## 2. 핵심 엔티티 (연결 중심성)\n")
    for i, e in enumerate(centrality[:10], 1):
        report.append(f"  {i:2d}. {e['canonical_name']} ({e['entity_type']})"
                       f" — degree: {e['degree']}, mentions: {e['mention_count']}")

    # Section 3: High Impact Events
    report.append("\n## 3. 고영향 이벤트\n")
    for i, e in enumerate(high_impact[:10], 1):
        report.append(f"  {i:2d}. [{e['magnitude']}] {e['headline']}"
                       f" ({e['event_type']}, entities: {e['entity_reach']})")

    # Section 4: Policy → Industry Impact
    report.append("\n## 4. 정책 → 산업 영향 체인\n")
    for p in policy_chains[:10]:
        report.append(f"  {p['policy_name']} --[{p['relation_type']}]--> "
                       f"{p['target_name']} ({p['target_type']})")
        if p.get("evidence"):
            report.append(f"    근거: {p['evidence'][:80]}")

    # Section 5: Industry Activity
    report.append("\n## 5. 산업별 활동\n")
    for ind in trends["industry_activity"][:10]:
        report.append(f"  {ind['canonical_name']}: 이벤트 {ind['event_count']}건")

    # Section 6: Company Activity
    report.append("\n## 6. 주요 기업\n")
    for c in trends["active_companies"][:10]:
        report.append(f"  {c['canonical_name']}: 뉴스 {c['news_count']}건")

    # Section 7: Industry Deep Dives
    if industries:
        for ind_name in industries:
            report.append(f"\n## 7. 산업 심층 분석: {ind_name}\n")
            dive = industry_deep_dive(ind_name)
            if "error" in dive:
                report.append(f"  {dive['error']}")
                continue

            report.append(f"  관련 기업/엔티티: {dive['total_members']}개")
            for m in dive["members"][:8]:
                report.append(f"    - {m['canonical_name']} ({m['entity_type']})")

            report.append(f"  관련 이벤트: {dive['total_events']}건")
            for et, cnt in sorted(dive["event_type_breakdown"].items(), key=lambda x: -x[1]):
                report.append(f"    {et}: {cnt}")

            report.append("  주요 이벤트:")
            for e in dive["events"][:5]:
                report.append(f"    - [{e['magnitude']}] {e['headline']}")

    # Section 8: LLM-Generated Insights
    report.append("\n## 8. AI 분석 인사이트\n")
    logger.info("  Generating LLM insights...")
    llm_text = generate_llm_insights(trends, stats, centrality, high_impact, policy_chains)
    if llm_text:
        report.append(llm_text)
    else:
        report.append("  (LLM 인사이트 생성 실패)")

    report.append("\n" + "=" * 60)

    duration = time.time() - start
    report_text = "\n".join(report)
    report_text += f"\n  Report generated in {duration:.0f}s\n"

    logger.info(report_text)

    # Save to log
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"\n{'#'*60}\n")
        f.write(f"# {datetime.now().isoformat()}\n")
        f.write(report_text)

    return report_text


if __name__ == "__main__":
    report = generate_full_report(industries=["AI", "반도체"])
    print(report)

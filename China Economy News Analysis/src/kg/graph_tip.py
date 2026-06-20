"""Graph-based Tip Generator — 그래프 경로에서 팁 데이터를 추출하고 Qwen으로 설명.

흐름: 뉴스 엔티티 → Neo4j 경로 조회 → 구조 데이터 추출 → Qwen 설명 생성.
fallback: 그래프 실패 시 기존 텍스트 기반 생성.
비침투: 그래프 지연/장애 시 사용자 응답 무영향.
"""

import json
import re
import logging
import time
import requests
from typing import Optional
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.kg.neo4j_adapter import is_available, session

logger = logging.getLogger("kg.graph_tip")

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "qwen2.5:14b"
GRAPH_TIMEOUT = 5  # 그래프 조회 최대 5초
KOREA_BLOCK = ["한국은", "한국의", "한국도", "한국에서", "한국 기업", "한국 경제", "한국 시장"]


# ══════════════════════════════════════
# 1. 그래프 경로 추출 (Neo4j → 구조 데이터)
# ══════════════════════════════════════

def extract_graph_context(news_id: int, entities: list[str]) -> dict:
    """뉴스 관련 엔티티에서 그래프 경로를 추출하여 구조 데이터 반환.

    Returns:
        {
            "cause": [{"entity": str, "type": str, "action": str}],
            "change": [{"from": str, "to": str, "relation": str}],
            "result": [{"entity": str, "impact": str}],
            "korea_impact": [{"path": str, "relevance": str}],
            "has_data": bool
        }
    """
    empty = {"cause": [], "change": [], "result": [], "korea_impact": [], "has_data": False}

    if not is_available() or not entities:
        return empty

    context = {"cause": [], "change": [], "result": [], "korea_impact": []}

    with session() as s:
        if s is None:
            return empty

        try:
            for ent in entities[:5]:
                # 1) 원인: 이 엔티티에 영향을 주는 정책/이벤트
                causes = s.run(
                    "MATCH (src)-[r:AFFECTS|REGULATES|TRIGGERS]->(tgt) "
                    "WHERE tgt.name CONTAINS $kw OR tgt.name_zh CONTAINS $kw "
                    "RETURN src.name AS entity, labels(src)[0] AS type, type(r) AS action, "
                    "       tgt.name AS target "
                    "LIMIT 3",
                    kw=ent
                ).data()
                for c in causes:
                    context["cause"].append({
                        "entity": c["entity"], "type": c["type"],
                        "action": c["action"], "target": c["target"]
                    })

                # 2) 변화: 이 엔티티의 관계 변화
                changes = s.run(
                    "MATCH (a)-[r]->(b) "
                    "WHERE a.name CONTAINS $kw OR a.name_zh CONTAINS $kw "
                    "RETURN a.name AS from_entity, b.name AS to_entity, "
                    "       type(r) AS relation, r.weight AS weight "
                    "ORDER BY r.weight DESC LIMIT 3",
                    kw=ent
                ).data()
                for ch in changes:
                    context["change"].append({
                        "from": ch["from_entity"], "to": ch["to_entity"],
                        "relation": ch["relation"],
                        "weight": ch.get("weight", 0)
                    })

                # 3) 결과: 이 엔티티가 영향을 미치는 대상
                impacts = s.run(
                    "MATCH (src)-[r:IMPACTS]->(tgt) "
                    "WHERE src.name CONTAINS $kw OR src.name_zh CONTAINS $kw "
                    "RETURN tgt.name AS entity, tgt.entity_type AS type "
                    "LIMIT 3",
                    kw=ent
                ).data()
                for imp in impacts:
                    context["result"].append({
                        "entity": imp["entity"], "impact": imp.get("type", "")
                    })

                # 4) 한국 영향: 2~3홉 경로로 한국 관련 연결
                korea = s.run(
                    "MATCH path=(a)-[*1..3]->(b) "
                    "WHERE (a.name CONTAINS $kw OR a.name_zh CONTAINS $kw) "
                    "  AND (b.name CONTAINS '한국' OR b.name CONTAINS 'Korea' "
                    "       OR b.name CONTAINS '글로벌' OR b.name CONTAINS '수출') "
                    "RETURN [n IN nodes(path) | n.name] AS path_names, "
                    "       length(path) AS hops "
                    "LIMIT 2",
                    kw=ent
                ).data()
                for k in korea:
                    path_str = " → ".join([n for n in k["path_names"] if n])
                    context["korea_impact"].append({
                        "path": path_str, "relevance": f"{k['hops']}홉 연결"
                    })

        except Exception as e:
            logger.warning(f"Graph context extraction failed: {e}")
            return empty

    # 중복 제거
    for key in context:
        seen = set()
        unique = []
        for item in context[key]:
            sig = str(item)
            if sig not in seen:
                seen.add(sig)
                unique.append(item)
        context[key] = unique[:3]

    context["has_data"] = any(len(v) > 0 for v in context.values() if isinstance(v, list))
    return context


# ══════════════════════════════════════
# 2. 그래프 데이터 → Qwen 팁 생성
# ══════════════════════════════════════

GRAPH_TIP_PROMPT = """지식그래프에서 추출된 데이터를 기반으로 투자자/비즈니스 독자를 위한 실전 팁을 작성하라.

## 그래프 데이터:
원인: {cause}
변화: {change}
결과: {result}
글로벌 영향: {korea_impact}

## 규칙:
1. 반드시 위 데이터에 있는 정보만 사용 (추측 금지)
2. "원인 → 변화 → 결과" 흐름으로 2~3문장 작성
3. 150자 이내, 💡로 시작, 마침표로 끝낼 것
4. 한국어만 사용 (한자 금지)
5. "이 뉴스는"으로 시작 금지
6. 투자/행동 시사점 1개 포함

뉴스 제목: {title}

💡"""

FALLBACK_TIP_PROMPT = """중국 경제 뉴스를 읽는 투자자/비즈니스 독자를 위한 실전 팁을 1개만 작성하라.

다음 중 하나만 선택:
- 투자 시사점: 이 뉴스가 관련 산업/종목에 미치는 구체적 영향
- 핵심 수치 해석: 뉴스 속 수치가 의미하는 바를 해석

규칙:
- "이 뉴스는"으로 시작 금지, "한국" 단어 금지
- 150자 이내, 💡로 시작, 마침표로 끝낼 것
- 뉴스 본문 데이터만 사용

뉴스: {title}
요약: {summary}

💡"""


def _call_ollama(prompt: str, temperature: float = 0.3) -> str:
    try:
        resp = requests.post(OLLAMA_URL, json={
            "model": MODEL, "prompt": prompt, "stream": False,
            "options": {"temperature": temperature, "num_predict": 200,
                        "num_gpu": 35, "num_ctx": 2048}
        }, timeout=120)
        resp.raise_for_status()
        return resp.json().get("response", "").strip()
    except Exception as e:
        logger.error(f"Ollama failed: {e}")
        return ""


def _format_context(ctx: dict) -> dict:
    """그래프 컨텍스트를 프롬프트에 넣을 문자열로 변환."""
    def _fmt_list(items):
        if not items:
            return "(없음)"
        parts = []
        for item in items[:3]:
            if isinstance(item, dict):
                parts.append(", ".join(f"{k}:{v}" for k, v in item.items() if v))
        return "; ".join(parts) or "(없음)"

    return {
        "cause": _fmt_list(ctx.get("cause", [])),
        "change": _fmt_list(ctx.get("change", [])),
        "result": _fmt_list(ctx.get("result", [])),
        "korea_impact": _fmt_list(ctx.get("korea_impact", [])),
    }


def _clean_tip(raw: str) -> str:
    """팁 후처리: 정리 + 150자 + 완결성."""
    if not raw:
        return ""
    tip = raw.strip().split('\n')[0].strip()
    tip = re.sub(r'\*\*', '', tip)
    if not tip.startswith('💡'):
        tip = '💡 ' + tip
    if len(tip) > 150:
        for i in range(149, 10, -1):
            if tip[i] in '.!?다요음함임됨며라니까습':
                return tip[:i+1]
        return tip[:147] + '.'
    return tip


# ══════════════════════════════════════
# 3. Public API
# ══════════════════════════════════════

def generate_graph_tip(news_id: int, title: str, summary: str,
                        entities: list[str] = None) -> dict:
    """그래프 기반 팁 생성 (fallback 포함).

    Args:
        news_id: 뉴스 ID
        title: 뉴스 제목
        summary: 요약 (한국어 또는 중국어)
        entities: 뉴스에 등장하는 엔티티 (없으면 제목에서 추출)

    Returns:
        {"tip": str, "source": "graph"|"fallback", "context": dict}
    """
    t0 = time.time()

    # 엔티티가 없으면 제목에서 추출
    if not entities:
        entities = _extract_entities_from_title(title)

    # ── 1단계: 그래프 경로 추출 ──
    graph_ctx = extract_graph_context(news_id, entities)

    if graph_ctx["has_data"]:
        # ── 2단계: 그래프 기반 팁 생성 ──
        formatted = _format_context(graph_ctx)
        raw = _call_ollama(GRAPH_TIP_PROMPT.format(
            title=title[:60], **formatted
        ))
        tip = _clean_tip(raw)

        # 한자 검사
        if tip and not re.search(r'[\u4e00-\u9fff]', tip):
            # 한국 언급 검사
            if not any(kw in tip for kw in KOREA_BLOCK):
                dur = time.time() - t0
                logger.info(f"  Graph tip OK ({dur:.1f}s): {tip[:50]}...")
                return {"tip": tip, "source": "graph", "context": graph_ctx,
                        "duration": round(dur, 1)}

        logger.info(f"  Graph tip failed validation, falling back")

    # ── fallback: 기존 텍스트 기반 ──
    raw = _call_ollama(FALLBACK_TIP_PROMPT.format(
        title=title[:60], summary=summary[:300]
    ))
    tip = _clean_tip(raw)

    # 한자 제거 (Papago)
    if tip and re.search(r'[\u4e00-\u9fff]', tip):
        try:
            from src.cni.translator import papago_translate
            clean = tip.lstrip('💡 ').strip()
            translated = papago_translate(clean)
            if translated and not re.search(r'[\u4e00-\u9fff]', translated):
                tip = '💡 ' + translated
                if len(tip) > 250:
                    tip = tip[:247] + '.'
        except Exception:
            pass

    dur = time.time() - t0
    logger.info(f"  Fallback tip ({dur:.1f}s): {tip[:50]}...")
    return {"tip": tip, "source": "fallback", "context": graph_ctx,
            "duration": round(dur, 1)}


def _extract_entities_from_title(title: str) -> list[str]:
    """제목에서 엔티티 후보 추출 (간단 규칙 기반)."""
    entities = []
    # 영문 브랜드명
    entities.extend(re.findall(r'[A-Z][A-Za-z]+(?:\s[A-Z][A-Za-z]+)?', title))
    # 중국어 고유명사 (2자 이상 연속)
    entities.extend(re.findall(r'[\u4e00-\u9fff]{2,6}', title))
    # 한국어 고유명사 (조사 앞)
    entities.extend(re.findall(r'([가-힣]{2,6})(?:은|는|이|가|의|,)', title))
    return list(set(entities))[:5]


# ══════════════════════════════════════
# 4. CLI 테스트
# ══════════════════════════════════════

if __name__ == "__main__":
    import argparse
    from src.database.models import get_connection

    parser = argparse.ArgumentParser()
    parser.add_argument("--news-id", type=int, required=True)
    args = parser.parse_args()

    conn = get_connection()
    r = conn.execute(
        "SELECT original_title, summary_zh, summary FROM news WHERE id=?",
        (args.news_id,)
    ).fetchone()
    conn.close()

    if r:
        result = generate_graph_tip(
            args.news_id,
            r["original_title"] or "",
            r["summary"] or r["summary_zh"] or ""
        )
        print(f"Source: {result['source']}")
        print(f"Tip: {result['tip']}")
        print(f"Duration: {result['duration']}s")
        if result['context']['has_data']:
            print(f"Context: {json.dumps(result['context'], ensure_ascii=False, indent=2)[:500]}")

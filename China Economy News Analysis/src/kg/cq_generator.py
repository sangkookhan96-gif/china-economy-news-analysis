"""Competency Question Generator — 뉴스에서 CQ 자동 생성 + 분류.

CQ는 지식그래프의 구조적 완성도를 측정하고 확장을 유도하는 질문이다.
뉴스 입력 → CQ 생성 (Qwen) → 유형 분류 → entities 추출.
"""

import json
import re
import logging
import requests
from typing import Optional

logger = logging.getLogger("kg.cq")

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "qwen2.5:14b"

CQ_TYPES = ["POLICY", "INDUSTRY", "COMPANY", "KOREA_IMPACT"]

CQ_PROMPT = """중국 경제 뉴스를 분석하여 지식그래프를 확장하기 위한 질문 5개를 생성하라.

## 유형별 필수 질문 (각 1개 이상):
- POLICY: 이 뉴스와 관련된 정책/규제는 무엇이며, 어떤 산업에 영향을 주는가?
- INDUSTRY: 이 뉴스가 속한 산업의 주요 기업과 시장 구도는?
- COMPANY: 언급된 기업의 공급망/투자/경쟁 관계는?
- KOREA_IMPACT: 이 뉴스가 관련 글로벌 산업/공급망에 미치는 영향은?

## 규칙:
1. 각 질문은 "원인 → 결과" 구조 포함
2. 구체적 기업명/정책명/수치 포함
3. entities 배열에 질문에 등장하는 핵심 개체 나열
4. JSON 배열만 출력 (설명 없이)

뉴스 제목: {title}
요약: {summary}

출력:
[
  {{"type": "POLICY", "question": "...", "entities": ["entity1", "entity2"]}},
  {{"type": "INDUSTRY", "question": "...", "entities": [...]}},
  {{"type": "COMPANY", "question": "...", "entities": [...]}},
  {{"type": "KOREA_IMPACT", "question": "...", "entities": [...]}},
  {{"type": "...", "question": "...", "entities": [...]}}
]"""


def _call_ollama(prompt: str) -> str:
    try:
        resp = requests.post(OLLAMA_URL, json={
            "model": MODEL, "prompt": prompt, "stream": False,
            "options": {"temperature": 0.3, "num_predict": 500,
                        "num_gpu": 35, "num_ctx": 2048}
        }, timeout=120)
        resp.raise_for_status()
        return resp.json().get("response", "").strip()
    except Exception as e:
        logger.error(f"Ollama CQ generation failed: {e}")
        return ""


def generate_cqs(title: str, summary: str) -> list[dict]:
    """뉴스에서 CQ 3~5개 생성.

    Returns:
        [{"type": str, "question": str, "entities": list[str]}, ...]
    """
    raw = _call_ollama(CQ_PROMPT.format(title=title[:100], summary=summary[:400]))
    if not raw:
        return []

    # JSON 파싱
    try:
        # ```json 블록 제거
        clean = re.sub(r'^```json\s*', '', raw)
        clean = re.sub(r'\s*```$', '', clean)
        start = clean.find('[')
        end = clean.rfind(']')
        if start >= 0 and end > start:
            clean = clean[start:end+1]
        cqs = json.loads(clean)
    except json.JSONDecodeError:
        logger.warning(f"CQ JSON parse failed: {raw[:100]}...")
        return []

    if not isinstance(cqs, list):
        return []

    # 검증 + 정규화
    valid = []
    for cq in cqs:
        if not isinstance(cq, dict):
            continue
        cq_type = (cq.get("type") or "").upper()
        question = cq.get("question", "")
        entities = cq.get("entities", [])

        if not question or len(question) < 10:
            continue

        # 유형 보정
        if cq_type not in CQ_TYPES:
            cq_type = _classify_cq(question)

        valid.append({
            "type": cq_type,
            "question": question,
            "entities": entities if isinstance(entities, list) else [],
        })

    # 최소 3개, 최대 5개
    return valid[:5]


def _classify_cq(question: str) -> str:
    """질문 텍스트에서 CQ 유형 추론."""
    q = question.lower()
    if any(kw in q for kw in ["정책", "규제", "법률", "보조금", "관세", "政策", "regulation"]):
        return "POLICY"
    if any(kw in q for kw in ["한국", "korea", "수출", "공급망", "글로벌"]):
        return "KOREA_IMPACT"
    if any(kw in q for kw in ["기업", "매출", "투자", "인수", "공급", "company", "企业"]):
        return "COMPANY"
    return "INDUSTRY"


# ══════════════════════════════════════
# CQ → Cypher 변환
# ══════════════════════════════════════

CYPHER_TEMPLATES = {
    "POLICY": [
        "MATCH (p:Policy)-[:AFFECTS]->(i:Industry) WHERE p.name CONTAINS $kw OR p.name_zh CONTAINS $kw RETURN p.name AS policy, i.name AS industry, 'AFFECTS' AS rel",
        "MATCH (p:Policy)-[:ANNOUNCED_BY]->(e) WHERE p.name CONTAINS $kw RETURN p.name AS policy, e.name AS announcer",
    ],
    "INDUSTRY": [
        "MATCH (c:Company)-[:BELONGS_TO]->(i:Industry) WHERE i.name CONTAINS $kw OR i.name_zh CONTAINS $kw RETURN c.name AS company, i.name AS industry",
        "MATCH (e:Event)-[:IMPACTS]->(c:Company)-[:BELONGS_TO]->(i:Industry) WHERE i.name CONTAINS $kw RETURN e.headline AS event, c.name AS company",
    ],
    "COMPANY": [
        "MATCH (c:Company)-[r]->(related) WHERE c.name CONTAINS $kw OR c.name_zh CONTAINS $kw RETURN c.name AS source, type(r) AS rel, related.name AS target, r.weight AS weight",
        "MATCH (a:Company)-[:SUPPLIES_TO]->(b:Company) WHERE a.name CONTAINS $kw OR b.name CONTAINS $kw RETURN a.name AS supplier, b.name AS buyer",
        "MATCH (a:Company)-[:COMPETES_WITH]-(b:Company) WHERE a.name CONTAINS $kw RETURN a.name AS company_a, b.name AS company_b",
    ],
    "KOREA_IMPACT": [
        "MATCH (e)-[r]->(related) WHERE (e.name CONTAINS $kw OR related.name CONTAINS $kw) RETURN e.name AS source, type(r) AS rel, related.name AS target, r.weight AS weight LIMIT 10",
        "MATCH (i:Industry)<-[:BELONGS_TO]-(c:Company) WHERE i.name CONTAINS $kw RETURN i.name AS industry, collect(c.name) AS companies LIMIT 5",
    ],
}


def cq_to_cyphers(cq: dict) -> list[dict]:
    """CQ를 실행 가능한 Cypher 쿼리 목록으로 변환.

    Returns:
        [{"cypher": str, "params": dict, "template_id": str}, ...]
    """
    cq_type = cq.get("type", "INDUSTRY")
    entities = cq.get("entities", [])
    templates = CYPHER_TEMPLATES.get(cq_type, CYPHER_TEMPLATES["INDUSTRY"])

    queries = []
    for ent in entities[:3]:  # 엔티티당 최대 3개
        for i, tmpl in enumerate(templates):
            queries.append({
                "cypher": tmpl,
                "params": {"kw": ent},
                "template_id": f"{cq_type}_{i}",
                "entity": ent,
            })

    return queries

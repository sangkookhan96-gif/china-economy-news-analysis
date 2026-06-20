# CQ + 지식그래프 연결 작업계획
> 작성일: 2026-04-04 | 현재 그래프: 372노드, 429관계

## 1. 전체 처리 흐름

```
뉴스 입력
  ↓
[Stage A] CQ 생성 (Qwen2.5:14b)
  → 3~5개 질문 자동 생성
  ↓
[Stage B] CQ 유형 분류 (규칙 기반)
  → POLICY / INDUSTRY / COMPANY / KOREA_IMPACT
  ↓
[Stage C] CQ → Cypher 변환 (템플릿 매칭)
  → 유형별 Cypher 쿼리 생성
  ↓
[Stage D] Neo4j 조회 실행
  → 결과 반환 또는 빈 결과
  ↓
[Stage E] 그래프 보완 판단
  → 결과 없음: 노드/관계 MERGE
  → 관계 약함: weight 강화
  ↓
[Stage F] 결과 저장 + 로그
  → cq_log 테이블 + Neo4j 업데이트
```

**비침투 원칙**: 전 과정 비동기. 실패 시 뉴스 파이프라인 무영향.

## 2. CQ 생성 설계

### 프롬프트
```
뉴스 제목: {title}
요약: {summary}

이 뉴스에 대해 지식그래프를 확장하기 위한 질문 5개를 JSON 배열로 생성하라.

규칙:
1. 반드시 아래 4개 유형을 각 1개 이상 포함:
   - POLICY: 이 뉴스와 관련된 정책은 무엇이며, 어떤 산업에 영향을 주는가?
   - INDUSTRY: 이 뉴스가 속한 산업의 주요 기업과 경쟁 구도는?
   - COMPANY: 언급된 기업의 공급망/투자 관계는?
   - KOREA_IMPACT: 이 뉴스가 한국 관련 산업/기업에 미치는 영향은?
2. 각 질문은 원인→결과 구조 포함
3. 구체적 기업명/정책명/수치 포함

출력 형식 (JSON만):
[
  {"type": "POLICY", "question": "...", "entities": ["엔티티1", "엔티티2"]},
  ...
]
```

### 품질 기준
- 최소 3개, 최대 5개
- 각 CQ에 entities 배열 필수 (그래프 매핑용)
- KOREA_IMPACT 최소 1개 필수

## 3. CQ 분류 체계

| 유형 | 키워드 패턴 | 그래프 탐색 방향 |
|------|-----------|----------------|
| POLICY | 정책, 규제, 법률, 보조금, 관세 | Policy → Industry/Company |
| INDUSTRY | 산업, 시장, 경쟁, 점유율, 성장률 | Industry ← Company, Event |
| COMPANY | 기업, 매출, 투자, 인수, 공급망 | Company → Company, Event |
| KOREA_IMPACT | 한국, 수출, 공급망, 경쟁 | Entity → Entity (cross-border) |

## 4. CQ → Cypher 변환 규칙

| CQ 유형 | Cypher 패턴 | 설명 |
|---------|------------|------|
| POLICY | `MATCH (p:Policy)-[:AFFECTS]->(i:Industry) WHERE p.name CONTAINS $keyword RETURN p, i` | 정책→산업 영향 경로 |
| INDUSTRY | `MATCH (c:Company)-[:BELONGS_TO]->(i:Industry) WHERE i.name CONTAINS $keyword RETURN c, i` | 산업 내 기업 조회 |
| COMPANY | `MATCH (c:Company)-[r]->(related) WHERE c.name CONTAINS $keyword RETURN c, type(r), related` | 기업 관계망 |
| KOREA_IMPACT | `MATCH (e1)-[r]->(e2) WHERE (e1.name CONTAINS $keyword OR e2.name CONTAINS $keyword) RETURN e1, r, e2` | 엔티티 간 영향 경로 |
| 공급망 | `MATCH path=(a:Company)-[:SUPPLIES_TO*1..3]->(b:Company) WHERE a.name CONTAINS $keyword RETURN path` | 공급망 다중 홉 |
| 경쟁 | `MATCH (a:Company)-[:COMPETES_WITH]-(b:Company) WHERE a.name CONTAINS $keyword RETURN a, b` | 경쟁 관계 |

## 5. 그래프 보완 로직

### 보완 조건
| 조건 | 동작 |
|------|------|
| CQ 엔티티가 그래프에 없음 | MERGE 노드 생성 |
| CQ 관계가 그래프에 없음 | MERGE 관계 생성 (weight=0.5) |
| 기존 관계 weight < 0.3 | weight += 0.3 (강화) |
| CQ 결과 0건 | 엔티티 + 관계 동시 생성 |

### MERGE 전략
```cypher
-- 엔티티 보완
MERGE (e:Company {entity_id: $eid})
ON CREATE SET e.name = $name, e.source = 'cq_expansion', e.created_at = datetime()

-- 관계 보완
MERGE (a)-[r:AFFECTS]->(b)
ON CREATE SET r.weight = 0.5, r.source = 'cq_inferred', r.created_at = datetime()
ON MATCH SET r.weight = r.weight + 0.3
```

## 6. 구현 파일 구조

| 파일 | 역할 |
|------|------|
| `src/kg/cq_generator.py` | CQ 생성 (Qwen) + 분류 + Cypher 변환 |
| `src/kg/cq_executor.py` | Neo4j 조회 + 보완 + 로그 |
| `src/kg/neo4j_adapter.py` | 기존 (MERGE/쿼리 함수) |

## 7. 로그 체계

| 필드 | 설명 |
|------|------|
| news_id | 뉴스 ID |
| cq_text | 생성된 질문 |
| cq_type | POLICY/INDUSTRY/COMPANY/KOREA_IMPACT |
| cypher | 실행된 쿼리 |
| result_count | 조회 결과 건수 |
| nodes_added | 추가된 노드 수 |
| relations_added | 추가된 관계 수 |
| timestamp | 실행 시각 |

## 8. 실행 체크리스트

- [ ] `cq_generator.py` 구현 (CQ 생성 + 분류)
- [ ] `cq_executor.py` 구현 (Cypher 변환 + 실행 + 보완)
- [ ] 파일럿 테스트 5건
- [ ] 로그 테이블 생성
- [ ] 배치 프로세서 연동

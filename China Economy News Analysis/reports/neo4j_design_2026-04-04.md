# CNI 지식그래프 Neo4j 전환 설계서
> 작성일: 2026-04-04 | 버전: v1.0

## 1. 전체 아키텍처

```
[외부 파이프라인 — 사용자 대면, 변경 없음]
뉴스수집 → 요약(ZH) → 팁 → Papago(KO) → 헤드라인(KO) → 사용자 제공
                                                          ↑ (읽기 전용 참조, 선택적)
[내부 파이프라인 — 백그라운드, 비동기]                      |
뉴스수집 → 개체추출 → 관계생성 → Neo4j저장 → 분석데이터 ──┘
```

### 핵심 원칙
- 두 파이프라인은 **완전 분리**
- Neo4j 장애 시 외부 파이프라인 100% 정상 작동
- 그래프는 팁 품질 향상에만 **보조적** 사용

## 2. 데이터 모델 (Neo4j 노드)

| 노드 | 라벨 | 주요 속성 | 기존 SQLite 대응 |
|------|------|-----------|-----------------|
| Company | :Company | name, name_zh, ticker, sector | kg_entities(COM) |
| Industry | :Industry | name, name_zh, gics_code | kg_entities(IND) |
| Policy | :Policy | name, name_zh, issuer, effective_date | kg_entities(POL) |
| Event | :Event | headline, event_type, magnitude, date | kg_events |
| Country | :Country | name, name_zh, iso_code | kg_entities(GEO) |
| Person | :Person | name, name_zh, title, org | kg_entities(PER) |
| News | :News | news_id, title, published_at, source | news 테이블 |
| Indicator | :Indicator | name, value, unit, period | kg_entities(IDX/FIN) |

## 3. 관계(Relationship) 설계

| 관계 | 설명 | 속성 |
|------|------|------|
| (Company)-[:BELONGS_TO]->(Industry) | 기업 소속 산업 | since |
| (Policy)-[:AFFECTS]->(Industry) | 정책의 산업 영향 | weight, direction(+/-) |
| (Event)-[:IMPACTS]->(Company) | 이벤트의 기업 영향 | magnitude, sentiment |
| (News)-[:MENTIONS]->(Entity) | 뉴스의 개체 언급 | role(actor/target/mention), confidence |
| (Entity)-[:RELATED_TO]->(Entity) | 범용 관계 | relation_type, weight, timestamp |
| (Company)-[:COMPETES_WITH]->(Company) | 경쟁 관계 | sector |
| (Policy)-[:ANNOUNCED_BY]->(Entity) | 정책 발표 주체 | date |
| (Company)-[:SUPPLIES_TO]->(Company) | 공급망 관계 | product |

## 4. 변환 로직 (뉴스 → 그래프)

```
1. 기존 Qwen2.5 추출 (quality_batch_v2) → ExtractionResult
2. ExtractionResult → Neo4j MERGE 쿼리 변환
3. 비동기 저장 (사용자 응답 차단 없음)
4. 실패 시 SQLite fallback + 로그
```

## 5. Qwen 연동 방식

| 단계 | 그래프 사용 | 방식 |
|------|------------|------|
| 요약 생성 | 사용 안 함 | 속도 최우선 |
| 팁 생성 | 선택적 참조 | 그래프에서 관련 엔티티/이벤트 조회 → 프롬프트 보강 |
| 헤드라인 | 사용 안 함 | KO 요약 기반 |
| KG 추출 | Qwen 사용 | 비동기 백그라운드 |

## 6. 성능 보호 전략

- Neo4j는 별도 프로세스로 운영 (Bolt 프로토콜)
- 사용자 요청 경로에 Neo4j 쿼리 없음
- timeout 5초 초과 시 그래프 조회 포기 → 기존 텍스트 기반 생성
- Neo4j 다운 시 자동 감지 → graceful degradation

## 7. 마이그레이션 계획

| 단계 | 내용 | 상태 |
|------|------|------|
| Phase 1 | Neo4j 설치 + 스키마 생성 | 진행 |
| Phase 2 | 기존 published 뉴스 일괄 추출 → Neo4j 저장 | 대기 |
| Phase 3 | 일일 배치 자동화 (cron/timer) | 대기 |
| Phase 4 | 팁 생성 시 그래프 보조 참조 도입 | 대기 |

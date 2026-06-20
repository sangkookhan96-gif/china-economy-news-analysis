# CEKG-V2 Graph Gap Detection

**STATUS: PASSIVE COLLECTION (T+0 ~ T+7)**
**활성화: T+7 (2026-03-28)**
**자동 적용: 금지**
**일괄 그래프 업데이트: 금지**

---

# 1. 핵심 원칙

```
이 시스템은 그래프의 "빈 곳"을 탐지하고
보강 후보를 제시한다. 관계를 직접 추가하지 않는다.

금지:
  - 자동 관계 생성
  - 일괄 그래프 업데이트
  - 기존 관계 수정/삭제

필수:
  - 모든 보강 후보 → 사람 승인
  - 건별 승인 (일괄 승인 금지)
```

---

# 2. 입력 소스

| 소스 | 테이블 | 사용 데이터 |
|------|--------|---------|
| 경로 거부 기록 | kg_trigger_failure_log (failure_type="path_missing") | source_event, target_entity, path_search_result, suggested_fix |
| TRIGGERS 미발동 | kg_trigger_failure_log (failure_type="rule_matched_but_not_fired") | rule_id, confidence, 실패 사유 |
| 경로 없음 충돌 | kg_conflict_log (conflict_type="no_entity_path") | source_event, target_entity |
| 그래프 상태 | kg_graph_health_snapshot | orphan_ratio, avg_degree, path_exists_rate |

---

# 3. PHASE 1: PASSIVE COLLECTION (T+0 ~ T+7)

## 수집 내용

```
매일:
  1. path_missing 건수 집계
  2. 거부된 target 엔티티 빈도표 작성
  3. 거부된 source→target 쌍 빈도표 작성
  4. suggested_fix 집계 (추천 관계 빈도)
  5. trigger_miss 사유 분류

저장: 일간 스냅샷으로 축적. 분석/권고 생성 안 함.
```

## 수집 테이블

```
┌─────────────────────────────────────────────┐
│  kg_gap_daily_snapshot                      │
├─────────────────────────────────────────────┤
│  snapshot_date      TEXT PRIMARY KEY        │
│  total_impacts_candidates  INTEGER          │
│  path_missing_count        INTEGER          │
│  path_missing_rate         REAL             │
│                                             │
│  top_missing_targets TEXT (JSON)            │
│    [                                        │
│      {"entity_id": "...",                  │
│       "entity_name": "...",                │
│       "miss_count": N},                    │
│      ...                                    │
│    ]                                        │
│                                             │
│  top_missing_pairs   TEXT (JSON)            │
│    [                                        │
│      {"source_type": "...",                │
│       "target_id": "...",                  │
│       "target_name": "...",                │
│       "miss_count": N,                     │
│       "suggested_relation": "..."},        │
│      ...                                    │
│    ]                                        │
│                                             │
│  trigger_miss_count  INTEGER                │
│  trigger_miss_rules  TEXT (JSON)            │
│    {"TR-01": N, "TR-02": N, ...}           │
│                                             │
│  created_at          TEXT                   │
└─────────────────────────────────────────────┘
```

---

# 4. PHASE 2: ACTIVE DETECTION (T+7~)

## 4-1. Missing Edges Frequency Analysis

### 주간 집계

```
7일간 kg_gap_daily_snapshot 합산:

missing_target_ranking:
  target 엔티티별 총 miss_count 내림차순

missing_pair_ranking:
  (source_actor_type → target) 쌍별 총 miss_count 내림차순

trigger_miss_ranking:
  rule_id별 총 miss_count 내림차순
```

### 유의미 gap 판별 기준

| 기준 | 임계값 | 의미 |
|------|--------|------|
| 동일 target 주간 miss ≥ 5회 | HIGH | 이 엔티티로 향하는 경로가 구조적으로 부재 |
| 동일 쌍 주간 miss ≥ 3회 | MEDIUM | 특정 연결이 반복적으로 필요 |
| 동일 TR 규칙 주간 miss ≥ 3회 | HIGH (if TR) | TRIGGERS 추론 체계적 장애 |

## 4-2. High-Impact Missing Path 식별

### 영향도 산정

```
gap_impact_score =
    miss_frequency               # 주간 miss 횟수
  × target_importance_tier       # T1=5, T2=4, T3=3, T4=2, T5=1
  × rule_priority_weight         # TRIGGERS 관련=3, IMPACTS만=1
  × estimated_auto_rate_gain     # 이 경로 추가 시 예상 자동화율 증가

estimated_auto_rate_gain:
  miss_count / total_candidates (해당 주)
```

### 우선순위 분류

| 등급 | gap_impact_score | 의미 |
|------|-----------------|------|
| P1 — Critical Gap | ≥ 30 | 핵심 추론 경로 부재. 즉시 보강 검토 |
| P2 — Important Gap | 15~29 | 주요 경로 부재. 주간 리뷰 시 검토 |
| P3 — Minor Gap | 5~14 | 보조 경로 부재. 월간 검토 |
| P4 — Noise | < 5 | 무시 (일회성 또는 저영향) |

## 4-3. TRIGGERS 영향 분석

### 특별 처리

```
IF gap이 TRIGGERS 규칙(TR-01~05) 미발동의 원인인 경우:
  priority를 1단계 상향

  예: P2 → P1로 격상

근거:
  TRIGGERS는 인과 추론의 핵심.
  TRIGGERS가 체계적으로 미발동하면 추론 엔진의 가치가 반감.
```

### TRIGGERS gap 진단

```
각 TR 규칙에 대해:

IF 주간 발동 0회 AND 해당 event_type 이벤트 ≥ 3건:
  1. trigger_failure_log에서 실패 사유 분류:
     - path_missing: {N}건 → graph gap
     - entity_overlap_zero: {N}건 → 엔티티 추출 문제
     - confidence_low: {N}건 → base_score 문제

  2. path_missing이 주요 사유 (≥ 50%)인 경우:
     → 해당 규칙에 필요한 경로 유형 식별
     → top_missing_pairs에서 해당 경로 추출
     → gap_enrichment_candidate 생성
```

---

# 5. OUTPUT

## 5-1. Top Missing Relations

### 주간 출력 형식

```
=== CEKG-V2 Graph Gap Report ===
Week: {N} ({start} ~ {end})
Phase: {PASSIVE_COLLECTION | ACTIVE_DETECTION}

━━━ GAP SUMMARY ━━━

  Total path_missing: {N}건 (주간)
  Path missing rate:  {rate}% (target: ≤15%)
  Unique missing targets: {N}개
  Unique missing pairs:   {N}개

━━━ TOP MISSING RELATIONS (by impact) ━━━

  [{P1}] #{1} — gap_impact: {score}
    Missing: {entity_A} → {entity_B}
    Suggested type: {BELONGS_TO | REGULATES | MEASURES | ...}
    Miss frequency: {N}회/주
    Target tier: {T1~T5}
    Affects triggers: {YES (TR-XX) | NO}
    Estimated auto_rate gain: +{X.X}%p
    Source: {suggested_fix 출처}

  [{P2}] #{2} — gap_impact: {score}
    ...

  [{P3}] #{3} — gap_impact: {score}
    ...
```

## 5-2. Graph Enrichment Candidates

### 후보 테이블

```
┌─────────────────────────────────────────────┐
│  kg_enrichment_candidates                   │
├─────────────────────────────────────────────┤
│  candidate_id       TEXT PRIMARY KEY        │
│  week_id            TEXT NOT NULL           │
│  priority           TEXT NOT NULL           │
│    # P1 | P2 | P3 | P4                     │
│  gap_impact_score   REAL                    │
│                                             │
│  suggested_source   TEXT NOT NULL           │
│  suggested_target   TEXT NOT NULL           │
│  suggested_type     TEXT NOT NULL           │
│    # BELONGS_TO | REGULATES | MEASURES |   │
│    # LOCATED_IN | SUPPLIES_TO | ...        │
│                                             │
│  miss_frequency     INTEGER                 │
│  affected_rules     TEXT (JSON array)       │
│    # ["TR-01", "IM-P2I-01"]               │
│  estimated_gain     REAL                    │
│    # 예상 auto_rate 증가                    │
│                                             │
│  evidence           TEXT (JSON)             │
│    {                                        │
│      "trigger_failures": [log_ids],        │
│      "conflict_logs": [log_ids],           │
│      "suggested_fix_sources": [log_ids]    │
│    }                                        │
│                                             │
│  human_decision     TEXT DEFAULT 'pending'  │
│    # pending | approved | rejected |        │
│    # deferred                               │
│  decision_note      TEXT                    │
│  decided_at         TEXT                    │
│  created_at         TEXT                    │
└─────────────────────────────────────────────┘
```

### 승인 프로세스

```
1. 주간 보고서에 P1~P3 후보 제시
2. 사람이 각 후보를 검토:
   - approved: 해당 관계를 kg_relations에 수동 추가
   - rejected: 부적절 (decision_note에 사유)
   - deferred: 추가 정보 필요 (다음 주 재제시)
3. 승인된 관계 추가 후 다음 주 gap report에서 효과 확인

일괄 승인 금지.
각 후보를 개별 검토하여 건별 승인.
```

---

# 6. 연동 관계

```
┌─────────────────────────────┐
│ kg_trigger_failure_log      │──┐
│ kg_conflict_log             │──┤
│ kg_graph_health_snapshot    │──┤
└─────────────────────────────┘  │
                                 ▼
                    ┌──────────────────────┐
                    │ GRAPH GAP DETECTION  │
                    │  (본 문서)            │
                    └──────────┬───────────┘
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
    kg_gap_daily_snapshot   주간 보고서   kg_enrichment_candidates
                                              │
                                              ▼
                                      사람 승인 → 수동 관계 추가
                                              │
                                              ▼
                                    다음 주 효과 확인
                                    (auto_rate, path_exists_rate)
```

### 기존 시스템과의 관계

| 기존 문서 | 역할 | 본 문서와의 관계 |
|---------|------|-------------|
| cekg_diagnostic_logging | trigger_failure_log, suggested_fix 정의 | 입력 소스 |
| cekg_insight_optimization | GRAPH_EXPANSION 권고 규칙 | 본 문서가 상세 탐지, optimization이 권고 생성 |
| cekg_weekly_learning | rule_decay + path_missing 학습 | 본 문서 결과를 주간 학습에 반영 |
| cekg_monitoring_v2 | no_path_rejection_rate 경고 | 경고 발생 시 본 문서의 상세 분석 참조 |

---

# 7. 성과 측정

### 효과 추적

```
관계 추가 승인 후:

측정 시점: 추가 후 7일
비교 대상: 추가 전 7일

추적 지표:
  path_exists_rate: 변화
  auto_generation_rate: 변화
  해당 target의 miss_count: 변화
  affected_rules 발동 횟수: 변화

효과 판정:
  path_exists_rate +3%p 이상 → effective
  miss_count 50% 이상 감소 → effective
  변화 미미 → neutral
  fp_rate 증가 → harmful (관계 부적절)
```

### 월간 요약

```
=== Graph Gap Resolution Summary (Month {N}) ===

  Candidates generated: {N}
  Approved: {N} ({rate}%)
  Rejected: {N}
  Deferred: {N}

  Approved outcomes:
    effective: {N}
    neutral:   {N}
    harmful:   {N}

  Cumulative impact:
    path_exists_rate: {start}% → {current}% (+{delta}%p)
    auto_generation_rate: {start}% → {current}% (+{delta}%p)
    orphan_ratio: {start}% → {current}% ({delta}%p)
```

---

# RISK ANALYSIS

```json
{
  "risk_detected": false,
  "summary": "Graph Gap Detection은 탐지와 후보 제시만 수행. 자동 관계 생성/일괄 업데이트 금지. 기존 시스템 무접촉",
  "safeguards": [
    "no_auto_apply: 관계 자동 추가 금지",
    "no_bulk_graph_update: 일괄 업데이트 금지",
    "human_review_required: 건별 승인 필수",
    "기존 kg_relations 직접 수정 금지 (승인 시 수동 추가)",
    "기존 시스템 접근 없음"
  ]
}
```

# CEKG-V2 Edge Value Scoring Engine

**STATUS: PASSIVE FEATURE COLLECTION (T+0 ~ T+7)**
**점수 산출 활성화: T+7 (2026-03-28)**
**자동 적용: 금지**
**주간 최대 추가: 5개**

---

# 1. 핵심 원칙

```
모든 후보 관계에 정량 점수를 부여하여
"어떤 관계를 먼저 추가해야 가장 효과적인가"를 판별한다.

금지:
  - 자동 관계 추가
  - 주간 5개 초과 추가
  - 점수 기반 자동 승인

필수:
  - 점수는 우선순위 참고용
  - 최종 결정은 사람
  - 매주 상위 5개만 승인 검토
```

---

# 2. 입력 소스

| 소스 | 데이터 | 용도 |
|------|--------|------|
| kg_enrichment_candidates | 보강 후보 관계 | 점수 부여 대상 |
| kg_pruning_candidates | 과거 추가 관계의 성과 | 점수 모델 학습 |
| kg_trigger_failure_log | path_missing, rule_matched_but_not_fired | trigger_block 피처 |
| kg_gap_daily_snapshot | miss 빈도, top_missing_pairs | co_occurrence 피처 |
| kg_false_positive_log | 관계별 fp 이력 | 위험도 피처 |
| kg_entities | importance_score, tier | entity_importance 피처 |
| kg_relations | 기존 관계 구조 | path_unblock 계산 |
| kg_recommendation_log | insight_optimization 권고 이력 | GRAPH_EXPANSION 권고와 교차 참조. 동일 후보가 insight에서도 권고된 경우 신뢰도 가산 |
| daily_insight_report | anomaly, top_issues | connectivity_gap 이상 탐지 시 해당 영역 후보 우선 |

---

# 3. PHASE 1: PASSIVE FEATURE COLLECTION (T+0 ~ T+7)

## 수집 피처

| 피처 | 정의 | 수집 방법 |
|------|------|---------|
| `co_occurrence_score` | source-target 엔티티가 동일 이벤트에 함께 등장한 빈도 | 이벤트의 actors+targets에서 동시 출현 쌍 집계 |
| `semantic_similarity` | source-target 엔티티의 의미적 유사도 | entity_type 일치도 + 동일 dimension 태그 공유 비율 |
| `path_unblock_count` | 이 관계 추가 시 새로 생성 가능해지는 IMPACTS 경로 수 | 현재 path_missing 건 중 이 관계가 경로를 완성하는 건수 |
| `trigger_block_flags` | 이 관계 부재가 TR 규칙 미발동의 원인인 횟수 | trigger_failure_log에서 해당 관계가 suggested_fix인 건수 |
| `entity_importance` | source-target 엔티티의 중요도 평균 | (source_importance + target_importance) / 2 |

## 피처 저장 테이블

```
┌─────────────────────────────────────────────┐
│  kg_edge_features                           │
├─────────────────────────────────────────────┤
│  feature_id         TEXT PRIMARY KEY        │
│  candidate_id       TEXT NOT NULL (FK)      │
│    # kg_enrichment_candidates 참조          │
│  snapshot_date      TEXT NOT NULL           │
│                                             │
│  source_entity_id   TEXT NOT NULL           │
│  target_entity_id   TEXT NOT NULL           │
│  suggested_type     TEXT NOT NULL           │
│                                             │
│  co_occurrence      REAL DEFAULT 0          │
│  semantic_sim       REAL DEFAULT 0          │
│  path_unblock       INTEGER DEFAULT 0       │
│  trigger_blocks     INTEGER DEFAULT 0       │
│  entity_importance  REAL DEFAULT 0          │
│                                             │
│  created_at         TEXT                    │
└─────────────────────────────────────────────┘
```

### 피처 수집 주기

```
일간: co_occurrence, path_unblock, trigger_blocks 갱신
주간: semantic_sim, entity_importance 재계산 (importance 주간 재계산과 동기)
```

---

# 4. PHASE 2: SCORING (T+7~)

## 4-1. Edge Value Score 공식

```
edge_value_score =
    w1 × norm(co_occurrence)
  + w2 × norm(path_unblock)
  + w3 × norm(trigger_blocks) × trigger_recovery_weight
  + w4 × norm(entity_importance)
  + w5 × norm(semantic_sim)
  - penalty

가중치:
  w1 = 0.15  (동시 출현 빈도)
  w2 = 0.30  (경로 해제 효과)
  w3 = 0.25  (TRIGGERS 복구 효과)
  w4 = 0.15  (엔티티 중요도)
  w5 = 0.15  (의미적 적합성)

trigger_recovery_weight = 1.5
  → w3 실효 가중치 = 0.25 × 1.5 = 0.375

penalty:
  historical_fp_penalty (아래 참조)

bonus:
  insight_corroboration_bonus (아래 참조)

정규화:
  norm(x) = percentile_rank(x, all_candidates) / 100
```

## 4-2. trigger_recovery_weight 적용

```
목적: TRIGGERS 규칙 복구에 기여하는 관계를 우선시

적용:
  trigger_blocks > 0인 후보에만 1.5배 가중
  trigger_blocks = 0이면 w3 기여 = 0 (가중 무의미)

효과:
  TRIGGERS 미발동 해소에 기여하는 관계가 상위 랭킹
```

## 4-3. Historical FP Penalty

```
과거 유사 관계(동일 relation_type + 동일 entity_type 쌍)의
pruning 이력에서 harmful 비율 참조:

historical_fp_penalty =
  (동일 유형 과거 harmful 건수) / (동일 유형 과거 평가 건수) × 0.3

초기 (이력 없음): penalty = 0
이력 축적 후: 최대 0.3까지 감산

효과:
  과거에 해로웠던 유형의 관계가 반복 추천되는 것을 억제
```

## 4-3b. Insight Corroboration Bonus

```
insight_optimization(cekg_insight_optimization.md)에서
동일 후보에 대해 GRAPH_EXPANSION 권고가 독립적으로 생성된 경우 가산:

insight_corroboration_bonus:
  kg_recommendation_log에 동일 source-target 쌍의
  GRAPH_EXPANSION 권고가 존재하면 → +0.10
  해당 권고가 approved 상태이면 → +0.15

  daily_insight_report에서 connectivity_gap anomaly가
  해당 후보의 target 엔티티를 포함하면 → +0.05

  최대 bonus: 0.20

효과:
  여러 시스템이 독립적으로 동일 관계를 권고 → 신뢰도 상승
```

## 4-4. 피처별 상세 계산

### co_occurrence_score

```
정의: source와 target이 동일 이벤트의 actors/targets에 함께 등장한 횟수

계산:
  co_occurrence = COUNT(
    events WHERE
      source_entity IN (actors ∪ targets)
      AND target_entity IN (actors ∪ targets)
  )

정규화: 전체 후보 중 percentile
```

### semantic_similarity

```
정의: 두 엔티티의 구조적 유사도

계산:
  type_match = (entity_type 동일) ? 0.3 : 0
  tag_overlap = |태그 교집합| / |태그 합집합|  (kg_entity_tags 기준)
  parent_proximity = (공통 parent 존재) ? 0.2 : 0

  semantic_sim = type_match + tag_overlap × 0.5 + parent_proximity

범위: 0 ~ 1.0
```

### path_unblock_count

```
정의: 이 관계 추가 시 path_missing에서 path_exists로 전환되는 IMPACTS 후보 수

계산:
  현재 path_missing 건 목록에서:
    이 관계를 가상 추가했을 때 경로가 완성되는 건수

방법:
  각 path_missing 건의 source→target 경로에서
  이 후보 관계가 끊어진 구간을 연결하는지 확인
```

### trigger_block_flags

```
정의: 이 관계 부재가 TR 규칙 미발동의 직접 원인인 횟수

계산:
  kg_trigger_failure_log에서:
    failure_type = "path_missing"
    AND suggested_fix에 이 관계가 포함된 건수
```

---

# 5. OUTPUT

## 5-1. Ranked Edge Candidates

### 주간 출력 형식

```
=== CEKG-V2 Edge Value Ranking ===
Week: {N}
Phase: {PASSIVE | SCORING}
Candidates evaluated: {N}
Max edges this week: 5

━━━ TOP 5 CANDIDATES ━━━

  #1  edge_value: {score}
      {source_name} → {target_name} ({relation_type})
      ┌──────────────────────────────────────┐
      │ co_occurrence:    {val} (pctl: {p})  │
      │ path_unblock:     {val} (pctl: {p})  │
      │ trigger_blocks:   {val} × 1.5 boost  │
      │ entity_importance:{val} (pctl: {p})  │
      │ semantic_sim:     {val} (pctl: {p})  │
      │ fp_penalty:      -{val}              │
      └──────────────────────────────────────┘
      Expected gain: auto_rate +{X.X}%p
      Triggers affected: {TR-XX, ...} or NONE
      Risk: {LOW | MEDIUM | HIGH}
      Status: PENDING REVIEW

  #2  edge_value: {score}
      ...

  #3 ~ #5  ...

━━━ REMAINING CANDIDATES ━━━

  #6 ~ #{N}: 이번 주 검토 대상 외. 다음 주 재평가.
```

## 5-2. Expected Performance Gain

### 추정 방법

```
개별 관계:
  estimated_auto_gain =
    path_unblock_count / total_weekly_candidates × 100

  estimated_trigger_gain =
    trigger_blocks / total_weekly_trigger_failures × 100

상위 5개 합산:
  total_estimated_gain = Σ(top5 estimated_auto_gain)

추정 등급:
  A: 과거 유사 관계의 실제 성과 기반 (30일 이후)
  B: 시뮬레이션 기반 (7일 이후)
  C: 논리적 추론 (초기)
```

---

# 6. SCORING LOG

```
┌─────────────────────────────────────────────┐
│  kg_edge_value_log                          │
├─────────────────────────────────────────────┤
│  score_id           TEXT PRIMARY KEY        │
│  candidate_id       TEXT NOT NULL (FK)      │
│  week_id            TEXT NOT NULL           │
│  score_date         TEXT NOT NULL           │
│                                             │
│  features           TEXT (JSON)             │
│    {                                        │
│      "co_occurrence": 0.0,                │
│      "semantic_sim": 0.0,                 │
│      "path_unblock": 0,                   │
│      "trigger_blocks": 0,                 │
│      "entity_importance": 0.0             │
│    }                                        │
│                                             │
│  normalized         TEXT (JSON)             │
│    {                                        │
│      "co_occurrence_pctl": 0.0,           │
│      "path_unblock_pctl": 0.0,            │
│      "trigger_blocks_pctl": 0.0,          │
│      "entity_importance_pctl": 0.0,       │
│      "semantic_sim_pctl": 0.0             │
│    }                                        │
│                                             │
│  penalty            REAL DEFAULT 0          │
│  edge_value_score   REAL NOT NULL           │
│  rank               INTEGER                 │
│                                             │
│  estimated_auto_gain REAL                   │
│  estimated_trigger_gain REAL                │
│  estimation_grade    TEXT                   │
│    # A | B | C                              │
│                                             │
│  human_decision      TEXT DEFAULT 'pending' │
│  decided_at          TEXT                   │
│  created_at          TEXT                   │
└─────────────────────────────────────────────┘
```

---

# 7. 주간 5개 제한 운영

### 제한 근거

```
목적: 관계 추가의 영향을 격리 관찰

1주에 5개 이상 추가하면:
  - 개별 관계의 영향 분리 불가
  - pruning 평가 시 원인 특정 곤란
  - fp 급증 시 어떤 관계가 원인인지 불명

5개 제한으로:
  - 각 관계의 7일 성과를 독립 측정 가능
  - 문제 발생 시 원인 관계 특정 용이
  - 점진적 그래프 성장으로 안정성 유지
```

### 5개 선정 프로세스

```
1. edge_value_score 내림차순 정렬
2. 상위 5개를 승인 검토 대상으로 제시
3. 사람이 건별 승인/거부
4. 승인 건만 추가 (5개 미만 가능)
5. 거부 건은 다음 주 재평가 (피처 갱신 후 재점수)
```

### 예외

```
IF 상위 5개 중 trigger_blocks > 0인 후보가 3개 미만:
  → trigger_blocks > 0인 후보를 최소 2개 포함하도록 목록 조정
  → "TRIGGERS 복구 우선" 표시

근거: TRIGGERS는 인과 추론의 핵심. 경로 보강 우선.
```

---

# 8. 연동 관계

```
 graph_gap_detection         edge_value_scoring         graph_pruning
 ┌──────────────┐           ┌──────────────┐           ┌──────────────┐
 │ 후보 탐지     │──────────→│ 점수 부여     │──────────→│ 성과 평가     │
 │ enrichment   │           │ ranking      │           │ pruning      │
 │ candidates   │           │ top 5 제시   │           │ 7일 후       │
 └──────────────┘           └──────┬───────┘           └──────┬───────┘
                                   │                          │
                                   ▼                          │
                             사람 승인                         │
                                   │                          │
                                   ▼                          │
                             관계 추가 ───────────────────────→│
                                                              │
                                                              ▼
                                                     historical_fp_penalty
                                                     → scoring에 피드백
```

---

# 9. 모델 자기 보정

### 점수 정확도 추적

```
주간:
  상위 5개 중 승인 → 추가 → 7일 후 성과 등급 확인

  score_accuracy =
    (EFFECTIVE 건 중 score 상위 50%에 속한 비율)

  IF score_accuracy ≥ 70%:
    "점수 모델 정상"

  IF score_accuracy < 50%:
    "가중치 재조정 검토 권고" (사람 승인)

가중치 조정 방법:
  EFFECTIVE 건의 피처 분포 vs HARMFUL 건의 피처 분포 비교
  → 차이가 큰 피처의 가중치 상향 권고
  → 차이가 작은 피처의 가중치 하향 권고
  → 모든 조정은 사람 승인 필수
```

---

# RISK ANALYSIS

```json
{
  "risk_detected": false,
  "summary": "Edge Value Scoring은 점수 산출과 랭킹만 수행. 자동 추가 금지. 주간 5개 제한",
  "safeguards": [
    "no_auto_apply: 점수 기반 자동 승인 금지",
    "max_edges_per_week: 5 (영향 격리)",
    "human_review_required: 건별 승인",
    "historical_fp_penalty: 과거 해로운 유형 억제",
    "trigger 후보 최소 2개 보장",
    "모델 자기 보정은 권고만 (자동 가중치 변경 금지)",
    "기존 시스템 접근 없음"
  ]
}
```

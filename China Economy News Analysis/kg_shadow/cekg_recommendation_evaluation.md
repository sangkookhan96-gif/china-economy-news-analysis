# CEKG-V2 Recommendation Evaluation System

**STATUS: ENABLED**
**개시일: 2026-03-21**
**모드: 시뮬레이션 기반 사후 평가**

---

# 1. 시스템 개요

```
목적: 권고의 품질을 추적하여 최적화 엔진 자체를 개선한다.

흐름:
  Day N: 권고 생성 (recommendation)
  Day N: 사람 승인/거부 (human_decision)
  Day N+1: 실제 결과 관측 (actual_outcome)
  Day N+1: 시뮬레이션 비교 (simulation vs actual)
  Day N+1: 권고 평가 (label: success/neutral/harmful)
```

---

# 2. EVALUATION PROCESS

## 2-1. 승인된 권고 평가

### 대상

```
kg_recommendation_log에서:
  human_decision = "approved"
  decided_at ≤ 어제
```

### 평가 단계

```
STEP 1: 적용 전 기준선 기록
  Day N (권고 승인일):
    baseline_auto_rate = auto_generation_rate
    baseline_fp_rate = false_positive_rate
    baseline_precision = precision (가용 시)
    baseline_path_exists = path_exists_rate

STEP 2: 적용 후 결과 관측
  Day N+1:
    actual_auto_rate = auto_generation_rate
    actual_fp_rate = false_positive_rate
    actual_precision = precision
    actual_path_exists = path_exists_rate

STEP 3: 델타 계산
    auto_rate_delta = actual - baseline
    fp_rate_delta = actual - baseline
    precision_delta = actual - baseline
    path_exists_delta = actual - baseline

STEP 4: 라벨 판정
```

### 라벨 판정 기준

| 라벨 | 조건 |
|------|------|
| **success** | auto_rate_delta > +2%p AND fp_rate_delta ≤ +1%p |
| **neutral** | \|auto_rate_delta\| ≤ 2%p AND \|fp_rate_delta\| ≤ 1%p |
| **harmful** | fp_rate_delta > +3%p OR auto_rate_delta < -5%p |

### 세분화 기준

| 세부 라벨 | 조건 |
|---------|------|
| `strong_success` | auto_rate_delta > +5%p AND fp_rate_delta ≤ 0%p |
| `mild_success` | +2%p < auto_rate_delta ≤ +5%p AND fp_rate_delta ≤ +1%p |
| `neutral_positive` | 0 < auto_rate_delta ≤ +2%p AND fp_rate_delta ≤ +1%p |
| `neutral_flat` | \|auto_rate_delta\| ≤ 1%p AND \|fp_rate_delta\| ≤ 0.5%p |
| `neutral_negative` | -2%p ≤ auto_rate_delta < 0 AND fp_rate_delta ≤ +1%p |
| `mild_harmful` | fp_rate_delta +1~3%p OR auto_rate_delta -2~-5%p |
| `severe_harmful` | fp_rate_delta > +3%p OR auto_rate_delta < -5%p |

## 2-2. 거부된 권고 평가 (반사실 시뮬레이션)

### 대상

```
kg_recommendation_log에서:
  human_decision = "rejected"
```

### 평가 단계

```
STEP 1: 권고가 적용되었다면의 시뮬레이션

  IF rec_type = GRAPH_EXPANSION:
    시뮬레이션: suggested_relations 추가 시
    → 영향받는 IMPACTS 후보 재계산
    → simulated_auto_rate 산출

  IF rec_type = THRESHOLD_SOFTENING:
    시뮬레이션: 0.65~0.69 구간 건수를 자동 생성으로 전환
    → simulated_auto_rate = baseline + (해당 건수/전체)
    → simulated_fp_rate = baseline + (해당 구간 예상 오판율)

  IF rec_type = ROLLBACK_REVIEW:
    시뮬레이션 불가 (상태 변경이 너무 큼). label = "not_evaluable"

STEP 2: 시뮬레이션 결과 vs 실제 결과 비교
  simulated_outcome vs actual_outcome

STEP 3: 라벨 판정
  IF simulated가 actual보다 우수:
    label = "rejection_was_suboptimal" (거부가 차선이었음)
  IF simulated가 actual과 유사:
    label = "rejection_was_neutral" (거부해도 무방)
  IF simulated가 actual보다 열등:
    label = "rejection_was_correct" (거부가 올바름)
```

---

# 3. METRICS

## 3-1. 핵심 지표

| 지표 | 정의 | 집계 |
|------|------|------|
| `recommendation_success_rate` | (success 라벨 건수) / (평가 완료 건수) | 일간, 7일 이동평균 |
| `avg_impact_score` | 승인된 권고의 auto_rate_delta 평균 | 일간 |
| `false_positive_impact` | 승인된 권고의 fp_rate_delta 평균 | 일간 |

## 3-2. 세부 지표

| 지표 | 정의 |
|------|------|
| `success_rate_by_type` | rec_type별 success 비율 |
| `avg_delta_by_type` | rec_type별 auto_rate_delta 평균 |
| `harmful_rate` | (harmful 라벨 건수) / (평가 완료 건수) |
| `rejection_accuracy` | (rejection_was_correct) / (전체 거부 건 중 평가 가능 건) |
| `time_to_decision` | 권고 생성 → 승인/거부까지 평균 시간 |

## 3-3. 목표

| 지표 | 초기 목표 (1개월) | 안정기 목표 |
|------|---------------|---------|
| recommendation_success_rate | ≥ 40% | ≥ 60% |
| avg_impact_score | > 0%p | > +3%p |
| false_positive_impact | ≤ +1%p | ≤ +0.5%p |
| harmful_rate | ≤ 20% | ≤ 10% |

---

# 4. PATTERN ANALYSIS

## 4-1. Top Success Patterns

### 집계 방법

```
주간 배치:

1. 지난 7일간 label = "success" 또는 "strong_success" 건 수집
2. rec_type별 그룹화
3. trigger_condition별 그룹화
4. 공통 패턴 추출:
   - 어떤 이슈 유형에서 성공률이 높은가
   - 어떤 priority에서 성공률이 높은가
   - 어떤 규모의 변경에서 효과가 큰가
```

### 출력 형식

```
=== Top Success Patterns (Week {N}) ===

1. GRAPH_EXPANSION + path_missing ≥ 5건/일
   Success rate: 75% (6/8)
   Avg auto_rate_delta: +4.2%p
   Pattern: 반복 path_missing 엔티티에 BELONGS_TO 추가 시 효과적

2. THRESHOLD_SOFTENING + fp_rate ≤ 3%
   Success rate: 67% (2/3)
   Avg auto_rate_delta: +3.1%p
   Pattern: 오판율 낮을 때 0.65 적용 안전

3. ...
```

## 4-2. Top Failure Patterns

### 집계 방법

```
주간 배치:

1. 지난 7일간 label = "harmful" 또는 "mild_harmful" 건 수집
2. 실패 원인 분류:
   a) fp_spike: 오판율 급등
   b) auto_drop: 자동화율 하락 (의도치 않은 부작용)
   c) cascade_effect: 한 변경이 다른 규칙에 연쇄 영향
3. 공통 패턴 추출
```

### 출력 형식

```
=== Top Failure Patterns (Week {N}) ===

1. THRESHOLD_SOFTENING + fp_rate > 5%
   Harmful rate: 100% (2/2)
   Avg fp_delta: +4.5%p
   Pattern: 오판율 높을 때 softening은 항상 해로움
   → 안전장치 확인: 기존 규칙(fp≤5% 조건) 정상 작동?

2. GRAPH_EXPANSION + 단일 관계 추가
   Harmful rate: 33% (1/3)
   Cause: 추가된 관계가 오판 IMPACTS 경로를 생성
   → 관계 추가 시 영향 범위 사전 시뮬레이션 강화 필요

3. ...
```

## 4-3. 패턴 → 규칙 피드백

```
주간 패턴 분석 결과에 따라:

IF success pattern이 3주 연속 확인됨:
  → 해당 rec_type의 priority 상향 권고 (사람 승인)

IF failure pattern이 2주 연속 확인됨:
  → 해당 optimization rule의 트리거 조건 강화 권고 (사람 승인)
  → 예: "THRESHOLD_SOFTENING은 fp_rate ≤ 3%일 때만 권고"로 조건 추가

모든 규칙 변경은 사람 승인 필수.
패턴 분석은 권고만 생성.
```

---

# 5. EVALUATION LOG

### 로그 구조

```
┌─────────────────────────────────────────────┐
│  kg_recommendation_eval_log                 │
├─────────────────────────────────────────────┤
│  eval_id            TEXT PRIMARY KEY        │
│  rec_id             TEXT NOT NULL (FK)      │
│  eval_date          TEXT NOT NULL           │
│                                             │
│  baseline_metrics   TEXT (JSON)             │
│    {                                        │
│      "auto_rate": 0.0,                     │
│      "fp_rate": 0.0,                       │
│      "precision": 0.0,                     │
│      "path_exists_rate": 0.0               │
│    }                                        │
│                                             │
│  actual_metrics     TEXT (JSON)             │
│    { same structure }                       │
│                                             │
│  simulated_metrics  TEXT (JSON)             │
│    { same structure, 거부 건만 }             │
│                                             │
│  deltas             TEXT (JSON)             │
│    {                                        │
│      "auto_rate_delta": 0.0,              │
│      "fp_rate_delta": 0.0,                │
│      "precision_delta": 0.0               │
│    }                                        │
│                                             │
│  label              TEXT NOT NULL           │
│    # strong_success | mild_success |        │
│    # neutral_positive | neutral_flat |      │
│    # neutral_negative | mild_harmful |      │
│    # severe_harmful | not_evaluable         │
│                                             │
│  label_category     TEXT NOT NULL           │
│    # success | neutral | harmful            │
│                                             │
│  evaluation_grade   TEXT                    │
│    # A | B | C (추정 정확도 등급)            │
│                                             │
│  notes              TEXT                    │
│  created_at         TEXT                    │
└─────────────────────────────────────────────┘
```

---

# 6. DAILY EVALUATION REPORT 형식

```
=== CEKG-V2 Recommendation Evaluation Report ===
Date: {YYYY-MM-DD}
Evaluation period: {전일} recommendations

━━━ EVALUATED RECOMMENDATIONS ━━━

  Total evaluated:     {N}
  Approved & applied:  {N}
  Rejected (simulated):{N}
  Not evaluable:       {N}

━━━ LABELS ━━━

  ✅ Success:  {N} ({rate}%)    target: ≥40%
    strong:  {N}
    mild:    {N}

  ➖ Neutral:  {N} ({rate}%)
    positive:{N}
    flat:    {N}
    negative:{N}

  ❌ Harmful:  {N} ({rate}%)    target: ≤20%
    mild:    {N}
    severe:  {N}

━━━ IMPACT ━━━

  Avg auto_rate_delta:  {+X.X%p} (approved only)
  Avg fp_rate_delta:    {+X.X%p} (approved only)
  Avg precision_delta:  {+X.X%p} (if available)

━━━ BY REC TYPE ━━━

  GRAPH_EXPANSION:       {success}/{total} ({rate}%)
  THRESHOLD_SOFTENING:   {success}/{total} ({rate}%)
  TRIGGER_ENRICHMENT:    {success}/{total} ({rate}%)
  ROLLBACK_REVIEW:       {evaluated}/{total}
  ARCHIVE_REVIEW:        {success}/{total} ({rate}%)
  KEYWORD_REVIEW:        {success}/{total} ({rate}%)
  WEIGHT_REVIEW:         {success}/{total} ({rate}%)

━━━ REJECTION ANALYSIS ━━━

  Rejected recommendations evaluated: {N}
    rejection_was_correct:    {N}
    rejection_was_neutral:    {N}
    rejection_was_suboptimal: {N}

  Rejection accuracy: {rate}%

━━━ CUMULATIVE (since launch) ━━━

  Total recommendations:   {N}
  Total evaluated:         {N}
  Overall success rate:    {rate}%
  Overall harmful rate:    {rate}%
  Avg impact score:        {+X.X%p}
  Time to decision (avg):  {X.X}h

━━━ WEEKLY PATTERNS (if available) ━━━

  Top success: {pattern_description}
  Top failure: {pattern_description}
```

---

# 7. 자기 개선 루프

```
  권고 생성 → 사람 승인 → 적용 → 결과 관측
      ↑                                │
      │                                ▼
  규칙 조건 강화 ← 패턴 분석 ← 평가 (label)
  (사람 승인)

모든 단계에서 사람 승인 필수.
자동 규칙 변경 = 금지.
```

---

# RISK ANALYSIS

```json
{
  "risk_detected": false,
  "summary": "평가 시스템은 관측과 기록만 수행. 규칙 자동 변경 금지. 기존 시스템 무접촉",
  "safeguards": [
    "평가 결과로 규칙 자동 수정 금지",
    "패턴 분석 → 권고 생성만 (사람 승인 필수)",
    "거부 건 시뮬레이션은 가상 계산만 수행",
    "모든 평가 → kg_recommendation_eval_log에 기록",
    "기존 시스템 접근 없음"
  ]
}
```

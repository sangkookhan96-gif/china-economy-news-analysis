# CEKG-V2 Weekly Learning Loop

**STATUS: PASSIVE COLLECTION (T+0 ~ T+7)**
**학습 활성화: T+7 (2026-03-28)**
**자동 적용: 금지**

---

# 1. 학습 루프 개요

```
PHASE 1: PASSIVE COLLECTION (T+0 ~ T+7)
  수집만. 분석/권고 없음.
  목적: 기준선(baseline) 확립

PHASE 2: LEARNING ACTIVE (T+7~)
  수집 + 분석 + 권고 생성
  모든 권고: 사람 승인 필수
```

---

# 2. PHASE 1: PASSIVE COLLECTION

## 수집 대상

| 데이터 | 소스 | 수집 항목 |
|--------|------|---------|
| 추론 결과 | kg_relations | 자동/리뷰/거부 건수, confidence 분포 |
| 규칙 발동 | 각 규칙 로그 | TR/IM/SU/CT 규칙별 발동 건수 |
| 오판 | kg_false_positive_log | expert verdict 결과 |
| 그래프 변화 | kg_graph_health_snapshot | 엔티티/관계 증감, 밀도 |
| 권고 결과 | kg_recommendation_eval_log | label 분포, 델타 |
| 이벤트 관계 | kg_relation_override_log | SUCCEEDS override 건수 |
| magnitude | kg_magnitude_trace_log | 유형별 자동/수동 비율 |

## 기준선 산출 (T+7 시점)

```
7일간 수집 데이터로 다음 기준선 확립:

baseline_auto_rate:        7일 평균 auto_generation_rate
baseline_fp_rate:          7일 평균 false_positive_rate
baseline_rejection_rate:   7일 평균 rejection_rate
baseline_path_exists:      7일 평균 path_exists_rate
baseline_orphan_ratio:     7일 평균 orphan_ratio
baseline_avg_degree:       7일 평균 avg_degree
baseline_confidence_dist:  7일 confidence 구간별 비율
baseline_rule_activation:  규칙별 7일 발동 횟수

저장: kg_learning_baseline
```

---

# 3. PHASE 2: LEARNING ACTIVE

## 3-1. 주간 학습 사이클

```
매주 월요일 실행:

STEP 1: COLLECT
  지난 7일간 전체 메트릭 집계

STEP 2: COMPARE
  현재 주 vs baseline
  현재 주 vs 직전 주

STEP 3: DETECT
  성능 변화 탐지 (개선/악화/정체)

STEP 4: DIAGNOSE
  변화 원인 분석

STEP 5: RECOMMEND
  개선 권고 생성 (사람 승인 필수)

STEP 6: RECORD
  학습 결과 기록
```

## 3-2. COLLECT — 주간 메트릭

```
┌─────────────────────────────────────────────┐
│  kg_weekly_metrics                          │
├─────────────────────────────────────────────┤
│  week_id            TEXT PRIMARY KEY        │
│  week_start         TEXT NOT NULL           │
│  week_end           TEXT NOT NULL           │
│                                             │
│  inference_metrics  TEXT (JSON)             │
│    {                                        │
│      "total_candidates": N,                │
│      "auto_generated": N,                  │
│      "review_queued": N,                   │
│      "rejected": N,                        │
│      "auto_rate": 0.0,                     │
│      "review_rate": 0.0,                   │
│      "rejection_rate": 0.0                 │
│    }                                        │
│                                             │
│  accuracy_metrics   TEXT (JSON)             │
│    {                                        │
│      "fp_rate": 0.0,                       │
│      "fp_count": N,                        │
│      "reviewed_count": N,                  │
│      "succeeds_detected": N,               │
│      "succeeds_total": N,                  │
│      "override_count": N                   │
│    }                                        │
│                                             │
│  graph_metrics      TEXT (JSON)             │
│    {                                        │
│      "entities_added": N,                  │
│      "relations_added": N,                 │
│      "events_added": N,                    │
│      "archived": N,                        │
│      "reactivated": N,                     │
│      "avg_degree": 0.0,                    │
│      "path_exists_rate": 0.0,              │
│      "orphan_ratio": 0.0                   │
│    }                                        │
│                                             │
│  rule_activation    TEXT (JSON)             │
│    {                                        │
│      "TR-01": N, "TR-02": N, ...,          │
│      "IM-P2I-01": N, ...,                 │
│      "CT-01": N, ...                       │
│    }                                        │
│                                             │
│  recommendation_metrics TEXT (JSON)         │
│    {                                        │
│      "generated": N,                       │
│      "approved": N,                        │
│      "rejected": N,                        │
│      "success_rate": 0.0,                  │
│      "harmful_rate": 0.0                   │
│    }                                        │
│                                             │
│  tier_distribution  TEXT (JSON)             │
│    {"T1": N, "T2": N, "T3": N,             │
│     "T4": N, "T5": N}                      │
│                                             │
│  created_at         TEXT                    │
└─────────────────────────────────────────────┘
```

## 3-3. COMPARE — 변화 탐지

### 비교 대상

| 비교 | 방법 | 유의미 변화 기준 |
|------|------|-------------|
| 현재 주 vs baseline | 절대 차이 | ±5%p |
| 현재 주 vs 직전 주 | 절대 차이 | ±3%p |
| 4주 추세 | 선형 기울기 | 3주 연속 동일 방향 |

### 변화 분류

| 분류 | 조건 |
|------|------|
| `improving` | auto_rate ↑ AND fp_rate ↓(또는 유지) |
| `degrading` | auto_rate ↓ OR fp_rate ↑ |
| `stable` | 양쪽 모두 ±2%p 이내 |
| `mixed` | auto_rate ↑ AND fp_rate ↑ (트레이드오프) |

## 3-4. DETECT — 학습 포인트 식별

### 학습 포인트 유형

| 유형 | 탐지 조건 | 의미 |
|------|---------|------|
| `rule_decay` | 특정 규칙 발동 3주 연속 감소 | 규칙 유효성 감소 또는 그래프 변화 |
| `confidence_drift` | confidence 분포 중심이 baseline 대비 ±0.1 이동 | 그래프 구조 변화에 의한 체계적 변동 |
| `graph_saturation` | entities 증가율 > relations 증가율 (2주 연속) | 관계 생성이 엔티티 성장을 따라가지 못함 |
| `review_bottleneck` | review_queue 처리율 < 70% (2주 연속) | 리뷰 부하 과다 또는 판별 모호 건 증가 |
| `fp_pattern` | 동일 rule_id에서 fp 3건 이상/주 | 특정 규칙의 정밀도 문제 |
| `tier_ossification` | T1 목록 4주 무변동 | 중요도 공식 또는 그래프 편향 |
| `optimization_effective` | 권고 success_rate ≥ 60% (2주 연속) | 최적화 엔진 정상 작동 확인 |

## 3-5. DIAGNOSE — 원인 분석

### 자동 진단 규칙

| 학습 포인트 | 진단 방법 |
|---------|---------|
| `rule_decay` | 해당 규칙의 trigger_failure_log 분석. 주요 실패 사유(path_missing/confidence_low/entity_overlap) 집계 |
| `confidence_drift` | confidence 구성 요소(base/path/time) 중 어느 것이 변동했는지 분해 |
| `graph_saturation` | 신규 엔티티 중 orphan 비율 확인. 관계 생성 실패 사유 집계 |
| `review_bottleneck` | review_queue의 review_type 분포 확인. "succeeds_or_supersedes" 비율 높으면 판별 모호 |
| `fp_pattern` | 해당 rule_id의 fp 건에서 공통 패턴 추출 (특정 entity_type? 특정 hop?) |
| `fp_cluster` | 주간 fp 전건을 다차원 클러스터링 (아래 상세) |
| `tier_ossification` | T2 상위(score ≥ 0.80)에 신규 후보 있는지 확인 |

### fp_cluster 상세 (false_positive_clustering)

주간 false positive 전건을 다음 축으로 분류하여 클러스터 식별:

| 축 | 값 |
|---|---|
| rule_id | IM-P2I-01, IM-P2C-02, ... |
| entity_type (target) | ORG, COM, IND, ... |
| hop_count | 0, 1, 2, 3 |
| confidence_band | 0.7~0.8, 0.8~0.9, 0.9~1.0 |
| magnitude | minor, moderate, major, critical |
| incorrect_reason | wrong_direction, wrong_target, irrelevant, overstated |

클러스터 탐지:

```
1. 동일 (rule_id + entity_type) 조합에서 fp ≥ 3건/주
   → "규칙-유형 클러스터"
   → 해당 규칙에 entity_type 제한 추가 권고

2. 동일 hop_count에서 fp 비율이 해당 hop의 전체 건 대비 ≥ 30%
   → "거리 클러스터"
   → 해당 hop의 path_factor 하향 또는 SG 조건 강화 권고

3. 동일 incorrect_reason이 주간 fp의 ≥ 50%
   → "사유 집중 클러스터"
   → 해당 사유별 대응:
     wrong_direction → direction 판별 규칙 보강
     wrong_target → entity_path 검증 강화
     irrelevant → confidence base 하향
     overstated → strength 매핑 재검토
```

출력:

```
=== FP Clusters (Week {N}) ===

Cluster 1: rule=IM-P2I-01, type=COM, fp=5건
  Pattern: 정책→산업→기업 경유 시 기업 관련성 낮은 케이스
  Suggested: IM-P2I-01에 COM target confidence 0.1 감산

Cluster 2: hop=2, fp_ratio=35%
  Pattern: 2-hop 경로에서 간접 영향 과대 추정
  Suggested: SG-01 조건을 confidence < 0.55로 상향
```

## 3-6. RECOMMEND — 주간 학습 권고

### 권고 유형

| 학습 포인트 | 권고 |
|---------|------|
| `rule_decay` + path_missing | GRAPH_EXPANSION (규칙 경로 보강) |
| `rule_decay` + confidence_low | 해당 규칙 base_score 또는 time_interval 재검토 |
| `confidence_drift` (하향) | 그래프 밀도 보강 또는 threshold 재검토 |
| `confidence_drift` (상향) | 긍정적 변화. threshold 상향 가능성 검토 |
| `graph_saturation` | 엔티티 추출 후 관계 자동 생성 규칙 강화 |
| `review_bottleneck` | PATCH-03 판별 키워드 확장 또는 threshold 조정 |
| `fp_pattern` | 해당 규칙 조건 강화 (entity_type 제한 등) |
| `tier_ossification` | importance 가중치 미세 조정 |
| `optimization_effective` | 현행 유지 확인 (변경 불필요) |

---

# 4. WEEKLY LEARNING REPORT

```
=== CEKG-V2 Weekly Learning Report ===
Week: {N} ({start} ~ {end})
Phase: {PASSIVE_COLLECTION | LEARNING_ACTIVE}

━━━ METRICS SUMMARY ━━━

  Inference:
    auto_rate:     {rate}% (baseline: {bl}%, Δ{delta})
    review_rate:   {rate}%
    rejection_rate:{rate}%

  Accuracy:
    fp_rate:       {rate}% (baseline: {bl}%, Δ{delta})
    succeeds_det:  {rate}%

  Graph:
    entities:      {N} (+{added})
    relations:     {N} (+{added})
    avg_degree:    {X.X} (baseline: {bl})
    orphan_ratio:  {rate}%

  Rules:
    active (≥1 firing): {N}/{total}
    inactive (0 firing): {list}

  Optimization:
    recommendations: {N} generated
    success_rate:     {rate}%

━━━ TREND (vs baseline / vs last week) ━━━

  auto_rate:    {baseline_trend} / {weekly_trend}
  fp_rate:      {baseline_trend} / {weekly_trend}
  avg_degree:   {baseline_trend} / {weekly_trend}

  Overall: {improving | stable | degrading | mixed}

━━━ LEARNING POINTS DETECTED ━━━

  {N}건 탐지

  [{priority}] {learning_point_type}
    Evidence: {탐지 근거}
    Diagnosis: {원인 분석}
    Duration: {지속 기간}

━━━ WEEKLY RECOMMENDATIONS ━━━

  {N}건 생성 (사람 승인 대기)

  [{priority}] LP-{id}: {recommendation}
    Expected impact: {description}
    Risk: {assessment}
    Action: {human_action_required}

━━━ PREVIOUS WEEK RECOMMENDATIONS OUTCOME ━━━

  LP-{id}: {approved|rejected} → {label} ({delta})

━━━ LEARNING LOOP HEALTH ━━━

  Baseline age:        {N} weeks
  Data completeness:   {rate}%
  Recommendation quality:
    cumulative_success: {rate}%
    cumulative_harmful: {rate}%

  IF cumulative_success ≥ 60%:
    "학습 루프 정상 작동"
  IF cumulative_harmful ≥ 20%:
    "학습 루프 품질 점검 필요"
```

---

# 5. LEARNING BASELINE 관리

### 기준선 갱신 규칙

```
초기 기준선: T+7에 확립 (7일 평균)

갱신 조건:
  - 4주마다 자동 갱신 (30일 이동평균으로 교체)
  - 사람이 수동 갱신 요청 시

갱신 방법:
  baseline_new = 최근 30일 평균

갱신 시 보존:
  - 이전 baseline을 kg_learning_baseline_history에 보존
  - 비교 시 "원본 baseline" vs "현재 baseline" 모두 참조 가능
```

### 기준선 테이블

```
┌─────────────────────────────────────────────┐
│  kg_learning_baseline                       │
├─────────────────────────────────────────────┤
│  baseline_id        TEXT PRIMARY KEY        │
│  established_at     TEXT NOT NULL           │
│  data_period        TEXT NOT NULL           │
│    # "2026-03-21 ~ 2026-03-28"             │
│  metrics            TEXT (JSON)             │
│    {                                        │
│      "auto_rate": 0.0,                     │
│      "fp_rate": 0.0,                       │
│      "rejection_rate": 0.0,                │
│      "path_exists_rate": 0.0,              │
│      "orphan_ratio": 0.0,                  │
│      "avg_degree": 0.0,                    │
│      "confidence_median": 0.0,             │
│      "rule_activation": {...}              │
│    }                                        │
│  is_current         INTEGER DEFAULT 1      │
│  superseded_by      TEXT                    │
│  created_at         TEXT                    │
└─────────────────────────────────────────────┘
```

---

# 6. PHASE 전환 조건

### PASSIVE → ACTIVE (T+7)

```
자동 전환 조건 (모두 충족):
  1. 7일간 데이터 수집 완료
  2. 기준선 산출 완료
  3. 최소 데이터량: 이벤트 ≥ 30건, IMPACTS 후보 ≥ 50건

수동 전환:
  데이터 부족 시 PASSIVE 기간 연장 (최대 T+14)
```

### ACTIVE 유지 조건

```
매주 점검:
  IF data_completeness < 80%:
    "데이터 불완전. 학습 결과 신뢰도 저하 가능" 경고
  IF cumulative_harmful ≥ 30%:
    "학습 루프 일시 중단 검토" 권고
```

---

# RISK ANALYSIS

```json
{
  "risk_detected": false,
  "summary": "주간 학습 루프는 수집·분석·권고만 수행. 자동 적용 금지. 기존 시스템 무접촉",
  "safeguards": [
    "no_auto_apply: true",
    "human_review_required: true",
    "PASSIVE 기간에는 분석/권고 생성 안 함",
    "기준선 갱신 시 이전 기준선 보존",
    "cumulative_harmful ≥ 30% 시 루프 중단 권고",
    "기존 시스템 접근 없음"
  ]
}
```

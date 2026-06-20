# CEKG-V2 Graph Pruning Loop

**STATUS: ENABLED**
**자동 삭제: 금지**
**주기: 주간**

---

# 1. 핵심 원칙

```
그래프는 성장만 하면 안 된다.
추가된 관계가 해로우면 제거해야 한다.
단, 사람만이 제거할 수 있다.

금지:
  - 관계 자동 삭제
  - 관계 자동 비활성화
  - 일괄 정리

필수:
  - 건별 사람 승인
  - 제거 전 영향 시뮬레이션
  - 제거 후 효과 추적
```

---

# 2. 입력

| 소스 | 데이터 | 용도 |
|------|--------|------|
| kg_enrichment_candidates | approved 관계 목록 (추가일, suggested_type) | 평가 대상 식별 |
| kg_relations | 추가된 관계의 현재 상태 | 관계 메타데이터 |
| monitoring metrics (추가 후 7일) | auto_rate, fp_rate, trigger_activation | 성과 측정 |
| kg_false_positive_log | 해당 관계 경유 fp 건수 | 해악 판별 |
| kg_recommendation_eval_log | 원본 권고의 label | 교차 검증 |

---

# 3. EVALUATION PROCESS

## 3-1. 평가 대상

```
대상: kg_enrichment_candidates에서
      human_decision = "approved"
      decided_at ≤ 7일 전 (추가 후 7일 경과)

각 관계에 대해 추가 전 7일 vs 추가 후 7일 비교
```

## 3-2. 성과 측정

### 측정 지표

| 지표 | 산식 | 의미 |
|------|------|------|
| delta_auto_rate | (추가후 7일 auto_rate) - (추가전 7일 auto_rate) | 자동화율 변화 |
| delta_fp_rate | (추가후 7일 fp_rate) - (추가전 7일 fp_rate) | 오판율 변화 |
| delta_trigger_activation | (추가후 7일 affected_rules 발동수) - (추가전 7일) | TRIGGERS 활성화 변화 |
| relation_fp_count | 해당 관계를 경유한 fp 건수 (7일간) | 직접 해악 |
| relation_usage_count | 해당 관계를 경유한 IMPACTS 생성 건수 (7일간) | 활용도 |

### 성과 등급

| 등급 | 조건 |
|------|------|
| **EFFECTIVE** | delta_auto_rate > +2%p AND delta_fp_rate ≤ +1%p |
| **NEUTRAL** | \|delta_auto_rate\| ≤ 2%p AND \|delta_fp_rate\| ≤ 1%p |
| **INEFFECTIVE** | delta_auto_rate ≤ 0%p AND delta_trigger_activation = 0 |
| **HARMFUL** | delta_fp_rate > +2%p OR relation_fp_count ≥ 3 |

---

# 4. PRUNING RULES

## Rule PR-01: 오판 증가 + 효과 없음

```
IF delta_fp_rate > +1%p
AND delta_auto_rate ≤ +1%p
AND relation_fp_count ≥ 2:

  action: MARK_FOR_REMOVAL
  priority: HIGH
  reason: "오판 경로 생성. 자동화 기여 없음"
```

## Rule PR-02: TRIGGERS 무영향

```
IF affected_rules에 TR 규칙 포함
AND delta_trigger_activation = 0 (7일간 발동 변화 없음)
AND relation_usage_count ≤ 1:

  action: DOWNGRADE_PRIORITY
  priority: MEDIUM
  reason: "TRIGGERS 활성화 목적으로 추가했으나 효과 없음"
  next_action: "14일 후 재평가. 여전히 무효과 시 MARK_FOR_REMOVAL"
```

## Rule PR-03: 지속적 부정 영향

```
IF 성과 등급 = HARMFUL
AND 7일 연속 유지 (추가 후 7~14일에도 fp 지속):

  action: MARK_FOR_REMOVAL
  priority: CRITICAL
  reason: "7일 이상 부정 영향 지속"
```

## Rule PR-04: 미사용 관계

```
IF relation_usage_count = 0 (7일간 한 번도 경유되지 않음)
AND 해당 관계의 source/target 중 하나가 T4/T5:

  action: DOWNGRADE_PRIORITY
  priority: LOW
  reason: "미사용 + 저중요도 엔티티"
  next_action: "30일 후 재평가. 여전히 미사용 시 MARK_FOR_REMOVAL"
```

## Rule PR-05: 유효 (제거 안 함)

```
IF 성과 등급 = EFFECTIVE:

  action: RETAIN
  note: "정상 기여. 제거 대상 아님"
```

---

# 5. PRUNING CANDIDATE LOG

```
┌─────────────────────────────────────────────┐
│  kg_pruning_candidates                      │
├─────────────────────────────────────────────┤
│  pruning_id         TEXT PRIMARY KEY        │
│  kg_relation_id     TEXT NOT NULL           │
│  enrichment_id      TEXT NOT NULL (FK)      │
│    # kg_enrichment_candidates 참조          │
│  evaluation_date    TEXT NOT NULL           │
│                                             │
│  performance_grade  TEXT NOT NULL           │
│    # EFFECTIVE | NEUTRAL |                  │
│    # INEFFECTIVE | HARMFUL                  │
│                                             │
│  metrics            TEXT (JSON)             │
│    {                                        │
│      "delta_auto_rate": 0.0,              │
│      "delta_fp_rate": 0.0,                │
│      "delta_trigger_activation": 0,        │
│      "relation_fp_count": 0,              │
│      "relation_usage_count": 0             │
│    }                                        │
│                                             │
│  pruning_rule       TEXT                    │
│    # PR-01 | PR-02 | PR-03 | PR-04 | PR-05│
│  action             TEXT NOT NULL           │
│    # MARK_FOR_REMOVAL | DOWNGRADE |        │
│    # RETAIN | PENDING_REEVAL                │
│  priority           TEXT                    │
│  reason             TEXT                    │
│                                             │
│  human_decision     TEXT DEFAULT 'pending'  │
│    # pending | remove_approved |            │
│    # keep | deferred                        │
│  decision_note      TEXT                    │
│  decided_at         TEXT                    │
│  created_at         TEXT                    │
└─────────────────────────────────────────────┘
```

---

# 6. REMOVAL PROCESS

### 승인 후 제거 절차

```
사람이 human_decision = "remove_approved" 설정 시:

STEP 1: 영향 사전 확인
  해당 관계를 경유하는 기존 IMPACTS 관계 목록 조회
  → 영향받는 IMPACTS 건수 표시

STEP 2: 관계 비활성화
  kg_relations에서:
    valid_until = 현재 시점 (종료)
    note에 "pruned: {pruning_id}" 추가

  삭제하지 않음. valid_until 설정으로 비활성화.

STEP 3: 영향받는 IMPACTS 처리
  경유 경로가 사라진 IMPACTS:
    entity_path 재계산
    대체 경로 존재 → 유지 (confidence 재계산)
    대체 경로 없음 → kg_review_queue로 이동 (재심사)

STEP 4: 효과 추적 (제거 후 7일)
  delta_auto_rate, delta_fp_rate 변화 기록
```

### 복원 규칙

```
제거 후 7일 내 부정 영향 발견 시:
  valid_until = NULL로 복원 (재활성화)
  pruning_candidates에 "restored" 기록
```

---

# 7. WEEKLY PRUNING REPORT

```
=== CEKG-V2 Graph Pruning Report ===
Week: {N}

━━━ EVALUATION SUMMARY ━━━

  Relations evaluated: {N} (추가 후 7일 경과)

  EFFECTIVE:   {N} → RETAIN
  NEUTRAL:     {N} → RETAIN (관찰 계속)
  INEFFECTIVE: {N} → DOWNGRADE / REEVAL
  HARMFUL:     {N} → MARK_FOR_REMOVAL

━━━ PRUNING CANDIDATES ━━━

  [{CRITICAL}] PR-03: {relation_description}
    Added: {date}, Impact: fp_rate +{X}%p (7일 지속)
    Action: REMOVE (사람 승인 대기)

  [{HIGH}] PR-01: {relation_description}
    Added: {date}, Impact: fp +{N}건, auto_rate 변화 없음
    Action: REMOVE (사람 승인 대기)

  [{MEDIUM}] PR-02: {relation_description}
    Added: {date}, Triggers affected: 0건 변화
    Action: 14일 후 재평가

━━━ PREVIOUS REMOVALS OUTCOME ━━━

  Removed last week: {N}건
    fp_rate change: {delta}
    auto_rate change: {delta}
    restored: {N}건

━━━ GRAPH HEALTH AFTER PRUNING ━━━

  Relations: {before} → {after} ({delta})
  Path exists rate: {before}% → {after}%
  Avg degree: {before} → {after}
```

---

# 8. 그래프 생애 주기 (완성)

```
  탐지               추가              평가              정리
  ┌──────┐      ┌──────┐      ┌──────────┐      ┌──────┐
  │ GAP  │─────→│ ADD  │─────→│ EVALUATE │─────→│PRUNE │
  │DETECT│      │(승인)│      │ (7일 후) │      │(승인)│
  └──────┘      └──────┘      └──────────┘      └──────┘
     ↑                              │                │
     │                              │                │
     └──────────────────────────────┘                │
         gap 재발 시 재탐지                           │
                                                     │
     ┌───────────────────────────────────────────────┘
     ▼
  효과 추적 (제거 후 7일)
     │
     ├── 정상 → 제거 확정
     └── 부정 → 복원
```

---

# RISK ANALYSIS

```json
{
  "risk_detected": false,
  "summary": "Graph Pruning은 평가와 후보 제시만 수행. 자동 삭제 금지. 비활성화 방식으로 데이터 보존",
  "safeguards": [
    "no_auto_delete: 관계 자동 삭제 금지",
    "human_review_required: 건별 승인 필수",
    "삭제 대신 valid_until 설정 (비활성화)",
    "제거 후 7일 내 복원 가능",
    "영향받는 IMPACTS 재심사 절차 포함",
    "기존 시스템 접근 없음"
  ]
}
```

# CEKG-V2 Insight-Driven Optimization

**MODE: RECOMMENDATION ONLY (자동 실행 금지)**
**개시일: 2026-03-21**

---

# 1. 핵심 원칙

```
┌──────────────────────────────────────────┐
│  이 시스템은 "권고"만 생성한다.           │
│  모든 변경은 반드시 사람이 승인한다.       │
│  자동 실행 = 금지                        │
│  자동 롤백 = 금지                        │
│  자동 임계값 변경 = 금지                  │
└──────────────────────────────────────────┘
```

---

# 2. OPTIMIZATION RULES

## 2-1. Path Missing → Graph Expansion 권고

### 트리거

```
IF issue_type = "path_missing"
AND daily_count(path_missing) ≥ 3
AND same_target_entity appears ≥ 2 times:
```

### 권고 생성

```
recommendation:
  type: GRAPH_EXPANSION
  priority: HIGH
  target_entity: {path_missing에서 반복 등장한 엔티티}
  suggested_relations:
    - trigger_failure_log의 suggested_fix 집계
    - 빈도순 상위 3개
  expected_impact:
    path_exists_rate: +{estimated}%
    auto_generation_rate: +{estimated}%
  human_action_required:
    "다음 관계 추가를 검토하십시오:
     1. {entity_A} → {entity_B} ({relation_type})
     2. {entity_C} → {entity_D} ({relation_type})
     3. ..."
```

### 영향 추정 방법

```
estimated_path_rate_increase =
  (반복 path_missing 건수) / (전체 IMPACTS 후보 건수) × 100

estimated_auto_rate_increase =
  estimated_path_rate_increase × 0.6
  (경로 존재해도 confidence 미달 가능성 40% 감안)
```

## 2-2. Mid-Confidence Cluster → Threshold Softening 권고

### 트리거

```
IF issue_type = "mid_confidence_cluster"
AND confidence 0.55~0.69 구간에 당일 후보의 40% 이상 집중
AND false_positive_rate ≤ 0.05 (오판율 낮음):
```

### 권고 생성

```
recommendation:
  type: THRESHOLD_SOFTENING
  priority: MEDIUM
  current_threshold: 0.70
  suggested_threshold: 0.65
  affected_relations: {0.65~0.69 구간 건수}
  expected_impact:
    auto_generation_rate: +{건수/전체}%
    estimated_fp_increase: +{0.65~0.69 구간 예상 오판율}%
  risk_assessment:
    IF estimated_fp_increase > 3%:
      "오판율 증가 우려. 0.68로 보수적 조정 권고"
    ELSE:
      "오판율 증가 미미. 0.65 적용 가능"
  human_action_required:
    "confidence 자동 생성 임계값을 {current} → {suggested}로
     조정할지 검토하십시오.
     예상 추가 자동 생성: {N}건/일
     예상 오판율 변화: {current_fp}% → {estimated_fp}%"
```

### 안전 장치

```
절대 하한: suggested_threshold ≥ 0.60
  0.60 미만 권고는 생성하지 않음.

오판율 조건: false_positive_rate ≤ 0.05일 때만 권고
  오판율이 높은 상태에서 임계값 하향은 금지.
```

## 2-3. Trigger Not Fired → Trigger Graph Enrichment 권고

### 트리거

```
IF issue_type = "trigger_not_fired"
AND specific TR rule 7일간 발동 0회
AND 해당 rule의 event_type 이벤트가 7일간 ≥ 3건 존재:
```

### 권고 생성

```
recommendation:
  type: TRIGGER_GRAPH_ENRICHMENT
  priority: MEDIUM
  inactive_rule: {TR-XX}
  rule_description: {규칙 설명}
  failure_analysis:
    total_candidates: {7일간 패턴 매칭 건수}
    confidence_too_low: {건수}
    path_missing: {건수}
    entity_overlap_zero: {건수}
  primary_cause: {가장 빈번한 실패 사유}
  suggested_action:
    IF primary_cause = "path_missing":
      "다음 관계 추가 검토: {suggested_relations}"
    IF primary_cause = "entity_overlap_zero":
      "이벤트 actors/targets 추출 품질 점검 필요"
    IF primary_cause = "confidence_too_low":
      "TR-XX base score 또는 time_interval 재검토"
  expected_impact:
    trigger_activation_rate: +{estimated}%
  human_action_required:
    "{inactive_rule}이 7일간 미발동.
     주요 원인: {primary_cause}
     권고 조치: {suggested_action}"
```

## 2-4. Auto Rate Drop → Rollback 검토 권고

### 트리거

```
IF anomaly = "auto_rate_drop"
AND auto_generation_rate < 0.20 (ALT-C02 수준)
AND 2일 연속:
```

### 권고 생성

```
recommendation:
  type: ROLLBACK_REVIEW
  priority: CRITICAL
  current_rate: {auto_generation_rate}
  baseline_rate: {7일 평균}
  drop_magnitude: {baseline - current}
  duration: {연속 일수}
  possible_causes:
    - graph_density_drop: {현재 edges/nodes vs 7일전}
    - confidence_distribution_shift: {구간별 변화}
    - new_entity_type_influx: {신규 유형 비율}
    - rule_conflict: {최근 패치 이력}
  rollback_snapshot: cekg_v2_release_2026-03-21
  human_action_required:
    "자동화율 {rate}% (2일 연속 20% 미만).
     추론 엔진 실질 무력화 상태.

     선택지:
     A) 원인 분석 후 규칙 수정 (PATCH v2.1)
     B) 스냅샷 롤백: cekg_v2_release_2026-03-21
     C) feature flag 일부 비활성화 후 관찰

     ⚠️ 자동 롤백은 수행하지 않습니다.
        반드시 위 선택지 중 하나를 지정하십시오."
```

### 안전 장치

```
자동 롤백 금지.
권고만 생성하고 사람의 명시적 승인을 대기.
24시간 미응답 시 재알림 (escalation).
```

---

# 3. 추가 OPTIMIZATION RULES

## 2-5. Archive Reactivation Spike → 아카이브 기준 재검토 권고

### 트리거

```
IF monthly_reactivation_count ≥ 10
AND reactivation_count / archived_count ≥ 0.15:
```

### 권고

```
recommendation:
  type: ARCHIVE_CRITERIA_REVIEW
  priority: LOW
  reactivation_count: {N}
  reactivation_rate: {rate}%
  most_reactivated_types: {entity_type 분포}
  suggested_action:
    "아카이브 조건이 과도할 수 있습니다.
     last_seen 기준을 180일 → 270일로 완화 검토.
     또는 특정 entity_type은 아카이브 제외 검토."
```

## 2-6. Override Rate Anomaly → 키워드 사전 검토 권고

### 트리거

```
IF override_trigger_rate > 0.20
AND 3일 연속:
```

### 권고

```
recommendation:
  type: KEYWORD_DICT_REVIEW
  priority: MEDIUM
  override_rate: {rate}%
  top_triggered_keywords: {빈도순 상위 5개}
  false_override_count: {expert가 "동일 사건"으로 정정한 건수}
  suggested_action:
    IF false_override_count > 3:
      "다음 키워드 제거 검토: {오판 유발 키워드}"
    ELSE:
      "override 빈도 높으나 정확도 양호. 현행 유지."
```

## 2-7. Importance Tier Stagnation → 가중치 검토 권고

### 트리거

```
IF T1 엔티티 목록이 30일간 변동 없음
AND 신규 이벤트 ≥ 100건 발생:
```

### 권고

```
recommendation:
  type: IMPORTANCE_WEIGHT_REVIEW
  priority: LOW
  stagnant_t1: {엔티티 목록}
  potential_t1_candidates: {T2에서 score ≥ 0.80인 엔티티}
  suggested_action:
    "T1 고정 30일. 가중치 분포 또는 percentile 구간 재검토."
```

---

# 4. RECOMMENDATION LOG

### 로그 구조

```
┌─────────────────────────────────────────────┐
│  kg_recommendation_log                      │
├─────────────────────────────────────────────┤
│  rec_id             TEXT PRIMARY KEY        │
│  rec_date           TEXT NOT NULL           │
│  rec_type           TEXT NOT NULL           │
│    # GRAPH_EXPANSION |                      │
│    # THRESHOLD_SOFTENING |                  │
│    # TRIGGER_GRAPH_ENRICHMENT |             │
│    # ROLLBACK_REVIEW |                      │
│    # ARCHIVE_CRITERIA_REVIEW |              │
│    # KEYWORD_DICT_REVIEW |                  │
│    # IMPORTANCE_WEIGHT_REVIEW               │
│  priority           TEXT NOT NULL           │
│    # CRITICAL | HIGH | MEDIUM | LOW         │
│  trigger_condition  TEXT                    │
│  recommendation     TEXT (JSON)             │
│  expected_impact    TEXT (JSON)             │
│  human_decision     TEXT                    │
│    # approved | rejected | deferred |       │
│    # modified | pending                     │
│  decision_note      TEXT                    │
│  decided_at         TEXT                    │
│  created_at         TEXT                    │
└─────────────────────────────────────────────┘
```

### 결정 추적

```
권고 생성 → human_decision = "pending"
사람 승인 → human_decision = "approved", decided_at 기록
사람 거부 → human_decision = "rejected", decision_note에 사유
사람 수정 → human_decision = "modified", decision_note에 변경 내용
보류      → human_decision = "deferred", 다음 리뷰 시 재제시
```

---

# 5. DAILY ACTION RECOMMENDATIONS 형식

```
=== CEKG-V2 Daily Optimization Recommendations ===
Date: {YYYY-MM-DD}

━━━ RECOMMENDATIONS ({N}건) ━━━

[{priority}] REC-{id}: {rec_type}
  Trigger: {trigger_condition 요약}
  Recommendation: {action 요약}
  Expected Impact:
    - {metric_1}: {current} → {estimated} ({change})
    - {metric_2}: {current} → {estimated} ({change})
  Risk: {risk_assessment}
  Action Required: {human_action_required}
  Status: PENDING REVIEW

---

[{priority}] REC-{id}: {rec_type}
  ...

━━━ PREVIOUS RECOMMENDATIONS STATUS ━━━

  REC-{id} ({date}): {rec_type} — {human_decision}
    {decision_note if any}

━━━ CUMULATIVE IMPACT (approved only) ━━━

  Auto Generation Rate: {baseline} → {current} ({approved 권고 반영 후})
  Path Exists Rate: {baseline} → {current}
  False Positive Rate: {baseline} → {current}
```

---

# 6. EXPECTED IMPACT ESTIMATION 방법론

### 추정 정확도 등급

| 등급 | 근거 | 신뢰도 |
|------|------|--------|
| A | 동일 패턴의 과거 권고 승인 후 실제 변화 데이터 기반 | 높음 |
| B | 테스트 시뮬레이션 결과 기반 (regression test) | 중간 |
| C | 규칙 구조에서의 논리적 추론 | 낮음 |

### 초기 운영 시

```
과거 데이터 없으므로 모든 추정은 등급 C (논리적 추론).
7일 이후: shadow comparison 데이터로 등급 B 가능.
30일 이후: 실제 승인/적용 결과로 등급 A 가능.

추정값에는 반드시 등급을 표기:
  "auto_generation_rate: +8% [C]"
  "path_exists_rate: +12% [B]"
```

---

# RISK ANALYSIS

```json
{
  "risk_detected": false,
  "summary": "Insight-Driven Optimization은 권고 전용. 자동 실행 금지. 모든 변경은 사람 승인 필수",
  "safeguards": [
    "require_human_review: true",
    "자동 롤백 금지",
    "자동 임계값 변경 금지",
    "threshold 하한 0.60 고정",
    "오판율 > 5%일 때 softening 권고 금지",
    "모든 권고 → kg_recommendation_log에 기록",
    "미응답 24시간 시 재알림 (자동 실행 아님)"
  ]
}
```

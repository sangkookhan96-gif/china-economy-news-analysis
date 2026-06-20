# CEKG V2 Monitoring System

**STATUS: ACTIVE**
**대상: CEKG Inference Engine V2**
**개시일: 2026-03-21**

---

# 1. METRICS DEFINITION

## 1-1. INFERENCE QUALITY

| 지표 | 정의 | 산식 | 목표 | 경고 |
|------|------|------|------|------|
| `auto_generation_rate` | 자동 생성된 IMPACTS 비율 | (confidence ≥ 0.7 생성 건수) / (전체 IMPACTS 후보 건수) | ≥ 60% | < 50% |
| `review_queue_rate` | 리뷰 대기로 분류된 비율 | (0.4 ≤ confidence < 0.7 건수) / (전체 후보 건수) | ≤ 30% | > 40% |
| `rejection_rate` | 거부된 비율 | (confidence < 0.4 + path=0 건수) / (전체 후보 건수) | ≤ 10% | > 20% |
| `safety_guard_block_rate` | SG-01, SG-02에 의해 차단된 비율 | (SG 차단 건수) / (전체 후보 건수) | 참고값 | > 15% |

### 기준선 (테스트 결과 기반)

```
EVT-T01~T05 시뮬레이션 결과:
  전체 후보: 13건
  자동 생성: 5건 → auto_generation_rate = 38.5%
  리뷰 대기: 4건 → review_queue_rate = 30.8%
  미달 거부:  1건 → rejection_rate (미달) = 7.7%
  경로 거부:  3건 → rejection_rate (경로) = 23.1%
  총 거부:    4건 → rejection_rate (합계) = 30.8%

⚠️ 기준선에서 auto_generation_rate(38.5%)가 목표(60%) 미달.
   원인: 테스트 그래프가 6개 관계만 보유 (sparse graph).
   실제 운영 시 관계 밀도 증가에 따라 entity_path_factor 상승 → 개선 예상.

⚠️ rejection_rate(30.8%)가 목표(10%) 초과.
   원인: 경로 없음 거부 3건. sparse graph 특성.
   그래프 밀도 증가에 따라 자연 감소 예상.
```

### 목표 조정 (운영 초기)

| 지표 | 이론 목표 | 운영 초기 목표 (1개월) | 안정기 목표 (3개월~) |
|------|---------|------------------|----------------|
| auto_generation_rate | ≥ 60% | ≥ 40% | ≥ 60% |
| review_queue_rate | ≤ 30% | ≤ 35% | ≤ 30% |
| rejection_rate | ≤ 10% | ≤ 35% | ≤ 15% |

## 1-2. RELATION ACCURACY

| 지표 | 정의 | 산식 | 목표 |
|------|------|------|------|
| `same_event_merge_rate` | 동일 사건 병합 비율 | (score≥0.80에서 SAME_EVENT 처리 건수) / (score≥0.80 전체 건수) | 참고값 (추이 관찰) |
| `succeeds_detection_rate` | SUCCEEDS 정상 탐지율 | (키워드 override로 SUCCEEDS 판정 건수) / (실제 후속 조치 건수) | ≥ 70% |
| `override_trigger_rate` | score≥0.80에서 SUCCEEDS override 발동률 | (V2 override 건수) / (score≥0.80 전체 건수) | 참고값 (추이 관찰) |
| `supersedes_accuracy` | supersedes 정확도 | (expert 승인된 supersedes) / (전체 supersedes 판정) | ≥ 80% |

### 측정 방법

```
succeeds_detection_rate 측정:
  분자: EVENT-RELATION-V2에서 키워드 기반 SUCCEEDS 판정 건수
  분모: expert review에서 SUCCEEDS로 최종 확인된 건수

  분모가 사후 확정되므로, 주 1회 배치로 지연 측정.

override_trigger_rate 추적:
  kg_relation_override_log에서 집계.
  초기 예상: 5~15% (대부분 score≥0.80은 실제 동일 사건)
  20% 초과 시: 키워드 사전 과다 또는 매칭 score 기준 재검토 필요
```

## 1-3. MACRO PRECISION

| 지표 | 정의 | 산식 | 목표 |
|------|------|------|------|
| `deviation_usage_rate` | expected_value가 존재하여 자동 산정된 비율 | (deviation 계산 건수) / (MACRO.DATA_RELEASE 전체 건수) | ≥ 70% |
| `macro_auto_vs_manual_ratio` | MACRO 자동 대 수동 비율 | (자동 산정 건수) / (수동 설정 건수) | ≥ 3:1 |
| `deviation_distribution` | deviation 값 분포 | minor / moderate / major 비율 | 참고값 |

### magnitude_trace_log 기반 집계

```
일별:
  SELECT estimation_method, result_magnitude, COUNT(*)
  FROM kg_magnitude_trace_log
  WHERE event_type LIKE 'MACRO%'
  GROUP BY estimation_method, result_magnitude

deviation_usage_rate:
  estimation_method = 'auto_macro_deviation' 건수 /
  event_type = 'MACRO.DATA_RELEASE' 전체 건수
```

## 1-4. GRAPH HEALTH

| 지표 | 정의 | 산식 | 목표 | 경고 |
|------|------|------|------|------|
| `avg_path_length` | IMPACTS 생성 시 평균 경로 길이 | Σ(hop) / (생성된 IMPACTS 수) | ≤ 1.5 | > 2.0 |
| `no_path_rejection_rate` | 경로 없음 거부 비율 | (path=0 거부 건수) / (전체 IMPACTS 후보 건수) | ≤ 15% | > 25% |
| `orphan_entity_count` | 관계 없는 엔티티 수 | degree_centrality = 0인 active 엔티티 수 | 추이 관찰 | 전체의 > 40% |
| `archive_rate` | 월간 아카이브 비율 | (아카이브 건수) / (전체 엔티티 수) | 참고값 | > 10% |
| `reactivation_count` | 월간 부활 건수 | kg_archive → kg_entities 복원 건수 | 참고값 | — |

---

# 2. ALERT RULES

## 2-1. 자동 경고

| Alert ID | 조건 | 메시지 | 심각도 | 대응 |
|----------|------|--------|--------|------|
| ALT-01 | review_queue_rate > 40% | "confidence threshold too strict" | WARNING | confidence 임계값(0.7) 하향 검토 또는 그래프 밀도 보강 |
| ALT-02 | no_path_rejection_rate > 25% | "graph connectivity issue" | WARNING | 엔티티 간 관계 누락 점검. BELONGS_TO, MEASURES, REGULATES 보강 |
| ALT-03 | succeeds_detection_rate < 70% | "keyword override failure" | WARNING | SUCCEEDS 키워드 사전 확장 또는 STEP 2~3 판별 로직 검토 |
| ALT-04 | auto_generation_rate < 40% (초기) / < 50% (안정기) | "auto generation below target" | WARNING | entity_path_factor 기준 완화 또는 그래프 밀도 보강 |
| ALT-05 | orphan_entity_count > 전체의 40% | "excessive orphan entities" | WARNING | 엔티티 추출 품질 점검 + 아카이브 조건 재검토 |
| ALT-06 | override_trigger_rate > 20% | "excessive SUCCEEDS override" | CAUTION | 키워드 사전 과다 여부 점검. 오분류 샘플 검토 |
| ALT-07 | safety_guard_block_rate > 15% | "safety guard over-blocking" | CAUTION | SG-01/SG-02 조건 재검토 |

## 2-2. 긴급 경고

| Alert ID | 조건 | 메시지 | 심각도 | 대응 |
|----------|------|--------|--------|------|
| ALT-C01 | rejection_rate > 50% | "majority of inferences rejected" | CRITICAL | 즉시 원인 분석. 그래프 단절 또는 confidence 산정 오류 가능 |
| ALT-C02 | auto_generation_rate < 20% | "inference engine effectively disabled" | CRITICAL | feature flag 점검. 롤백 검토 |
| ALT-C03 | 기존 시스템(news.db) 접근 탐지 | "isolation breach detected" | CRITICAL | 즉시 중단. 격리 원칙 위반 조사 |

---

# 3. REPORT STRUCTURE

## 3-1. DAILY SUMMARY

```
=== CEKG V2 Daily Report ===
Date: {YYYY-MM-DD}

[INFERENCE QUALITY]
  Total candidates:     {N}
  Auto generated:       {N} ({rate}%)    target: ≥40%/60%
  Review queue:         {N} ({rate}%)    target: ≤35%/30%
  Rejected:             {N} ({rate}%)    target: ≤35%/15%
  Safety guard blocked: {N} ({rate}%)

[RELATION ACCURACY]
  Score≥0.80 events:    {N}
    → SAME_EVENT:       {N}
    → SUCCEEDS override:{N}
  Score 0.60~0.79:      {N}
    → supersedes:       {N}
    → SUCCEEDS:         {N}
    → review_queue:     {N}

[MACRO]
  MACRO.DATA_RELEASE:   {N}
    → auto (deviation): {N}
    → manual/default:   {N}
  Deviation distribution: minor:{N} moderate:{N} major:{N}

[GRAPH HEALTH]
  Avg path length:      {X.X}
  No-path rejections:   {N} ({rate}%)
  Active entities:      {N}
  Orphan entities:      {N} ({rate}%)

[ALERTS]
  {alert_id}: {message} (if any)

[SAFETY GUARD LOG]
  SG-01 blocks: {N}
  SG-02 blocks: {N}
```

## 3-2. WEEKLY DRIFT ANALYSIS

```
=== CEKG V2 Weekly Drift Report ===
Week: {YYYY-Wnn}

[TREND]
  auto_generation_rate:  {prev_week}% → {this_week}% ({+/-}%)
  review_queue_rate:     {prev_week}% → {this_week}% ({+/-}%)
  rejection_rate:        {prev_week}% → {this_week}% ({+/-}%)
  no_path_rejection:     {prev_week}% → {this_week}% ({+/-}%)

[RELATION TREND]
  override_trigger_rate: {prev_week}% → {this_week}% ({+/-}%)
  succeeds_detection:    measured this week: {rate}% (target ≥70%)

[GRAPH GROWTH]
  New entities:          {N} (this week)
  New events:            {N}
  New relations:         {N}
  Archived entities:     {N}
  Reactivated entities:  {N}

[IMPORTANCE SHIFT]
  Tier changes:
    T1: {N} entities (changes: {N})
    T2: {N} entities (changes: {N})
    T3: {N} entities (changes: {N})
    T4: {N} entities (changes: {N})
    T5: {N} entities (changes: {N})
  2-tier jumps flagged: {N}

[DRIFT ASSESSMENT]
  Graph density:         {edges/nodes ratio} ({trend})
  Confidence avg:        {X.XX} ({trend})
  Path length avg:       {X.X} ({trend})

  Drift status: STABLE / DRIFTING / ALERT

  IF DRIFTING:
    Direction: {more_conservative / more_permissive}
    Recommended action: {description}

[ALERTS THIS WEEK]
  {alert_id}: {message} — {count} times

[MANUAL REVIEW BACKLOG]
  Pending reviews:       {N}
  Oldest pending:        {days} days
  IF pending > 50: ALERT "review backlog growing"
```

---

# 4. MONITORING INFRASTRUCTURE

## 4-1. 데이터 소스

| 소스 | 제공 지표 |
|------|---------|
| kg_relations | auto_generation_rate, review_queue_rate, rejection_rate, avg_path_length |
| kg_conflict_log | no_path_rejection_rate, rejection 상세 |
| kg_relation_override_log | override_trigger_rate, succeeds_detection_rate |
| kg_magnitude_trace_log | deviation_usage_rate, macro_auto_vs_manual_ratio |
| kg_review_queue | review backlog, pending count |
| kg_entities | orphan_entity_count, tier distribution |
| kg_archive | archive_rate, reactivation_count |

## 4-2. 집계 주기

| 주기 | 작업 |
|------|------|
| 실시간 | ALERT 조건 검사 (ALT-C01~C03) |
| 일 1회 | Daily Summary 생성, 일반 ALERT 검사 (ALT-01~07) |
| 주 1회 | Weekly Drift Analysis, importance 재계산, succeeds_detection 지연 측정 |
| 월 1회 | 아카이브 배치, 전체 지표 리뷰, 목표 재조정 검토 |

## 4-3. 목표 재조정 기준

```
매월 리뷰 시:

IF 3주 연속 목표 달성:
  목표 상향 검토 (예: auto_generation ≥60% → ≥65%)

IF 3주 연속 목표 미달:
  원인 분석 후 다음 중 택 1:
    a) 목표 하향 (환경 제약 인정)
    b) 규칙 수정 (패치 적용)
    c) 그래프 보강 (관계 밀도 개선)

운영 초기 목표 → 안정기 목표 전환 시점:
  그래프 엔티티 수 ≥ 200 AND 관계 수 ≥ 500 달성 시
```

---

# RISK ANALYSIS

```json
{
  "risk_detected": false,
  "summary": "모니터링 시스템은 설계 문서로만 정의. 실제 집계 로직 미구현. 기존 시스템 무접촉",
  "note": "모든 지표 산식은 kg_ 접두어 테이블만 참조. 기존 news.db 참조 없음"
}
```

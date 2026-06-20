# CEKG-V2 Diagnostic Logging & Auto Insight

**STATUS: ENABLED**
**개시일: 2026-03-21**

---

# 1. DIAGNOSTIC LOGGING

## 1-1. Decision Diff Analysis

### 목적

동일 뉴스에 대해 기존 시스템(legacy)과 CEKG-V2의 판단 차이를 구조적으로 기록하여 V2의 판단 품질을 검증한다.

### 로그 구조

```
┌─────────────────────────────────────────────┐
│  kg_decision_diff_log                       │
├─────────────────────────────────────────────┤
│  diff_id            TEXT PRIMARY KEY        │
│  event_id           TEXT NOT NULL           │
│  event_date         TEXT NOT NULL           │
│  headline           TEXT                    │
│                                             │
│  legacy_output      TEXT (JSON)             │
│    {                                        │
│      "category": "...",                     │
│      "tags": [...],                         │
│      "importance": 0.0,                     │
│      "action": "published|discarded"        │
│    }                                        │
│                                             │
│  v2_output          TEXT (JSON)             │
│    {                                        │
│      "entities_extracted": [...],           │
│      "events_created": [...],               │
│      "relations_created": [...],            │
│      "relations_rejected": [...]            │
│    }                                        │
│                                             │
│  confidence_breakdown TEXT (JSON)           │
│    {                                        │
│      "impacts": [                           │
│        {                                    │
│          "target": "...",                   │
│          "base": 0.0,                       │
│          "path_factor": 0.0,               │
│          "time_factor": 0.0,               │
│          "final_confidence": 0.0,          │
│          "threshold_result": "auto|review|  │
│                               reject"      │
│        }                                    │
│      ]                                      │
│    }                                        │
│                                             │
│  path_analysis       TEXT (JSON)            │
│    {                                        │
│      "paths_found": [                       │
│        {"from": "...", "to": "...",         │
│         "hops": 2, "route": [...]}          │
│      ],                                     │
│      "paths_missing": [                     │
│        {"from": "...", "to": "...",         │
│         "reason": "no_connection"}          │
│      ]                                      │
│    }                                        │
│                                             │
│  diff_type           TEXT                   │
│    # agree | v2_more_specific |             │
│    # v2_missed | v2_extra | contradictory   │
│                                             │
│  expert_verdict      TEXT                   │
│    # v2_correct | legacy_correct |          │
│    # both_valid | both_wrong | pending      │
│                                             │
│  created_at          TEXT                   │
│  reviewed_at         TEXT                   │
└─────────────────────────────────────────────┘
```

### diff_type 분류 기준

| diff_type | 정의 | 예시 |
|-----------|------|------|
| `agree` | 양쪽 판단 일치 | 동일 분류, 동일 중요도 판정 |
| `v2_more_specific` | V2가 더 세분화된 판단 | legacy: "정책 뉴스" → V2: POLICY.ANNOUNCE + IMPACTS 3건 |
| `v2_missed` | V2가 탐지하지 못한 관계 | legacy가 관련성을 인식했으나 V2는 경로 부재로 거부 |
| `v2_extra` | V2가 추가로 탐지한 관계 | legacy에 없는 TRIGGERS/IMPACTS를 V2가 생성 |
| `contradictory` | 양쪽 판단이 상반 | legacy: positive impact → V2: negative impact |

## 1-2. False Positive Sampling

### 설정

```
sample_rate: 1.0 (전수 조사)
max_per_day: 100건

대상: confidence ≥ 0.7로 자동 생성된 IMPACTS 관계 전건
기록: 자동 생성 → expert review → 적절/부적절 판정
```

### 로그 구조

```
┌─────────────────────────────────────────────┐
│  kg_false_positive_log                      │
├─────────────────────────────────────────────┤
│  fp_id              TEXT PRIMARY KEY        │
│  kg_relation_id     TEXT NOT NULL           │
│  relation_type      TEXT NOT NULL           │
│  source_id          TEXT NOT NULL           │
│  target_id          TEXT NOT NULL           │
│  confidence         REAL NOT NULL           │
│  magnitude          TEXT                    │
│  strength           TEXT                    │
│  hop_count          INTEGER                 │
│  generation_rule    TEXT                    │
│    # IM-P2I-01, IM-P2C-02, etc.            │
│                                             │
│  expert_verdict     TEXT                    │
│    # correct | incorrect | ambiguous        │
│  incorrect_reason   TEXT                    │
│    # wrong_direction | wrong_target |       │
│    # irrelevant | overstated               │
│                                             │
│  created_at         TEXT                    │
│  reviewed_at        TEXT                    │
└─────────────────────────────────────────────┘
```

### 집계 산식

```
daily_false_positive_rate =
  COUNT(expert_verdict = 'incorrect') /
  COUNT(expert_verdict IN ('correct', 'incorrect'))

ambiguous는 분모에서 제외.
reviewed_at이 NULL인 건은 집계에서 제외 (미리뷰).
```

## 1-3. Trigger Activation Failure

### 기록 조건

| 조건 | 의미 | 기록 내용 |
|------|------|---------|
| `rule_matched_but_not_fired` | TR 규칙 패턴 매칭되었으나 confidence 미달로 미생성 | 규칙 ID, 두 이벤트 ID, confidence, 미달 사유 |
| `path_missing` | TRIGGERS/IMPACTS 후보에서 경로 부재로 차단 | source actors, target, 탐색된 경로, 실패 지점 |

### 로그 구조

```
┌─────────────────────────────────────────────┐
│  kg_trigger_failure_log                     │
├─────────────────────────────────────────────┤
│  failure_id         TEXT PRIMARY KEY        │
│  failure_type       TEXT NOT NULL           │
│    # rule_matched_but_not_fired |           │
│    # path_missing                           │
│  rule_id            TEXT                    │
│    # TR-01, IM-P2I-01, etc.                │
│  source_event_id    TEXT                    │
│  target_entity_id   TEXT                    │
│  confidence_calculated REAL                 │
│  threshold_required  REAL                   │
│  path_search_result TEXT (JSON)             │
│    {                                        │
│      "searched_from": [...],               │
│      "searched_to": "...",                 │
│      "max_hops_searched": 3,               │
│      "paths_found": 0,                     │
│      "nearest_connection": "...",          │
│      "nearest_distance": null              │
│    }                                        │
│  suggested_fix      TEXT                    │
│    # "add_relation: X→Y (BELONGS_TO)" 등    │
│  created_at         TEXT                    │
└─────────────────────────────────────────────┘
```

### suggested_fix 자동 생성 규칙

```
IF failure_type = "path_missing":
  source_actors = 이벤트의 actors
  target = 차단된 target 엔티티

  nearest = 그래프에서 source_actors와 target에 각각 가장 가까운 엔티티 탐색

  IF nearest 발견 (각각 1-hop 이내):
    suggested_fix = "add_relation: {nearest_to_source} → {nearest_to_target} ({추정 관계})"
  ELSE:
    suggested_fix = "graph_gap: no nearby connection found"
```

## 1-4. Path Missing Detection

### Graph Metrics

| 지표 | 정의 | 집계 주기 |
|------|------|---------|
| `avg_degree` | 전체 active 엔티티의 평균 관계 수 | hourly |
| `path_exists_rate` | IMPACTS 후보 중 경로가 존재하는 비율 | hourly |
| `orphan_ratio` | degree=0인 active 엔티티 비율 | hourly |

### 스냅샷 구조

```
┌─────────────────────────────────────────────┐
│  kg_graph_health_snapshot                   │
├─────────────────────────────────────────────┤
│  snapshot_id        TEXT PRIMARY KEY        │
│  snapshot_time      TEXT NOT NULL           │
│  total_entities     INTEGER                 │
│  active_entities    INTEGER                 │
│  archived_entities  INTEGER                 │
│  total_relations    INTEGER                 │
│  total_events       INTEGER                 │
│  avg_degree         REAL                    │
│  path_exists_rate   REAL                    │
│  orphan_ratio       REAL                    │
│  tier_distribution  TEXT (JSON)             │
│    {"T1": N, "T2": N, "T3": N,             │
│     "T4": N, "T5": N}                      │
│  created_at         TEXT                    │
└─────────────────────────────────────────────┘
```

---

# 2. AUTO INSIGHT GENERATION

## 2-1. 시스템 개요

```
입력:
  - monitoring_metrics (cekg_monitoring_v2.md 지표)
  - diagnostic_logs (본 문서 4개 로그)

처리:
  1. 이상 탐지 (anomaly detection)
  2. 추세 요약 (trend summary)
  3. 일간 상위 5개 이슈 (top 5 issues)

출력:
  - daily_insight_report

주기: 일 1회 (Daily Report 직후)
```

## 2-2. Anomaly Detection

### 탐지 규칙

| Rule | 대상 지표 | 이상 판별 기준 | 유형 |
|------|---------|-------------|------|
| AN-01 | auto_generation_rate | 전일 대비 ±15%p 이상 변동 | `rate_shift` |
| AN-02 | confidence 분포 | 특정 구간(예: 0.4~0.5)에 50% 이상 집중 | `distribution_skew` |
| AN-03 | false_positive_rate | 3일 연속 상승 추세 | `rising_trend` |
| AN-04 | orphan_ratio | 전일 대비 +5%p 이상 증가 | `graph_degradation` |
| AN-05 | review_queue_size | 3일 연속 순증 (처리량 < 유입량) | `backlog_growth` |
| AN-06 | trigger_failure 빈도 | path_missing이 일간 IMPACTS 후보의 30% 초과 | `connectivity_gap` |
| AN-07 | override_trigger_rate | 전일 대비 2배 이상 급증 | `keyword_anomaly` |

### 탐지 방법

```
각 규칙에 대해:

1. 당일 지표 값 계산
2. 기준선 비교 (전일, 3일 평균, 7일 평균)
3. 이상 판별 (규칙별 기준 적용)
4. 이상 발견 시 → anomaly_record 생성

anomaly_record:
  {
    "rule": "AN-XX",
    "metric": "...",
    "current_value": X,
    "baseline_value": X,
    "deviation": X,
    "severity": "info | warning | critical",
    "context": "..."
  }
```

## 2-3. Trend Summary

### 추세 분석 항목

| 항목 | 분석 방법 | 출력 |
|------|---------|------|
| 그래프 성장 | 일간 엔티티/관계 증가율 3일 이동평균 | "그래프 일간 +X 엔티티, +Y 관계 (가속/감속/안정)" |
| 추론 효율 | auto_generation_rate 7일 이동평균 | "자동화율 X% → Y% (상승/하락/안정)" |
| 정밀도 | false_positive_rate 3일 이동평균 | "오판율 X% (안정/상승 주의)" |
| 리뷰 부하 | review_queue 처리율 (처리/유입) | "리뷰 처리율 X% (소화 가능/백로그 증가)" |
| 규칙 활성도 | TR/CT 규칙별 발동 횟수 7일 합계 | "활성 규칙: TR-01(N회), TR-02(N회)... 미활성: TR-05(0회)" |

### 추세 판정 기준

```
3일 이동평균 기준:

상승: 3일 연속 증가 (각 +1%p 이상)
하락: 3일 연속 감소 (각 -1%p 이상)
안정: 변동폭 ±1%p 이내
급변: 단일일 ±10%p 이상 변동
```

## 2-4. Top 5 Issues Per Day

### 이슈 우선순위 산정

```
issue_priority_score =
    severity_weight × frequency × impact_scope

severity_weight:
  critical = 10
  warning = 5
  info = 1

frequency:
  해당 이슈 유형의 당일 발생 건수

impact_scope:
  전체 IMPACTS에 영향 = 3
  특정 규칙에만 영향 = 2
  단건 = 1
```

### 이슈 유형 카탈로그

| 이슈 유형 | 소스 | 예시 |
|---------|------|------|
| `high_rejection` | monitoring | "경로 부재 거부율 28% (경고 임계 25% 초과)" |
| `confidence_skew` | anomaly AN-02 | "confidence 0.4~0.5 구간 편중 (55%)" |
| `fp_spike` | false_positive_log | "오판율 12% (전일 5%에서 급등)" |
| `backlog_growing` | anomaly AN-05 | "리뷰 대기 85건 (3일 연속 증가)" |
| `rule_inactive` | trigger_failure | "TR-01 7일간 미발동 (경로 부재)" |
| `graph_sparse` | graph_health | "orphan 비율 42% (경고 40% 초과)" |
| `override_surge` | override_log | "SUCCEEDS override 22% (전일 8%)" |
| `magnitude_manual` | magnitude_trace | "MACRO 수동 설정 비율 40% (목표 < 30%)" |

## 2-5. Daily Insight Report 형식

```
=== CEKG-V2 Daily Insight Report ===
Date: {YYYY-MM-DD}
Generated: {HH:MM}

━━━ ANOMALIES DETECTED ━━━

{anomaly_count}건 탐지

  [{severity}] AN-XX: {metric} — {description}
    Current: {value} | Baseline: {value} | Deviation: {value}
    Context: {context}

━━━ TREND SUMMARY ━━━

  그래프:    +{N} entities, +{N} relations ({trend})
  자동화율:  {rate}% ({trend}, 7일 avg: {rate}%)
  오판율:    {rate}% ({trend})
  리뷰 부하: 처리율 {rate}% ({assessment})
  규칙 활성: {active_rules}/{total_rules} 활성

━━━ TOP 5 ISSUES ━━━

  1. [{priority_score}] {issue_type}: {description}
     Action: {recommended_action}

  2. [{priority_score}] {issue_type}: {description}
     Action: {recommended_action}

  3. ...
  4. ...
  5. ...

━━━ RECOMMENDED ACTIONS ━━━

  즉시 조치:    {count}건
  모니터링:     {count}건
  다음 리뷰 시: {count}건

━━━ GRAPH HEALTH SNAPSHOT ━━━

  Entities: {active} active / {archived} archived
  Relations: {total}
  Avg Degree: {X.X}
  Path Exists Rate: {rate}%
  Orphan Ratio: {rate}%
  Tier: T1:{N} T2:{N} T3:{N} T4:{N} T5:{N}
```

---

# 3. 로그 보존 정책

| 로그 | 보존 기간 | 아카이브 |
|------|---------|--------|
| kg_decision_diff_log | 90일 | 90일 초과 시 /kg_shadow/archive/로 이동 |
| kg_false_positive_log | 90일 | 동일 |
| kg_trigger_failure_log | 30일 | 30일 초과 시 삭제 (패턴만 insight에 보존) |
| kg_graph_health_snapshot | 365일 | 일간 스냅샷. 7일 초과 시 일간→주간 요약으로 압축 |
| daily_insight_report | 365일 | 텍스트 파일로 보존 |

---

# 4. 전체 로그 테이블 요약

| 테이블 | 용도 | 생성 시점 |
|--------|------|---------|
| kg_decision_diff_log | legacy vs V2 판단 비교 | shadow comparison 기간 (7일) |
| kg_false_positive_log | 오판 추적 | 자동 생성 IMPACTS 발생 시 |
| kg_trigger_failure_log | 규칙 미발동 추적 | TRIGGERS/IMPACTS 후보 거부 시 |
| kg_graph_health_snapshot | 그래프 상태 이력 | hourly |
| kg_relation_override_log | V2 keyword override 기록 | 기존 (deployment에서 정의) |
| kg_magnitude_trace_log | magnitude 산정 이력 | 기존 (deployment에서 정의) |
| kg_conflict_log | 경로/시간 충돌 기록 | 기존 (base design에서 정의) |
| kg_review_queue | 수동 심사 대기열 | 기존 (inference design에서 정의) |

---

# RISK ANALYSIS

```json
{
  "risk_detected": false,
  "summary": "진단 로깅 및 인사이트 시스템은 설계 문서로만 정의. 실제 구현 없음. 기존 시스템 무접촉",
  "notes": [
    "모든 로그 테이블: kg_ 접두어",
    "decision_diff의 legacy_output: 기존 시스템 읽기 전용 참조",
    "기존 DB 쓰기/수정 없음",
    "/kg_shadow/ 내 완결"
  ]
}
```

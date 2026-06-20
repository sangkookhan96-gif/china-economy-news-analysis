# CEKG-V2 Production Activation Record

**활성화일: 2026-03-21**
**상태: PRODUCTION ACTIVE**

---

# 1. ACTIVATION CONFIRMATION

```
╔══════════════════════════════════════════════╗
║  CEKG INFERENCE ENGINE V2                    ║
║  STATUS: ██████████ PRODUCTION ACTIVE        ║
║                                              ║
║  Validation:  60/60 PASS                     ║
║  Deployment:  cekg_deployment_v2.md          ║
║  Snapshot:    cekg_v2_release_2026-03-21      ║
║  Monitoring:  cekg_monitoring_v2.md          ║
║  Regression:  cekg_regression_test_v2.md     ║
╚══════════════════════════════════════════════╝
```

### Feature Flags (ALL ENABLED)

| Flag | Status |
|------|--------|
| magnitude_auto_estimation | ██ ON |
| macro_deviation_rule | ██ ON |
| event_relation_keyword_override | ██ ON |
| confidence_thresholding | ██ ON |
| path_validation | ██ ON |
| archive_auto_cleanup | ██ ON |
| safety_guard_sg01 | ██ ON |
| safety_guard_sg02 | ██ ON |

---

# 2. REALTIME MONITORING

## 2-1. 모니터링 지표

| 지표 | 집계 주기 | 목표 | 경고 임계값 |
|------|---------|------|---------|
| `auto_generation_rate` | 1h / daily | ≥ 40% (초기) → ≥ 60% (안정기) | < 30% |
| `review_queue_size` | realtime | 누적 < 100 | > 500 |
| `false_positive_rate` | daily | ≤ 5% | > 10% |
| `confidence_distribution` | 1h | 참고값 (분포 추이) | 편중 발생 시 |
| `trigger_activation_rate` | daily | ≥ 30% | < 15% |
| `contradicts_detection_rate` | daily | 참고값 | 0% (7일 연속) |

## 2-2. 집계 윈도우

```
Realtime:
  - review_queue_size (즉시 업데이트)
  - ALT-C01~C03 긴급 경고 (즉시 판별)

1-Hour Aggregation:
  - auto_generation_rate (최근 1시간)
  - confidence_distribution (최근 1시간)
  - IMPACTS 생성/거부/리뷰 건수

Daily Report:
  - 전 지표 일간 합산
  - false_positive_rate (expert review 결과 반영)
  - trigger_activation_rate
  - contradicts_detection_rate
  - 그래프 성장 지표 (엔티티/이벤트/관계 증감)
```

## 2-3. Alert 규칙

### 즉시 (Realtime)

| Alert | 조건 | 대응 |
|-------|------|------|
| ALT-R01 | auto_generation_rate < 0.30 (1h 윈도우) | 원인 분석: 그래프 밀도? confidence 산정 이상? |
| ALT-R02 | review_queue_size > 500 (누적) | 리뷰 백로그 경고. 리뷰 우선순위 재조정 또는 임계값 완화 검토 |
| ALT-R03 | false_positive_rate > 0.10 (일간) | 규칙 정밀도 점검. 해당 규칙 ID별 오판 분석 |

### 긴급 (Critical)

| Alert | 조건 | 대응 |
|-------|------|------|
| ALT-C01 | rejection_rate > 50% | 즉시 원인 분석. 롤백 검토 |
| ALT-C02 | auto_generation_rate < 20% | 엔진 실질 무력화. 롤백 검토 |
| ALT-C03 | 기존 시스템 접근 탐지 | 즉시 중단. 격리 위반 조사 |

## 2-4. false_positive_rate 측정 방법

```
측정 시점: expert review 완료 후 (지연 측정)

false_positive =
  (expert가 거부한 자동 생성 관계 수) /
  (전체 자동 생성 관계 수)

판별 기준:
  expert가 자동 생성된 IMPACTS를 "부적절"로 판정한 경우
  → false positive로 카운트

집계: 일간 (리뷰 완료 건 기준)
초기 7일은 calibration 기간으로 경고 발생해도 즉시 조치하지 않고 관찰
```

---

# 3. SHADOW COMPARISON MODE

## 3-1. 비교 설계

```
목적: CEKG-V2와 기존 시스템(legacy)의 판단 차이 분석
방법: 동일 뉴스 입력에 대해 양쪽 결과를 병렬 기록
기간: 7일 (2026-03-21 ~ 2026-03-28)

중요: 기존 시스템에 어떠한 변경도 가하지 않음.
      CEKG-V2 결과를 기존 시스템과 비교할 때,
      기존 시스템의 출력을 읽기 전용으로만 참조.
```

## 3-2. 비교 지표

| 지표 | 정의 | 측정 방법 |
|------|------|---------|
| `precision` | CEKG-V2 자동 판단 중 정확한 비율 | expert review 기반 |
| `recall` | 실제 관련 있는 관계 중 CEKG-V2가 탐지한 비율 | expert가 식별한 전체 관계 대비 |
| `latency` | 이벤트 입력 → 관계 생성까지 소요 시간 | 설계 단계에서는 단계 수로 대리 측정 |
| `decision_diff_rate` | 기존 시스템과 CEKG-V2의 판단이 다른 비율 | 동일 뉴스에 대한 분류/연결 결과 비교 |

## 3-3. 비교 프로토콜

```
일간 프로세스:

1. 당일 공개된 뉴스 10~15건 수집 (기존 시스템 출력)
2. 동일 뉴스에 대해 CEKG-V2 추론 시뮬레이션 수행
3. 비교 기록:
   - 기존: 어떤 분류/태그를 부여했는가
   - CEKG: 어떤 엔티티/이벤트/관계를 생성했는가
   - 차이: decision_diff 건수 및 유형

4. Expert 판정: 차이 발생 건에 대해 어느 쪽이 적절한지 판정

비교 결과 저장: /kg_shadow/comparison/ (일자별)
```

## 3-4. 7일 비교 종료 후 판단 기준

| 결과 | 조치 |
|------|------|
| precision ≥ 80% + decision_diff에서 CEKG 우세 ≥ 60% | CEKG-V2 우수. 본격 운영 전환 근거 확보 |
| precision 60~80% | 규칙 미세 조정 후 2주차 비교 연장 |
| precision < 60% | 규칙 대폭 수정 필요. PATCH v3 설계 |
| recall < 50% | 그래프 밀도 부족. 엔티티/관계 초기 등록 보강 |

---

# 4. POST-DEPLOYMENT REVIEW SCHEDULE

## T+3 Review (2026-03-24)

| 항목 | 점검 내용 |
|------|---------|
| 그래프 밀도 | 엔티티 수, 관계 수, edges/nodes 비율 |
| auto_generation_rate 추이 | 3일간 추이. 목표(40%) 도달 여부 |
| review_queue 백로그 | 누적 건수, 처리 속도 |
| false_positive 초기 calibration | 오판 패턴 식별 |
| TRIGGERS 활성화 | TR-01~05 중 실제 발동 건수. 그래프 밀도와 상관 분석 |
| 모니터링 지표 정상 작동 | 각 지표 집계 정상 여부 |

### T+3 판단 기준

```
IF auto_generation_rate ≥ 35% AND false_positive_rate ≤ 15%:
  → 정상 운영 계속

IF auto_generation_rate < 25%:
  → 긴급 점검: 그래프 밀도 보강 또는 confidence 임계값 조정

IF false_positive_rate > 15%:
  → 규칙 정밀도 점검: 오판 상위 3개 규칙 분석
```

## T+7 Review (2026-03-28)

| 항목 | 점검 내용 |
|------|---------|
| 전 지표 주간 요약 | Weekly Drift Analysis 첫 회차 |
| Shadow Comparison 결과 | 7일 비교 종합 (precision, recall, decision_diff) |
| 그래프 성장 궤적 | 일간 엔티티/관계 증가율 |
| TRIGGERS 회복 분석 | 관계 밀도 증가에 따른 TRIGGERS 발동률 변화 |
| auto_generation_rate 안정성 | 일간 변동폭 ±5% 이내인지 |
| 중요도 분포 | T1~T5 비율이 예상(2/8/25/35/30%)에 근접하는지 |
| 아카이브 | 첫 월간 배치 전 예비 점검 |

### T+7 판단 기준

```
종합 평가:

IF 전 지표 정상 + comparison precision ≥ 80%:
  → PRODUCTION CONFIRMED. 안정기 목표로 전환
  → 모니터링 주기: 주간으로 완화

IF 일부 지표 미달 (경고 수준):
  → PATCH v2.1 minor fix 검토
  → 모니터링 유지 (일간)

IF 다수 지표 미달 + comparison precision < 60%:
  → ROLLBACK 검토
  → cekg_v2_release_2026-03-21 스냅샷으로 복원
  → PATCH v3 설계 진입
```

---

# 5. OPERATIONAL RUNBOOK

## 일간 운영 흐름

```
매일 오전:
  1. 전일 Daily Report 확인
  2. review_queue 백로그 확인 → 10건 이내 유지 목표
  3. Alert 발생 여부 확인
  4. Shadow Comparison 기록 (7일간)

매일 오후:
  5. Expert review 수행 (리뷰 대기 건)
  6. false_positive 판정 기록
  7. 신규 엔티티 aliases 승인 (Stage 3 매칭 후보)
```

## 주간 운영

```
매주 월요일:
  1. Weekly Drift Analysis 생성
  2. importance_score 전체 재계산
  3. 티어 변동 2단계 이상 건 점검
  4. 목표 재조정 검토
```

## 월간 운영

```
매월 1일:
  1. 아카이브 배치 실행
  2. 전체 지표 월간 리뷰
  3. 운영 초기 → 안정기 목표 전환 판단
  4. 규칙 개선 필요 사항 취합
```

---

# 6. DOCUMENT REGISTRY

| 문서 | 역할 | 상태 |
|------|------|------|
| cekg_base_design.md | 기본 구조 (스키마, 엔티티, 관계) | PRODUCTION v2 |
| cekg_inference_design.md | 추론 규칙 (IMPACTS, TRIGGERS, magnitude) | PRODUCTION v2 |
| cekg_deployment_v2.md | 배포 기록 (feature flags, safety guard) | ACTIVE |
| cekg_monitoring_v2.md | 모니터링 정의 (지표, 경고, 리포트) | ACTIVE |
| cekg_regression_test_v2.md | 회귀 테스트 (60/60 PASS) | COMPLETED |
| cekg_production_activation.md | 본 문서 (활성화, shadow, 리뷰 일정) | ACTIVE |
| cekg_inference_test_report.md | 추론 시뮬레이션 (EVT-T01~T05) | COMPLETED |
| cekg_patch_applied.md | 패치 이력 (PATCH-01~05) | ARCHIVED |
| cekg_validation_report.md | 아키텍처 검증 (FIX 1~5 도출) | ARCHIVED |

---

# RISK ANALYSIS

```json
{
  "risk_detected": false,
  "summary": "PRODUCTION 활성화는 설계 상태 전환. 실제 시스템 구현/실행 없음. 기존 시스템 무접촉",
  "isolation_status": "MAINTAINED",
  "legacy_system_impact": "NONE",
  "notes": [
    "Shadow Comparison은 기존 시스템 출력을 읽기 전용으로만 참조",
    "모든 CEKG 데이터는 /kg_shadow/ 내에서만 처리",
    "실제 구현 시에도 kg_ 접두어 + KG- ID 격리 원칙 유지"
  ]
}
```

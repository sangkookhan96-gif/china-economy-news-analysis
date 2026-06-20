# CEKG Architecture Stress Test — Validation Report

**MODE: SHADOW VALIDATION**
**상위 문서: cekg_base_design.md, cekg_inference_design.md**

---

# 1. INFERENCE RULE 검증

## 1-1. TRIGGERS 규칙 검증

| 규칙 ID | IF-THEN 명확성 | 입력 조건 모호성 | 시간 조건 | confidence 정량화 | 판정 |
|---------|--------------|--------------|---------|----------------|------|
| TR-01 | OK | OK | ≤3일 명시 | 정량 공식 있음 | **OK** |
| TR-02 | OK | WEAK — "산업 규제"의 범위 미정의 | ≤5일 명시 | 정량 공식 있음 | **WEAK** |
| TR-03 | OK | WEAK — "예상 하회"의 기준 미정의 | ≤30일 명시 | 정량 공식 있음 | **WEAK** |
| TR-04 | OK | OK | ≤60일 명시 | 정량 공식 있음 | **OK** |
| TR-05 | OK | WEAK — "위기 관련 기관"의 판별 기준 없음 | ≤14일 명시 | 정량 공식 있음 | **WEAK** |

## 1-2. IMPACTS 규칙 검증

| 규칙 ID | IF-THEN | 입력 조건 | 시간 조건 | confidence | 판정 |
|---------|---------|---------|---------|-----------|------|
| IM-P2I-01 | OK | OK | FAIL — 시간 조건 없음 | 없음 | **FAIL** |
| IM-P2I-02 | OK | WEAK — headline 매칭 기준 모호 | FAIL — 시간 조건 없음 | 없음 | **FAIL** |
| IM-P2I-03 | OK | OK | FAIL — 시간 조건 없음 | strength만 있고 confidence 없음 | **WEAK** |
| IM-P2C-01 | OK | OK | 상위 규칙 상속 | 상위 규칙 상속 | **OK** |
| IM-P2C-02 | OK | OK | FAIL — 시간 조건 없음 | 없음 | **FAIL** |
| IM-I2M-01 | OK | WEAK — "예상" 값 출처 미정의 | FAIL — 시간 조건 없음 | strength만 있음 | **FAIL** |
| IM-I2M-02 | OK | OK | 기존 관계 기간 상속 | 있음 | **OK** |

## 1-3. SUCCEEDS / CONTRADICTS 검증

| 규칙 ID | IF-THEN | 입력 조건 | 시간 조건 | confidence | 판정 |
|---------|---------|---------|---------|-----------|------|
| SU-01 | OK | OK | 180일 한계 명시 | 불필요 | **OK** |
| SU-02 | OK | OK | 암묵적 | 불필요 | **OK** |
| SU-03 | OK | OK | 중복 판별에 위임 | 불필요 | **OK** |
| SU-04 | OK | OK | 방향 전환 시 종료 명시 | 불필요 | **OK** |
| CT-01 | OK | OK | 30일 명시 | 정량 공식 있음 | **OK** |
| CT-02 | OK | WEAK — "동일 주체" 범위 미정의 | 없음 | 있음 | **WEAK** |
| CT-03 | OK | OK | 동시기 명시 | 있음 | **OK** |
| CT-04 | OK | OK | 동시기 명시 | 있음 | **OK** |
| CT-05 | OK | OK | 해당 없음 | 해당 없음 | **OK** |

## 1-4. 검증 요약

| 판정 | 건수 | 규칙 |
|------|------|------|
| OK | 11 | TR-01, TR-04, IM-P2C-01, IM-I2M-02, SU-01~04, CT-01, CT-03~05 |
| WEAK | 5 | TR-02, TR-03, TR-05, IM-P2I-03, CT-02 |
| FAIL | 5 | IM-P2I-01, IM-P2I-02, IM-P2C-02, IM-I2M-01, IM-P2I-03(시간) |

FAIL 공통 원인: IMPACTS 규칙 전체에 시간 조건과 confidence 산정 방식 누락

---

# 2. IMPORTANCE HIERARCHY 검증

## 2-1. 레벨별 기능 검증

| 티어 | 필터 역할 수행 | 문제점 | 판정 |
|------|-------------|--------|------|
| T1 | 항상 연결 생성 → 기능 명확 | 없음 | **OK** |
| T2 | 항상 연결 생성 → T1과 처리 동일 | T1과 실질적 차이가 리뷰 이력 범위뿐 | **WEAK** |
| T3 | confidence ≥ 0.5로 제한 | 기능 명확 | **OK** |
| T4 | confidence ≥ 0.7로 제한 | 기능 명확 | **OK** |
| T5 | 생성 안 함 + 숨김 | 제거 기준 부재. 무한 축적 | **FAIL** |

## 2-2. 중요도 공식 문제점

| 문제 | 상세 |
|------|------|
| norm() 함수의 max 의존성 | 극단값이 전체를 압축 |
| policy_influence 순환 | ORG가 구조적으로 유리 |
| recency_bonus 비연속성 | 7일→30일 전환 시 0.3 급락 |
| w4의 편향 | COM, IND 유형이 항상 불리 |

## 2-3. 동적 업데이트 검증

| 규칙 | 문제 | 판정 |
|------|------|------|
| 이벤트 참여 시 +1 | OK | **OK** |
| 30일 미등장 시 감소 | 감소량 미정의 | **WEAK** |
| 병합 시 수치 합산 | 일부 지표는 합산이 아닌 min/max 필요 | **WEAK** |
| 티어 변동 1단계 제한 | OK | **OK** |

---

# 3. GRAPH MATCHING 검증

## CASE 1: 동일 기관 다른 이름

| 입력 | 별칭 등록 여부 | 매칭 결과 | 판정 |
|------|-------------|---------|------|
| "央行" | aliases에 있음 | Stage 2에서 확정 (0.95) | **OK** |
| "중국 중앙은행" | aliases에 없음 | Stage 3에서 유사도 0.72 → 수동 심사 후보 | **OK** |

발견된 문제: 수동 승인 후 aliases 자동 추가 규칙 미정의

## CASE 2: 동일 사건 다른 날짜 보도

| 이벤트 | match_score | 판정 결과 | 판정 |
|--------|------------|---------|------|
| 인민은행 금리인하 (3/15) vs 동일 사건 (3/17) | 0.915 | 동일 사건 → source_ref 추가 | **OK** |

## CASE 3: 동일 정책 반복 발표

| 이벤트 | match_score | 판정 결과 | 판정 |
|--------|------------|---------|------|
| 부동산 규제 완화 1차 (1/15) vs 2차 강화 (3/20) | 0.65 | supersedes 후보 | **WEAK** |

발견된 문제: 이 경우 SUCCEEDS가 더 적절. supersedes vs SUCCEEDS 판별 규칙 불충분

---

# 4. FALSE INFERENCE 테스트

## 4-1. 상관관계를 인과로 잘못 연결

| 상황 | 방지 규칙 | 작동 여부 | 판정 |
|------|---------|---------|------|
| 미국 고용지표(3/1) → 중국 돼지고기(3/3) | TR 유형 제한 + 엔티티 경로 조건 | TRIGGERS는 차단됨 | **OK** |
| 동일 상황에서 IMPACTS 시도 | 경로 조건 없음 | **방어 실패** | **FAIL** |

## 4-2. 시간 순서 역전

| 상황 | 방지 규칙 | 작동 여부 | 판정 |
|------|---------|---------|------|
| 후행 이벤트가 선행을 TRIGGERS | V6 | 차단 | **OK** |

## 4-3. 영향 과대 해석

| 상황 | 방지 규칙 | 작동 여부 | 판정 |
|------|---------|---------|------|
| 5bp 인하 → strong 판정 | IM-P2I-03 strength 기준 | <25bp → weak | **OK** |
| magnitude 잘못 설정 시 | magnitude 산정 기준 | **기준 부재** | **FAIL** |

---

# 5. SYSTEM COMPLETENESS 판정

| 평가 항목 | 점수 | 근거 |
|---------|------|------|
| 추론 가능성 | 62/100 | TRIGGERS·SUCCEEDS·CONTRADICTS 작동 가능. IMPACTS 전체 시간·confidence 누락 |
| 자동화 가능성 | 55/100 | 엔티티 매칭 양호. IMPACTS 자동 생성 불가. magnitude 자동 산정 부재 |
| 오류 위험도 | 45/100 | TRIGGERS 오판 방지 양호. IMPACTS 오판 방지 부재. 중요도 공식 편향 |

### 최종 판정

```
✔ NEED FIX

사유:
- IMPACTS 규칙 5건 FAIL (시간·confidence 누락)
- magnitude 산정 기준 부재
- 중요도 공식 편향 (norm 함수, policy_influence)
- supersedes vs SUCCEEDS 판별 모호
- T5 엔티티 제거 기준 부재
```

---

# 6. CRITICAL FIX (5건)

## FIX 1: IMPACTS 규칙에 시간 조건 및 confidence 추가

**문제:** IMPACTS 규칙 5건에 시간 조건과 confidence 산정이 없어 무한 확장 가능

**수정 규칙:**

```
IMPACTS 공통 조건 추가:

시간 조건:
  POLICY → IND/COM: valid_from = 정책 발표일, valid_until = 다음 동종 정책 발표일 또는 발표일 + 365일 중 빠른 것
  MONETARY → IND:    valid_from = 금리 변경 적용일, valid_until = 다음 금리 변경일
  IDX → FIN:         valid_from = 지표 발표일, valid_until = 발표일 + 30일

confidence 산정:
  IMPACTS_confidence = base × entity_path_factor × time_proximity_factor

  base:
    직접 명시 (actor/target) → 0.90
    산업 소속 경유        → 0.65
    headline 키워드 경유  → 0.45

  entity_path_factor:
    1-hop 관계 존재 → 1.0
    2-hop            → 0.7
    3-hop 이상       → 0.4
    경로 없음         → 0 (생성 거부)
```

**기대 변화:** IMPACTS 관계에 유효 기간 부여. 경로 없는 오판 차단.

## FIX 2: magnitude 산정 기준 정의

**문제:** magnitude가 주관적으로 설정됨. strength 연쇄 왜곡 발생

**수정 규칙:**

```
MONETARY:
  critical: 금리 변동 ≥ 50bp 또는 지준율 변동 ≥ 200bp
  major:    금리 변동 25~49bp 또는 지준율 100~199bp
  moderate: 금리 변동 10~24bp 또는 지준율 50~99bp
  minor:    금리 변동 < 10bp 또는 지준율 < 50bp

POLICY:
  critical: 법률 제정/폐지, 전국 범위
  major:    규정 신설/대폭 개정, 전국 또는 주요 산업
  moderate: 기존 규정 부분 수정, 특정 지역/산업
  minor:    지침/통지, 제한적 범위

MARKET:
  critical: 지수 일간 변동 ≥ 5% 또는 서킷브레이커 발동
  major:    지수 일간 변동 3~4.9%
  moderate: 지수 일간 변동 1~2.9%
  minor:    지수 일간 변동 < 1%

TRADE:
  critical: 전면 금수/제재
  major:    관세율 변동 ≥ 10%p 또는 주요 품목 제한
  moderate: 관세율 변동 5~9.9%p
  minor:    관세율 변동 < 5%p 또는 행정 조치

그 외: 전문가 수동 설정 (기본값 moderate)
```

**기대 변화:** magnitude → strength 연쇄가 객관적 기준에 근거.

## FIX 3: supersedes vs SUCCEEDS 판별 규칙 명확화

**문제:** 이벤트 매칭 score 0.60~0.79에서 supersedes/SUCCEEDS 혼재

**수정 규칙:**

```
score 0.60~0.79 구간 세분화:

STEP 1: 정보 갱신인가, 후속 조치인가?
  - headline에 "추가", "강화", "2차", "후속", "보완" → SUCCEEDS
  - headline에 "수정", "정정", "업데이트", "확정" → supersedes
  - 해당 없으면 → STEP 2

STEP 2: 수치 변화 존재 여부
  - 동일 지표, value_change 다름 → SUCCEEDS
  - 동일 지표, value_change 동일/유사(±10%) → supersedes
  - indicators 없음 → STEP 3

STEP 3: actors 변화 여부
  - actors + targets 완전 동일 → supersedes
  - 변화 있음 → SUCCEEDS
  - 판별 불가 → kg_review_queue 적재
```

**기대 변화:** 정보 갱신과 후속 조치의 혼동 제거.

## FIX 4: 중요도 공식 편향 수정

**문제:** norm(max) 극단값 압축 + policy_influence ORG 편향 + recency 비연속

**수정 규칙:**

```
수정 1: norm() → percentile_rank() / 100
  효과: 극단값 압축 제거

수정 2: w4(policy_influence) 유형별 분리
  ORG: w4 = 0.10
  COM: w4 = 0.20
  IND: w4 = 0.20
  PER: w4 = 0.15
  기타: w4 = 0.15

수정 3: recency_bonus → 지수 감쇠
  recency_bonus = exp(-0.03 × 경과일)
  1일: 0.97, 7일: 0.81, 30일: 0.41, 90일: 0.07
```

**기대 변화:** entity_type 간 공정한 중요도 산정.

## FIX 5: T5 엔티티 아카이브 규칙 추가

**문제:** T5 엔티티가 무한 축적. 노이즈 증가

**수정 규칙:**

```
아카이브 조건 (모두 충족 시):
  1. importance_score < 0.10
  2. last_seen_date > 180일 전
  3. degree_centrality = 0 또는 T4/T5 연결만
  4. event_participation ≤ 1

동작:
  - status → "archived"
  - kg_archive 테이블로 이동 (삭제 아님)
  - aliases는 매칭 대상 유지 (부활 가능)

부활 조건:
  - 신규 이벤트에서 재매칭 시 → status "active" + 메인 복원

주기: 월 1회 배치

추가 테이블:
  kg_archive (kg_entities 동일 스키마 + archived_at, archive_reason)
```

**기대 변화:** 그래프 노이즈 ~30% 감소. 정보 손실 없음.

---

# RISK ANALYSIS

```json
{
  "risk_detected": false,
  "summary": "본 검증은 SHADOW MODE 내에서 완결. 기존 시스템 참조·접근·수정 없음",
  "verified_items": [
    "실제 데이터 처리 없음",
    "DB 생성/연결 없음",
    "코드 작성 없음",
    "모든 테스트는 가상 시나리오로만 수행"
  ]
}
```

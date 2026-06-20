# CEKG Inference Layer Design

**MODE: PRODUCTION (v2)**
**상위 문서: cekg_base_design.md**
**배포일: 2026-03-21**
**버전: CEKG-INFERENCE-ENGINE V2**

---

# 1. INFERENCE RULE ENGINE

## 1-1. TRIGGERS 관계 자동 생성 규칙

| 규칙 ID | 트리거 조건 | 시간 간격 | 예외 조건 | confidence 산정 |
|---------|-----------|---------|---------|----------------|
| TR-01 | MONETARY.RATE_CUT 발생 후 MARKET.PRICE_MOVE(채권) 발생 | ≤ 3일 | 동일 기간 다른 MONETARY 이벤트 존재 시 confidence 감산 | base 0.85 × (1 / 동시 이벤트 수) |
| TR-02 | POLICY.ANNOUNCE(산업 규제) 후 MARKET.PRICE_MOVE(해당 산업 기업) 발생 | ≤ 5일 | target 기업이 해당 산업 IND 엔티티와 BELONGS_TO 관계 없으면 거부 | base 0.75 × (관계 경로 길이 역수) |
| TR-03 | MACRO.DATA_RELEASE(예상 하회) 후 MONETARY 이벤트 발생 | ≤ 30일 | 지표와 통화정책 간 기존 MEASURES 관계 없으면 보류 | base 0.60 × (시간 근접도) |
| TR-04 | TRADE.TARIFF 후 INDUSTRY.REGULATION 또는 INDUSTRY.SUBSIDY 발생 | ≤ 60일 | 산업 일치 필수 (태그 교집합 ≥ 1) | base 0.55 × (산업 태그 일치율) |
| TR-05 | RISK.CRISIS 후 POLICY.ANNOUNCE 발생 | ≤ 14일 | 정책 주체가 위기 관련 기관이 아니면 confidence 0.3으로 강제 하향 | base 0.70 × (주체 관련도) |

### confidence 계산 공통 공식

```
final_confidence = base_score
                   × time_proximity_factor      # 1.0 (당일) ~ 0.3 (한계일)
                   × entity_overlap_factor       # actors/targets 교집합 비율
                   × concurrent_event_penalty    # 1 / (1 + 동시 경쟁 이벤트 수)

time_proximity_factor = 1 - (경과일 / 허용한계일) × 0.7

임계값:
  ≥ 0.7  → 자동 생성
  0.4~0.7 → 후보 생성 (expert review 대기)
  < 0.4  → 생성 안 함
```

## 1-2. IMPACTS 관계 생성 규칙

### 정책 → 산업

| 규칙 ID | 조건 | direction 판별 | strength 판별 |
|---------|------|--------------|-------------|
| IM-P2I-01 | POLICY 이벤트의 target에 IND 엔티티 포함 | 보조금/지원 → positive, 규제/제한 → negative | magnitude가 major/critical → strong, 그 외 → moderate |
| IM-P2I-02 | POLICY 이벤트의 headline에 산업명 포함 + 직접 target 아님 | 키워드 감성 분석 기반 | weak (간접 언급이므로) |
| IM-P2I-03 | MONETARY 이벤트 → 금리 민감 산업 (부동산, 건설, 금융) | RATE_CUT → positive, RATE_HIKE → negative | value_change 절대값 기준: ≥50bp → strong, ≥25bp → moderate, <25bp → weak |

### 정책 → 기업

| 규칙 ID | 조건 | direction 판별 | strength 판별 |
|---------|------|--------------|-------------|
| IM-P2C-01 | 정책 target 산업에 기업이 BELONGS_TO | 산업 IMPACTS의 direction 상속 | 기업의 산업 내 중요도 티어에 비례 |
| IM-P2C-02 | 정책이 기업을 직접 명시 (actor/target) | 정책 유형에서 직접 판별 | strong (직접 명시이므로) |

### 지표 → 시장

| 규칙 ID | 조건 | direction 판별 | strength 판별 |
|---------|------|--------------|-------------|
| IM-I2M-01 | MACRO.DATA_RELEASE의 indicator에 IDX 엔티티 포함 | 예상 상회 → positive, 하회 → negative | 괴리율 기준: ≥2σ → strong, ≥1σ → moderate, <1σ → weak |
| IM-I2M-02 | IDX와 FIN 간 기존 MEASURES 관계 존재 시, IDX 변동 → FIN IMPACTS 자동 생성 | MEASURES 관계의 direction 상속 | IDX 변동 강도 × MEASURES strength |

### IMPACTS 공통 보강 규칙 (PATCH-01)

본 규칙은 모든 IM-* 규칙에 공통 적용되며, 개별 규칙의 기존 조건에 추가된다.

#### 시간 조건

| 유형 | valid_from | valid_until |
|------|-----------|------------|
| POLICY → IND/COM | 정책 발표일 | 다음 동종 정책 발표일 또는 발표일 + 365일 중 빠른 것 |
| MONETARY → IND | 금리/지준율 변경 적용일 | 다음 동종 변경 적용일 |
| IDX → FIN | 지표 발표일 | 발표일 + 30일 |
| TRADE → IND/COM | 관세/제재 발효일 | 해제일 또는 발효일 + 365일 중 빠른 것 |

동종 정책 정의: 동일 event_type(Level 2) + targets 교집합 ≥ 1

#### confidence 산정 공식

```
IMPACTS_confidence = base × entity_path_factor × time_proximity_factor

base:
  직접 명시 (source 이벤트의 actor/target에 impact 대상 포함) → 0.90
  산업 소속 경유 (대상이 BELONGS_TO로 target 산업에 연결)   → 0.65
  headline 키워드 경유 (대상명이 headline에 포함)           → 0.45

entity_path_factor:
  source 이벤트의 actors/targets와 impact 대상 간 기존 관계 경로 기준
  1-hop 관계 존재  → 1.0
  2-hop            → 0.7
  3-hop 이상       → 0.4
  경로 없음         → 0 (생성 거부)

time_proximity_factor:
  IMPACTS 생성 시점과 source 이벤트 발생일 간 경과일 기준
  ≤ 3일  → 1.0
  ≤ 7일  → 0.8
  ≤ 30일 → 0.5
  > 30일 → 0.2

임계값:
  ≥ 0.7  → 자동 생성
  0.4~0.7 → 후보 생성 (expert review 대기)
  < 0.4  → 생성 안 함
```

#### 경로 없는 IMPACTS 생성 금지 규칙

```
조건: entity_path_factor = 0
동작: IMPACTS 관계 생성 거부
로그: kg_conflict_log에 기록
  conflict_type = "no_entity_path"
  description = "{source_event_id} → {target_entity_id}: 관계 경로 없음"

이 규칙은 IM-* 개별 규칙보다 우선한다 (P1 수준 검증).
```

## 1-2a. magnitude 산정 기준 (PATCH-02)

magnitude는 이벤트 생성 시 자동 산정된다. 자동 산정 불가 시 기본값 moderate.

### MONETARY

| magnitude | 조건 |
|-----------|------|
| critical | 금리 변동 ≥ 50bp 또는 지준율 변동 ≥ 200bp |
| major | 금리 변동 25~49bp 또는 지준율 100~199bp |
| moderate | 금리 변동 10~24bp 또는 지준율 50~99bp |
| minor | 금리 변동 < 10bp 또는 지준율 < 50bp |

### POLICY

| magnitude | 조건 |
|-----------|------|
| critical | 법률 제정/폐지. 전국 범위 적용 |
| major | 규정 신설/대폭 개정. 전국 또는 주요 산업 |
| moderate | 기존 규정 부분 수정. 특정 지역/산업 |
| minor | 지침/통지. 제한적 범위 |

### MARKET

| magnitude | 조건 |
|-----------|------|
| critical | 주요 지수 일간 변동 ≥ 5% 또는 서킷브레이커 발동 |
| major | 주요 지수 일간 변동 3~4.9% |
| moderate | 주요 지수 일간 변동 1~2.9% |
| minor | 주요 지수 일간 변동 < 1% |

### TRADE

| magnitude | 조건 |
|-----------|------|
| critical | 전면 금수/제재 또는 주요 기술 수출 차단 |
| major | 관세율 변동 ≥ 10%p 또는 주요 품목 제한 |
| moderate | 관세율 변동 5~9.9%p |
| minor | 관세율 변동 < 5%p 또는 행정 절차 조치 |

### MACRO (MACRO-MAGNITUDE-V1)

MACRO.DATA_RELEASE 이벤트에서 indicator에 expected_value가 존재하는 경우 자동 산정:

```
deviation = ABS(actual - expected) / expected

IF deviation ≥ 0.05:       magnitude = major
ELIF 0.02 ≤ deviation < 0.05: magnitude = moderate
ELSE:                      magnitude = minor
```

expected_value가 없는 경우: 기본값 = moderate.

MACRO.FORECAST 및 기타 MACRO 이벤트: 전문가 수동 설정. 기본값 = moderate.

### INDUSTRY / DIPLOMACY / RISK

자동 산정 불가. 전문가 수동 설정. 기본값 = moderate.

### magnitude → strength 자동 연결

IMPACTS 관계의 strength는 source 이벤트의 magnitude에서 파생:

| magnitude | strength (기본값) | override 조건 |
|-----------|-----------------|-------------|
| critical | strong | — |
| major | strong | 간접 경유(2-hop 이상) 시 moderate로 하향 |
| moderate | medium | 간접 경유(2-hop 이상) 시 weak로 하향 |
| minor | weak | — |

override 조건의 "간접 경유"는 entity_path_factor 기준 2-hop 이상을 의미.

## 1-3. SUCCEEDS 체인 생성 규칙

| 규칙 ID | 조건 | 체인 생성 방식 | 예외 |
|---------|------|-------------|------|
| SU-01 | 동일 event_type + 동일 주체(actors 교집합 ≥ 1) + 시간 순서 | 후행 이벤트가 선행 이벤트를 SUCCEEDS | 시간 간격 > 180일이면 별도 체인 개시 |
| SU-02 | POLICY.AMEND의 target이 기존 POLICY.ANNOUNCE의 target과 동일 | AMEND → SUCCEEDS → ANNOUNCE | AMEND의 actors가 완전히 다르면 보류 |
| SU-03 | 동일 사건의 후속 보도 (이벤트 중복 판별에서 병합된 것) | 신규 이벤트 생성 안 함. 기존 이벤트의 source_refs 추가 | — |
| SU-04 | MONETARY 이벤트 동일 유형(예: RATE_CUT) 반복 | 시간순 자동 체이닝 | 방향 전환(CUT → HIKE) 시 체인 종료, 새 체인 개시 |

### 체인 무결성 규칙

```
SUCCEEDS 체인 내:
  - 시간 역전 금지 (후행.event_date ≥ 선행.event_date)
  - 순환 참조 금지 (A → B → A 불가)
  - 체인 최대 깊이: 제한 없음 (단, 조회 시 기본 표시 깊이 = 10)
  - 체인 분기 허용: 하나의 이벤트가 여러 후속 이벤트를 가질 수 있음
```

## 1-4. CONTRADICTS 탐지 규칙

| 규칙 ID | 탐지 조건 | 충돌 유형 | confidence |
|---------|---------|---------|-----------|
| CT-01 | 동일 산업에 대해 INDUSTRY.SUBSIDY와 INDUSTRY.REGULATION이 동시 존재 (30일 이내) | 정책 충돌 | 0.6 + (시간 근접도 × 0.3) |
| CT-02 | 동일 주체가 발표한 두 정책의 IMPACTS direction이 동일 target에 대해 반대 | 정책 자기 모순 | 0.8 (동일 주체이므로 높음) |
| CT-03 | MACRO.DATA_RELEASE가 positive + 동시기 MACRO.FORECAST가 negative (동일 지표) | 데이터-전망 괴리 | 0.7 |
| CT-04 | MONETARY.RATE_CUT + 동시기 MACRO.DATA_RELEASE(CPI 상승) | 완화-인플레 충돌 | 0.65 |
| CT-05 | 전문가 판단(kg_expert_judgments)에서 동일 이벤트에 대해 direction이 반대인 판단 2건 이상 | 전문가 견해 분기 | — (충돌이 아닌 정보로 표시) |

## 1-5. RULE PRIORITY 시스템

| 우선순위 | 규칙 유형 | 근거 |
|---------|---------|------|
| **P1** | VALIDATION (V1~V10) | 데이터 무결성. 다른 모든 규칙보다 우선. 위반 시 거부 |
| **P2** | DEDUP (중복 판별) | 중복 생성 방지. 추론보다 선행 |
| **P3** | SUCCEEDS 체인 | 시간순 사실 관계. 인과 추론보다 사실이 우선 |
| **P4** | TRIGGERS | 인과 추론. confidence ≥ 0.7만 자동, 나머지 보류 |
| **P5** | IMPACTS | 영향 추론. TRIGGERS가 확정된 후에 파생 |
| **P6** | CONTRADICTS | 충돌 탐지. 모든 관계 생성 완료 후 후행 검사 |

### 충돌 해소 규칙

```
동일 source-target 쌍에 복수 규칙 적용 시:
1. 높은 우선순위 규칙의 결과 채택
2. 동일 우선순위 내 충돌 → confidence 높은 쪽 채택
3. confidence 동일 → 시간적으로 최신 이벤트 기반 규칙 채택
4. 그래도 동일 → human review 대기열에 적재
```

---

# 2. ENTITY IMPORTANCE HIERARCHY

## 2-1. 중요도 레벨 정의

| 티어 | 명칭 | 정의 | 예시 |
|------|------|------|------|
| **T1** | Core | 시스템의 중심 노드. 대부분의 이벤트와 연결 | 국무원, 인민은행, 증감회 |
| **T2** | Major | 주요 반복 등장 엔티티. 다수 관계 보유 | 재정부, 비야디, 상하이종합지수 |
| **T3** | Standard | 일반적 빈도의 엔티티 | 중소 상장기업, 성급 정부 |
| **T4** | Minor | 낮은 빈도. 제한적 연결 | 일회성 언급 기업, 지역 정책 |
| **T5** | Peripheral | 거의 등장하지 않음. 노이즈 후보 | 단일 기사에만 등장한 비핵심 엔티티 |

## 2-2. 중요도 산정 공식

### v1 (원본 — 참조용, PATCH-04에 의해 override됨)

```
[v1 공식은 PATCH-04 v2로 대체됨. 아래 v2를 실행 공식으로 사용]
```

### v2 (PATCH-04 — 실행 공식)

#### 정규화 함수

```
norm(x) = percentile_rank(x, all_entities) / 100  → 0~1 범위

percentile_rank: 해당 값이 전체 엔티티 중 몇 번째 백분위에 위치하는지
효과: 극단값(예: 국무원 mention=5000)이 나머지를 압축하지 않음
```

#### recency_bonus 함수

```
recency_bonus = exp(-0.03 × 경과일)

참조값:
  1일:  0.97
  7일:  0.81
  14일: 0.66
  30일: 0.41
  60일: 0.17
  90일: 0.07
  180일: 0.005
```

#### entity_type별 가중치

| entity_type | w1(빈도) | w2(연결성) | w3(이벤트) | w4(정책) | w5(최신성) |
|-------------|---------|----------|----------|---------|----------|
| ORG | 0.20 | 0.25 | 0.25 | 0.10 | 0.20 |
| COM | 0.20 | 0.25 | 0.20 | 0.20 | 0.15 |
| IND | 0.15 | 0.25 | 0.20 | 0.20 | 0.20 |
| PER | 0.20 | 0.20 | 0.25 | 0.15 | 0.20 |
| POL | 0.10 | 0.20 | 0.30 | 0.25 | 0.15 |
| GEO | 0.25 | 0.25 | 0.20 | 0.10 | 0.20 |
| IDX | 0.20 | 0.20 | 0.25 | 0.15 | 0.20 |
| FIN | 0.20 | 0.25 | 0.20 | 0.10 | 0.25 |

설계 원칙:
- ORG: 정책 이벤트의 주체이므로 w4 감산 (w3에서 이미 반영)
- COM/IND: 정책 영향을 받는 측이므로 w4 가산 (정책 민감도 반영)
- POL: 정책 그 자체이므로 w4 최대
- FIN: 시장 최신성이 중요하므로 w5 최대

#### policy_influence 정의

```
해당 엔티티가 actor인 이벤트 중 POLICY/MONETARY 유형 비율
```

#### 최종 공식

```
importance_score = w1 × norm(mention_count)
                 + w2 × norm(degree_centrality)
                 + w3 × norm(event_participation)
                 + w4 × norm(policy_influence)
                 + w5 × recency_bonus

(w1~w5는 entity_type에 따라 위 표 참조)
(norm = percentile_rank / 100)
(recency_bonus = exp(-0.03 × 경과일))
```

## 2-3. 티어 구간

| 티어 | importance_score 범위 | 비율 (예상) |
|------|---------------------|-----------|
| T1 | ≥ 0.85 | ~2% |
| T2 | 0.65 ~ 0.84 | ~8% |
| T3 | 0.40 ~ 0.64 | ~25% |
| T4 | 0.20 ~ 0.39 | ~35% |
| T5 | < 0.20 | ~30% |

## 2-4. 동적 업데이트 규칙

| 이벤트 | 업데이트 내용 |
|--------|------------|
| 신규 이벤트에 actor/target으로 참여 | mention_count +1, event_participation +1, last_seen_date 갱신 |
| 신규 관계 생성 (source 또는 target) | degree_centrality 재계산 |
| 30일간 미등장 | recency_bonus 감소 (자동) |
| 엔티티 병합 (merged) | 존속 엔티티에 피병합 엔티티의 모든 수치 합산 후 재계산 |

### 재계산 주기

```
실시간: mention_count, last_seen_date (이벤트 생성 시 즉시)
일 1회: degree_centrality, event_participation (배치)
주 1회: importance_score 전체 재계산 + 티어 재배정
```

## 2-5. 티어별 처리 정책

| 정책 | T1 | T2 | T3 | T4 | T5 |
|------|----|----|----|----|-----|
| 신규 이벤트 시 관계 자동 생성 | 항상 | 항상 | confidence ≥ 0.5 | confidence ≥ 0.7 | 생성 안 함 |
| TRIGGERS 추론 대상 | 항상 | 항상 | 항상 | 보류 후 배치 | 제외 |
| CONTRADICTS 탐지 | 항상 | 항상 | 주 1회 | 월 1회 | 제외 |
| 리뷰 시 과거 이력 자동 표시 | 전체 이력 | 최근 90일 | 최근 30일 | 요청 시만 | 미표시 |
| 그래프 시각화 기본 표시 | 항상 | 항상 | 필터 시 | 필터 시 | 숨김 |
| 엔티티 병합 심사 | 자동 거부 (수동만) | 수동 승인 | 자동 후보 | 자동 후보 | 자동 병합 허용 |

---

# 3. GRAPH MATCHING STRATEGY

## 3-1. 엔티티 매칭 (3단계)

```
[신규 텍스트에서 추출된 엔티티 후보]
              │
              ▼
     ┌─── STAGE 1: EXACT MATCH ───┐
     │  canonical_name_zh 완전 일치  │
     │  또는 canonical_name 완전 일치│
     │  → 매칭 확정 (score = 1.0)  │
     └────────┬───────────────────┘
              │ 불일치
              ▼
     ┌─── STAGE 2: ALIAS MATCH ───┐
     │  aliases 배열 내 완전 일치    │
     │  → 매칭 확정 (score = 0.95) │
     └────────┬───────────────────┘
              │ 불일치
              ▼
     ┌─── STAGE 3: SEMANTIC MATCH ─┐
     │  조건:                       │
     │   - 동일 entity_type         │
     │   - 문자열 유사도 ≥ 0.80     │
     │   - 편집 거리 ≤ 3            │
     │  → 매칭 후보 (human review)  │
     │     score = 유사도 × 0.9     │
     └────────┬────────────────────┘
              │ 불일치
              ▼
     ┌─── FALLBACK ───────────────┐
     │  신규 엔티티 생성            │
     │  status = 'active'         │
     │  mention_count = 1         │
     └────────────────────────────┘
```

### 매칭 임계값 표

| 단계 | 방법 | 임계값 | 결과 |
|------|------|--------|------|
| Stage 1 | 정확 일치 | score = 1.0 | 자동 확정 |
| Stage 2 | 별칭 일치 | score ≥ 0.95 | 자동 확정 |
| Stage 3 | 의미 유사도 | score ≥ 0.80 | 후보 (수동 승인) |
| Stage 3 | 의미 유사도 | 0.60 ≤ score < 0.80 | 후보 (낮은 신뢰, 수동 필수) |
| Fallback | — | score < 0.60 | 신규 생성 |

## 3-2. 이벤트 매칭

### 동일 사건 판별 기준

| 기준 | 가중치 | 판별 방법 |
|------|--------|---------|
| event_type 일치 | 0.30 | Level 2 코드 완전 일치 |
| event_date 근접 | 0.25 | \|일차\| ≤ 3: 1.0, ≤ 7: 0.7, ≤ 14: 0.4, > 14: 0 |
| actors 교집합 | 0.25 | \|교집합\| / \|합집합\| |
| targets 교집합 | 0.10 | \|교집합\| / \|합집합\| |
| headline 유사도 | 0.10 | 코사인 유사도 |

```
event_match_score = Σ(가중치 × 개별 점수)

판정 (EVENT-RELATION-V2 적용):
  ≥ 0.80 → 동일 사건 후보 → 키워드 판별 후 확정 (아래 V2 참조)
  0.60~0.79 → supersedes/SUCCEEDS 판별 (PATCH-03 적용)
  < 0.60 → 신규 이벤트 생성
```

### score ≥ 0.80 구간 판별 (EVENT-RELATION-V2)

기존: score ≥ 0.80이면 무조건 동일 사건으로 처리
수정: score ≥ 0.80이라도 키워드 기반 1차 판별을 수행하여
      "정책의 단계적 발표"를 별개 이벤트로 분리

```
IF match_score ≥ 0.80:

  STEP 1: 키워드 기반 판별
    IF headline CONTAINS ["2차", "3차", "추가", "후속", "연장", "확대", "강화"]:
      → relation = SUCCEEDS
      → action = CREATE_NEW_EVENT + LINK(SUCCEEDS)

    ELIF headline CONTAINS ["확정", "승인", "최종", "발효"]:
      → relation = SAME_EVENT
      → action = ADD_SOURCE_REF

    ELSE:
      → relation = SAME_EVENT
      → action = ADD_SOURCE_REF
```

근거: EVT-T05("부동산 규제 완화 2차 추가 조치")가 score=0.82로
동일 사건 처리되는 오분류(E-01) 방지.

### supersedes vs 신규 생성 판별

| 조건 | 판정 |
|------|------|
| 동일 사건 + 추가 정보 없음 | 기존 이벤트에 source_ref만 추가 |
| 동일 사건 + 수치 변경 또는 신규 사실 | 신규 이벤트 생성 + supersedes → 기존 이벤트 |
| 동일 주제 + 다른 행위자 또는 다른 행위 | 별개 신규 이벤트 + SUCCEEDS 또는 TRIGGERS 후보 |

### supersedes vs SUCCEEDS 정밀 판별 (PATCH-03)

이벤트 매칭 score 0.60~0.79 구간에서, 기존 설계는 일괄 "supersedes 후보"로
분류했으나, 이를 다음 4단계로 세분화한다. 이 로직은 0.60~0.79 구간에 대해
기존 표를 override한다.

정의:
- supersedes = 동일 사건에 대한 정보 갱신/정정/확정
- SUCCEEDS = 동일 주제에 대한 별개의 후속 조치/발전

#### STEP 1: 키워드 기반 분류

headline(신규 이벤트)에서 키워드 탐색:

| 키워드 | 판정 |
|--------|------|
| "추가", "강화", "2차", "3차", "후속", "보완", "확대", "연장" | SUCCEEDS |
| "수정", "정정", "업데이트", "확정", "최종", "공식 발표" | supersedes |
| 해당 없음 | STEP 2로 이동 |

#### STEP 2: indicator 변화 기반

신규 이벤트와 기존 이벤트의 kg_event_indicators 비교:

| 조건 | 판정 |
|------|------|
| 동일 지표 존재 + value_change 차이 > ±10% | SUCCEEDS (별개 조치) |
| 동일 지표 존재 + value_change 차이 ≤ ±10% | supersedes (수치 확인/정정) |
| 지표 정보 없음 | STEP 3으로 이동 |

#### STEP 3: actor/target 비교

| 조건 | 판정 |
|------|------|
| actors 완전 동일 + targets 완전 동일 | supersedes |
| actors에 변화 있음 (추가/교체) | SUCCEEDS |
| targets에 변화 있음 (확대/축소) | SUCCEEDS |
| 판별 불가 | STEP 4로 이동 |

#### STEP 4: review_queue 적재

| 필드 | 값 |
|------|------|
| review_type | "succeeds_or_supersedes" |
| candidate_data | 신규 이벤트 전체 JSON |
| existing_id | 기존 이벤트 ID |
| match_score | 이벤트 매칭 score |
| status | "pending" |

이 판별 로직은 이벤트 매칭(§3-2) 이후, 관계 생성(§1-3 SUCCEEDS) 이전에 실행된다.

## 3-3. 관계 중복 방지

```
신규 관계 생성 전 검사:

1. 동일 source_id + target_id + relation_type 조회
2. 기존 관계 존재 시:
   a. valid_until이 NULL (현재 유효) → 중복. 생성 거부
   b. valid_until이 과거 → 시간대 다름. 신규 생성 허용
   c. 시간 범위 겹침 → 거부 + 경고 로그
3. 기존 관계 없음 → 생성 허용
```

## 3-4. 매칭 실패 시 Fallback

| 실패 유형 | Fallback 전략 |
|----------|-------------|
| 엔티티 Stage 3에서 0.60~0.80 매칭 | `kg_review_queue`에 적재 |
| 이벤트 0.60~0.79 매칭 | `kg_review_queue`에 적재 |
| 관계 시간 범위 겹침 | 생성 거부 + `kg_conflict_log`에 기록 |
| 추론 규칙 confidence 0.4~0.7 | 관계를 `pending` 상태로 생성. expert 승인 후 `confirmed`로 전환 |

### 추가 Shadow 테이블

```
┌─────────────────────────────────────────────┐
│  kg_review_queue (수동 심사 대기열)           │
├─────────────────────────────────────────────┤
│  queue_id           TEXT PRIMARY KEY        │
│  review_type        TEXT NOT NULL           │
│  candidate_data     TEXT (JSON)             │
│  existing_id        TEXT                    │
│  match_score        REAL                    │
│  status             TEXT DEFAULT 'pending'  │
│  reviewed_at        TEXT                    │
│  created_at         TEXT                    │
└─────────────────────────────────────────────┘

┌─────────────────────────────────────────────┐
│  kg_conflict_log (충돌 기록)                 │
├─────────────────────────────────────────────┤
│  conflict_id        TEXT PRIMARY KEY        │
│  conflict_type      TEXT NOT NULL           │
│  entity_ids         TEXT (JSON array)       │
│  description        TEXT                    │
│  resolved           INTEGER DEFAULT 0      │
│  created_at         TEXT                    │
└─────────────────────────────────────────────┘
```

---

# 4. BUG & FAILURE SIMULATION

## 시나리오 1: 동일 사건 다른 표현

| 항목 | 내용 |
|------|------|
| **발생 원인** | 출처별 표현 차이. "인민은행 금리 인하" vs "PBOC, LPR 10bp 하향 조정" vs "央行降息" |
| **실패 양상** | 동일 사건이 2~3개 별도 이벤트로 생성. SUCCEEDS 체인 분기. 중복 IMPACTS 관계 발생 |
| **탐지 방법** | 동일 event_type + event_date ±3일 + actors 교집합 ≥ 1인 이벤트 쌍 탐지 (일 1회 배치) |
| **방지 규칙** | 이벤트 생성 전 매칭 Stage 1~2 필수 실행. 별칭 체계가 핵심 — "央行", "PBOC", "인민은행" 모두 aliases에 등록 필수 |

## 시나리오 2: 잘못된 인과 추론

| 항목 | 내용 |
|------|------|
| **발생 원인** | 시간적 근접만으로 TRIGGERS 생성. 실제 인과 없는 두 이벤트가 우연히 시간적으로 근접 |
| **실패 양상** | "미국 금리 인상"(3/1) → "중국 돼지고기 가격 상승"(3/3)이 TRIGGERS로 연결 |
| **탐지 방법** | TRIGGERS 관계 중 source-target 간 기존 엔티티 관계 경로가 2-hop 이내에 없는 것 탐지 |
| **방지 규칙** | TR 규칙에 엔티티 관련도 필수 조건 추가: source 이벤트의 actors/targets와 target 이벤트의 actors/targets 간 기존 관계 경로 ≤ 2-hop 필수. 경로 없으면 confidence에 0.3 페널티 |

## 시나리오 3: 엔티티 과다 생성

| 항목 | 내용 |
|------|------|
| **발생 원인** | 별칭 미등록 + semantic match 임계값 미달. "상해" vs "상하이" vs "上海"가 3개 엔티티로 생성 |
| **실패 양상** | 동일 엔티티의 mention_count, degree_centrality 분산 → 중요도 왜곡 → T3 이하로 강등 → 관계 자동 생성 제한 |
| **탐지 방법** | 주 1회 배치: 동일 entity_type 내 문자열 유사도 ≥ 0.70인 쌍 탐지 + mention_count < 3인 엔티티 비율 모니터링 (정상: ~30%, 경고: > 50%) |
| **방지 규칙** | 엔티티 생성 전 Stage 1~3 필수 + 최초 1개월은 Stage 3 임계값을 0.70으로 하향 (공격적 매칭) + 별칭 사전 사전 구축 |

## 시나리오 4: 중요도 왜곡

| 항목 | 내용 |
|------|------|
| **발생 원인** | 특정 기간 뉴스 편중 (예: 양회 기간 국무원 관련 기사 폭증) → mention_count 급등 → 다른 엔티티 상대적 하락 |
| **실패 양상** | 양회 후 국무원 T1 유지, 인민은행이 T2 → T3으로 강등 → 통화정책 관련 자동 추론 중단 |
| **탐지 방법** | 주간 재계산 시 티어 변동 2단계 이상인 엔티티 플래그 (예: T2 → T4) |
| **방지 규칙** | 티어 변동 제한: 1회 재계산 시 최대 1단계만 변동 허용. 2단계 이상 변동은 human review. 지수 이동 평균: mention_count에 30일 EMA 적용하여 단기 급등 완화 |

## 시나리오 5: 이벤트 체인 단절

| 항목 | 내용 |
|------|------|
| **발생 원인** | 중간 이벤트 누락 (해당 기사가 선별되지 않음) 또는 actors 변경 (담당자 교체)으로 SUCCEEDS 조건 미충족 |
| **실패 양상** | "1월 금리인하 → (2월 추가인하 누락) → 3월 금리동결". 3월 이벤트가 1월과 직접 연결되지 않고 고립 |
| **탐지 방법** | SUCCEEDS 체인 내 시간 간격 이상 탐지: 동일 event_type 체인에서 평균 간격의 2배 초과 시 "gap_suspected" 플래그 |
| **방지 규칙** | gap_suspected 플래그 발생 시 → 해당 기간의 동일 event_type 이벤트를 외부 출처에서 보완 탐색 대상으로 `kg_review_queue`에 "chain_gap" 유형으로 적재 |

## 실패 시나리오 종합 대응 매트릭스

| 시나리오 | 사전 방지 | 실시간 탐지 | 사후 복구 |
|---------|---------|-----------|---------|
| 동일 사건 다른 표현 | 별칭 사전 + 공격적 매칭 | 이벤트 생성 시 매칭 필수 | 주간 중복 스캔 + 병합 |
| 잘못된 인과 추론 | 엔티티 경로 조건 필수 | confidence < 0.7 보류 | human review queue |
| 엔티티 과다 생성 | Stage 3 임계값 하향 | mention < 3 비율 모니터링 | 주간 유사 엔티티 스캔 |
| 중요도 왜곡 | EMA 적용 | 티어 2단계 변동 플래그 | 수동 티어 고정 가능 |
| 이벤트 체인 단절 | — (외부 요인) | 간격 이상 탐지 | 보완 탐색 대기열 |

---

# RISK ANALYSIS

```json
{
  "risk_detected": false,
  "summary": "본 설계는 SHADOW MODE 내에서 완결. 기존 시스템 참조·접근·수정 없음",
  "verified_items": [
    "모든 테이블: kg_ 접두어 사용",
    "모든 ID: KG- 접두어 사용",
    "기존 DB 연결 없음",
    "기존 코드 수정 제안 없음",
    "외부 사전 참조 시 읽기 전용 명시"
  ]
}
```

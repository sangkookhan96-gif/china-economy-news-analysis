# CEKG Base Design — China Economy Knowledge Graph

**MODE: PRODUCTION (v2)**
**배포일: 2026-03-21**
**버전: CEKG-INFERENCE-ENGINE V2**

---

## 1. DESIGN SUMMARY

| 항목 | 정의 |
|------|------|
| **시스템 명칭** | China Economy Knowledge Graph (CEKG) |
| **설계 범위** | 엔티티·이벤트·관계의 구조 설계만 수행. 구현 없음 |
| **격리 수준** | 기존 시스템과 완전 분리. 테이블·ID·경로 모두 독립 |
| **가상 경로** | `/kg_shadow/` |
| **테이블 접두어** | `kg_` |
| **ID 체계** | `KG-` 접두어 + 유형코드 + 타임스탬프 해시 (기존 정수 ID와 충돌 불가) |

---

## 2. ENTITY STANDARD

### 2-1. 엔티티 유형 체계

| 유형코드 | 유형 | 정의 | 예시 |
|---------|------|------|------|
| `ORG` | 기관 | 정부기관, 중앙은행, 규제기관, 국제기구 | 국무원, 인민은행, 증감회 |
| `COM` | 기업 | 상장·비상장 기업, 기업 그룹 | 비야디, 화웨이, 알리바바 |
| `PER` | 인물 | 정책 결정자, 기업 경영자 | 리창 총리, 이강 행장 |
| `POL` | 정책 | 법률, 규정, 지침, 통지 | 부동산 신정책, LPR 조정 |
| `IND` | 산업 | 산업 분류 | 반도체, 신에너지차, 부동산 |
| `GEO` | 지역 | 국가, 성, 도시, 경제특구 | 상하이, 광둥성, 하이난 |
| `IDX` | 지표 | 거시경제 수치, 지수 | CPI, PMI, GDP 성장률, 위안 환율 |
| `FIN` | 금융상품 | 주식, 채권, 펀드, 통화 | 상하이종합지수, 10년물 국채 |

### 2-2. 엔티티 표준 속성

```
kg_entity:
  kg_entity_id    : KG-ENT-{type}-{hash8}    # 예: KG-ENT-ORG-a3f7c210
  canonical_name  : 표준 명칭 (한국어)
  canonical_name_zh : 표준 명칭 (중국어 원문)
  aliases         : 별칭 목록                  # ["인민은행", "PBOC", "央行", "중국인민은행"]
  entity_type     : 유형코드
  description     : 1줄 정의
  first_seen_date : 최초 등장일
  last_seen_date  : 최근 등장일
  mention_count   : 누적 언급 횟수
  status          : active | merged | deprecated
  merged_into     : 병합 대상 ID (중복 엔티티 통합 시)
```

### 2-3. 엔티티 표준화 규칙

| 규칙 | 내용 |
|------|------|
| **명칭 정규화** | 중국어 공식 명칭 → 한국어 표준 표기 1:1 매핑. "中国人民银行" → "중국인민은행" |
| **별칭 통합** | 동일 엔티티의 모든 표기를 aliases에 등록. 신규 표기 발견 시 자동 후보 제시, 수동 승인 |
| **계층 관계** | ORG, GEO는 상위-하위 관계 허용. "국무원 → 재정부 → 세무총국" |
| **시간 한정** | 인물-직책 관계는 반드시 임기 시작/종료일 포함 |
| **교차 참조 금지** | 기존 시스템의 ID를 엔티티 속성으로 저장하지 않음. 향후 연결 시 별도 매핑 테이블 사용 |

---

## 3. EVENT TAXONOMY

### 3-1. 이벤트 유형 체계

| Level 1 | Level 2 | 정의 |
|---------|---------|------|
| **POLICY** | POLICY.ANNOUNCE | 정책·규정 발표 |
| | POLICY.IMPLEMENT | 시행·적용 개시 |
| | POLICY.AMEND | 기존 정책 수정 |
| | POLICY.REPEAL | 폐지·철회 |
| **MONETARY** | MONETARY.RATE_CUT | 금리 인하 |
| | MONETARY.RATE_HIKE | 금리 인상 |
| | MONETARY.RRR_CUT | 지급준비율 인하 |
| | MONETARY.LIQUIDITY | 유동성 공급·회수 |
| **MARKET** | MARKET.IPO | 상장 |
| | MARKET.EARNINGS | 실적 발표 |
| | MARKET.M_AND_A | 인수·합병 |
| | MARKET.DEFAULT | 채무불이행 |
| | MARKET.PRICE_MOVE | 주요 가격 변동 |
| **TRADE** | TRADE.TARIFF | 관세 부과·변경 |
| | TRADE.SANCTION | 제재·수출통제 |
| | TRADE.AGREEMENT | 무역 협정 |
| **MACRO** | MACRO.DATA_RELEASE | 경제지표 발표 |
| | MACRO.FORECAST | 전망·예측 발표 |
| **INDUSTRY** | INDUSTRY.REGULATION | 산업 규제 |
| | INDUSTRY.SUBSIDY | 보조금·지원 |
| | INDUSTRY.TECH_BREAK | 기술 돌파 |
| **DIPLOMACY** | DIPLOMACY.SUMMIT | 정상회담 |
| | DIPLOMACY.TENSION | 외교 갈등 |
| **RISK** | RISK.CRISIS | 위기 사건 |
| | RISK.INVESTIGATION | 조사·단속 |

### 3-2. 이벤트 표준 속성

```
kg_event:
  kg_event_id     : KG-EVT-{type_l2}-{hash8}  # 예: KG-EVT-RATE_CUT-b2e9f411
  event_type      : Level2 코드
  headline        : 이벤트 요약 (1문장)
  event_date      : 발생일 (정확일 또는 추정)
  date_precision  : exact | month | quarter | year
  actors          : [kg_entity_id 목록]         # 주체
  targets         : [kg_entity_id 목록]         # 대상
  indicators      : [{지표ID, 변동값, 변동방향}] # 수치 변화
  magnitude       : minor | moderate | major | critical
  source_refs     : [출처 참조 목록]             # 기사 참조 (외부 키 아님, 메타데이터)
  expert_judgment : {방향, 강도, 확신도, 시간범위} # 구조화된 전문가 판단
  supersedes      : 이전 이벤트 ID              # 동일 사건 업데이트 시
```

### 3-3. magnitude 산정 기준 (PATCH-02 연동)

magnitude는 이벤트 생성 시 아래 기준으로 자동 산정. 자동 산정 불가 시 기본값 moderate.

| 유형 | critical | major | moderate | minor |
|------|---------|-------|----------|-------|
| MONETARY | ≥ 50bp (금리) 또는 ≥ 200bp (지준율) | 25~49bp / 100~199bp | 10~24bp / 50~99bp | < 10bp / < 50bp |
| POLICY | 법률 제정/폐지, 전국 범위 | 규정 신설/대폭 개정 | 부분 수정, 특정 지역/산업 | 지침/통지, 제한적 범위 |
| MARKET | 지수 일간 ≥ 5% 또는 서킷브레이커 | 3~4.9% | 1~2.9% | < 1% |
| TRADE | 전면 금수/제재 | 관세 ≥ 10%p, 주요 품목 제한 | 관세 5~9.9%p | < 5%p, 행정 조치 |
| MACRO.DATA_RELEASE | deviation ≥ 5% | deviation ≥ 5% | 2~5% | < 2% |
| 기타 | 수동 설정 | 수동 설정 | **기본값** | 수동 설정 |

MACRO.DATA_RELEASE deviation 산정: `ABS(actual - expected) / expected`. expected_value 없으면 기본값 moderate.

magnitude → strength 자동 매핑:

| magnitude | strength | override |
|-----------|----------|----------|
| critical | strong | — |
| major | strong | 간접 경유(2-hop) 시 medium |
| moderate | medium | 간접 경유(2-hop) 시 weak |
| minor | weak | — |

---

## 4. RELATION ONTOLOGY

### 4-1. 관계 유형

| 관계코드 | 방향 | 정의 | 예시 |
|---------|------|------|------|
| `REGULATES` | A → B | A가 B를 규제 | 증감회 → 부동산 산업 |
| `ANNOUNCES` | A → B | A가 B를 발표 | 국무원 → 신에너지차 보조금 정책 |
| `TRIGGERS` | A → B | A 사건이 B 사건을 촉발 | 금리 인하 → 위안 약세 |
| `IMPACTS` | A → B | A가 B에 영향 | 부동산 규제 → 건설 산업 |
| `BELONGS_TO` | A → B | A가 B에 소속 | 재정부 → 국무원 |
| `LOCATED_IN` | A → B | A가 B에 위치 | 비야디 → 선전 |
| `COMPETES_WITH` | A ↔ B | 경쟁 관계 | 비야디 ↔ 테슬라 |
| `SUPPLIES_TO` | A → B | 공급 관계 | CATL → 비야디 |
| `INVESTS_IN` | A → B | 투자 관계 | 국부펀드 → AI 산업 |
| `SUCCEEDS` | A → B | A가 B의 후속 사건 | 3월 금리인하 → 4월 추가인하 |
| `CONTRADICTS` | A ↔ B | 상충 관계 | 경기부양 ↔ 물가안정 |
| `MEASURES` | A → B | A 지표가 B를 측정 | PMI → 제조업 경기 |

### 4-2. 관계 표준 속성

```
kg_relation:
  kg_relation_id  : KG-REL-{type}-{hash8}
  relation_type   : 관계코드
  source_entity   : kg_entity_id 또는 kg_event_id
  target_entity   : kg_entity_id 또는 kg_event_id
  direction       : positive | negative | neutral | mixed
  strength        : weak | moderate | strong
  confidence      : high | medium | low
  valid_from      : 유효 시작일
  valid_until      : 유효 종료일 (NULL = 현재 유효)
  source_refs     : [출처 참조 목록]
  created_by      : system | expert
  note            : 부가 설명 (선택)
```

### 4-3. 관계 제약 규칙

| 규칙 | 내용 |
|------|------|
| **유형 제약** | `REGULATES`: source는 반드시 ORG, target은 ORG·IND·COM만 허용 |
| **시간 필수** | `TRIGGERS`, `SUCCEEDS`: valid_from 필수 |
| **방향 필수** | `IMPACTS`: direction 필수 (neutral 허용하되 사유 기재) |
| **자기 참조 금지** | source = target 금지 |
| **중복 관계** | 동일 source-target-type 조합에서 시간 범위가 겹치면 거부 |

---

## 5. ID & DEDUP LOGIC

### 5-1. ID 생성 규칙

| 대상 | 형식 | 생성 로직 |
|------|------|---------|
| 엔티티 | `KG-ENT-{TYPE}-{hash8}` | SHA256(canonical_name_zh + entity_type)[:8] |
| 이벤트 | `KG-EVT-{L2CODE}-{hash8}` | SHA256(event_type + event_date + actor_ids_sorted)[:8] |
| 관계 | `KG-REL-{TYPE}-{hash8}` | SHA256(source_id + target_id + relation_type + valid_from)[:8] |

### 5-2. 중복 판별 로직

**엔티티 중복:**

```
판별 순서:
1. canonical_name_zh 완전 일치 → 동일 엔티티
2. aliases 교집합 존재 → 병합 후보 (수동 승인)
3. 유사도 ≥ 0.85 + 동일 entity_type → 병합 후보 (수동 승인)
4. 그 외 → 신규 엔티티

병합 시:
- 피병합 엔티티의 status → "merged"
- merged_into → 존속 엔티티 ID
- 피병합 aliases를 존속 엔티티에 추가
- 기존 관계의 source/target을 존속 ID로 재지정
```

**이벤트 중복:**

```
판별 순서:
1. 동일 event_type + 동일 event_date + 동일 actors → 동일 이벤트
2. 동일 event_type + event_date ±3일 + actors 교집합 ≥ 50% → 병합 후보
3. 그 외 → 신규 이벤트

동일 사건의 후속 보도:
- 신규 이벤트 생성하지 않음
- 기존 이벤트의 source_refs에 추가
- 정보 갱신 시 supersedes 체인 사용
```

---

## 6. VALIDATION RULES

| # | 규칙 | 검증 시점 | 실패 시 처리 |
|---|------|---------|-----------|
| V1 | 모든 엔티티는 canonical_name + canonical_name_zh + entity_type 필수 | 엔티티 생성 시 | 거부 |
| V2 | 모든 이벤트는 최소 1개 actor 필수 | 이벤트 생성 시 | 거부 |
| V3 | 관계의 source/target은 반드시 존재하는 엔티티/이벤트 ID | 관계 생성 시 | 거부 |
| V4 | valid_from > valid_until이면 거부 | 관계 생성 시 | 거부 |
| V5 | 동일 엔티티 ID 중복 생성 시도 → 기존 엔티티 반환 | 엔티티 생성 시 | 기존 반환 |
| V6 | TRIGGERS 관계에서 target 이벤트 날짜 < source 이벤트 날짜 → 거부 | 관계 생성 시 | 거부 |
| V7 | expert_judgment의 direction은 enum 값만 허용 | 이벤트 저장 시 | 거부 |
| V8 | mention_count는 자동 계산 필드, 수동 수정 금지 | 항상 | 무시 |
| V9 | deprecated 엔티티를 관계의 source/target으로 사용 금지 | 관계 생성 시 | 거부 |
| V10 | 출처 없는 관계 생성 금지 (source_refs 최소 1개) | 관계 생성 시 | 거부 |

---

## 7. SHADOW SCHEMA (가상 DB 구조)

```
가상 경로: /kg_shadow/cekg.db
접두어: kg_
기존 시스템과 공유 없음
```

### 테이블 구조

```
┌─────────────────────────────────────────────┐
│  kg_entities                                │
├─────────────────────────────────────────────┤
│  kg_entity_id       TEXT PRIMARY KEY        │
│  canonical_name     TEXT NOT NULL           │
│  canonical_name_zh  TEXT NOT NULL           │
│  aliases            TEXT (JSON array)       │
│  entity_type        TEXT NOT NULL           │
│  parent_entity_id   TEXT (자기참조 FK)       │
│  description        TEXT                    │
│  first_seen_date    TEXT                    │
│  last_seen_date     TEXT                    │
│  mention_count      INTEGER DEFAULT 0      │
│  status             TEXT DEFAULT 'active'   │
│  merged_into        TEXT                    │
│  created_at         TEXT                    │
│  updated_at         TEXT                    │
└─────────────────────────────────────────────┘

┌─────────────────────────────────────────────┐
│  kg_events                                  │
├─────────────────────────────────────────────┤
│  kg_event_id        TEXT PRIMARY KEY        │
│  event_type         TEXT NOT NULL           │
│  headline           TEXT NOT NULL           │
│  event_date         TEXT NOT NULL           │
│  date_precision     TEXT DEFAULT 'exact'    │
│  magnitude          TEXT                    │
│  supersedes         TEXT                    │
│  source_refs        TEXT (JSON array)       │
│  created_at         TEXT                    │
│  updated_at         TEXT                    │
└─────────────────────────────────────────────┘

┌─────────────────────────────────────────────┐
│  kg_event_actors  (이벤트-엔티티 주체)       │
├─────────────────────────────────────────────┤
│  kg_event_id        TEXT NOT NULL (FK)      │
│  kg_entity_id       TEXT NOT NULL (FK)      │
│  role                TEXT DEFAULT 'actor'   │
│  PRIMARY KEY (kg_event_id, kg_entity_id)    │
└─────────────────────────────────────────────┘

┌─────────────────────────────────────────────┐
│  kg_event_targets  (이벤트-엔티티 대상)      │
├─────────────────────────────────────────────┤
│  kg_event_id        TEXT NOT NULL (FK)      │
│  kg_entity_id       TEXT NOT NULL (FK)      │
│  PRIMARY KEY (kg_event_id, kg_entity_id)    │
└─────────────────────────────────────────────┘

┌─────────────────────────────────────────────┐
│  kg_event_indicators  (이벤트 수치 변화)     │
├─────────────────────────────────────────────┤
│  kg_event_id        TEXT NOT NULL (FK)      │
│  kg_entity_id       TEXT NOT NULL (FK)      │
│  value_change       REAL                    │
│  direction          TEXT                    │
│  unit               TEXT                    │
│  PRIMARY KEY (kg_event_id, kg_entity_id)    │
└─────────────────────────────────────────────┘

┌─────────────────────────────────────────────┐
│  kg_expert_judgments  (구조화된 전문가 판단)  │
├─────────────────────────────────────────────┤
│  kg_judgment_id     TEXT PRIMARY KEY        │
│  kg_event_id        TEXT NOT NULL (FK)      │
│  impact_direction   TEXT                    │
│  impact_strength    TEXT                    │
│  confidence         TEXT                    │
│  time_horizon       TEXT                    │
│  reasoning          TEXT                    │
│  created_at         TEXT                    │
└─────────────────────────────────────────────┘

┌─────────────────────────────────────────────┐
│  kg_relations                               │
├─────────────────────────────────────────────┤
│  kg_relation_id     TEXT PRIMARY KEY        │
│  relation_type      TEXT NOT NULL           │
│  source_id          TEXT NOT NULL           │
│  source_kind        TEXT NOT NULL           │
│  target_id          TEXT NOT NULL           │
│  target_kind        TEXT NOT NULL           │
│  direction          TEXT                    │
│  strength           TEXT                    │
│  confidence         TEXT                    │
│  valid_from         TEXT                    │
│  valid_until        TEXT                    │
│  source_refs        TEXT (JSON array)       │
│  created_by         TEXT DEFAULT 'system'   │
│  note               TEXT                    │
│  created_at         TEXT                    │
└─────────────────────────────────────────────┘

┌─────────────────────────────────────────────┐
│  kg_entity_tags  (다차원 태깅)              │
├─────────────────────────────────────────────┤
│  kg_entity_id       TEXT NOT NULL (FK)      │
│  dimension          TEXT NOT NULL           │
│  tag_value          TEXT NOT NULL           │
│  PRIMARY KEY (kg_entity_id, dimension,      │
│               tag_value)                    │
└─────────────────────────────────────────────┘

┌─────────────────────────────────────────────┐
│  kg_source_map  (향후 비침투 연결용)         │
├─────────────────────────────────────────────┤
│  kg_id              TEXT NOT NULL           │
│  kg_kind            TEXT NOT NULL           │
│  external_system    TEXT NOT NULL           │
│  external_ref       TEXT NOT NULL           │
│  mapped_at          TEXT                    │
│  PRIMARY KEY (kg_id, external_system,       │
│               external_ref)                 │
└─────────────────────────────────────────────┘
```

### 테이블 관계도

```
kg_entities ──┬──< kg_event_actors >──── kg_events
              │                              │
              ├──< kg_event_targets >─────────┤
              │                              │
              ├──< kg_event_indicators >──────┤
              │                              │
              ├──< kg_entity_tags             ├──< kg_expert_judgments
              │                              │
              └──< kg_relations (source) >────┘
                   kg_relations (target) >────┘

kg_source_map ── 독립 매핑 (비침투 연결 전용)

kg_entities ←── (reactivation) ──→ kg_archive
                                     │
                                     └── 아카이브된 엔티티 보관
                                         aliases는 매칭 검색 유지
```

### kg_archive 테이블 (PATCH-05)

```
┌─────────────────────────────────────────────┐
│  kg_archive                                 │
├─────────────────────────────────────────────┤
│  kg_entity_id       TEXT PRIMARY KEY        │
│  canonical_name     TEXT NOT NULL           │
│  canonical_name_zh  TEXT NOT NULL           │
│  aliases            TEXT (JSON array)       │
│  entity_type        TEXT NOT NULL           │
│  parent_entity_id   TEXT                    │
│  description        TEXT                    │
│  first_seen_date    TEXT                    │
│  last_seen_date     TEXT                    │
│  mention_count      INTEGER                │
│  status             TEXT  ('archived')      │
│  merged_into        TEXT                    │
│  created_at         TEXT                    │
│  updated_at         TEXT                    │
│  archived_at        TEXT NOT NULL           │
│  archive_reason     TEXT NOT NULL           │
└─────────────────────────────────────────────┘
```

스키마는 kg_entities와 동일 + archived_at, archive_reason 추가.

### 아카이브 규칙

#### 아카이브 조건 (4가지 모두 충족 시)

1. importance_score < 0.10 (T5 하위)
2. last_seen_date > 180일 전
3. degree_centrality = 0 또는 모든 연결된 관계의 상대방이 T4/T5 엔티티만
4. event_participation ≤ 1

#### 아카이브 동작

1. kg_entities에서 해당 레코드를 kg_archive로 이동
2. status = "archived"
3. archived_at = 아카이브 실행 시점
4. archive_reason = "auto_t5_cleanup" 또는 수동 사유
5. 해당 엔티티가 source/target인 kg_relations: 유지 (삭제하지 않음). 단, 관계 조회 시 archived 엔티티는 기본적으로 제외
6. kg_entity_tags: 유지 (아카이브와 함께 이동)

#### 매칭에서의 처리

- Stage 1 (exact match): kg_archive의 canonical_name도 검색 대상
- Stage 2 (alias match): kg_archive의 aliases도 검색 대상
- Stage 3 (semantic match): kg_archive 제외 (성능 보호)

#### 부활(reactivation) 규칙

조건: 아카이브된 엔티티가 Stage 1 또는 Stage 2에서 매칭된 경우

동작:
1. kg_archive에서 kg_entities로 레코드 복원
2. status = "active"
3. mention_count += 1
4. last_seen_date = 현재
5. importance_score 재계산 (다음 주간 배치에서)
6. kg_archive에서 해당 레코드 삭제

#### 실행 주기

- 아카이브 배치: 월 1회 (매월 1일)
- 부활: 실시간 (엔티티 매칭 시 즉시)

---

## 8. RISK ANALYSIS

| # | 위험 요소 | 영향 수준 | 안전 조치 |
|---|---------|---------|---------|
| R1 | 기존 DB 참조 유혹 | 차단됨 | kg_source_map의 external_ref는 문자열 메타데이터일 뿐, 기존 DB에 FK나 쿼리를 수행하지 않음 |
| R2 | 테이블 이름 충돌 | 차단됨 | 모든 테이블에 `kg_` 접두어. 기존 시스템의 `news`, `reviews` 등과 충돌 불가 |
| R3 | ID 충돌 | 차단됨 | 기존 시스템은 정수 ID, CEKG는 `KG-` 접두어 문자열. 물리적 충돌 불가 |
| R4 | 파일 경로 충돌 | 차단됨 | `/kg_shadow/` 가상 경로 사용. 기존 `/data/`, `/src/` 등과 분리 |
| R5 | 향후 연결 시 기존 시스템 변경 필요성 | 관리 가능 | `kg_source_map`으로 읽기 전용 매핑만 수행. 기존 시스템 코드·스키마 변경 없이 단방향 참조 가능 |
| R6 | 엔티티 추출 시 기존 파이프라인 수정 유혹 | 차단됨 | CEKG 파이프라인은 독립 프로세스로 설계. 기존 수집·번역·리뷰 파이프라인에 코드 삽입 금지 |

---

## 최종 재정의

> "이 시스템은 뉴스 수집이 아니라, 중국 경제 도메인의 엔티티·이벤트·인과관계를 시계열로 축적하고 전문가 판단을 구조화하여 추론 가능한 상태로 유지하는 지식 축적 시스템이다."

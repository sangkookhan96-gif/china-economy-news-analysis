# 공개 즉시 번역 보완 (On-Publish Translation Refine) — 설계

> 뉴스가 **공개되는 즉시** 번역 품질을 자동 보완한다.
> 적용 방식: **자동 교체 + 품질 게이트** / 대상: **요약·헤드라인·팁** / 방식: **원문 중국어 대조 재번역**.
> 트리거: **공개 클릭 시점**(분리 프로세스로 즉시 착수) + **주기 스윕 안전망**.

---

## 0. 절대 제약 — UI/웹소켓 비차단 (사고 재발 방지)

과거 `📢 공개` 버튼이 동기로 `refine_korean()`(LLM)을 호출 → 웹소켓 끊김 → **로그인 세션 소실**
(`dashboard-mobile-publish-fix` 사고). 본 설계의 재번역은 **요약+헤드라인+팁 ≈ 75초**로 더 무겁다.

→ **공개 핸들러는 LLM을 직접/동기 호출하지 않는다.** 공개 상태만 빠르게 커밋하고,
   재번역은 **Streamlit 런타임과 완전히 분리된 별도 OS 프로세스**가 수행한다.
   (Streamlit 백그라운드 스레드도 세션 수명에 묶여 불안정하므로 사용하지 않는다 — 별도 프로세스 사용.)

---

## 1. 배경 / 문제

공개본 한국어는 `cni_summaries.refined_ko`(요약), `news.card_headline`(헤드라인),
`news.hansanguk_tip`(팁)에서 나온다. 기존 보완기 `refine_korean()`
(`src/cni/postprocess.py:305`)은 **이미 번역된 한국어만 후처리**하며 **원문 중국어를 보지 않는다.**

| 문제 | 현 `refine_korean()` | 본 설계 |
|---|---|---|
| 경어체 아님 | 정규식(`ensure_polite_korean`) 부분 가능, 누락 多 | LLM 재번역 + 정규식 이중 보강 |
| 어색한 번역 | **불가** (원문 대조 필요) | LLM이 (원문 中 + 현재 韓) 대조 재생성 |
| 단어 누락 | **원천 불가** (원문 없음) | 원문 대조로 누락 복원 |

→ 단순 후처리 강화가 아니라 **원문 기준 LLM 재번역 패스**가 핵심.

---

## 2. 범위

- **대상 뉴스**: 새로 공개되는 **CNI 경로** 뉴스
  (`news.pipeline_status='published' AND cni_summaries.summary_ko IS NOT NULL`).
- **대상 필드**: `요약(refined_ko)`, `헤드라인(card_headline)`, `팁(hansanguk_tip)`.
- **제외**: 레거시 `expert_reviews.expert_comment` (편집자 논평 — 번역문 아님).

---

## 3. 아키텍처 — 공개 즉시 + 안전망

```
[에디터가 대시보드에서 📢 공개 클릭]
        │  (동기, ~ms)
        ▼
publish 핸들러 (expert_dashboard.py)
   1) pipeline_status='published', published_at=now
   2) refine_status='pending'                ← 큐 마킹
   3) subprocess.Popen(detached):            ← 즉시 착수, UI 비차단
        python3 -m src.cni.refine_worker --news-id <id>
   4) 즉시 UI 반환 (LLM 대기 없음 → 웹소켓/세션 안전)
        │
        ▼ (별도 OS 프로세스, Streamlit과 무관)
src/cni/refine_worker.py  (단건 처리)
        │
        ├─ 안전망: [news-refine-sweep.timer ~5분] 가 동일 worker를
        │   refine_status='pending' 인 누락/실패분에 대해 일괄 재호출
        │   (공개-시점 spawn 실패·서버 재시작 대비)
        ▼
src/cni/onpublish_refine.py  ← 핵심 로직 (worker가 호출)
   1) load           원문 中 + 현재 韓(요약/헤드라인/팁) 로드
   2) needs_refine() 멱등: refine_log에 (source_hash, refiner_ver) 있으면 skip
   3) retranslate()  필드별 Ollama qwen2.5:7b 재번역
   4) postprocess    run_qc() + format_proper_nouns() + refine_korean() 재사용
   5) quality_gate() 통과 시에만 채택 (§6)
   6) apply()        refined_ko / card_headline / hansanguk_tip UPDATE
   7) log()          refine_log 기록(before/after/판정/사유) → refine_status='done'
        │
        ▼
[Telegram 알림] (반려 발생 시 등, src/utils/notifications.py 재사용)
```

- **"즉시"의 의미**: 공개 클릭과 동시에 분리 프로세스가 착수 → 통상 **수십 초 내** 공개본 갱신.
  공개 직후 잠깐은 기존(미보완) 번역이 노출되고, 보완 완료 시 무중단 교체됨.
- **안전망 스윕**: spawn 실패·프로세스 크래시·서버 재시작으로 `pending`에 남은 건을
  ~5분 주기로 쓸어담아 멱등 처리. → 유실 없음.

---

## 4. 데이터 모델

### 4-1. `news` 테이블 — 큐 상태 컬럼 추가
```sql
ALTER TABLE news ADD COLUMN refine_status TEXT;   -- NULL | pending | done | failed
ALTER TABLE news ADD COLUMN refined_done_at DATETIME;
CREATE INDEX IF NOT EXISTS idx_news_refine_status ON news(refine_status);
```
- 공개 시 `pending` → worker 완료 시 `done`(+시각) / 게이트 전부 반려여도 `done`(원본 유지) / 예외 시 `failed`.
- 스윕은 `refine_status='pending'` 또는 `failed`(재시도 상한 내)를 대상으로.

### 4-2. 신규 테이블 `refine_log` (감사 / 롤백)
```sql
CREATE TABLE IF NOT EXISTS refine_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       TEXT    NOT NULL,         -- 처리 배치/단건 ID (롤백 단위)
    news_id      INTEGER NOT NULL,
    field        TEXT    NOT NULL,         -- summary | headline | tip
    old_text     TEXT,                     -- 변경 전 (롤백 원본 = 행 단위 백업)
    new_text     TEXT,                     -- LLM 재번역 결과
    decision     TEXT    NOT NULL,         -- applied | rejected | unchanged
    reason       TEXT,                     -- 게이트 실패/skip 사유
    metrics      TEXT,                     -- JSON: 한국어비율, 길이비, 숫자일치 등
    source_hash  TEXT,                     -- sha1(original_content + field) 멱등 키
    refiner_ver  TEXT    NOT NULL,         -- 프롬프트/게이트 버전 (예: 'r1')
    model        TEXT,                     -- qwen2.5:7b
    refined_at   DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_refine_log_news ON refine_log(news_id, field);
CREATE INDEX IF NOT EXISTS idx_refine_log_run  ON refine_log(run_id);
```
- 멱등: `(source_hash, refiner_ver)`가 이미 `applied/unchanged`면 재처리 skip
  (중복 공개·재공개·스윕 중복 호출에도 1회만 LLM 실행).
- `refiner_ver`를 올리면 향후 공개분부터 새 프롬프트/게이트 적용. 과거분 일괄 재처리는
  `scripts/refine_backfill.py --force`(선택적 운영 도구)로.

---

## 5. 재번역 프롬프트 (필드별)

공통 원칙: **원문에 없는 사실 추가 금지(환각)**, **숫자·고유명사 보존**, **경어체(~습니다) 통일**.

**요약(summary)** — 누락·오역 교정:
```
다음은 중국 경제 뉴스 원문(중국어)과 현재 한국어 요약이다.
원문과 대조하여 (1) 누락된 핵심 정보를 채우고 (2) 오역을 바로잡고
(3) 자연스러운 한국어 경어체(~습니다)로 다시 작성하라.
- 원문에 없는 내용은 절대 추가하지 마라.
- 숫자·기업명·인명·기관명은 원문과 정확히 일치시켜라.
- 분량은 150~300자.
[원문] {original_content[:3000]}
[현재 요약] {current_summary}
다시 작성한 한국어 요약:
```
**헤드라인(headline)** — 보완된 요약에서 재도출, 36자/72byte 재적용(기존 헤드라인 로직 + `title_postprocessor`).
**팁(tip)** — 논평 성격이라 보수적: 문체·경어체·자연스러움만 개선, 의미·관점 변경 금지, 200자 이내.

> ⚠️ 프롬프트는 f-string. 중괄호 예시는 `{{ }}` 이스케이프 (CLAUDE.md §10 장애 재발 방지).

---

## 6. 품질 게이트 (자동 교체 조건 — 전부 통과해야 채택)

| # | 검사 | 기준 | 재사용 |
|---|---|---|---|
| 1 | 한국어 비율 | Hangul/비공백 ≥ 0.9 | 신규 |
| 2 | 중국어 잔재 없음 | `has_chinese_residue()==False` | `postprocess.py` |
| 3 | 길이 정상범위 | 원본 대비 0.6×~1.6× (절삭·폭주 차단) | 신규 |
| 4 | 완결 문장 | `_is_complete()==True` | `translation_qc.py` |
| 5 | 경어체 | `ensure_polite_korean(new)==new` | `postprocess.py` |
| 6 | 헤드라인 길이 | ≤ 36자 / 72 byte (헤드라인 필드만) | 기존 제약 |
| 7 | **숫자 정합성** | new의 모든 숫자 토큰이 원문에 존재 (수치 환각 차단) | 신규 — 금융 필수 |
| 8 | 실질 변경 | old≠new (동일하면 `unchanged`, 무의미 갱신 방지) | 신규 |

- 하나라도 실패 → **원본 유지**, `refine_log.decision='rejected'` + `reason` 기록.
- 7번은 기관투자자용이라 특히 중요: 날짜·증가율·금액 환각을 막는 최후 방어선.

---

## 7. 트리거 & 워커 (스케줄 아님)

- **즉시 착수**: 공개 핸들러가 `subprocess.Popen([...,'-m','src.cni.refine_worker','--news-id',id])`로
  detached 실행. Streamlit 세션/웹소켓과 무관 → 사고 재발 없음.
- **안전망 스윕**: `news-refine-sweep.timer` (예: `OnCalendar=*:0/5`, 5분 간격) →
  `refine_worker --sweep` 가 `refine_status IN ('pending','failed')` 잔여분 처리.
  서버 재시작·spawn 실패에도 결국 보완됨.
- **재시도 상한**: `failed` 3회 초과 시 텔레그램 경고 + 스윕 대상 제외(무한 루프 방지).
- **GPU 충돌**: 단건 ~75초로 가벼움. 스케줄러는 에디션 시간대 분석을 일시정지(기존 동작)하므로
  공개 트래픽과 큰 충돌 없음. 동시 공개 다수 시 worker가 `refine_log` 멱등 + 단건 직렬로 처리.

---

## 8. 운영 / 안전장치

- **드라이런**: `python3 -m src.cni.refine_worker --news-id <id> --dry-run --show`
  → before/after diff만 출력, DB 미변경.
- **롤백**: `scripts/refine_rollback.py --run-id <ID>` (또는 `--news-id`) →
  `applied` 행을 `old_text`로 복원.
- **알림**: 반려/실패 발생 시 텔레그램 — news_id, 필드, 사유.
- **백업**: `refine_log.old_text`가 행 단위 백업 역할.

---

## 9. 신규/변경 파일

| 파일 | 내용 |
|---|---|
| `src/cni/onpublish_refine.py` | 핵심 로직 (load/retranslate/gate/apply/log) |
| `src/cni/refine_worker.py` | CLI 워커: `--news-id` 단건 / `--sweep` 일괄 / `--dry-run` |
| `src/ui/expert_dashboard.py` | 공개 핸들러에 `refine_status='pending'` + detached Popen 추가 |
| `scripts/refine_rollback.py` | run_id/news_id 단위 롤백 |
| `scripts/refine_backfill.py` | (선택) 과거 공개분 일괄 재처리 — `--force --days N` |
| `src/database/models.py` | `refine_log` DDL + `news.refine_status/refined_done_at` 추가 |
| `infra/news-refine-sweep.{service,timer}` | 5분 안전망 스윕 유닛 |
| (재사용) `postprocess.py`, `translation_qc.py`, `proper_noun_formatter.py`, `notifications.py`, `summary_store.update_refined()` | 후처리·QC·알림·저장 |

---

## 10. 단계별 구현 순서 (제안)

1. `models.py`: `refine_log` 테이블 + `news.refine_status/refined_done_at` 컬럼.
2. `onpublish_refine.py` 핵심: load → retranslate(summary) → gate → dry-run 출력 (요약 단일 필드부터).
3. 품질 게이트 8종 + 숫자 정합성 단위 테스트(`tests/` 단독 스크립트 패턴).
4. `refine_worker.py` (`--news-id`, `--dry-run`) + apply + refine_log + 롤백 스크립트.
5. 헤드라인·팁 필드 확장.
6. 공개 핸들러 연동(detached Popen) — **동기 호출 금지 재확인**.
7. `--sweep` + `news-refine-sweep.timer` 안전망 + 텔레그램 알림.
8. 수동 단건 검증 → 표본 diff 검수 → 운영 활성화. (필요 시 `refine_backfill.py`로 기존 공개분 1회 소급)
```

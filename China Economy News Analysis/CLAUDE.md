# China Economy News Analysis Platform

중국 경제 뉴스를 자동 수집 및 분석하여, 한국 기관투자자 대상으로 매일 3판(아침/오후/저녁) 큐레이션된 뉴스를 발행하는 플랫폼. 크롤링 → 분석(Ollama/Qwen) → 에디션 선정 → CNI 파이프라인(헤드라인·요약·팁 생성 + 번역) → 전문가 리뷰 → 공개 발행의 풀사이클을 자동화한다.

> **개발 실행 / 테스트**
> - 대시보드 로컬 실행: `bash run.sh` (Streamlit, 포트 8503 — 운영 8501과 분리). 진입점은 `run_dashboard.py` → `src.ui.expert_dashboard.main()`.
> - 관리 CLI: `python3 manage.py healthcheck` (시스템 상태), `python3 manage.py select --edition morning` (에디션 선정 수동 실행).
> - KG 풀파이프라인: `python3 run_full_pipeline.py [--companies "BYD,CATL"] [--skip-extract]`.
> - 테스트는 pytest가 아니라 **단독 스크립트**다. 개별 실행: `python3 tests/test_filter_consistency.py`, `python3 tests/test_content_scorer.py`, `python3 scripts/test_translation_qc.py`. 각 파일이 `__main__`에서 직접 검증을 돌린다.
> - 모든 진입점은 루트에서 실행해야 `sys.path` 삽입이 맞는다 (`PYTHONPATH` 함정 주의 — 운영 systemd는 python3.12 사용).

---

## 1. 디렉토리 구조

```
src/
  agents/          스케줄러 데몬(crawl→analyze 루프), 에디션 선정기
  analyzer/        ClaudeAnalyzer — Ollama(qwen2.5:7b)로 번역·요약·점수 산출
  api/             공개 피드 엔드포인트 (JSON/RSS)
  cni/             CNI 파이프라인 — 헤드라인·요약·팁 생성, Papago 번역, 품질 스코어링, 큐
  collector/       웹 크롤러, 뉴스 필터, 콘텐츠 스코어링, 소스 관리
  database/        SQLite 모델 (news, expert_reviews, cni_summaries, KG 테이블)
  kg/              지식그래프 — 엔티티 추출, 이벤트 정규화, 관계 매핑
  ui/              Streamlit 전문가 대시보드 (리뷰·발행 관리)
  utils/           고유명사 병기, 헤드라인 생성, 정치 민감도 체크, 백업
  web/             Flask 웹앱 (아카이브·공개 뉴스 열람)
config/            설정 (settings.py, gics_taxonomy.py, content_scoring.py)
scripts/           유틸리티 스크립트 (백필, 에디션 실행, 헤드라인 수정 등)
data/              SQLite DB (news.db, kg.db)
logs/              애플리케이션 로그
reviews/           전문가 리뷰 마크다운 파일 (일자별 정리)
infra/             systemd 유닛 파일
```

---

## 2. 파이프라인 흐름

```
수집 → 분석 → 에디션 선정 → CNI 큐 → 전문가 리뷰 → 발행
```

| 단계 | 담당 모듈 | 트리거 | 산출물 |
|---|---|---|---|
| 수집 | `src/agents/scheduler_agent.py` → `src/collector/crawler.py` | 상시 루프 | `news` 행 (original_title, original_content) |
| 분석 | `src/analyzer/claude_analyzer.py` → Ollama | 수집 직후 | translated_title, summary, market_impact, 점수 4종, GICS 분류 |
| 에디션 선정 | `src/agents/daily_news_selector.py` | systemd 타이머 07/14/20시 | pipeline_status='selected', edition 배정 |
| CNI 큐 처리 | `src/cni/process_queue.py` → `generate_cni_fields.py` | 사용자 [요약번역] 클릭 또는 cron 5분 | card_headline, cni_summaries.summary_ko, hansanguk_tip |
| 전문가 리뷰 | `src/ui/expert_dashboard.py` | 수동 | expert_reviews.expert_comment, publish_status |
| 발행 | `src/api/public_feed.py` + `src/web/app.py` | 에디터 [공개] 클릭 | 브라우저·RSS·피드 노출 |

---

## 3. Systemd 서비스

| 서비스 | 역할 | 스케줄 |
|---|---|---|
| `news-scheduler.service` | 상시 데몬: 크롤링 → 분석 루프 | Always-on, Restart=on-failure |
| `news-morning.timer` | 아침판 에디션 선정 | 매일 07:00 |
| `news-afternoon.timer` | 오후판 에디션 선정 | 매일 14:00 |
| `news-evening.timer` | 저녁판 에디션 선정 | 매일 20:00 |
| `news-watchdog.timer` | 헬스체크 + 텔레그램 알림 | 10분 간격 |
| `news-dashboard.service` | Streamlit 대시보드 (포트 8501) | 수동 기동 |
| `news-web.service` | Flask 웹앱 (포트 8502) | 수동 기동 |
| `kg-daily.timer` | 지식그래프 일일 업데이트 | 매일 23:30 |

cron: `process_queue.py` (5분 주기, cni_summaries 큐 소비).

---

## 4. 데이터베이스 (SQLite: data/news.db)

### news 테이블 핵심 컬럼

- `original_title`, `original_content` — 중국어 원문
- `translated_title`, `summary`, `market_impact` — Claude/Qwen 분석 결과 (한국어, 고유명사 병기 적용)
- `importance_score`, `market_relevance_score`, `uncertainty_score`, `expert_explainability_score` — 0.0~1.0
- `industry_category` — GICS L4/L2/L1 코드 또는 EXT_POLICY/EXT_GEOPOLITICS/EXT_MACRO
- `content_type` — policy | corporate | industry | market | opinion
- `sentiment` — positive | negative | neutral
- `card_headline` — 최대 72바이트 (한국어 36자)
- `hansanguk_tip` — 최대 500자
- `pipeline_status` — NULL → selected → processing → translated/failed → published/unpublished
- `expert_review_status` — none | queued_today | commented | skipped
- `edition` — morning | afternoon | evening

### expert_reviews 테이블

- `expert_comment`, `ai_comment`, `ai_final_review` — 세 필드 모두 공개 피드에 노출 가능
- `publish_status` — draft | published | discarded | rejected

### cni_summaries 테이블

- `summary_zh`, `summary_ko`, `refined_ko` — 공개 피드는 `COALESCE(refined_ko, summary_ko)` 사용
- `filter_result`, `filter_reason`, `kg_processed`, `translation_status`

---

## 5. 기술 스택

| 구성요소 | 상세 |
|---|---|
| LLM | Ollama + **qwen2.5:7b** (localhost:11434), MAX_TOKENS=4096. 14b는 12GB GPU에서 OOM/CPU-offload(~90s/call)라 7b로 전면 이관 완료 — 되돌리지 말 것 |
| 번역 | Papago API (zh→ko) |
| 대시보드 | Streamlit (포트 8501) |
| 웹앱 | Flask (포트 8502) |
| 외부 접속 | Cloudflare Tunnel |
| DB | SQLite (WAL 모드) |
| 알림 | Telegram Bot |

---

## 6. 고유명사 병기 시스템

한국어 텍스트에서 고유명사를 첫 등장 시 1회만 중국어(+영문) 병기한다.

| 종류 | 형식 | 예 |
|---|---|---|
| 인물 | 음차 직책(汉字[, English]) | 시진핑 국가주석(习近平) |
| 기업 | 음차(汉字[, English]) | 닝더스다이(宁德时代, CATL) |
| 기관 | 약칭(汉字[, English]) | 국무원(国务院), 연준(美联储, Fed) |
| 지명 | 음차(汉字[, English]) | 상하이(上海, Shanghai) |

### 핵심 파일

- `src/utils/proper_nouns.py` — PEOPLE / COMPANIES / AGENCIES / PLACES 4개 dict (총 ~320 엔트리). `all_entries()`가 `(zh, info, kind)` 평면 리스트 반환.
- `src/utils/proper_noun_formatter.py` — 첫 등장 1회 병기 포매터. 원문 중국어에 실제 등장하는 엔티티만 후보로 삼아 환각 방지. 토큰 마스킹으로 부분 문자열 충돌 차단(광둥성 안의 광둥 재매칭 방지), 기존 괄호 안 항목 재주석 방지.
- `src/analyzer/claude_analyzer.py` — 뉴스 분석 직후 translated_title / summary / market_impact에 포매터 호출. 프롬프트에서 고유명사 첫 1회 괄호 병기 허용.
- `scripts/backfill_proper_nouns.py` — 기존 데이터 백필.
  - `--target news` (기본): news 테이블 3개 필드만 갱신.
  - `--target published`: 공개 뉴스만 선택, news + expert_reviews + cni_summaries 함께 갱신.
  - 멱등: 재실행해도 기존 주석이 누적되지 않음. 사전 확장 후 재실행하면 신규 엔티티만 추가.

### 사전 확장 시 주의

- 같은 한국어 음차를 공유하는 별칭(中国人民银行/人民银行/央行 → 인민은행)은 `used_ko`로 중복 차단. 별칭 자유롭게 추가 가능.
- 영문 키 약칭(IMF, WTO, AMD 등)은 `zh == en` 일 때 영문 중복 자동 제거.
- 부분 문자열(핑안 vs 중국핑안, 광둥 vs 광둥성)은 길이 우선 정렬 + 토큰 마스킹으로 자동 해결.

---

## 7. "공개된 뉴스" 정의

브라우저·피드·웹앱에 노출되는 뉴스는 두 경로 중 하나를 만족:

1. **레거시 경로**: `expert_reviews.publish_status='published' AND expert_comment IS NOT NULL`
2. **CNI 경로**: `news.pipeline_status='published' AND cni_summaries.summary_ko IS NOT NULL`

- 두 경로는 UNION으로 합산, 중복은 CNI 쪽에서 NOT IN 조건으로 제거.
- `expert_dashboard.py`는 `analyzed_at IS NOT NULL`만 거르는 편집자 시야이며 공개 지면이 아님.
- 브라우저 상세 페이지에서 summary와 expert_review가 동일할 경우(CNI 경로) summary 섹션을 숨김.

---

## 8. 콘텐츠 길이 제약

| 필드 | 목표 길이 | DB 한도 | 초과 시 처리 |
|---|---|---|---|
| card_headline | 36자 (한국어) | 72바이트 | 절삭 |
| hansanguk_tip | 200자 | 500자 | Qwen에게 200자 이내 축약 요청 → 실패 시 문장 단위 절삭 |
| summary | 150–300자 | TEXT | 프롬프트 지시 |
| market_impact | 1–2문장 | TEXT | 프롬프트 지시 |

---

## 9. 대시보드 UI 구조 (expert_dashboard.py)

뉴스 카드 구성 (간소화 완료):

1. **원문 링크** — expander 바깥 최상단에 승격. 리뷰어가 바로 원문 열람 가능.
2. **CNI 상태 폼** (pipeline_status='translated' 일 때):
   - 📰 헤드라인 입력
   - 📝 한국어 요약 textarea
   - 💡 한상국의 팁 textarea
   - ✏️ 입력 확정 (form submit → 세 필드 동시 저장)
   - 📢 공개 / 🔒 비공개 / 💾 수정저장 / 💡 팁생성 / 🔄 초기화 버튼
3. **🛠 고급 편집** expander (접힌 상태):
   - 요약·시장 영향 표시, 분류 수정, 키워드, 8기준 레이더 차트, 태그, 전문가 논평(Markdown+Git), AI 최종 리뷰

---

## 10. 프롬프트 안전 규칙

**`claude_analyzer.py`의 프롬프트는 파이썬 f-string이다.** 중괄호 `{}`를 탬플릿 표시용으로 넣으면 SyntaxError가 발생하여 스케줄러 전체가 죽는다 (2026-04-11 장애 원인). 프롬프트 안에 예시용 중괄호가 필요하면 이스케이프(`{{`, `}}`)를 쓰거나 평문으로 대체해야 한다.

---

## 11. 에디션 선정 로직

`src/agents/daily_news_selector.py` 가 실행하는 흐름:

1. 시간 윈도우(lookback=1일) 내 수집된 뉴스 중 `analyzed_at IS NOT NULL`이고 아직 선정/리뷰되지 않은 후보를 필터링.
2. `filter_news`: 중복 제거(유사도 0.4 이상), 콘텐츠 스코어링.
3. `balance_categories`: GICS 카테고리 균형 + 소스 분산으로 ~10건 선정.
4. 선정된 뉴스의 `expert_review_status`를 `queued_today`로, `edition`을 morning/afternoon/evening으로 업데이트.

---

## 12. 운영 명령 모음

```bash
# 서비스 상태 확인
systemctl status news-scheduler news-dashboard news-web --no-pager

# 서비스 재시작
sudo systemctl restart news-scheduler.service
sudo systemctl restart news-dashboard.service news-web.service

# 오늘 에디션 선정 결과 확인
tail -30 /home/jeozeohan/logs/daily_news_morning_systemd.log

# 스케줄러 로그 최근 이상 확인
tail -50 logs/scheduler.log | grep -iE "error|fail|traceback"

# 고유명사 백필 (공개 뉴스 전체)
python3 scripts/backfill_proper_nouns.py --target published --days 100000

# 고유명사 백필 (dry-run으로 먼저 확인)
python3 scripts/backfill_proper_nouns.py --target published --days 30 --dry-run --show 8

# DB 수집·분석 통계 확인
python3 -c "
from src.database.models import get_connection
c = get_connection().cursor()
c.execute(\"SELECT date(collected_at) d, COUNT(*) n FROM news WHERE collected_at >= datetime('now','-7 days') GROUP BY d ORDER BY d\")
for r in c.fetchall(): print(f'{r[\"d\"]}: {r[\"n\"]} collected')
"
```

---

## 13. 장애 대응 체크리스트

1. **뉴스 수집이 급감했다**: `systemctl status news-scheduler` 확인. 로그에 SyntaxError/ImportError가 있으면 해당 모듈 수정 후 `sudo systemctl restart news-scheduler.service`.
2. **에디션 선정이 0건이다**: `tail logs/daily_news_*_systemd.log`에서 "시간윈도우 후보" 건수 확인. 0이면 수집 장애가 원인.
3. **대시보드 접속 불가**: `sudo systemctl restart news-dashboard.service`. 포트 8501 확인.
4. **웹 상세 페이지에 요약이 중복 표시된다**: `news_detail.html`에서 `summary != expert_review` 조건이 빠졌는지 확인.
5. **팁이 잘린다**: `hansanguk_tip` DB 한도(500자)와 `_ensure_tip_complete` 절삭 기준(200자) 확인. Qwen 축약이 실패하면 문장 단위 fallback 적용.

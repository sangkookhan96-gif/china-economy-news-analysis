# 사용자 노출 파이프라인 구현 확정안

**확정일**: 2026-02-05
**상태**: 최종 승인 완료 — 이후 변경 불가
**기술 스택**: Flask + Jinja2 + Vanilla CSS/JS (Google Material 감성)
**전제**: 관리자 대시보드(Streamlit :8501)는 완성 상태이며, 사용자 서버는 별도 포트(:8502)로 운영된다.

---

## 1단계: 데이터 API 계층 구축

`src/api/public_feed.py`를 생성한다. `get_published_news(limit, offset)` 함수가 전문가 리뷰 완료(`expert_comment IS NOT NULL`) 뉴스만 반환한다. 노출 필드는 headline(translated_title), expert_review, original_article, source, date, importance, category, summary로 확정되었다.

## 2단계: Flask 사용자 서버 + 라우팅

`src/web/app.py`와 `run_web.py`를 생성한다. 라우팅은 `GET /`(오늘의 피드), `GET /archive`(날짜별 아카이브), `GET /news/<id>`(개별 상세·공유 링크)로 구성된다. 1단계의 피드 함수를 호출하여 Jinja2 템플릿에 전달하며, 날짜별(오늘/어제/이전) 그룹핑을 적용한다.

## 3단계: 웹 UI 구현 (데스크톱 + Material Design)

`templates/`에 base.html, feed.html, news_detail.html을, `static/`에 style.css, main.js를 배치한다. 카드 1개 = 뉴스 1건이며, 정보 순서는 **카테고리 배지+날짜 → 제목 → 전문가 해설 → 원문(접힘)** 으로 고정되었다. 폰트는 Noto Sans KR + Roboto, 카드 폭 max-width 720px, 리뷰 영역은 #f8f9fa 배경 + 좌측 파란 보더, 원문은 #f1f3f4 배경으로 시각 분리한다. 중요도 배지는 0.8+/0.6+/0.4+/기타 4단계 색상이다. JS는 Vanilla로 원문 접기/펼치기를 구현한다.

## 4단계: 모바일 반응형 최적화

`@media max-width: 768px` 기준으로 분기한다. 카드 폭 100%-16px, 제목 20px+2줄 말줄임, 원문 버튼은 전폭(높이 48px), 원문 영역은 max-height 60vh + 내부 스크롤로 처리한다. 접기 버튼은 원문 내부에 배치하여 뒤로가기 없이 접을 수 있다. Safe area와 viewport 메타태그를 적용한다.

## 5단계: 통합·배포·연결

관리자가 리뷰를 저장하면 `expert_review_status`가 `commented`로 전환되고, 사용자 피드는 이 상태만 쿼리하므로 별도 발행 버튼 없이 자동 노출된다. 헤더에 "한상국의 쉬운 중국경제뉴스 해설"+날짜를 표시하고, 원문 렌더링에는 XSS 방지(bleach)를 적용한다. 10건 단위 페이지네이션을 사용한다.

---

**파일 구조**: `src/api/public_feed.py` → `src/web/app.py` → `src/web/templates/` → `src/web/static/` → `run_web.py`
**실행 순서**: 1→2→3→4→5 순차 진행, 각 단계는 이전 단계 완료를 전제한다.

# PRD Step 1: 이미지 인식 (MVP)

## 프로젝트 개요

냉장고 사진을 업로드하면 AI가 재료를 인식하고, 해당 재료로 만들 수 있는 레시피를 추천하는 웹 애플리케이션

### 기술 스택
- **Backend**: Python (Flask)
- **Frontend**: HTML/CSS/JavaScript
- **AI Model**: OpenRouter API - `google/gemma-3-27b-it:free`

---

## 1단계 목표

사용자가 냉장고 사진을 업로드하면 Gemma 모델이 재료를 인식하여 목록으로 반환

---

## 기능 요구사항

| ID | 기능 | 설명 | 우선순위 |
|----|------|------|----------|
| 1.1 | 이미지 업로드 | 사용자가 냉장고 사진을 업로드 (JPG, PNG, WebP 지원) | P0 |
| 1.2 | 재료 인식 | Gemma 모델로 이미지에서 식재료 추출 | P0 |
| 1.3 | 재료 목록 표시 | 인식된 재료를 목록 형태로 화면에 표시 | P0 |
| 1.4 | 재료 편집 | 사용자가 인식된 재료를 추가/삭제/수정 가능 | P1 |

---

## API 엔드포인트

```
POST /api/analyze-image
- Request: multipart/form-data (image file)
- Response: { "ingredients": ["당근", "양파", "계란", ...] }
```

---

## 화면 구성

### 메인 페이지
- 이미지 업로드 영역 (드래그앤드롭 지원)
- 업로드된 이미지 미리보기
- "재료 인식하기" 버튼
- 로딩 인디케이터

### 결과 영역
- 인식된 재료 목록 (체크박스로 선택/해제 가능)
- 재료 추가 입력 필드
- "레시피 추천받기" 버튼 (2단계에서 활성화)

---

## 프로젝트 구조

```
study-04/
├── app.py                 # Flask 메인 애플리케이션
├── config.py              # 설정 (API 키)
├── openrouter_client.py   # OpenRouter API 클라이언트 (기존)
├── models.txt             # 사용 가능한 모델 목록
├── requirements.txt       # 의존성
├── .env                   # API 키
│
├── static/
│   ├── css/
│   │   └── style.css
│   └── js/
│       └── main.js
│
├── templates/
│   └── index.html         # 메인 페이지
│
└── uploads/               # 업로드된 이미지 임시 저장
```

---

## 상세 구현 사항

### 1. Flask 앱 설정 (app.py)
- 파일 업로드 처리
- 이미지 분석 API 엔드포인트
- 정적 파일 서빙

### 2. 이미지 분석 프롬프트
```
이 냉장고/식품 이미지에서 보이는 모든 식재료를 인식해주세요.
다음 JSON 형식으로만 응답해주세요:
{"ingredients": ["재료1", "재료2", "재료3"]}
```

### 3. 프론트엔드 기능
- 드래그앤드롭 이미지 업로드
- 이미지 미리보기
- AJAX로 API 호출
- 재료 목록 동적 렌더링
- 재료 추가/삭제 UI

---

## 완료 조건

- [ ] Flask 웹 서버 구동
- [ ] 이미지 업로드 UI 구현
- [ ] Gemma 모델 연동하여 재료 인식
- [ ] 재료 목록 JSON 형태로 반환
- [ ] 재료 편집 (추가/삭제) 기능
- [ ] 기본 스타일링 적용

---

## 의존성 (requirements.txt 추가)

```
flask
python-dotenv
requests
```

# PRD Step 3: 사용자 프로필 & 레시피 저장

## 목표

사용자 계정을 생성하고 좋아하는 레시피를 저장/관리

### 기술 스택 추가
- **Database**: SQLite
- **인증**: Flask-Login 또는 세션 기반

---

## 기능 요구사항

| ID | 기능 | 설명 | 우선순위 |
|----|------|------|----------|
| 3.1 | 회원가입/로그인 | 이메일 기반 간단한 인증 | P0 |
| 3.2 | 레시피 저장 | 마음에 드는 레시피를 "내 레시피"에 저장 | P0 |
| 3.3 | 저장된 레시피 조회 | 저장한 레시피 목록 확인 | P0 |
| 3.4 | 레시피 삭제 | 저장된 레시피 삭제 | P0 |
| 3.5 | 식이 제한 설정 | 채식, 알레르기 등 식이 제한 설정 | P1 |
| 3.6 | 레시피 히스토리 | 과거 생성된 레시피 히스토리 조회 | P2 |
| 3.7 | 레시피 평점/메모 | 저장된 레시피에 평점, 메모 추가 | P2 |

---

## 데이터베이스 스키마

```sql
-- 사용자 테이블
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    dietary_restrictions TEXT,  -- JSON: ["채식", "견과류 알레르기"]
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 저장된 레시피 테이블
CREATE TABLE saved_recipes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    recipe_name TEXT NOT NULL,
    recipe_data TEXT NOT NULL,  -- JSON: 전체 레시피 정보
    rating INTEGER,             -- 1-5 별점
    notes TEXT,                 -- 사용자 메모
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

-- 레시피 생성 히스토리 테이블
CREATE TABLE recipe_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,            -- NULL 허용 (비로그인 사용자)
    ingredients TEXT NOT NULL,  -- JSON: 사용된 재료
    recipes_generated TEXT NOT NULL,  -- JSON: 생성된 레시피들
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
```

---

## API 엔드포인트

### 인증 API
```
POST /api/auth/register
- Request: { "email": "user@example.com", "password": "password123" }
- Response: { "success": true, "user": { "id": 1, "email": "user@example.com" } }

POST /api/auth/login
- Request: { "email": "user@example.com", "password": "password123" }
- Response: { "success": true, "user": { "id": 1, "email": "user@example.com" } }

POST /api/auth/logout
- Response: { "success": true }

GET /api/auth/me
- Response: { "user": { "id": 1, "email": "user@example.com", "dietary_restrictions": [] } }
```

### 레시피 저장 API
```
GET /api/recipes
- Response: { "recipes": [...] }

POST /api/recipes
- Request: { "recipe_data": {...} }
- Response: { "success": true, "recipe_id": 1 }

DELETE /api/recipes/{id}
- Response: { "success": true }

PATCH /api/recipes/{id}
- Request: { "rating": 5, "notes": "맛있었다!" }
- Response: { "success": true }
```

### 프로필 API
```
PATCH /api/profile
- Request: { "dietary_restrictions": ["채식", "견과류 알레르기"] }
- Response: { "success": true }
```

### 히스토리 API
```
GET /api/history
- Response: { "history": [...] }
```

---

## 화면 구성

### 헤더 (공통)
- 로고/홈 링크
- 로그인 상태에 따른 표시:
  - 비로그인: "로그인" / "회원가입" 버튼
  - 로그인: 사용자 이메일, "마이페이지", "로그아웃"

### 로그인/회원가입 모달
- 이메일 입력
- 비밀번호 입력
- 회원가입/로그인 전환 링크

### 마이페이지
- **프로필 섹션**
  - 이메일 표시
  - 식이 제한 설정 (체크박스)
    - 채식
    - 비건
    - 글루텐 프리
    - 유제품 알레르기
    - 견과류 알레르기
    - 해산물 알레르기

- **저장된 레시피 섹션**
  - 저장된 레시피 카드 목록
  - 각 카드: 요리명, 저장 날짜, 별점
  - 삭제 버튼
  - 클릭 시 상세 보기

- **히스토리 섹션**
  - 날짜별 생성된 레시피 목록
  - 사용된 재료 표시

### 레시피 상세 (기존 수정)
- "저장하기" 버튼 활성화 (로그인 시)
- 저장된 레시피의 경우:
  - 별점 입력 (1-5 별)
  - 메모 입력 필드
  - "삭제" 버튼

---

## 프로젝트 구조 (최종)

```
study-04/
├── app.py                 # Flask 메인 + 인증 라우트
├── config.py              # 설정
├── openrouter_client.py   # OpenRouter API 클라이언트
├── database.py            # DB 초기화 및 연결
├── models.txt             # 모델 목록
├── requirements.txt       # 의존성
├── .env                   # API 키
├── recipe.db              # SQLite 데이터베이스
│
├── static/
│   ├── css/
│   │   └── style.css
│   └── js/
│       ├── main.js        # 메인 기능
│       └── auth.js        # 인증 관련
│
└── templates/
    ├── index.html         # 메인 페이지
    └── mypage.html        # 마이페이지
```

---

## 상세 구현 사항

### 1. 데이터베이스 초기화 (database.py)
```python
import sqlite3

def init_db():
    conn = sqlite3.connect('recipe.db')
    # 테이블 생성 SQL 실행
    conn.close()

def get_db():
    conn = sqlite3.connect('recipe.db')
    conn.row_factory = sqlite3.Row
    return conn
```

### 2. 비밀번호 해싱
- `werkzeug.security` 사용
- `generate_password_hash()`, `check_password_hash()`

### 3. 세션 관리
- Flask 세션 사용
- 로그인 시 `session['user_id']` 저장

### 4. 식이 제한 적용
- 레시피 생성 시 프롬프트에 식이 제한 포함
```
다음 재료들로 만들 수 있는 요리 레시피를 추천해주세요.
재료: {ingredients}
식이 제한: {dietary_restrictions}
위 식이 제한을 반드시 준수해주세요.
```

---

## 완료 조건

- [ ] SQLite 데이터베이스 설정
- [ ] 사용자 테이블 생성
- [ ] 회원가입 API 구현 (비밀번호 해싱)
- [ ] 로그인/로그아웃 API 구현
- [ ] 세션 관리 구현
- [ ] 레시피 저장 API 구현
- [ ] 레시피 조회/삭제 API 구현
- [ ] 평점/메모 기능 구현
- [ ] 마이페이지 UI 구현
- [ ] 식이 제한 설정 기능 구현
- [ ] 레시피 생성 시 식이 제한 적용

---

## 의존성 추가 (requirements.txt)

```
flask
python-dotenv
requests
werkzeug
```

---

## 보안 고려사항

- 비밀번호는 반드시 해시화하여 저장
- SQL 인젝션 방지 (파라미터화된 쿼리 사용)
- 세션 시크릿 키 환경변수로 관리
- CSRF 토큰 사용 고려

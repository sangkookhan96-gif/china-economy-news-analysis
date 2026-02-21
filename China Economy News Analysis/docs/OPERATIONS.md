# 운영 매뉴얼 (Operations Manual)

## 시스템 구조

```
cron (legacy, 비활성)
  └─ 마이그레이션 완료 → systemd

systemd
  ├── news-scheduler.service     # 상시 데몬: 크롤링 + 본문수집 + 분석 (30분 주기)
  ├── news-morning.timer/service  # 매일 07:00 조간 선정
  ├── news-afternoon.timer/service # 매일 14:00 오후 선정
  ├── news-evening.timer/service  # 매일 22:00 석간 선정
  └── news-watchdog.timer/service # 10분마다 헬스체크 + 자동복구

monitoring/
  ├── watchdog.py     # 헬스체크 (스케줄러, 에디션, 분석)
  └── notifier.py     # Telegram 알림

manage.py              # CLI 관리도구
```

---

## 1. systemd 설치 및 활성화

```bash
# 설치 (root 권한 필요)
sudo bash scripts/install_systemd.sh

# 설치 후 확인
systemctl list-timers 'news-*'
systemctl status news-scheduler
```

### 수동 설치 (install_systemd.sh 대신)

```bash
# 1. unit 파일 복사
sudo cp systemd/*.service systemd/*.timer /etc/systemd/system/

# 2. systemd 리로드
sudo systemctl daemon-reload

# 3. 타이머 활성화 및 시작
sudo systemctl enable --now news-morning.timer
sudo systemctl enable --now news-afternoon.timer
sudo systemctl enable --now news-evening.timer
sudo systemctl enable --now news-watchdog.timer

# 4. 스케줄러 데몬 활성화 및 시작
sudo systemctl enable --now news-scheduler.service
```

---

## 2. 상태 확인 명령어

### 전체 헬스체크
```bash
python3 manage.py healthcheck
```
출력 항목: Status(OK/WARN/FAIL), 수집건수, 분석건수, 에디션, 스케줄러, Ollama, DB크기

### 타이머 상태
```bash
systemctl list-timers 'news-*' --all
```

### 서비스 상태
```bash
systemctl status news-scheduler       # 스케줄러 데몬
systemctl status news-morning          # 조간 선정
systemctl status news-afternoon        # 오후 선정
systemctl status news-evening          # 석간 선정
systemctl status news-watchdog         # 워치독
```

### 로그 확인
```bash
# systemd 저널
journalctl -u news-scheduler --since today
journalctl -u news-watchdog --since today

# 애플리케이션 로그
tail -50 logs/scheduler.log
tail -50 logs/morning.log
tail -50 logs/watchdog.log
```

---

## 3. 수동 실행

### 에디션 수동 선정
```bash
python3 manage.py select --edition morning
python3 manage.py select --edition afternoon
python3 manage.py select --edition evening
```

### 스케줄러 1회 실행 (크롤링+분석)
```bash
python3 src/agents/scheduler_agent.py
```

### 워치독 수동 실행
```bash
python3 monitoring/watchdog.py
```

---

## 4. 장애 복구

### 스케줄러 미작동
```bash
# 상태 확인
systemctl status news-scheduler

# 재시작
sudo systemctl restart news-scheduler

# 로그 확인
journalctl -u news-scheduler -n 50
```

### 에디션 미선정
```bash
# 수동 선정
python3 manage.py select --edition afternoon

# 또는 systemd 서비스 수동 트리거
sudo systemctl start news-afternoon
```

### Ollama 다운
```bash
# Ollama 상태
curl -s localhost:11434/api/tags | python3 -m json.tool

# Ollama 재시작
sudo systemctl restart ollama

# 모델 확인
ollama list
```

### DB 문제
```bash
# DB 무결성 검사
sqlite3 data/news.db "PRAGMA integrity_check;"

# 백업에서 복원
cp data/backups/news_YYYYMMDD.db data/news.db
```

---

## 5. Telegram 알림 설정

### 환경변수 설정
`.env` 파일에 추가:
```
TELEGRAM_BOT_TOKEN=your_bot_token_from_botfather
TELEGRAM_CHAT_ID=your_chat_or_group_id
```

### Bot 생성 방법
1. Telegram에서 @BotFather 검색
2. `/newbot` 명령으로 봇 생성
3. 받은 토큰을 `TELEGRAM_BOT_TOKEN`에 설정
4. 봇에게 메시지 전송 후 `https://api.telegram.org/bot<TOKEN>/getUpdates`로 chat_id 확인
5. `TELEGRAM_CHAT_ID`에 설정

### 알림 테스트
```bash
python3 -c "
from monitoring.notifier import send_telegram, is_configured
print('Configured:', is_configured())
if is_configured():
    send_telegram('<b>테스트</b>\n알림 테스트입니다.')
"
```

---

## 6. 장애 시 자동 동작 시나리오

### 시나리오 A: 스케줄러 크래시
```
news-scheduler.service 크래시
  → systemd가 30초 후 자동 재시작 (Restart=on-failure)
  → 10분 내 5회 이상 크래시 시 systemd가 중단 (StartLimitBurst=5)
  → watchdog이 10분마다 감지
    → 1차 실패: systemctl restart news-scheduler 자동 시도
    → 2차 연속 실패: Telegram 알림 발송
```

### 시나리오 B: 에디션 선정 실패
```
news-morning.service 실패 (예: DB 오류)
  → systemd가 60초 후 1회 재시도 (Restart=on-failure)
  → watchdog이 10분마다 감지
    → 1차 실패: daily_news_selector.py 자동 실행
    → 2차 연속 실패: Telegram 알림 발송
```

### 시나리오 C: 분석 부족
```
09시 이후 분석 완료 기사 < 5건
  → watchdog이 10분마다 감지
    → 2차 연속 실패: Telegram 알림 ("분석 부족, Ollama 확인 필요")
    → 수동 조치: Ollama 재시작 + 스케줄러 재시작
```

### 시나리오 D: 서버 재부팅
```
서버 재부팅
  → systemd가 자동으로 모든 enabled 서비스 시작
  → news-scheduler.service 자동 시작 (WantedBy=multi-user.target)
  → 모든 timer 자동 시작
  → Persistent=true: 부팅 전 놓친 타이머 즉시 실행
  → watchdog이 5분 후 첫 체크 (OnBootSec=5min)
```

---

## 7. cron에서 마이그레이션

`install_systemd.sh`가 자동으로:
1. 기존 crontab 백업 (`~/crontab_backup_YYYYMMDD.txt`)
2. 뉴스 관련 cron 항목에 `# MIGRATED TO SYSTEMD:` 접두사 추가
3. systemd 타이머 활성화

수동 확인:
```bash
# cron 항목 확인
crontab -l

# systemd 타이머가 정상 동작하는지 확인
systemctl list-timers 'news-*'
```

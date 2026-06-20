# CNI 3단계 통합 아키텍처 — 자기 진화형 지식 플랫폼
> 구현일: 2026-04-05 | 그래프: 2,749노드, 3,052관계

## 1. 전체 통합 아키텍처

```
┌─────────────────────────────────────────────────────────┐
│ [실시간 경로 — 사용자 대면]                                │
│ 뉴스 → 요약(ZH) → Papago(KO) → 헤드라인(KO) → 사용자    │
│                                                         │
│    ↓ 팁: 그래프 우선 → fallback                          │
│    ←── Neo4j 고weight 경로 조회                          │
└─────────────┬───────────────────────────┬───────────────┘
              │                           │
              ▼                           ▼
┌─────────────────────┐    ┌──────────────────────────┐
│ [비동기: 그래프 학습]  │    │ [비동기: 사용자 행동 수집] │
│                     │    │                          │
│ CQ 생성 (Qwen)      │    │ click/scroll/dwell/share │
│ ↓                   │    │ ↓                        │
│ Neo4j 조회           │    │ user_events 테이블       │
│ ↓                   │    │ ↓                        │
│ 그래프 확장 (MERGE)   │    │ reward 계산              │
│ ↓                   │    │ ↓                        │
│ weight 학습 반영      │◄───│ Neo4j weight 업데이트    │
└─────────────────────┘    └──────────────────────────┘
              │                           │
              └───────────┬───────────────┘
                          ▼
              ┌───────────────────────┐
              │ 다음 뉴스 처리에 반영   │
              │ - 트렌딩 엔티티 우선   │
              │ - 고 weight 경로 활용  │
              │ - CQ 우선순위 조정     │
              └───────────────────────┘
```

## 2. 모듈 구성도

| 모듈 | 파일 | 역할 | 경로 |
|------|------|------|------|
| 뉴스 처리 | `generate_cni_fields.py` | 요약/팁/헤드라인 생성 | 실시간 |
| CQ 생성 | `cq_generator.py` | CQ 자동 생성 + 분류 + Cypher 변환 | 비동기 |
| 그래프 처리 | `cq_executor.py` | CQ 실행 + 그래프 확장 | 비동기 |
| 팁 생성 | `graph_tip.py` | 그래프 경로 → 팁 데이터 → Qwen 설명 | 실시간 |
| 행동 수집 | `reward_engine.py` | 이벤트 기록 + 보상 계산 | 비동기 |
| 학습 통합 | `learning_loop.py` | 전체 루프 오케스트레이션 | 비동기 |

## 3. 데이터 흐름

### 뉴스 → 그래프 → 팁
```json
{
  "news_id": 132770,
  "graph_context": {
    "cause": [{"entity": "GPT-5.4", "action": "TRIGGERS", "target": "AI산업"}],
    "change": [{"from": "AI산업", "to": "홍콩시장", "relation": "RELATED_TO"}],
    "result": [{"entity": "Token", "impact": "소비량 급증"}],
    "korea_impact": [{"path": "AI산업 → 글로벌 → 공급망", "relevance": "2홉"}]
  },
  "tip": "💡 GPT-5.4와 XPeng의 활약으로 AI 산업이..."
}
```

### 사용자 → reward → weight
```json
{
  "event": {"news_id": 132770, "type": "completion", "value": 1.0},
  "reward": 6.0,
  "weight_update": {"delta": 0.3, "relations_updated": 6}
}
```

## 4. fallback 전략

| 단계 | 실패 시 | fallback |
|------|---------|----------|
| CQ 생성 | Ollama 실패 | 건너뜀 (그래프 확장 생략) |
| 그래프 조회 | Neo4j 다운 | 텍스트 기반 팁 생성 |
| 팁 생성 | 그래프 데이터 없음 | 기존 TIP_PROMPT 사용 |
| 학습 | reward 실패 | 기존 weight 유지 |
| 전체 루프 | 어떤 단계든 실패 | 실시간 파이프라인 100% 정상 |

## 5. 실행 명령어

```bash
# 학습 상태 확인
python3 -m src.kg.learning_loop --status

# 단일 뉴스 학습
python3 -m src.kg.learning_loop --news 132770

# 일일 배치 (cron 01:00)
python3 -m src.kg.learning_loop --daily --limit 50

# reward 처리
python3 -m src.kg.reward_engine --process

# decay 적용
python3 -m src.kg.reward_engine --decay
```

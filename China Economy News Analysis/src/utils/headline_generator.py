"""Card headline generator for mobile UI.

Generates attention-grabbing headlines (max 18 Korean characters)
from original news titles using Ollama local LLM.
"""

import logging
from pathlib import Path
import sys

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from src.database.models import get_connection

logger = logging.getLogger(__name__)

# Maximum characters for card headline (Korean)
# 18자: 모바일 375px 1위 헤드라인 한 줄 표시 기준
MAX_HEADLINE_LENGTH = 18
MAX_RETRY_COUNT = 2  # 18자 초과시 재생성 시도 횟수

# Ollama configuration
OLLAMA_URL = "http://localhost:11434/api/generate"
HEADLINE_MODEL = "exaone3.5:2.4b"
HEADLINE_MODEL_FALLBACK = "llama3:8b"

# Forbidden phrases
FORBIDDEN_PHRASES = [
    "에 따르면", "관련", "에 대한", "대해", "위한",
    "주목", "관심", "이슈", "화제", "논란",
]

HEADLINE_PROMPT = """역할: 당신은 숙련된 뉴스 데스크 편집자다.
목표: 독자가 헤드라인만 보고도 기사 핵심 내용을 70% 이상 추측할 수 있게 한다.

## 핵심 규칙 (반드시 준수)

★★★ 글자 수: 반드시 18자 이내 (공백 포함, 절대 초과 금지) ★★★

1. "사실 + 대상 + 결과" 구조로 작성
2. 숫자, 고유명사, 행위 주체 포함
3. 추상적 표현 금지 ('논란', '파장', '관심', '주목' 사용 금지)
4. 제목만 읽어도 "무슨 일이 있었는지" 명확해야 함

## 예시 (모두 18자 이내)

원본: "밍밍헌마오 900억 위안 IPO 이면"
헤드라인: 밍밍헌마오 900억 IPO (12자)

원본: "대형 증권사 집중 투자, 우주항공 IPO"
헤드라인: 대형 증권사 우주 IPO 투자 (14자)

원본: "오늘 77종목 상한가, 상하이지수 4100선"
헤드라인: 상하이 4100선 77종목 상한가 (16자)

원본: "EU 집행위, 중국 풍력기업 조사 착수"
헤드라인: EU 중국 풍력기업 조사 착수 (15자)

원본: "국가외환관리국 1월 외환보유고 발표"
헤드라인: 중국 1월 외환보유고 발표 (13자)

## 입력

원본 제목: {title}

## 출력

18자 이내 헤드라인만 출력. 따옴표, 설명, 글자수 표기 없이 헤드라인만."""


RETRY_PROMPT = """이전 헤드라인이 18자를 초과했습니다.

이전 시도: "{previous}" ({length}자)

★★★ 반드시 18자 이내로 더 짧게 작성하세요 ★★★

- 불필요한 조사/수식어 제거
- 핵심 키워드만 남기기
- 숫자 축약 (예: 6300억 → 6천억)

원본: {title}

18자 이내 헤드라인만 출력:"""


def _get_available_model() -> str:
    """Check which headline model is available on Ollama."""
    try:
        resp = requests.get("http://localhost:11434/api/tags", timeout=5)
        if resp.ok:
            models = [m["name"] for m in resp.json().get("models", [])]
            # Prefer EXAONE, fall back to llama3
            for candidate in [HEADLINE_MODEL, HEADLINE_MODEL_FALLBACK]:
                if candidate in models:
                    return candidate
                # Check without tag (e.g. "exaone3.5:2.4b" matches "exaone3.5:2.4b")
                base = candidate.split(":")[0]
                for m in models:
                    if m.startswith(base):
                        return m
    except Exception:
        pass
    return HEADLINE_MODEL_FALLBACK


def _call_ollama(prompt: str, model: str) -> str | None:
    """Call Ollama API and return the generated text."""
    try:
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "num_predict": 64,
                "temperature": 0.3,
            },
        }
        resp = requests.post(OLLAMA_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json().get("response", "").strip()
    except Exception as e:
        logger.error(f"Ollama call failed: {e}")
        return None


def generate_headline(title: str) -> str:
    """Generate a card headline from the original title.

    Uses Ollama LLM with retry logic. Falls back to truncation on failure.

    Args:
        title: Original translated news title

    Returns:
        Card headline (max 18 characters)
    """
    if not title or not title.strip():
        return ""

    # Already short enough
    if len(title) <= MAX_HEADLINE_LENGTH:
        return _clean_headline(title)

    model = _get_available_model()
    prompt = HEADLINE_PROMPT.format(title=title)
    raw = _call_ollama(prompt, model)

    if not raw:
        logger.warning(f"Ollama failed, using fallback for: {title[:30]}")
        return _fallback_headline(title)

    headline = _clean_headline(raw)

    # Retry if over limit
    for attempt in range(MAX_RETRY_COUNT):
        if len(headline) <= MAX_HEADLINE_LENGTH:
            break

        logger.info(f"Headline too long ({len(headline)}자), retry {attempt+1}: {headline}")
        retry_prompt = RETRY_PROMPT.format(
            previous=headline, length=len(headline), title=title
        )
        raw = _call_ollama(retry_prompt, model)
        if raw:
            headline = _clean_headline(raw)

    # Final force truncation if still over limit
    if len(headline) > MAX_HEADLINE_LENGTH:
        logger.warning(f"Force truncating: {headline} ({len(headline)}자)")
        headline = headline[:MAX_HEADLINE_LENGTH - 1] + "…"

    return headline


def _clean_headline(headline: str) -> str:
    """Clean and validate the generated headline."""
    # Remove quotes if wrapped
    headline = headline.strip('"\'')

    # Remove forbidden phrases
    for phrase in FORBIDDEN_PHRASES:
        headline = headline.replace(phrase, "")

    # Clean up whitespace
    headline = " ".join(headline.split())

    return headline


def _fallback_headline(title: str) -> str:
    """Fallback headline when LLM fails.

    Truncates the original title to fit the limit.
    """
    # Remove common prefixes
    prefixes_to_remove = ["속보:", "긴급:", "[속보]", "[긴급]", "장중 필독 |", "장중 필독|"]
    for prefix in prefixes_to_remove:
        if title.startswith(prefix):
            title = title[len(prefix):].strip()

    if len(title) <= MAX_HEADLINE_LENGTH:
        return title

    return title[:MAX_HEADLINE_LENGTH-1] + "…"


def save_headline(news_id: int, headline: str) -> bool:
    """Save the card headline to database.

    Args:
        news_id: News ID
        headline: Card headline to save

    Returns:
        True if successful
    """
    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE news
            SET card_headline = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (headline, news_id))

        conn.commit()
        conn.close()
        return True

    except Exception as e:
        print(f"Error saving headline: {e}")
        return False


def get_headline(news_id: int) -> str | None:
    """Get the card headline for a news item.

    Args:
        news_id: News ID

    Returns:
        Card headline or None if not set
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT card_headline FROM news WHERE id = ?", (news_id,))
    row = cursor.fetchone()
    conn.close()

    return row['card_headline'] if row else None


def generate_and_save_headline(news_id: int, title: str) -> str:
    """Generate headline and save to database.

    Args:
        news_id: News ID
        title: Original translated title

    Returns:
        Generated headline
    """
    headline = generate_headline(title)
    save_headline(news_id, headline)
    return headline

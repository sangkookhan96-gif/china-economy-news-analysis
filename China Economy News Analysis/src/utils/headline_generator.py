"""Card headline generator for mobile UI.

Generates attention-grabbing headlines (max 18 Korean characters)
from original news titles using Claude API.
"""

import anthropic
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from config.settings import ANTHROPIC_API_KEY, CLAUDE_MODEL
from src.database.models import get_connection

# Maximum characters for card headline (Korean)
MAX_HEADLINE_LENGTH = 18

# Forbidden phrases
FORBIDDEN_PHRASES = [
    "에 따르면", "관련", "에 대한", "대해", "위한",
    "주목", "관심", "이슈", "화제", "논란",
]

HEADLINE_PROMPT = """당신은 중국 경제 뉴스의 카드 헤드라인 작성 전문가입니다.

원본 제목을 모바일 카드용 헤드라인으로 변환하세요.

## 규칙 (엄격히 준수)

1. **글자수**: 반드시 18자 이내 (공백 포함)
2. **목표**: 사용자가 "왜?" "무슨 일이지?" 궁금해하도록
3. **표현**:
   - 숫자, 변화, 갈등, 정책, 시장 충격 요소 반영
   - 방향성 표현: 상승/하락, 확대/축소, 규제/완화, 위기/회복
   - 간결하고 임팩트 있게
4. **금지**:
   - "~에 따르면", "~관련", "~에 대한" 사용 금지
   - "주목", "관심", "이슈" 등 추상적 표현 금지
   - 설명, 부연, 해설 금지
   - 불필요한 수식어 금지

## 예시

원본: "밍밍헌마오(鸣鸣很忙) 900억 위안 IPO 이면: 겨울에 대어를 잡은 사람"
헤드라인: 900억 IPO, 숨은 승자는

원본: "중금·중신·건투 등 대형 증권사 집중 투자, 상업 우주항공 IPO가 자본시장 새 강자로"
헤드라인: 증권사들, 우주산업 베팅

원본: "장중 필독 | 오늘 77종목 상한가, 상하이지수 4100선 회복"
헤드라인: 상하이 4100 돌파, 왜?

원본: "EU 집행위, 중국 풍력발전 기업 심층 조사 착수... 상무부 반발"
헤드라인: EU, 중국 풍력에 칼 뽑다

## 입력

원본 제목: {title}

## 출력

헤드라인만 출력하세요. 따옴표, 설명, 다른 텍스트 없이 헤드라인만."""


def generate_headline(title: str) -> str:
    """Generate a card headline from the original title.

    Args:
        title: Original translated news title

    Returns:
        Card headline (max 18 characters)
    """
    if not ANTHROPIC_API_KEY:
        return _fallback_headline(title)

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=100,
            messages=[
                {
                    "role": "user",
                    "content": HEADLINE_PROMPT.format(title=title)
                }
            ]
        )

        headline = response.content[0].text.strip()

        # Validate and clean
        headline = _clean_headline(headline)

        # Ensure length limit
        if len(headline) > MAX_HEADLINE_LENGTH:
            headline = headline[:MAX_HEADLINE_LENGTH-1] + "…"

        return headline

    except Exception as e:
        print(f"Headline generation error: {e}")
        return _fallback_headline(title)


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
    """Fallback headline when API fails.

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

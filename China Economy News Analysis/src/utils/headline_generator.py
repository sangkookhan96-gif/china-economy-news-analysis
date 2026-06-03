"""Card headline generator for mobile UI.

Generates attention-grabbing headlines (max 18 Korean characters)
from original news titles using Ollama local LLM.
"""

import logging
import re
from pathlib import Path
import sys

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from src.database.models import get_connection

logger = logging.getLogger(__name__)

# Maximum characters for card headline (Korean)
# 36자: 모바일 2줄 표시 기준 (기존 18자 한 줄 → 36자 두 줄로 확장)
MAX_HEADLINE_LENGTH = 36
MAX_RETRY_COUNT = 2  # 36자 초과시 재생성 시도 횟수

# Ollama configuration
OLLAMA_URL = "http://localhost:11434/api/generate"
HEADLINE_MODEL = "qwen2.5:7b"
HEADLINE_MODEL_FALLBACK = "qwen2.5:7b"
OLLAMA_NUM_GPU = 35
OLLAMA_NUM_CTX = 2048

# Forbidden phrases
FORBIDDEN_PHRASES = [
    "에 따르면", "관련", "에 대한", "대해", "위한",
    "주목", "관심", "이슈", "화제", "논란",
]

HEADLINE_PROMPT = """역할: 당신은 숙련된 뉴스 데스크 편집자다.
목표: 독자가 헤드라인만 보고도 기사 핵심 내용을 70% 이상 추측할 수 있게 한다.

## 핵심 규칙 (반드시 준수)

★★★ 글자 수: 반드시 18자 이내 (공백 포함, 절대 초과 금지) ★★★
★★★ 한국어만 사용. 중국어 한자(汉字) 절대 사용 금지 ★★★

1. "사실 + 대상 + 결과" 구조로 작성
2. 숫자, 고유명사, 행위 주체 포함
3. 추상적 표현 금지 ('논란', '파장', '관심', '주목' 사용 금지)
4. 제목만 읽어도 "무슨 일이 있었는지" 명확해야 함
5. 고유명사 처리 기준:
   - 중국 기업: 한국어 음차 또는 브랜드명 (예: 宁德时代→CATL, 比亚迪→BYD, 华为→화웨이)
   - 중국 인명: 한국어 음차 (예: 习近平→시진핑, 李强→리창)
   - 중국 기관: 번역 또는 약칭 (예: 国务院→국무원, 国家发改委→발개위)

## 예시 (모두 18자 이내, 한국어만 사용)

원본: "밍밍헌마오 900억 위안 IPO 이면"
헤드라인: 밍밍헌마오 900억 IPO

원본: "대형 증권사 집중 투자, 우주항공 IPO"
헤드라인: 대형 증권사 우주 IPO 투자

원본: "오늘 77종목 상한가, 상하이지수 4100선"
헤드라인: 상하이 4100선 77종목 상한가

원본: "EU 집행위, 중국 풍력기업 조사 착수"
헤드라인: EU 중국 풍력기업 조사 착수

원본: "국가외환관리국 1월 외환보유고 발표"
헤드라인: 중국 1월 외환보유고 발표

원본: "화웨이, 5G 장비 수출 규제 대응 전략 발표"
헤드라인: 화웨이 5G 수출규제 대응

## 입력

원본 제목: {title}{summary_section}

## 출력

18자 이내 헤드라인만 출력. 따옴표, 설명, 글자수 표기, 한자 없이 순수 한국어 헤드라인만."""


RETRY_PROMPT = """이전 헤드라인이 18자를 초과했습니다.

이전 시도: "{previous}" ({length}자)

★★★ 반드시 18자 이내로 더 짧게 작성하세요 ★★★
★★★ 한국어만 사용. 중국어 한자 절대 금지 ★★★

- 불필요한 조사/수식어 제거
- 핵심 키워드만 남기기
- 숫자 축약 (예: 6300억 → 6천억)
- 한자가 있으면 한국어 음차로 교체

원본: {title}

18자 이내 순수 한국어 헤드라인만 출력:"""


REVIEW_HEADLINE_PROMPT = """역할: 숙련된 뉴스 데스크 편집자.
목표: 전문가 리뷰 본문에서 가장 핵심적인 사실 하나를 독자가 한눈에 파악하도록 압축한다.

## 핵심 규칙 (반드시 준수)

★★★ 글자 수: 반드시 18자 이내 (공백 포함, 절대 초과 금지) ★★★
★★★ 한국어만 사용. 중국어 한자(汉字) 절대 사용 금지 ★★★
★★★ 주어는 반드시 리뷰 본문의 실제 행위 주체여야 함 ★★★

1. 리뷰 본문에서 가장 중요한 팩트 1개를 선택
2. 숫자, 기업명, 행위 주체 등 구체적 정보 포함
3. 추상적 표현 금지 ('주목', '관심', '파장', '논란' 사용 금지)
4. 헤드라인만 읽어도 "무슨 일이 있었는지" 명확해야 함
5. 고유명사는 한국어 음차 또는 영문 브랜드명 사용

## 예시

리뷰: "2025년 중국 전체 그린채권 발행액이 10778.8억 위안을 기록해 역대 최고치를 달성했습니다..."
헤드라인: 중국 그린채권 역대 최고 1조 위안

리뷰: "화웨이가 새로운 5G 칩셋을 공개하며 미국의 수출규제에 정면으로 대응하는 모습을 보였습니다..."
헤드라인: 화웨이 5G 칩 공개, 미국 규제 정면돌파

리뷰: "광둥성은 제15차 5개년 계획 첫해 제조업과 서비스업 협동 발전 대회를 개최했습니다..."
헤드라인: 광둥성, 제조·서비스 협동발전 대회

리뷰: "MiniMax와 Zhipu 두 AI 대모델 기업이 홍콩 증시에서 주가가 폭등했습니다..."
헤드라인: MiniMax·Zhipu 홍콩 증시 급등

## 입력

전문가 리뷰 본문: {review}

## 출력

18자 이내 헤드라인만 출력. 따옴표, 설명, 글자수 표기, 한자 없이 순수 한국어 헤드라인만."""


def _get_available_model() -> str:
    """Check which headline model is available on Ollama."""
    try:
        resp = requests.get("http://localhost:11434/api/tags", timeout=5)
        if resp.ok:
            models = [m["name"] for m in resp.json().get("models", [])]
            # Qwen2.5 단일 모델
            for candidate in [HEADLINE_MODEL, HEADLINE_MODEL_FALLBACK]:
                if candidate in models:
                    return candidate
                # Check without tag (e.g. "qwen2.5:14b" matches "qwen2.5:14b")
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
                "num_gpu": OLLAMA_NUM_GPU,
                "num_ctx": OLLAMA_NUM_CTX,
            },
        }
        resp = requests.post(OLLAMA_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json().get("response", "").strip()
    except Exception as e:
        logger.error(f"Ollama call failed: {e}")
        return None


def _call_ollama_fast(prompt: str, model: str, num_predict: int = 15) -> str | None:
    """Fast Ollama call for short responses (validation, subject extraction)."""
    try:
        resp = requests.post(OLLAMA_URL, json={
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"num_predict": num_predict, "temperature": 0.0,
                       "num_gpu": OLLAMA_NUM_GPU, "num_ctx": OLLAMA_NUM_CTX},
        }, timeout=30)
        resp.raise_for_status()
        return resp.json().get("response", "").strip()
    except Exception as e:
        logger.error(f"Ollama fast call failed: {e}")
        return None


def validate_headline_subject(headline: str, review: str, model: str) -> tuple[bool, str]:
    """Validate headline subject/predicate against review content.

    Two-layer approach:
      Layer 1 (rule-based, 0ms): first token of headline exists in review text
      Layer 2 (LLM, ~3s): extract correct subject, confirm mismatch

    Returns:
        (is_valid, correct_subject)  — correct_subject is "" when valid
    """
    if not headline or not review:
        return True, ""

    # Layer 1: rule-based — first token present in review?
    first_token = re.split(r'[,\s·]', headline.strip())[0]
    if first_token and first_token.lower() in review.lower():
        return True, ""

    logger.info(f"Rule check fail: [{first_token}] not in review — escalating to LLM")

    # Layer 2: LLM — extract correct subject from review
    subject_prompt = (
        f"리뷰에서 행위의 주체(주어)가 누구인가? 이름만 짧게 답하라.\n리뷰: {review[:200]}"
    )
    correct_subject = _call_ollama_fast(subject_prompt, model, num_predict=15) or ""
    correct_subject = correct_subject.strip()

    # LLM binary validation
    val_prompt = (
        f"헤드라인 주어가 리뷰 주체와 일치하면 true, 불일치하면 false만 출력.\n"
        f"헤드라인: {headline}\n리뷰: {review[:200]}"
    )
    val_raw = _call_ollama_fast(val_prompt, model, num_predict=5) or "false"
    is_valid = "true" in val_raw.lower()

    if not is_valid:
        logger.warning(
            f"Subject mismatch — headline: [{headline}], "
            f"correct: [{correct_subject}]"
        )

    return is_valid, correct_subject if not is_valid else ""


def generate_headline_from_review(review_text: str, title: str = "") -> str:
    """Generate a card headline purely from the expert review text.

    Title is intentionally excluded from the LLM prompt to prevent
    translator hallucinations (e.g. wrong subject) from contaminating
    the headline. The review is written in Korean and already contains
    the correct subject.

    Args:
        review_text: Expert review content (Korean)
        title: Used only for fallback when Ollama fails (not sent to LLM)

    Returns:
        Card headline (max 18 characters, Korean only)
    """
    if not review_text or not review_text.strip():
        return generate_headline(title) if title else ""

    model = _get_available_model()
    review_excerpt = review_text.strip()[:300]

    # ① title을 프롬프트에 전달하지 않음 — 오염 원천 차단
    prompt = REVIEW_HEADLINE_PROMPT.format(review=review_excerpt)
    raw = _call_ollama(prompt, model)

    if not raw:
        logger.warning(f"Ollama failed on review, falling back to title: {title[:30]}")
        return generate_headline(title) if title else ""

    headline = _clean_headline(raw)

    # 길이·한자 재시도 루프
    for attempt in range(MAX_RETRY_COUNT):
        too_long = len(headline) > MAX_HEADLINE_LENGTH
        has_chinese = _has_chinese(headline)
        if not too_long and not has_chinese:
            break
        reason = f"{len(headline)}자" if too_long else "한자 포함"
        logger.info(f"Review headline retry ({reason}) #{attempt+1}: {headline}")
        retry_prompt = RETRY_PROMPT.format(
            previous=headline, length=len(headline), title=review_excerpt[:50]
        )
        raw = _call_ollama(retry_prompt, model)
        if raw:
            headline = _clean_headline(raw)

    if len(headline) > MAX_HEADLINE_LENGTH:
        logger.warning(f"Force truncating review headline: {headline}")
        headline = headline[:MAX_HEADLINE_LENGTH - 1] + "…"

    if _has_chinese(headline):
        logger.warning(f"Chinese chars remain in review headline: {headline}")

    # ② 주어·술어 논리 검증 + 재생성 (1회 한도)
    is_valid, correct_subject = validate_headline_subject(headline, review_excerpt, model)
    if not is_valid and correct_subject:
        logger.info(f"Subject fix: [{headline}] → subject hint [{correct_subject}]")
        regen_prompt = (
            f"리뷰: {review_excerpt}\n"
            f"주어는 반드시 \"{correct_subject}\"이어야 한다.\n"
            f"18자 이내 한국어 헤드라인만:"
        )
        raw2 = _call_ollama(regen_prompt, model)
        if raw2:
            candidate = _clean_headline(raw2)
            if len(candidate) <= MAX_HEADLINE_LENGTH and not _has_chinese(candidate):
                headline = candidate
                logger.info(f"Subject-corrected headline: [{headline}]")
            else:
                logger.warning(f"Subject-fix candidate rejected: [{candidate}]")

    return headline


def generate_headline(title: str, summary: str = "") -> str:
    """Generate a card headline from the original title.

    Uses Ollama LLM with retry logic. Falls back to truncation on failure.

    Args:
        title: Original translated news title
        summary: Optional Korean summary for context (improves proper noun handling)

    Returns:
        Card headline (max 18 characters)
    """
    if not title or not title.strip():
        return ""

    # Already short enough AND no Chinese characters — skip LLM
    if len(title) <= MAX_HEADLINE_LENGTH and not _has_chinese(title):
        return _clean_headline(title)

    model = _get_available_model()
    # Build summary section for prompt (방안 C)
    summary_section = ""
    if summary and summary.strip():
        summary_section = f"\n요약 (참고): {summary.strip()[:150]}"
    prompt = HEADLINE_PROMPT.format(title=title, summary_section=summary_section)
    raw = _call_ollama(prompt, model)

    if not raw:
        logger.warning(f"Ollama failed, using fallback for: {title[:30]}")
        return _fallback_headline(title)

    headline = _clean_headline(raw)

    # Retry if over limit or still contains Chinese characters
    for attempt in range(MAX_RETRY_COUNT):
        too_long = len(headline) > MAX_HEADLINE_LENGTH
        has_chinese = _has_chinese(headline)
        if not too_long and not has_chinese:
            break

        reason = f"{len(headline)}자" if too_long else "한자 포함"
        logger.info(f"Headline needs retry ({reason}), retry {attempt+1}: {headline}")
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

    if _has_chinese(headline):
        logger.warning(f"Chinese characters remain after retries: {headline}")

    return headline


def _has_chinese(text: str) -> bool:
    """Check if text contains Chinese characters."""
    return bool(re.search(r'[\u4e00-\u9fff]', text))


def _clean_headline(headline: str) -> str:
    """Clean and validate the generated headline."""
    # Remove quotes if wrapped
    headline = headline.strip('"\'')

    # Remove (N자) character count annotations that LLM sometimes appends
    headline = re.sub(r'\s*\(\d+자\)\s*$', '', headline)

    # Remove forbidden phrases
    for phrase in FORBIDDEN_PHRASES:
        headline = headline.replace(phrase, "")

    # 방안 A: 중국어 한자 탐지 시 사전 교체 시도
    if _has_chinese(headline):
        try:
            from src.utils.title_postprocessor import apply_dictionary
            headline, _ = apply_dictionary(headline)
        except Exception:
            pass

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

    Priority:
      1. expert_comment (published review) — Korean text, no Chinese chars
      2. translated_title + summary — fallback when review not yet written

    Args:
        news_id: News ID
        title: Original translated title (used as fallback / proper noun context)

    Returns:
        Generated headline
    """
    expert_comment = ""
    summary = ""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        # 1순위: 게시된 리뷰 텍스트
        cursor.execute(
            "SELECT expert_comment FROM expert_reviews WHERE news_id = ? AND publish_status = 'published'",
            (news_id,)
        )
        row = cursor.fetchone()
        if row and row["expert_comment"]:
            expert_comment = row["expert_comment"]
        # summary는 fallback 경로용으로 조회
        if not expert_comment:
            cursor.execute("SELECT summary FROM news WHERE id = ?", (news_id,))
            row = cursor.fetchone()
            if row and row["summary"]:
                summary = row["summary"]
        conn.close()
    except Exception:
        pass

    if expert_comment:
        # title을 전달하지 않음 — 잘못된 번역 제목이 헤드라인을 오염시키는 것을 방지
        headline = generate_headline_from_review(expert_comment)
    else:
        headline = generate_headline(title, summary=summary)

    save_headline(news_id, headline)
    return headline

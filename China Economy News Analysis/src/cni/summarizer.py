"""CNI Summarizer — 중문 원문 → 중문 300자 요약.

LLM: Ollama qwen2.5:7b (로컬, 무료)
"""

import logging
import requests

logger = logging.getLogger("cni_summarizer")

OLLAMA_URL = "http://localhost:11434/api/generate"
SUMMARIZER_MODEL = "qwen2.5:7b"
TIMEOUT = 120
MAX_SUMMARY_LENGTH = 300

SUMMARIZE_PROMPT = """Summarize the following Chinese economic news article in Chinese (中文).

Rules:
- Keep the summary under 300 Chinese characters
- Focus on: WHO did WHAT, WHEN, and WHAT IMPACT
- Preserve key numbers, company names, and policy names exactly
- Output Chinese text ONLY, no explanation, no English

Article:
{text}

Chinese summary (300字以内):"""


def summarize_zh(full_text: str) -> str:
    """Generate 300-char Chinese summary from full text.

    Args:
        full_text: Chinese news article text

    Returns:
        Chinese summary (max 300 chars). Fallback: first 300 chars of original.
    """
    if not full_text or not full_text.strip():
        return ""

    # Trim input to avoid token overflow
    input_text = full_text[:2000]

    prompt = SUMMARIZE_PROMPT.format(text=input_text)

    try:
        resp = requests.post(
            OLLAMA_URL,
            json={"model": SUMMARIZER_MODEL, "prompt": prompt,
                  "stream": False,
                  "options": {"temperature": 0.1, "num_predict": 500}},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        result = resp.json().get("response", "").strip()

        # Clean markdown fences
        import re
        result = re.sub(r'^```[a-z]*\s*', '', result)
        result = re.sub(r'\s*```$', '', result)
        result = result.strip()

        if result and len(result) > 10:
            return result[:MAX_SUMMARY_LENGTH]

    except Exception as e:
        logger.error(f"Summarize failed: {e}")

    # Fallback: first 300 chars
    logger.warning("Using fallback: first 300 chars of original")
    return full_text[:MAX_SUMMARY_LENGTH]

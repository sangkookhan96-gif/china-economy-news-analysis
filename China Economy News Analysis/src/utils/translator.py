"""Free Chinese-to-Korean translation using Google Translate.

Translation pipeline:
1. Google Translate (Chinese → Korean)
2. Post-processing (dictionary replacements, awkward patterns)
3. Validation (perspective correction: 우리나라 → 중국)
"""

import logging
import time
from typing import Optional

from deep_translator import GoogleTranslator

logger = logging.getLogger(__name__)

_translator = GoogleTranslator(source='zh-CN', target='ko')

# Rate limit: minimum seconds between API calls
_MIN_INTERVAL = 0.3
_last_call_time = 0.0


def _apply_postprocessing(text: str) -> str:
    """Apply post-processing to translated text.

    Args:
        text: Translated Korean text

    Returns:
        Post-processed text
    """
    try:
        from src.utils.title_postprocessor import postprocess
        from src.utils.title_validator import correct_title

        # Step 1: Apply dictionary and pattern corrections
        text = postprocess(text)

        # Step 2: Fix perspective expressions (우리나라 → 중국)
        text = correct_title(text)

        return text
    except ImportError as e:
        logger.warning(f"Post-processing modules not available: {e}")
        return text
    except Exception as e:
        logger.warning(f"Post-processing failed: {e}")
        return text


def translate_zh_to_ko(text: str, max_length: int = 5000, apply_postprocess: bool = True) -> Optional[str]:
    """Translate Chinese text to Korean using Google Translate (free).

    Args:
        text: Chinese text to translate.
        max_length: Truncate input to this length (Google free limit ~5000 chars).
        apply_postprocess: Apply post-processing (dictionary, patterns, validation).

    Returns:
        Korean translation, or None on failure.
    """
    global _last_call_time

    if not text or not text.strip():
        return None

    text = text.strip()[:max_length]

    # Rate limiting
    elapsed = time.time() - _last_call_time
    if elapsed < _MIN_INTERVAL:
        time.sleep(_MIN_INTERVAL - elapsed)

    try:
        result = _translator.translate(text)
        _last_call_time = time.time()

        # Apply post-processing if enabled
        if result and apply_postprocess:
            result = _apply_postprocessing(result)

        return result
    except Exception as e:
        logger.warning(f"Translation failed: {e}")
        _last_call_time = time.time()
        return None


def translate_news_titles(batch_size: int = 50, dry_run: bool = False) -> int:
    """Translate all untranslated news titles in the database.

    Args:
        batch_size: Number of titles to translate per run.
        dry_run: If True, print translations without saving.

    Returns:
        Number of titles translated.
    """
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    from src.database.models import get_connection

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, original_title FROM news
        WHERE translated_title IS NULL
          AND original_title IS NOT NULL
        ORDER BY id DESC
        LIMIT ?
    """, (batch_size,))

    rows = cursor.fetchall()
    if not rows:
        logger.info("No untranslated titles found")
        conn.close()
        return 0

    translated_count = 0

    for row in rows:
        news_id = row['id']
        title = row['original_title']

        ko_title = translate_zh_to_ko(title)

        if ko_title:
            if dry_run:
                print(f"[{news_id}] {title}")
                print(f"    → {ko_title}")
            else:
                cursor.execute(
                    "UPDATE news SET translated_title = ? WHERE id = ?",
                    (ko_title, news_id)
                )
                translated_count += 1

                if translated_count % 10 == 0:
                    conn.commit()
                    logger.info(f"Translated {translated_count}/{len(rows)}")
        else:
            logger.warning(f"Skipped news {news_id}: translation failed")

    if not dry_run:
        conn.commit()

    conn.close()
    logger.info(f"Translation complete: {translated_count}/{len(rows)}")
    return translated_count


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description="Translate Chinese news titles to Korean")
    parser.add_argument("--batch", type=int, default=50, help="Batch size")
    parser.add_argument("--dry-run", action="store_true", help="Preview without saving")
    parser.add_argument("--all", action="store_true", help="Translate all untranslated")
    args = parser.parse_args()

    batch = 9999 if args.all else args.batch
    count = translate_news_titles(batch_size=batch, dry_run=args.dry_run)
    print(f"\nDone: {count} titles translated")

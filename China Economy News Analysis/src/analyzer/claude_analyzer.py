"""Claude AI-based news analyzer."""

import json
import logging
from datetime import datetime
from typing import Optional

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from config.settings import ANTHROPIC_API_KEY, CLAUDE_MODEL, MAX_TOKENS
from src.database.models import get_connection

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ClaudeAnalyzer:
    """Analyzer using Claude API for translation, summarization, and scoring."""

    def __init__(self):
        if not ANTHROPIC_API_KEY:
            raise ValueError("ANTHROPIC_API_KEY not set")

        import anthropic
        self.client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        self.model = CLAUDE_MODEL

    def analyze_news(self, news_id: int) -> dict:
        """Analyze a single news item: translate, summarize, classify, score."""
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM news WHERE id = ?", (news_id,))
        news = cursor.fetchone()

        if not news:
            return {"error": "News not found"}

        title = news["original_title"]
        content = news["original_content"] or ""

        # Build prompt
        prompt = f"""다음 중국어 뉴스를 분석해주세요.

원문 제목: {title}
원문 내용: {content[:3000] if content else "(본문 없음)"}

다음 JSON 형식으로 응답해주세요:
{{
    "translated_title": "한국어 번역 제목",
    "summary": "150-300자 한국어 요약 (3-5문장)",
    "importance_score": 0.0-1.0 사이 점수 (정책 영향력, 산업 파급력, 시장 영향 기준),
    "industry_category": "semiconductor/ai/new_energy/bio/aerospace/quantum/materials/other 중 하나",
    "content_type": "policy/corporate/industry/market/opinion 중 하나",
    "sentiment": "positive/negative/neutral 중 하나",
    "keywords": ["키워드1", "키워드2", "키워드3"],
    "market_impact": "시장 영향 예측 (1-2문장)"
}}

전문 용어는 정확하게 번역하고, 중요도 평가는 기관투자자/기업전략팀 관점에서 판단해주세요."""

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}]
            )

            result_text = response.content[0].text

            # Parse JSON from response
            json_match = result_text
            if "```json" in result_text:
                json_match = result_text.split("```json")[1].split("```")[0]
            elif "```" in result_text:
                json_match = result_text.split("```")[1].split("```")[0]

            result = json.loads(json_match.strip())

            # Update database
            cursor.execute("""
                UPDATE news SET
                    translated_title = ?,
                    summary = ?,
                    importance_score = ?,
                    industry_category = ?,
                    content_type = ?,
                    sentiment = ?,
                    keywords = ?,
                    market_impact = ?,
                    analyzed_at = ?,
                    updated_at = ?
                WHERE id = ?
            """, (
                result.get("translated_title"),
                result.get("summary"),
                result.get("importance_score", 0.5),
                result.get("industry_category"),
                result.get("content_type"),
                result.get("sentiment"),
                json.dumps(result.get("keywords", []), ensure_ascii=False),
                result.get("market_impact"),
                datetime.now(),
                datetime.now(),
                news_id,
            ))
            conn.commit()

            logger.info(f"Analyzed news {news_id}: {result.get('translated_title', '')[:30]}...")
            return result

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON response: {e}")
            return {"error": f"JSON parse error: {e}"}
        except Exception as e:
            logger.error(f"Analysis failed for news {news_id}: {e}")
            return {"error": str(e)}
        finally:
            conn.close()

    def analyze_unanalyzed(self, limit: int = 10) -> list[dict]:
        """Analyze all unanalyzed news items."""
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id FROM news
            WHERE analyzed_at IS NULL
            ORDER BY collected_at DESC
            LIMIT ?
        """, (limit,))
        news_ids = [row["id"] for row in cursor.fetchall()]
        conn.close()

        results = []
        for news_id in news_ids:
            result = self.analyze_news(news_id)
            results.append({"news_id": news_id, **result})

        return results


def main():
    """Run analyzer on unanalyzed news."""
    analyzer = ClaudeAnalyzer()
    results = analyzer.analyze_unanalyzed(limit=5)
    print(f"Analyzed {len(results)} news items")


if __name__ == "__main__":
    main()

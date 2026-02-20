"""Claude AI-based news analyzer."""

import json
import logging
import requests
from datetime import datetime
from typing import Optional

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from config.settings import MAX_TOKENS
from src.database.models import get_connection

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ClaudeAnalyzer:
    """Analyzer using Claude API for translation, summarization, and scoring."""

    def __init__(self):
        self.ollama_url = "http://localhost:11434/api/generate"
        self.model = "llama3:8b"

    def _validate_scores(self, result: dict) -> dict:
        """Validate and clamp score values from Ollama."""
        score_fields = [
            'importance_score', 'market_relevance_score',
            'uncertainty_score', 'expert_explainability_score'
        ]
        for field in score_fields:
            val = result.get(field)
            if val is None:
                result[field] = 0.5
            elif not isinstance(val, (int, float)):
                try:
                    result[field] = float(val)
                except (ValueError, TypeError):
                    result[field] = 0.5
            result[field] = round(max(0.0, min(1.0, float(result[field]))), 2)
        return result

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

        # Build prompt (English prompt for better JSON/numeric accuracy with llama3:8b)
        prompt = f"""You are a Chinese economic news analyst for Korean institutional investors.
Analyze the following Chinese news article.

Title: {title}
Content: {content[:3000] if content else "(no content)"}

Respond ONLY with a JSON object. No explanation, no markdown.

## SCORING RUBRIC (scores MUST vary between articles!)

### importance_score (policy/industry impact)
0.1-0.2: Local district events, routine statistics, personnel appointments, ceremony notices
0.3-0.4: Individual company earnings, local government policy, minor industry news
0.5-0.6: Industry trends, central ministry routine policy, major corporate strategy changes
0.7-0.8: State Council policy, large M&A (>10B CNY), US-China trade, core industry regulation
0.9-1.0: NPC/Politburo decisions, GDP/interest rate changes, nationwide industrial restructuring

### market_relevance_score (direct financial market impact)
0.1-0.3: Not market-related (social, cultural, local administration)
0.4-0.6: Indirect impact (industry policy, technology trends, long-term plans)
0.7-0.9: Direct market impact (interest rates, forex, IPO, stock market regulation, earnings)

### uncertainty_score (information uncertainty)
0.1-0.3: Confirmed announcement (law passed, finalized earnings, official data release)
0.4-0.6: Direction set but details pending (policy draft, preliminary results)
0.7-0.9: Under review, rumors, forecasts, speculation

### expert_explainability_score (need for expert explanation)
0.1-0.3: General public can understand (simple event reporting)
0.4-0.6: Some background knowledge needed (industry context)
0.7-0.9: Expert explanation essential (complex policy, technical financial instruments)

## DISTRIBUTION GUIDE
- ~50% of news should score importance 0.3-0.5 (routine news)
- ~30% should score importance 0.5-0.7 (notable news)
- ~15% should score importance 0.7-0.8 (significant news)
- ~5% should score importance 0.9+ (major breaking news)
- DO NOT default to 0.8 for everything. Most news is routine (0.3-0.5).

## EXAMPLES

Example 1 (LOW importance=0.2):
"深圳市宝安区举办2024年创新创业大赛" → Local district event, no market impact

Example 2 (MEDIUM importance=0.5):
"宁德时代发布2024年固态电池技术路线图" → Major company tech roadmap, indirect market impact

Example 3 (HIGH importance=0.85):
"国务院发布半导体产业扶持新政 总投资3000亿元" → State Council policy, massive investment, direct market impact

## OUTPUT FORMAT
{{
  "translated_title": "Korean translation of title",
  "summary": "150-300자 한국어 요약 (3-5문장)",
  "importance_score": <float 0.0-1.0>,
  "market_relevance_score": <float 0.0-1.0>,
  "uncertainty_score": <float 0.0-1.0>,
  "expert_explainability_score": <float 0.0-1.0>,
  "industry_category": "<semiconductor|ai|new_energy|bio|aerospace|quantum|materials|other>",
  "content_type": "<policy|corporate|industry|market|opinion>",
  "sentiment": "<positive|negative|neutral>",
  "keywords": ["keyword1", "keyword2", "keyword3"],
  "market_impact": "1-2 sentence market impact prediction in Korean"
}}"""

        try:
            payload = {"model": self.model, "prompt": prompt, "stream": False, "format": "json", "options": {"num_predict": MAX_TOKENS}}

            response = requests.post(self.ollama_url, json=payload)
            response.raise_for_status()

            result_json = response.json()
            result_text = result_json.get("response", "")


            # Parse JSON from response
            json_match = result_text
            if "```json" in result_text:
                json_match = result_text.split("```json")[1].split("```")[0]
            elif "```" in result_text:
                json_match = result_text.split("```")[1].split("```")[0]

            result = json.loads(json_match.strip())
            result = self._validate_scores(result)

            # Update database
            cursor.execute("""
                UPDATE news SET
                    translated_title = ?,
                    summary = ?,
                    importance_score = ?,
                    market_relevance_score = ?,
                    uncertainty_score = ?,
                    expert_explainability_score = ?,
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
                result.get("market_relevance_score", 0.5),
                result.get("uncertainty_score", 0.5),
                result.get("expert_explainability_score", 0.5),
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

            # Generate topic vector after analysis
            try:
                from src.analyzer.embeddings import generate_topic_vector
                generate_topic_vector(news_id)
            except Exception as e:
                logger.warning(f"Failed to generate topic vector for news {news_id}: {e}")

            logger.info(
                f"Analyzed news {news_id}: "
                f"imp={result.get('importance_score', 0):.2f} "
                f"mkt={result.get('market_relevance_score', 0):.2f} "
                f"unc={result.get('uncertainty_score', 0):.2f} "
                f"| {result.get('translated_title', '')[:40]}..."
            )
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

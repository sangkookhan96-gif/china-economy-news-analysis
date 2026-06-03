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
        # qwen2.5:7b — 14b는 12GB GPU에 35/49만 적재돼 호출당 ~90초였음. 7b는
        # 전체 GPU 적재로 ~3배 빠르고 CNI 경로와 동일 모델이라 load/unload 경합도
        # 감소. num_gpu=35는 7b(29레이어)에선 전체 적재를 의미. (2026-06-02)
        self.model = "qwen2.5:7b"

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

        # Build prompt
        from datetime import date as _date
        _today = _date.today().strftime("%Y-%m-%d")
        prompt = f"""You are a Chinese economic news analyst for Korean institutional investors.
Today's date is {_today}. All 2025 data and earlier are CONFIRMED PAST facts, not forecasts.
When translating 2025 statistics, use past tense (e.g. "달성했습니다", "기록했습니다"),
NOT future/prediction language (e.g. "예상됩니다", "것으로 보입니다").
Analyze the following Chinese news article.

CRITICAL LANGUAGE REQUIREMENT:
- translated_title: MUST be written in Korean (한국어). Translate the actual title above, do NOT copy this example: "국무원, 반도체 산업 지원 신정책 발표"
- summary: MUST be written in Korean (한국어).
- market_impact: MUST be written in Korean (한국어).
- The prose must be Korean, but proper nouns MAY be annotated with their Chinese
  (and English, if widely known) form on FIRST mention only, using parentheses:
    · 인물: "음차 직책(汉字)"            예) 시진핑 국가주석(习近平)
    · 중국 기업: "음차(汉字)"             예) 샤오미(小米)
    · 영문 통용 기업: "음차(汉字, English)" 예) 닝더스다이(宁德时代, CATL)
    · 정부·기관: "약칭(汉字)"             예) 국무원(国务院)
  Only annotate the FIRST occurrence. Reuse the plain Korean form afterwards.
  Do NOT insert Chinese or English for generic words — only proper nouns.

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
"深圳市宝安区举办2024年创新创业大赛" → translated_title: "선전시 바오안구, 2024 창업대회 개최"

Example 2 (MEDIUM importance=0.5):
"宁德时代发布2024年固态电池技术路线图" → translated_title: "CATL, 2024 전고체 배터리 기술 로드맵 발표"

Example 3 (HIGH importance=0.85):
"国务院发布半导体产业扶持新政 总投资3000亿元" → translated_title: "국무원, 반도체 산업 지원 신정책 발표…총투자 3,000억 위안"

## OUTPUT FORMAT
{{
  "translated_title": "반드시 한국어로 번역한 제목",
  "summary": "150-300자 한국어 요약 (3-5문장)",
  "importance_score": <float 0.0-1.0>,
  "market_relevance_score": <float 0.0-1.0>,
  "uncertainty_score": <float 0.0-1.0>,
  "expert_explainability_score": <float 0.0-1.0>,
## INDUSTRY CLASSIFICATION (GICS-based — choose the MOST SPECIFIC code you can confidently assign)

GICS Sub-Industry L4 (prefer these):
  20101010=항공우주방산 | 45301020=반도체 | 45102010=AI·소프트웨어 | 45301010=반도체장비소재
  25102010=자동차EV | 25101010=자동차부품EV배터리 | 55105020=재생에너지 | 55101010=전력유틸리티
  55105010=발전에너지저장 | 50203010=인터넷플랫폼 | 45201020=통신장비 | 20106020=산업기계로봇
  15104020=광업희토류 | 45101030=인터넷인프라 | 15101050=특수화학신소재
  50102010=무선통신 | 50101020=통신서비스 | 10102010=석유가스
  40101010=종합은행 | 40201060=핀테크결제 | 35201010=바이오 | 30202030=식품농업
  40203020=증권IPO | 35202010=제약 | 45102020=시스템소프트웨어 | 40301030=보험
  60102030=부동산개발 | 35101010=의료기기 | 40203010=자산운용 | 45101010=IT서비스 | 25302010=교육

GICS L2/L1 fallback (when L4 uncertain):
  4530=반도체산업군 | 4510=소프트웨어서비스 | 45=정보기술섹터 | 40=금융섹터

Extension (no GICS code fits the article topic):
  EXT_POLICY=정부정책규제(보조금·5개년계획·복수산업동시해당)
  EXT_GEOPOLITICS=지정학무역(중미관계·관세·수출통제 — 특정산업미귀속)
  EXT_MACRO=거시경제지표(GDP·PMI·인민은행기준금리·사융 — 특정산업미귀속)
  other=기타

Rule: L4 first → L2/L1 fallback → Extension → other. ONE code only.
  "industry_category": "<one code from the lists above>",
  "content_type": "<policy|corporate|industry|market|opinion>",
  "sentiment": "<positive|negative|neutral>",
  "keywords": ["keyword1", "keyword2", "keyword3"],
  "market_impact": "한국어로 작성한 1-2문장 시장 영향 예측"
}}"""

        try:
            payload = {"model": self.model, "prompt": prompt, "stream": False, "format": "json",
                       "options": {"num_predict": MAX_TOKENS, "num_gpu": 35, "num_ctx": 2048}}

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

            # Title post-processing: dictionary-based Chinese→Korean replacement
            # and awkward expression cleanup. Must run BEFORE proper noun
            # formatter so residual Chinese (同比, 净利润, etc.) is converted first.
            try:
                from src.utils.title_postprocessor import postprocess_title
                if result.get("translated_title"):
                    pp = postprocess_title(result["translated_title"])
                    result["translated_title"] = pp.processed
            except Exception as e:
                logger.warning(f"Title postprocess failed for {news_id}: {e}")

            # Unified post-translation QC — run on the raw translation BEFORE
            # proper-noun annotation so the Papago fallback (if needed) doesn't
            # strip annotations. Fixes 我国/우리나라→중국, Chinese-only leaks,
            # 평어체, and sentence truncation on every analyze.
            try:
                from src.cni.translation_qc import run_qc
                for _f in ("translated_title", "summary", "market_impact"):
                    if result.get(_f):
                        result[_f], _iss = run_qc(result[_f], _f, news_id)
            except Exception as e:
                logger.warning(f"Translation QC failed for {news_id}: {e}")

            # Dual-script proper noun rendering (first-occurrence only).
            # LLM is instructed to annotate inline, but we normalize via the
            # seed registry so known entities are always rendered consistently.
            try:
                from src.utils.proper_noun_formatter import format_proper_nouns
                source_zh = f"{title or ''}\n{content or ''}"
                for field in ("translated_title", "summary", "market_impact"):
                    if result.get(field):
                        result[field] = format_proper_nouns(result[field], source_zh)
            except Exception as e:
                logger.warning(f"Proper noun formatting failed for {news_id}: {e}")

            # GICS category validation
            from config.gics_taxonomy import ALL_VALID_CODES, get_baseline_score
            raw_cat = (result.get("industry_category") or "other").strip()
            category = raw_cat if raw_cat in ALL_VALID_CODES else "other"
            result["industry_category"] = category

            # Apply category baseline score as importance_score floor
            baseline = get_baseline_score(category)
            current_score = result.get("importance_score", baseline)
            result["importance_score"] = round(max(float(current_score), baseline), 2)

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

    # 분석 대상 최대 연령(일). 에디션 선정은 1일 윈도우만 쓰므로 그보다 오래된
    # 뉴스를 분석하는 것은 낭비이며 CNI/분석 경합만 유발한다. 이 컷오프로 stale
    # 백로그(7일+ 4천여 건) 분석을 제외해 GPU 부하를 ~4배 줄인다. (2026-06-03)
    ANALYZE_MAX_AGE_DAYS = 3

    def analyze_unanalyzed(self, limit: int = 10) -> list[dict]:
        """Analyze unanalyzed news items collected within ANALYZE_MAX_AGE_DAYS."""
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id FROM news
            WHERE analyzed_at IS NULL
              AND COALESCE(collected_at, created_at) >= datetime('now', ?)
            ORDER BY collected_at DESC
            LIMIT ?
        """, (f"-{self.ANALYZE_MAX_AGE_DAYS} days", limit))
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

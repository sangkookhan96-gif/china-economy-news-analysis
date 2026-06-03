"""CNI News Analyzer — 독립 분석 모듈.

원칙:
- 요약/번역과 완전 독립 (이전 생성물을 사실로 사용 금지)
- 입력: 원문 + 요약 (원문에서 직접 분석)
- 추론은 반드시 "추측" 표시
- 근거 없는 주장 금지
"""

import re
import logging
import requests
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

logger = logging.getLogger("cni_analyzer")

OLLAMA_URL = "http://localhost:11434/api/generate"
ANALYSIS_MODEL = "qwen2.5:14b"
TIMEOUT = 120

ANALYSIS_PROMPT = """你是中国经济分析师。请基于以下新闻原文进行分析。

绝对规则：
1. 只基于原文中的事实进行分析
2. 推测性内容必须标注"（推测）"
3. 不要添加原文中没有的数字或事实
4. 不要提及韩国
5. 用中文写，300字以内

分析结构：
- 经济意义：这条新闻对中国经济/行业意味着什么
- 市场影响：可能对相关市场产生的影响
- 关注点：投资者应关注的要点

原文：
{text}

分析："""


def analyze_news(news_id: int, original_content: str, summary_zh: str = "") -> dict:
    """Analyze news independently from summary/translation.

    Args:
        news_id: News ID
        original_content: Original Chinese text (primary source)
        summary_zh: Chinese summary (reference only, not treated as fact)

    Returns:
        {"analysis_zh": str, "analysis_ko": str} or {"error": str}
    """
    text = (original_content or "")[:1500]
    if not text or len(text) < 50:
        return {"error": "Content too short for analysis"}

    try:
        resp = requests.post(
            OLLAMA_URL,
            json={"model": ANALYSIS_MODEL, "prompt": ANALYSIS_PROMPT.format(text=text),
                  "stream": False,
                  "options": {"temperature": 0.1, "num_predict": 400}},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        analysis_zh = resp.json().get("response", "").strip()

        if not analysis_zh or len(analysis_zh) < 20:
            return {"error": "Analysis generation failed"}

        # Clean
        analysis_zh = re.sub(r'^```.*', '', analysis_zh)
        analysis_zh = re.sub(r'```$', '', analysis_zh).strip()

        # Fact-check: 한국 언급 차단
        if any(kw in analysis_zh for kw in ["韩国", "한국"]):
            logger.warning(f"  Analysis blocked: Korea mention in #{news_id}")
            return {"error": "Analysis contained Korea reference"}

        # Papago 번역
        from src.cni.translator import papago_translate
        analysis_ko = papago_translate(analysis_zh)

        if analysis_ko:
            # 후처리
            from src.cni.postprocess import (
                replace_company_names, fix_currency, ensure_polite_korean
            )
            analysis_ko = replace_company_names(analysis_ko)
            analysis_ko = fix_currency(analysis_ko)
            analysis_ko = ensure_polite_korean(analysis_ko)
            analysis_ko = analysis_ko.replace("**", "")
            # 통합 QC 게이트 (시점/정치/평어체/문장중단)
            from src.cni.translation_qc import run_qc
            analysis_ko, _ = run_qc(analysis_ko, "analysis_ko", news_id)

        return {
            "analysis_zh": analysis_zh,
            "analysis_ko": analysis_ko or "",
        }

    except Exception as e:
        logger.error(f"  Analysis failed for #{news_id}: {e}")
        return {"error": str(e)}


if __name__ == "__main__":
    test = "4月1日，能特科技（002102）收到证监会《立案告知书》，因涉嫌信息披露违法违规被立案调查。公司主要从事油脂化工产品的研发生产。"
    result = analyze_news(0, test)
    print(f"Analysis: {result}")

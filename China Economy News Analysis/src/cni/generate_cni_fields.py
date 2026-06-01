"""CNI Field Generator v3 — Optimized pipeline.

Pipeline: 요약(데이터추출 통합) → 헤드라인 → 팁 → Papago 번역
Target: 1건 ~2분, 10건 ~20분
"""

import re
import logging
import time
import requests
from datetime import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from src.database.models import get_connection
from src.cni.translator import papago_translate
from src.cni.summary_store import update_translation, update_refined
from src.cni.pipeline_service import set_pipeline_status

LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "generate_cni_fields.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
    force=True,
)
logger = logging.getLogger("cni_gen_v2")

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "qwen2.5:14b"              # 전 단계 단일 모델 (헤드라인/요약/팁)
TIMEOUT_SUMMARY = 600              # 요약 timeout
TIMEOUT_HEADLINE = 120             # 헤드라인 timeout
TIMEOUT_TIP = 300                  # 팁 timeout (num_predict=260, 큰 토큰량으로 별도 분리)
OLLAMA_NUM_GPU = 35                # GPU 레이어 수 (TITAN X 12GB VRAM 대응)
OLLAMA_NUM_CTX = 2048              # 컨텍스트 길이 (VRAM 절약)
MAX_RETRY = 3                      # 한국어 필터 실패 시 최대 재시도
MAX_HEADLINE_LEN = 36              # 헤드라인 최대 길이
FAIL_LOG = LOG_DIR / "fail_korean_filter.log"

# 한국어 불완전 종결 어미 (절삭 시 이들로 끝나면 의미 훼손)
_INCOMPLETE_ENDINGS = (
    '을', '를', '의', '에', '과', '와', '으로', '이', '가', '은', '는',
    '하여', '되어', '에서', '으며', '하고', '및', '한', '된', '할',
    '위', '대', '중', '수', '것', '바', '데', '등', '로',
)


# ══════════════════════════════════════
# Korean Quality Filter
# ══════════════════════════════════════

def _has_chinese(text: str) -> bool:
    return bool(re.search(r'[\u4e00-\u9fff]', text or ''))


# CLAUDE.md \u00a76 \uace0\uc720\uba85\uc0ac \ubcd1\uae30 \ud5c8\uc6a9: "(\u6c49\u5b57)" or "(\u6c49\u5b57, English)" \u2014 \ud55c\uc790 1~8\uc790
_ANNOTATION_RE = re.compile(
    r'\(([\u4e00-\u9fff]{1,8})(?:,\s*[A-Za-z][A-Za-z0-9 .&\-]*)?\)'
)


def _strip_annotations(text: str) -> str:
    """CLAUDE.md \u00a76 \uc815\ucc45\uc758 \uad04\ud638 \ud55c\uc790 \ubcd1\uae30\ub9cc \uc81c\uac70 \u2014 \ud55c\uc790 \uc678 \ud14d\uc2a4\ud2b8 \uac80\uc0ac\uc6a9."""
    return _ANNOTATION_RE.sub('', text or '')


def _korean_ratio(text: str) -> float:
    """한국어 + 숫자 + 영문 + 공백 비율 (한자 제외)."""
    if not text:
        return 0.0
    total = len(text)
    non_chinese = len(re.sub(r'[\u4e00-\u9fff]', '', text))
    return non_chinese / total


def _is_sentence_complete(text: str) -> bool:
    """문장이 종결어미로 끝나는지 확인."""
    if not text:
        return False
    last = text.rstrip().rstrip('"\')')
    if not last:
        return False
    return last[-1] in '.。!?다요음함임됨며라니까습'


def _smart_truncate_headline(text: str, max_len: int = MAX_HEADLINE_LEN) -> str:
    """의미 단위를 유지하며 헤드라인 절삭."""
    if not text or len(text) <= max_len:
        return text
    truncated = text[:max_len]
    # 불완전 종결어미로 끝나면 이전 단어까지 후퇴
    for ending in _INCOMPLETE_ENDINGS:
        if truncated.endswith(ending):
            cut = truncated[:-(len(ending))].rstrip()
            # 최소 길이 보장
            if len(cut) >= max_len * 0.5:
                return cut
            break
    # 마지막 공백/쉼표 기준 절단
    last_break = max(truncated.rfind(' '), truncated.rfind(','), truncated.rfind('，'))
    if last_break > max_len * 0.5:
        candidate = truncated[:last_break].rstrip()
        # 후보도 불완전 종결이면 한 번 더 후퇴
        for ending in _INCOMPLETE_ENDINGS:
            if candidate.endswith(ending):
                cut2 = candidate[:-(len(ending))].rstrip()
                if len(cut2) >= max_len * 0.4:
                    return cut2
                break
        return candidate
    return truncated


def _ensure_korean(text: str, field: str, news_id: int,
                   regen_fn=None, max_retry: int = MAX_RETRY) -> str:
    """한국어 100% 출력 보장 필터.

    1) 한자 미포함이면 통과
    2) 한자 포함 → 곧바로 Papago 변환 (LLM 재생성 제거)
    3) Papago 실패 → 실패 로그 기록 + 원본 반환

    LLM 재생성(regen_fn)은 ~100초/회씩 소요되면서도 재생성 출력에 한자가
    남아 결국 Papago로 폴백되는 경우가 대부분(처리 호출의 ~60%가 낭비)이라
    제거했다. regen_fn/max_retry 인자는 호출부 호환을 위해 유지하되 사용하지
    않는다.

    Args:
        text: 검증 대상 텍스트
        field: 필드명 (headline/tip/summary_ko) — 로그용
        news_id: 뉴스 ID — 로그용
        regen_fn: (미사용, 호환 유지)
        max_retry: (미사용, 호환 유지)
    """
    if not text:
        return text

    stripped = _strip_annotations(text)
    if not _has_chinese(stripped) and _korean_ratio(stripped) >= 0.95:
        return text  # 통과 (CLAUDE.md §6 괄호 병기는 허용)

    # 한자 감지 → LLM 재생성 없이 Papago 직행
    logger.info(f"  Korean filter [{field}] 한자 감지 → Papago 직행")
    clean = re.sub(r'^\*\*|^\d+[\.\)）]\s*', '', text).strip()
    translated = papago_translate(clean)
    if translated and not _has_chinese(_strip_annotations(translated)) and len(translated) > 3:
        logger.info(f"  Korean filter [{field}] Papago OK")
        return translated

    # Papago 실패 → 로그 기록
    _log_korean_fail(news_id, field, text, "papago_failed")
    logger.warning(f"  Korean filter [{field}] FINAL FAIL: {text[:40]}...")
    return text  # 원본 반환 (중국어 혼합 저장 방지는 호출부에서 판단)


def _ensure_tip_complete(tip: str) -> str:
    """팁 문장 완결성 보장 + 200자 제한 (초과 시 Qwen으로 축약)."""
    if not tip:
        return tip
    tip = re.sub(r'\*\*', '', tip)
    tip_line = tip.strip().split('\n')[0].strip()
    if not tip_line.startswith('💡'):
        tip_line = '💡 ' + tip_line
    if len(tip_line) <= 200:
        if not _is_sentence_complete(tip_line):
            for i in range(len(tip_line)-1, 10, -1):
                if tip_line[i] in '.。!?다요음함임됨며라니까습':
                    return tip_line[:i+1]
        return tip_line
    # 200자 초과: Qwen에게 축약 요청
    condensed = _call_ollama(
        f"다음 팁을 200자 이내로 축약하라. 핵심 정보를 유지하고 완전한 문장으로 끝내라. "
        f"💡로 시작하고 마침표로 끝낼 것. 반드시 한국어로 작성.\n\n{tip_line}\n\n축약:",
        num_predict=200, temperature=0.1)
    if condensed:
        condensed = condensed.strip().split('\n')[0].strip()
        if not condensed.startswith('💡'):
            condensed = '💡 ' + condensed
        if len(condensed) <= 200 and len(condensed) >= 10:
            logger.info(f"  TIP condensed by LLM: {len(tip_line)}→{len(condensed)}자")
            return condensed
    # LLM 축약 실패 시 문장 단위 절삭 fallback
    sentences = re.split(r'(?<=[.。!?다요음함임됨며라니까습])\s*', tip_line)
    result = ''
    for s in sentences:
        candidate = (result + ' ' + s).strip() if result else s
        if len(candidate) > 200:
            break
        result = candidate
    return result if result and len(result) >= 10 else sentences[0] if sentences else tip_line


def _log_korean_fail(news_id: int, field: str, text: str, reason: str):
    """실패 로그 기록."""
    try:
        with open(FAIL_LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat()}|#{news_id}|{field}|{reason}|{text[:80]}\n")
    except Exception:
        pass

# ══════════════════════════════════════
# Prompts
# ══════════════════════════════════════

TIP_PROMPT = """현재 2026년이다. 2025년 이전 데이터는 확정된 과거 사실이다.
중국 경제 뉴스 속 전문 용어를 한국 독자가 이해할 수 있도록 설명하라.

우선순위 (반드시 순서대로 적용):
1순위: 뉴스에 나온 중국 경제/금융 전문 용어 1개를 골라 쉽게 풀이
2순위: 용어가 없으면, 이 뉴스와 직접 관련된 최근 사건이나 배경을 설명
3순위: 관련 사건도 없으면, 투자자 관점의 시사점을 분석

절대 금지:
- "이 뉴스는"으로 시작 금지
- "한국" 단어 금지
- 뉴스 본문에 없는 내용 추측 금지
- GPT, XPeng, 6G 등 뉴스와 무관한 기업/기술 언급 금지
- 영어 사용 금지: 약어의 풀네임도 반드시 한국어로 표기
  (예: eVTOL → eVTOL(전기 수직이착륙기) ✓, eVTOL(Vertical Take-Off and Landing) ✗)
  (예: CCL → CCL(동박적층판) ✓, CCL(Copper Clad Laminate) ✗)
  (예: PPI → PPI(생산자물가지수) ✓, PPI(Producer Price Index) ✗)
- 기업명도 한국어로 표기 (예: Manycore Tech → 매니코어테크 ✓)

규칙:
- 1개만 작성, 200자 이내, 💡 로 시작, 마침표로 끝낼 것
- 반드시 이 뉴스 본문의 데이터만 사용
- 전체를 한국어(한글)로만 작성할 것 (약어·고유명사 제외)

좋은 예시:
💡 LPR(대출우대금리)은 중국인민은행이 매월 발표하는 기준금리로, 인하 시 기업 대출 비용이 직접 낮아진다.
💡 과창판(科创板)은 상하이거래소의 기술혁신 기업 전용 상장 플랫폼으로, 나스닥과 유사한 역할을 한다.
💡 쌍순환(双循环)은 내수(국내순환)와 수출(국제순환)을 동시에 강화하는 중국의 경제 전략이다.
💡 ESG채권 발행액 1조 위안 돌파는 중국 녹색금융 시장이 본격 성장기에 진입했음을 의미한다.

뉴스: {title}
내용: {summary}

💡"""

DATA_EXTRACT_PROMPT = """从以下新闻原文中提取所有关键数据。严格按类别列出，没有的写"无"。

金额：
百分比/增长率：
公司/机构名：
人物（姓名+职务）：
政策/法规名：
时间/日期：

原文：
{text}

关键数据："""

SUMMARY_PROMPT = """你是新闻编辑。请压缩以下原文为中文摘要。
今天日期是2026年。2025年及以前的数据均为已确认的过去事实，不是预测。
摘要中提及2025年数据时使用过去时态（如"达到""实现""录得"），不要用"预计""将达到"等未来表述。

绝对规则：
1. 只能使用原文中已有的信息，禁止添加任何新内容
2. 原文中没有的数字、机构名、人物名，绝对不能出现在摘要中
3. 300-500字，自然叙述体，不使用任何小标题或标记
4. 保留原文中的所有数字、机构名、人物名、政策名
5. 语气客观中立
6. 货币单位保持原文写法（亿元、万亿元）

格式要求（严格遵守）：
- 直接开始写摘要正文，第一个字就是新闻内容
- 禁止任何开头语：不要写"Here is""以下是""摘要：""**标题**"等
- 禁止使用markdown格式（**加粗**、##标题 等）
- 禁止使用英文句子
- 只输出纯中文段落

原文中的关键数据：{extracted_data}

原文：
{text}

"""

HEADLINE_PROMPT = """你是面向韩国读者的中国经济新闻编辑。

任务：生成中文新闻标题，翻译成韩语后韩国读者能一眼看懂"发生了什么"。

## 核心规则
1. 禁止复制原标题，也禁止只把原标题字词重新排列
2. 25个字以内（严格限制）
3. 必须包含：主体（谁）+ 核心动作（做了什么）+ 关键数字（如有）
4. 企业/机构名用韩语标准名，括号内加英文或中文原名
   例：比亚迪(BYD), 国务院(중국 국무원), 中国建设银行(건설은행)
5. 货币单位：必须写"亿元""万亿元"（翻译后变为"억 위안""조 위안"）
6. 中国内部术语、缩写要展开解释
   例：发改委 → 国家发展改革委员会, A股 → A股(中国大陆股市)
7. 禁止：文学表达、比喻、"新突破""新发展""推进""加强"等模糊词
8. 只输出一行标题文字，不要任何解释或注释

## 好的标题
比亚迪(BYD) 2025年营收8039亿元创新高 净利润降19%
中国央行降准0.5个百分点 释放长期资金约1万亿元
小米(Xiaomi)新款SU7上市 与特斯拉正面竞争
云南白药押注创新药 2025年营收411亿元

## 坏的标题（禁止）
看见中国经济的进与新
将创新的种子播撒在产业的沃土
科技创新实现新突破

原标题（禁止复制）：{original_title}
核心数据：{key_numbers}
摘要：{summary}

新标题："""


# ══════════════════════════════════════
# Text Preprocessing
# ══════════════════════════════════════

def _deduplicate_text(text):
    """Remove duplicated paragraphs and truncated news previews from crawled content."""
    if not text:
        return ""
    # Remove truncated news previews (lines ending with '...' that are unrelated snippets)
    import re
    lines = text.split('\n')
    cleaned_lines = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # Skip lines that look like truncated previews from other news
        if re.match(r'^\d+月\d+日，', line) and line.endswith('...'):
            continue
        if line.endswith('...') and len(line) < 30:
            continue
        cleaned_lines.append(line)
    text = '\n'.join(cleaned_lines)

    paragraphs = text.split('\n')
    seen = set()
    unique = []
    for p in paragraphs:
        p = p.strip()
        if not p:
            continue
        # Use first 50 chars as dedup key (handles near-duplicates)
        key = p[:50]
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return '\n'.join(unique)


def _trim_to_complete_sentence(text):
    """Trim to last complete sentence (ending with 。！？.)."""
    if not text:
        return ""
    endings = ['。', '！', '？', '.']
    for i in range(len(text) - 1, -1, -1):
        if text[i] in endings:
            return text[:i+1]
    return text


# ══════════════════════════════════════
# LLM Call
# ══════════════════════════════════════

def _call_ollama(prompt, model=None, timeout=TIMEOUT_SUMMARY, num_predict=500, temperature=0.1):
    model = model or MODEL
    try:
        t0 = time.time()
        resp = requests.post(
            OLLAMA_URL,
            json={"model": model, "prompt": prompt, "stream": False,
                  "options": {"temperature": temperature, "num_predict": num_predict,
                              "num_gpu": OLLAMA_NUM_GPU, "num_ctx": OLLAMA_NUM_CTX}},
            timeout=timeout)
        resp.raise_for_status()
        dur = time.time() - t0
        result = resp.json().get("response", "").strip()
        result = re.sub(r'^```[a-z]*\s*', '', result)
        result = re.sub(r'\s*```$', '', result)
        logger.info(f"    [{model}] {dur:.0f}s, {len(result)}chars, np={num_predict}")
        return result.strip()
    except Exception as e:
        logger.error(f"Ollama failed [{model}]: {e}")
        return ""


# ══════════════════════════════════════
# Validation
# ══════════════════════════════════════

def _extract_numbers(text):
    """Extract key numbers from text."""
    patterns = [
        r'\d+\.?\d*[%％]',           # percentages
        r'\d+\.?\d*[亿万]',          # amounts
        r'\d+\.?\d*万亿',            # trillions
        r'\d{4}年',                  # years
        r'[+-]?\d+\.?\d*个百分点',    # basis points
        r'\d+\.?\d*元',              # yuan
    ]
    numbers = []
    for p in patterns:
        numbers.extend(re.findall(p, text or ''))
    return list(set(numbers))


def _clean_title(title):
    if not title:
        return ""
    clean = re.sub(r'\*\*', '', title)
    clean = clean.split('\n')[0].strip()
    # Remove English meta-explanations from LLM
    clean = re.sub(r"^(Here'?s?\s+a\s+new\s+title|This title|Note:|Here is|The title|New title|Title:).*?[:：]\s*", '', clean, flags=re.IGNORECASE).strip()
    clean = re.sub(r'(This title|Note:|Here is|（核心|The title|that meets).*$', '', clean, flags=re.IGNORECASE).strip()
    # Remove if entirely English (LLM failed to generate Chinese)
    if clean and not re.search(r'[\u4e00-\u9fff\uac00-\ud7af]', clean):
        return ""  # Return empty to trigger fallback
    clean = clean.strip('"\'「」""')
    clean = re.sub(r'^\d+[\.\)）]\s*', '', clean)
    return clean


def _clean_summary(text):
    """Remove LLM artifacts from summary output."""
    if not text:
        return ""
    # Remove English preamble
    text = re.sub(r'^(Here is the summary|Here is a summary|Here\'s the|Summary:).*?\n', '', text, flags=re.IGNORECASE).strip()
    # Remove markdown bold headers
    text = re.sub(r'^\*\*[^*]+\*\*\s*\n?', '', text).strip()
    # Remove "中国经济新闻摘要" header
    text = re.sub(r'^中国经济新闻摘要\s*\n?', '', text).strip()
    # Remove all markdown bold
    text = re.sub(r'\*\*', '', text)
    # Remove remaining markdown headers
    text = re.sub(r'^#{1,3}\s+.*?\n', '', text, flags=re.MULTILINE).strip()
    # Remove structure tags
    text = re.sub(r'[【\[](核心事实|背景|影响|结论|事件核心|Background|Impact)[】\]]', '', text).strip()
    return text


EXPAND_PROMPT = """以下摘要太短，请扩展到300-500字。补充原文中的更多细节、数据和背景信息。

注意：不要添加原文中没有的信息。直接输出扩展后的摘要，不要任何开头语或标记。

原文：
{text}

当前摘要：
{summary}

扩展后的摘要："""


def validate_headline(original_title, generated_title, key_numbers):
    """Validate headline quality."""
    if not generated_title or len(generated_title) < 5:
        return False, "too_short"
    # 원제와 동일하면 거부
    if generated_title.strip() == (original_title or "").strip():
        return False, "same_as_original"
    # 원문에 수치가 있는데 제목에 하나도 없으면 거부
    if key_numbers and not any(n in generated_title for n in key_numbers[:3]):
        return False, "missing_numbers"
    # 모호한 표현
    vague = ["新突破", "新发展", "新变化", "看见", "启示", "探索"]
    if any(v in generated_title for v in vague) and len(generated_title) < 15:
        return False, "vague"
    return True, "ok"


def validate_summary(summary, extracted_data):
    """Validate summary quality — editorial standard."""
    if not summary or len(summary) < 320:
        return False, "too_short_320"
    # 구조 태그가 들어있으면 경고 (서술식이 아님)
    if "【核心事实】" in summary or "【背景】" in summary or "[핵심사실]" in summary:
        return False, "has_structure_tags"
    # 하나의 자연스러운 문단도 허용 (Papago 번역 후 줄바꿈 없을 수 있음)
    # 최소 3개 문장 이상이면 OK
    sentences = [s.strip() for s in re.split(r'[。.！？]', summary) if s.strip() and len(s.strip()) > 5]
    if len(sentences) < 3:
        return False, "too_few_sentences"
    return True, "ok"


# ══════════════════════════════════════
# Main Generation Pipeline
# ══════════════════════════════════════

def _smart_fallback(text):
    """Timeout fallback: 원문 첫 2문장 추출."""
    sentences = re.split(r'[。！？]', text)
    result = []
    for s in sentences:
        s = s.strip()
        if len(s) > 10:
            result.append(s)
        if len(result) >= 2:
            break
    return '。'.join(result) + '。' if result else text[:200]


def generate_enhanced(news_id, original_title, original_content, enable_papago=True):
    """Single-model pipeline (Qwen2.5): summary → tip → Papago → headline(from KO)."""
    gen_start = time.time()

    # 전처리: 원문 중복 문단 + 잘린 미리보기 제거
    raw_text = _deduplicate_text(original_content or "")
    text = raw_text[:1500]
    if not text or len(text) < 100:
        return None

    # 핵심 수치 추출 (정규식, LLM 불필요)
    key_numbers = _extract_numbers(text)
    logger.info(f"  Numbers: {key_numbers[:5]}")

    # ── Stage 1: 요약 (Qwen2.5) ──
    logger.info(f"  [1/4] Summary (qwen2.5)...")
    _summary_prompt = SUMMARY_PROMPT.format(
        extracted_data=", ".join(key_numbers[:5]) if key_numbers else "(없음)", text=text)
    summary_zh = _call_ollama(_summary_prompt, model=MODEL,
                               timeout=TIMEOUT_SUMMARY, num_predict=400)

    # 아티팩트 제거
    summary_zh = _clean_summary(summary_zh)

    if not summary_zh:
        # 스마트 fallback: 원문 첫 2문장
        summary_zh = _smart_fallback(text)
        logger.warning(f"  Summary fallback: smart extract ({len(summary_zh)}chars)")

    # 요약 검증 — CPU에서는 재시도 1회만 (2-pass 제거: 속도 우선)
    valid_s, reason_s = validate_summary(summary_zh, "")
    if not valid_s and reason_s == "too_short_320":
        # 짧은 요약도 허용 (CPU 환경 — 150자 이상이면 진행)
        if len(summary_zh) >= 150:
            logger.info(f"  Summary short ({len(summary_zh)}chars) but acceptable for CPU mode")
        else:
            logger.info(f"  Summary too short ({len(summary_zh)}chars), retry...")
            summary_zh_retry = _call_ollama(_summary_prompt, model=MODEL,
                                             timeout=TIMEOUT_SUMMARY, num_predict=400)
            summary_zh_retry = _clean_summary(summary_zh_retry)
            if summary_zh_retry and len(summary_zh_retry) > len(summary_zh):
                summary_zh = summary_zh_retry

    # 후처리
    summary_zh = _trim_to_complete_sentence(summary_zh)
    summary_zh = _deduplicate_text(summary_zh)

    # ── Stage 2: 팁 생성 (그래프 우선 → 텍스트 fallback) ──
    # ── Stage 2: 팁 생성 (용어 설명 우선) ──
    logger.info(f"  [2/4] Tip (terminology focus)...")
    hansanguk_tip = None

    if True:  # 텍스트 기반 우선 (그래프는 성숙 후 전환)
        logger.info(f"  TIP fallback: text-based generation...")
        KOREA_BLOCK = ["한국은", "한국의", "한국도", "한국에서", "한국 기업", "한국 경제", "한국 시장"]

        def _gen_tip():
            return _call_ollama(
                TIP_PROMPT.format(title=original_title or "", summary=summary_zh[:300]),
                model=MODEL, timeout=TIMEOUT_TIP, num_predict=260, temperature=0.3)

        tip_raw = _gen_tip()
        if tip_raw and not any(kw in tip_raw for kw in KOREA_BLOCK):
            tip_line = _ensure_tip_complete(tip_raw)
            if tip_line and len(tip_line) >= 5:
                tip_line = _ensure_korean(tip_line, "tip", news_id,
                                          regen_fn=lambda: _ensure_tip_complete(_gen_tip()))
                tip_line = _ensure_tip_complete(tip_line)
                hansanguk_tip = tip_line[:500]
                logger.info(f"  TIP [fallback]: {hansanguk_tip[:50]}...")

    # ── 팁 후처리: 한자 치환 + 용어 보정 + 경어체 ──
    if hansanguk_tip:
        from src.cni.postprocess import (
            replace_company_names as _rcp,
            explain_terms as _et,
            ensure_polite_korean,
        )
        hansanguk_tip = _rcp(hansanguk_tip)
        hansanguk_tip = _et(hansanguk_tip)
        hansanguk_tip = ensure_polite_korean(hansanguk_tip)

    # ── Stage 3: Papago 요약 번역 ──
    gen_llm_done = time.time()
    logger.info(f"  LLM stages done in {gen_llm_done - gen_start:.0f}s")

    card_headline = None
    summary_ko = None

    if enable_papago:
        logger.info(f"  [3/4] Papago translation (zh→ko)...")

        # 요약 번역
        summary_ko = papago_translate(summary_zh)
        if not summary_ko:
            logger.warning(f"  Papago summary failed")

        # ── Papago 후처리 ──
        from src.cni.postprocess import (
            remove_structure_tags, replace_company_names,
            ensure_polite_korean, fix_headline_terms,
            fix_currency, explain_terms,
        )
        if summary_ko:
            summary_ko = remove_structure_tags(summary_ko)
            summary_ko = replace_company_names(summary_ko)
            summary_ko = fix_currency(summary_ko)
            summary_ko = explain_terms(summary_ko)
            summary_ko = ensure_polite_korean(summary_ko)
            summary_ko = fix_headline_terms(summary_ko)
            summary_ko = _ensure_korean(summary_ko, "summary_ko", news_id)

        # ── 팁 한국어 필터 + 문장 완결성 ──
        if hansanguk_tip:
            hansanguk_tip = _ensure_korean(hansanguk_tip, "tip", news_id,
                                           regen_fn=lambda: _ensure_tip_complete(_gen_tip()))
            hansanguk_tip = _ensure_tip_complete(hansanguk_tip)

        # ── Stage 4: 헤드라인 (한국어 요약에서 추출) ──
        logger.info(f"  [4/4] Headline from KO summary...")

        _HL_KO_PROMPT = (
            "한국어 뉴스 요약에서 핵심 사실을 추출하여 헤드라인을 작성하라.\n\n"
            "규칙:\n"
            "1. 반드시 36자 이내 (공백 포함)\n"
            "2. 한국어만 사용 (중국어 한자 절대 금지)\n"
            "3. 구조: \"주체(기업/기관명) + 핵심 수치 + 결과\"\n"
            "4. 요약 안에 있는 수치를 반드시 1개 이상 포함\n"
            "5. 요약에 없는 정보를 추가하지 말 것\n\n"
            "좋은 예시:\n"
            "- BYD 2025년 매출 8040억 위안, 순이익 19% 감소\n"
            "- 홍콩 IPO 79일 만에 1000억 홍콩달러 돌파\n"
            "- 6대 국유은행 신규 대출 총 9.4조 위안 초과\n"
            "- TSMC 일본 3나노 공장 170억 달러 투자\n\n"
            "나쁜 예시 (금지):\n"
            "- 중국 정부, 산업 발전 새로운 추진\n"
            "- 과학기술과 산업 혁신의 깊은 융합\n\n"
            "요약:\n{summary}\n\n"
            "36자 이내 한국어 헤드라인만 출력:"
        )

        def _gen_headline_ko():
            raw = _call_ollama(
                _HL_KO_PROMPT.format(summary=(summary_ko or "")[:500]),
                model=MODEL, timeout=TIMEOUT_HEADLINE, num_predict=80)
            if not raw:
                return ""
            hl = raw.strip().split('\n')[0].strip()
            hl = re.sub(r'\*\*', '', hl)
            hl = hl.strip('"\'')
            hl = re.sub(r'^\d+[\.\)]\s*', '', hl)
            return hl

        card_headline = _gen_headline_ko()
        card_headline = _ensure_korean(card_headline, "headline", news_id,
                                        regen_fn=_gen_headline_ko)

        if card_headline:
            card_headline = _smart_truncate_headline(card_headline, MAX_HEADLINE_LEN)

        # fallback: Papago 번역 원제
        if not card_headline or len(card_headline) < 5:
            card_headline = papago_translate(original_title or "")
            if card_headline:
                card_headline = replace_company_names(card_headline)
                card_headline = fix_currency(card_headline)
                card_headline = _smart_truncate_headline(card_headline, MAX_HEADLINE_LEN)
            logger.warning(f"  Headline fallback: {card_headline}")

        logger.info(f"  Headline: {card_headline} ({len(card_headline or '')}chars)")

        # ── 정치적 민감도 검증 ──
        from src.cni.political_check import check_and_neutralize
        if card_headline:
            card_headline, hl_score, hl_reasons = check_and_neutralize(card_headline)
            if hl_score >= 0.3:
                logger.info(f"  Political check (headline): {hl_score:.1f} {hl_reasons}")
        if summary_ko:
            summary_ko, sm_score, sm_reasons = check_and_neutralize(summary_ko)
            if sm_score >= 0.3:
                logger.info(f"  Political check (summary): {sm_score:.1f} {sm_reasons}")
    else:
        logger.info(f"  Papago/Headline skipped (disabled)")

    # ── 최종 검증: 팁 경어체 + 요약-헤드라인 정합성 ──
    from src.cni.postprocess import ensure_polite_korean as _epk

    # 팁 경어체 재확인
    if hansanguk_tip:
        hansanguk_tip = _epk(hansanguk_tip)

    # 요약에 헤드라인 핵심 키워드가 포함되어 있는지 검증
    if card_headline and summary_ko:
        _hl_words = [w for w in re.split(r'[\s,，·]', card_headline) if len(w) >= 2]
        _hl_in_summary = sum(1 for w in _hl_words if w in summary_ko)
        if _hl_words and _hl_in_summary == 0:
            logger.warning(f"  WARN: headline keywords not in summary_ko — regenerating headline")
            # 헤드라인 재생성 (요약 기반)
            if '_gen_headline_ko' in dir():
                new_hl = _gen_headline_ko()
                if new_hl and len(new_hl) >= 5:
                    new_hl = _smart_truncate_headline(new_hl, MAX_HEADLINE_LEN)
                    card_headline = new_hl
                    logger.info(f"  Headline regenerated: {card_headline}")

    # ── 품질 평가 + 로깅 ──
    try:
        from src.cni.quality_scorer import score_all
        _scores = score_all(
            title_zh="", summary_zh=summary_zh,
            tip=hansanguk_tip or "",
            original_title=original_title or "",
            original_content=original_content or "",
            summary_ko=summary_ko or "",
        )
        _grade = _scores["grade"]
        logger.info(f"  QUALITY: {_scores['pct']}% ({_grade}) — "
                     f"HL:{_scores['headline']['pct']}% "
                     f"SM:{_scores['summary']['pct']}% "
                     f"TIP:{_scores['tip']['pct']}%")

        _qlog = LOG_DIR / f"quality_scores_{datetime.now().strftime('%Y%m%d')}.log"
        with open(_qlog, "a", encoding="utf-8") as _qf:
            _qf.write(f"{datetime.now().isoformat()}|{news_id}|"
                       f"input={len(text)}|zh={len(summary_zh)}|"
                       f"hl_score={_scores['headline']['total']}/{_scores['headline']['max']}|"
                       f"sm_score={_scores['summary']['total']}/{_scores['summary']['max']}|"
                       f"tip_score={_scores['tip']['total']}/{_scores['tip']['max']}|"
                       f"total={_scores['pct']}%|grade={_grade}\n")
    except Exception as _qe:
        logger.warning(f"  Quality scoring failed (non-blocking): {_qe}")

    gen_total = time.time() - gen_start
    logger.info(f"  TOTAL generate_enhanced: {gen_total:.0f}s")

    return {
        "title_zh": "",
        "summary_zh": summary_zh,
        "card_headline": card_headline,
        "summary_ko": summary_ko,
        "key_numbers": key_numbers,
        "hansanguk_tip": hansanguk_tip,
        "gen_time": gen_total,
    }


def generate_tip_ondemand(news_id: int) -> str:
    """On-demand 팁 생성 (Qwen2.5).

    대시보드에서 [팁 생성] 버튼 클릭 시 호출.
    """
    conn = get_connection()
    row = conn.execute(
        "SELECT title_zh, summary_zh, original_title FROM news WHERE id=?", (news_id,)
    ).fetchone()
    conn.close()

    if not row:
        return None

    title = row["title_zh"] or row["original_title"] or ""
    summary = row["summary_zh"] or ""

    logger.info(f"  [TIP on-demand] #{news_id}...")
    tip_raw = _call_ollama(
        TIP_PROMPT.format(title=title, summary=summary[:300]),
        model=MODEL, timeout=TIMEOUT_TIP, num_predict=260, temperature=0.3)

    if not tip_raw:
        return None

    # 한국 언급 차단
    KOREA_BLOCK = ["한국은", "한국의", "한국도", "한국에서", "한국 기업", "한국 경제", "한국 시장"]
    for kw in KOREA_BLOCK:
        if kw in tip_raw:
            logger.warning(f"  TIP BLOCKED: Korea mention ({kw})")
            return None

    tip_line = tip_raw.strip().split('\n')[0].strip()
    if tip_line and len(tip_line) >= 5:
        if not tip_line.startswith('💡'):
            tip_line = '💡 ' + tip_line
        tip_line = _ensure_tip_complete(tip_line)
        # 한자 치환 + 용어 보정
        from src.cni.postprocess import replace_company_names as _rcp2, explain_terms as _et2
        tip_line = _rcp2(tip_line)
        tip_line = _et2(tip_line)

        # DB 저장
        conn = get_connection()
        conn.execute("UPDATE news SET hansanguk_tip=? WHERE id=?", (tip_line[:500], news_id))
        conn.commit()
        conn.close()
        logger.info(f"  TIP saved: {tip_line[:50]}...")
        return tip_line

    return None


def run(limit: int = 30, enable_papago: bool = True):
    """Generate enhanced fields for selected news."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT id, original_title, original_content
        FROM news
        WHERE pipeline_status = 'selected'
          AND summary_zh IS NULL
          AND original_content IS NOT NULL
          AND LENGTH(original_content) > 100
        ORDER BY importance_score DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()

    n = len(rows)
    logger.info(f"=== CNI Enhanced Generation: {n} docs (papago={enable_papago}) ===")

    if n == 0:
        logger.info("No pending docs.")
        return

    counts = {"ok": 0, "fail": 0, "translated": 0}
    start = time.time()

    for i, row in enumerate(rows, 1):
        nid = row["id"]
        title = (row["original_title"] or "")[:50]
        logger.info(f"[{i:02d}/{n}] #{nid}: {title}...")

        result = generate_enhanced(
            nid, row["original_title"], row["original_content"],
            enable_papago=enable_papago)

        if not result:
            counts["fail"] += 1
            logger.warning(f"  Generation failed")
            continue

        # Save to news table
        conn = get_connection()
        conn.execute("""
            UPDATE news SET summary_zh = ?, title_zh = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (result["summary_zh"], result["title_zh"], nid))

        # Save card_headline if Papago succeeded
        if result.get("card_headline"):
            conn.execute("UPDATE news SET card_headline = ? WHERE id = ?",
                         (result["card_headline"][:72], nid))

        # Save hansanguk_tip (non-blocking, NULL OK)
        if result.get("hansanguk_tip"):
            conn.execute("UPDATE news SET hansanguk_tip = ? WHERE id = ?",
                         (result["hansanguk_tip"][:500], nid))

        conn.commit()
        conn.close()

        # Save Korean translation + set status (preserve published if already published)
        if result.get("summary_ko"):
            update_translation(nid, result["summary_ko"])
            update_refined(nid, result["summary_ko"])
            # Check current status — don't downgrade published → translated
            _conn_chk = get_connection()
            _cur_status = _conn_chk.execute(
                "SELECT pipeline_status FROM news WHERE id = ?", (nid,)
            ).fetchone()
            _conn_chk.close()
            if _cur_status and _cur_status["pipeline_status"] == "published":
                logger.info(f"  → published 상태 유지 (재생성)")
            else:
                set_pipeline_status(nid, "translated")
            counts["translated"] += 1
            logger.info(f"  → translated (headline: {result['card_headline'][:30]})")
        else:
            logger.info(f"  → selected (no Papago, zh only)")

        counts["ok"] += 1

    duration = time.time() - start
    logger.info(f"Complete: {counts['ok']} ok, {counts['fail']} fail, "
                f"{counts['translated']} translated, {duration:.0f}s")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--no-papago", action="store_true")
    args = parser.parse_args()
    run(limit=args.limit, enable_papago=not args.no_papago)

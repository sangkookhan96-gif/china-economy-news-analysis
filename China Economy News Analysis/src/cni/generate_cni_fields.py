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
MODEL = "qwen2.5:7b"               # 전 단계 단일 모델 (헤드라인/요약/팁). 14b 대비
                                   # ~3배 빠르고(12GB GPU에 전체 적재 가능) 한국어
                                   # 품질 동등~우수 (2026-06-02 샘플 비교). 14b는
                                   # 35/49 레이어만 GPU 적재돼 호출당 ~90초.
TIMEOUT_SUMMARY = 600              # 요약 timeout
TIMEOUT_HEADLINE = 120             # 헤드라인 timeout
TIMEOUT_TIP = 300                  # 팁 timeout (num_predict=260, 큰 토큰량으로 별도 분리)
OLLAMA_NUM_GPU = 35                # GPU 레이어 수 (TITAN X 12GB VRAM 대응)
OLLAMA_NUM_CTX = 2048              # 컨텍스트 길이 (VRAM 절약)
MAX_RETRY = 3                      # 한국어 필터 실패 시 최대 재시도
MAX_HEADLINE_LEN = 40              # 헤드라인 최대 길이 (승인 코퍼스 중앙 31·평균 31,
                                   # 36자 초과가 11% — 36 하드컷이 과도해 40으로 완화)
MOBILE_HEADLINE_LEN = 24           # 1순위 길이(2026-06-18 원칙: 사실 1개·명료·모바일 미잘림)
HEADLINE_RELAX_LEN = 36            # 24자로 명료 불가 시에만 완화하는 상한
FAIL_LOG = LOG_DIR / "fail_korean_filter.log"


def _date_ctx():
    """프롬프트에 주입할 현재 날짜 컨텍스트(동적). 하드코딩 연도 금지.

    중국어는 시제 표지가 없어 모델이 '지금이 몇 년인지'를 모르면 과거 실적을
    미래형으로 옮긴다. today/올해·작년·내년을 매 호출마다 주입해 기준 시점을 고정.
    """
    n = datetime.now()
    return {"today": n.strftime("%Y년 %m월 %d일"),
            "today_zh": n.strftime("%Y年%m月%d日"),
            "cur_year": n.year, "prev_year": n.year - 1, "next_year": n.year + 1}


def _fmt(template, **kw):
    """날짜 컨텍스트 + 호출별 인자를 합쳐 프롬프트 포맷."""
    return template.format(**_date_ctx(), **kw)


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


# 과거시제 어간 뒤의 독립절 연결어미 '~으며' / '~고'(+선택적 쉼표)를 문장 분리
# 대상으로. 쉼표 나열로 이은 여러 사실(예: "A했고, B했으며, C했다")까지 끊는다.
# 인용형(다고/라고)·현재형(하고 있다)은 어간 패턴이 달라 미매칭이라 안전하고,
# 명사 나열("반도체, 전기차, 배터리")은 앞에 연결어미가 없어 분리되지 않는다.
_CONNECTIVE_RE = re.compile(r"(았|었|였|했|됐|갔|왔|렸|졌)(?:으며|고)[,，]?\s+")


def _split_long_sentences(text: str, threshold: int = 30) -> str:
    """가독성: 두 사실 이상을 연결어미로 이은 장문을 짧은 문장으로 분리.

    threshold(50자) 초과 문장에서만, 명확한 독립절 연결어미(과거시제+으며/고,
    선택적 쉼표 포함)를 '~습니다.'로 끊는다. 짧은 문장·괄호 병기·명사 나열은
    건드리지 않는다. (예: "A를 발표했고, B를 추진했으며, C를 검토했습니다"
    → "A를 발표했습니다. B를 추진했습니다. C를 검토했습니다")
    """
    if not text or not text.strip():
        return text
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    out = []
    for sent in parts:
        if len(sent) > threshold:
            sent = _CONNECTIVE_RE.sub(lambda m: m.group(1) + "습니다. ", sent)
            sent = re.sub(r"하였으며[,，]?\s+", "하였습니다. ", sent)
            sent = re.sub(r"\s{2,}", " ", sent).strip()
        out.append(sent)
    return " ".join(out)


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


# 낚시성·모호 원제 표지(이런 제목은 충실 번역보다 사실 기반이 명료) — 2026-06-18 하이브리드.
_CLICKBAIT_ZH = ("一文", "看懂", "看见", "揭秘", "起底", "刷屏", "解读", "重磅", "火爆",
                 "真相", "背后", "来了", "神话", "最靓", "对决", "VS", "?", "!", "？", "！",
                 "谁", "深度", "盘点", "起飞", "炸", "刷新", "揭晓", "独家")


_FACT_SEP = re.compile(r'\s*[,，]\s*')


def _one_fact(text):
    """사실 1개만 남긴다(두 사실 포함 금지). '주체, 사실1, 사실2'→'주체, 사실1',
    '사실1, 사실2'→'사실1'. 주체(짧고 수치 없는 첫 절)는 사실과 함께 유지."""
    if not text:
        return text
    text = re.split(r'\s+및\s+|\s+또한\s+|\s+동시에\s+|\s+한편\s+|\s*;\s*', text)[0].strip()
    segs = [s.strip() for s in _FACT_SEP.split(text) if s.strip()]
    if len(segs) <= 1:
        return text
    s0 = segs[0]
    if len(s0) <= 9 and not re.search(r'\d', s0):             # 짧은 주체 → 주체+사실1
        return f"{s0}, {segs[1]}".strip().rstrip(',')
    if not re.search(r'\d', s0):                              # 첫 절이 수치없는 토픽
        fact = next((s for s in segs[1:] if re.search(r'\d', s)), None)
        if fact:                                             # 수치 있는 사실 절 우선
            combined = f"{s0} {fact}"
            return combined if len(combined) <= MOBILE_HEADLINE_LEN else fact
    return s0.strip()                                         # 그 외: 첫 사실만


def _fit_headline(text):
    """길이 단계: ①사실1개 24자 이내 → 그대로 ②24자 초과면 명료한 24자 절삭 시도,
    충분히 명료하면(>=18자) 24자 준수 ③그래도 모호하면 36자까지 완화."""
    text = _one_fact((text or "").strip())
    if len(text) <= MOBILE_HEADLINE_LEN:
        return text
    t24 = _smart_truncate_headline(text, MOBILE_HEADLINE_LEN)
    if len(t24) >= 18:                                         # 24자로도 명료 → 준수(규칙2)
        return t24
    return _smart_truncate_headline(text, HEADLINE_RELAX_LEN)  # 불가 시 36자 완화(규칙3)


def _fact_headline(summary_ko="", current=None):
    """사실 기반 폴백: 기존 헤드라인(있으면) 또는 요약 첫 문장 → 사실 1개·길이단계."""
    base = (current or "").strip()
    if not base or len(base) < 5:
        s = re.sub(r'^💡\s*', '', (summary_ko or '').strip())
        parts = re.split(r'(?<=[.。!?])\s', s)
        base = (parts[0] if parts else s).strip()
    return _fit_headline(base)


# 선전성·추상성 표지(번역 제목이 이런 표현 위주면 사실 정보가 없음) — 2026-06-19.
_ABSTRACT_KO = (
    "도약", "비상", "비약", "날개", "힘을 발휘", "힘을 모아", "활력", "심혈", "고품질",
    "새 장", "새로운 장", "신기원", "청사진", "비전", "붐", "열풍", "질주", "달려",
    "이끌다", "이끈다", "가속화", "융합", "번영", "잠재력", "큰 걸음", "박차", "시너지",
    "하드코어", "히트 상품", "발돋움", "마중물", "강군", "강국", "부상", "꽃피", "웅비",
    "약진", "매력", "빛나", "선도", "견인", "기염", "훨훨", "날다", "새 시대", "신시대",
    "신호탄", "쾌거", "주목", "관심", "탐구", "탐색", "조명", "재편", "주도", "촉진",
    "활성화", "본격화", "가속", "성장 동력", "새로운 동력", "심층", "깊은", "서사",
)
# 구체 사건 동사(수치 없어도 사실로 인정)
_CONCRETE_EVENT = (
    "확정", "발표", "발효", "승인", "체결", "타결", "인수", "합병", "상장", "출시",
    "적발", "처벌", "제재", "판결", "기소", "사망", "개막", "폐막", "설립", "해산",
    "파산", "합의", "서명", "통과", "부결", "인하", "인상", "증가", "감소", "상승",
    "하락", "돌파", "모집", "조달", "발사", "성공", "중단", "금지", "해제", "출범",
    "착공", "준공", "매입", "매각", "출하", "수주", "선정", "지급", "배당", "감원",
    "리콜", "압수", "구속", "체포", "사퇴", "취임", "복귀", "철회", "연장", "재개",
)
_HL_NUM_RE = re.compile(r'[0-9]|％|%|억|만|조\b|천|위안|달러|유로|엔|원|GW|㎾|kWh|톤|배|건|개|명|년|월|일|분기|％')


def _is_abstract(hl: str) -> bool:
    """번역 제목이 선전성·추상적이거나 잘려서 사실 정보가 부족한지 판정.
    True면 요약에서 사실을 추출해 제목을 재작성해야 한다."""
    h = (hl or "").strip()
    if not h or len(h) < 5:
        return True
    # 잘린 제목(콜론·생략부호·세로줄 종결, 또는 丨/｜ 포함)
    if h.endswith((":", "：", "…", "·", "丨", "|", "￨", "‧")) or "丨" in h or "｜" in h:
        return True
    if any(h.endswith(e) for e in _INCOMPLETE_ENDINGS):
        return True
    has_num = bool(_HL_NUM_RE.search(h))
    has_event = any(e in h for e in _CONCRETE_EVENT)
    has_abs = any(a in h for a in _ABSTRACT_KO)
    if has_abs and not has_num:        # 선전성 표현 + 수치 없음 → 추상
        return True
    if not has_num and not has_event:  # 수치도 구체 사건도 없음 → 추상
        return True
    return False


# 헤드라인 말미의 수치·행위(뉴스 알맹이)가 절삭되는 것을 막기 위해 먼저 제거할
# 부차적 시점 수식어 — "6월 20일부터", "올해", "최근" 등.
_TEMPORAL_DROP = re.compile(
    r'(?:\d{4}년\s*)?\d{1,2}월\s*\d{1,2}일\s*부터\s*'
    r'|올해\s*|최근\s*|이번\s*|연내\s*|향후\s*')

# 핵심 사실 뒤에 붙는 해설·전망절(둘째 사실)을 제거하기 위한 연결어 — 이 지점부터 절삭.
_COMMENTARY_CUT = re.compile(r'\s+(?:관련|로 인해|으로 인해|에 따라|로써|으로써|함으로써|면서)\b.*$')


def _compress_headline(text, max_len=MOBILE_HEADLINE_LEN):
    """24자 초과 시 핵심 사실(수치·행위)을 보존하며 길이를 맞춘다:
    ① 후행 해설·전망절(관련/…에 따라 등) 제거 → ② 부차적 시점 수식어 제거
    → ③ 그래도 길면 말미 절삭(폴백)."""
    t = (text or "").strip()
    if len(t) <= max_len:
        return t

    def _clean(s):
        return re.sub(r'\s{2,}', ' ', re.sub(r',\s*,', ',', s)).strip().strip(',').strip()

    # ① 둘째 사실(해설·전망절) 제거
    cut = _clean(_COMMENTARY_CUT.sub('', t))
    if 5 <= len(cut) < len(t):
        t = cut
        if len(t) <= max_len:
            return t
    # ② 수치·행위가 말미에 있으면 시점 수식어 제거로 보존 시도
    has_payload = bool(_HL_NUM_RE.search(t)) or any(e in t for e in _CONCRETE_EVENT)
    if has_payload:
        stripped = _clean(_TEMPORAL_DROP.sub('', t))
        if 5 <= len(stripped) <= max_len:
            return stripped
        if 5 <= len(stripped) < len(t):
            t = stripped
    return _smart_truncate_headline(t, max_len)


_FACT_HL_PROMPT = (
    "다음은 중국 경제 뉴스의 요약이다. 이 뉴스의 가장 중요한 사실 1개를 뽑아 신문 제목을 작성하라.\n"
    "규칙:\n"
    "- 구체적 사실(주체·행위, 가능하면 수치·시점)을 담아라. 선전성·추상적 표현"
    "(도약/활력/힘을 발휘/고품질/붐/이끌다/주도/촉진 등) 금지.\n"
    "- 사실 1개만(두 사실 금지), 24자 이내(꼭 필요할 때만 최대 36자), 한국어만, 완결된 제목.\n"
    "- 요약에 없는 내용을 지어내지 마라. 제목만 한 줄 출력.\n"
    "요약: {src}\n제목:"
)


def _fact_from_news(summary_ko="", summary_zh="", original_title="", news_id=None):
    """요약에서 핵심 사실 1개를 뽑아 제목 재작성(Qwen). 선전성·추상성 제거가 목적.
    실패(빈값·한자잔류·여전히 추상·불완전)면 None 반환 → 호출부가 결정적 폴백."""
    from src.utils.proper_noun_formatter import format_proper_nouns
    src = (summary_ko or "").strip() or (summary_zh or "").strip()
    if not src or len(src) < 10:
        return None
    src = re.sub(r'^💡\s*', '', src)[:500]
    raw = _call_ollama(_FACT_HL_PROMPT.format(src=src), num_predict=50, temperature=0.2)
    if not raw:
        return None
    h = raw.strip().split("\n")[0].strip().strip("\"'“”‘’").strip()
    h = re.sub(r'^\d+[\.\)）]\s*', '', h)
    h = re.sub(r'^(제목|헤드라인)\s*[:：]\s*', '', h)
    h = _ensure_korean(h, "headline", news_id or 0)
    try:
        h = format_proper_nouns(h, f"{original_title or ''} {summary_zh or ''}",
                                max_annotations=0)
    except Exception:
        pass
    h = _fit_headline(h.strip())
    if not h or len(h) < 5 or _has_chinese(h):
        return None
    if any(h.endswith(e) for e in _INCOMPLETE_ENDINGS):   # 불완전 종결
        return None
    if _is_abstract(h):                                   # 여전히 추상 → 실패
        return None
    return h


_TIP_HL_PROMPT = (
    "다음은 중국 경제 뉴스에 대한 한 줄 해설(팁)이다. 이 해설이 설명하는 "
    "가장 중요한 사실 1개를 뽑아 신문 제목을 작성하라.\n"
    "규칙:\n"
    "- 사실 1개만(두 사실 금지).\n"
    "- 구체적 사실(주체·행위, 가능하면 수치·시점)을 담아라. 선전성·추상적 표현"
    "(도약/활력/힘을 발휘/고품질/붐/이끌다/주도/촉진 등) 금지.\n"
    "- 24자 이내, 한국어만, 완결된 제목.\n"
    "- 해설에 없는 내용을 지어내지 마라. 제목만 한 줄 출력.\n"
    "해설: {src}\n제목:"
)


def _fact_from_tip(tip="", summary_zh="", original_title="", news_id=None):
    """Qwen이 쓴 팁(hansanguk_tip)에서 핵심 사실 1개를 뽑아 24자 이내 제목 작성
    (2026-06-20 원칙: ①한 개 사실 ②구체 사실 ③24자 이내).
    실패(빈값·한자잔류·추상·불완전·24자 초과 불가)면 None → 호출부가 요약 기반 폴백."""
    from src.utils.proper_noun_formatter import format_proper_nouns
    src = re.sub(r'^💡\s*', '', (tip or '').strip())
    if not src or len(src) < 10:
        return None
    raw = _call_ollama(_TIP_HL_PROMPT.format(src=src[:500]),
                       num_predict=50, temperature=0.2)
    if not raw:
        return None
    h = raw.strip().split("\n")[0].strip().strip("\"'“”‘’").strip()
    h = re.sub(r'^\d+[\.\)）]\s*', '', h)
    h = re.sub(r'^(제목|헤드라인)\s*[:：]\s*', '', h)
    h = _ensure_korean(h, "headline", news_id or 0)
    try:
        h = format_proper_nouns(h, f"{original_title or ''} {summary_zh or ''}",
                                max_annotations=0)
    except Exception:
        pass
    # 규칙1·3: 사실 1개 + 24자 이내(완화 없음, 수치·행위 보존 압축)
    h = _one_fact(h.strip())
    if len(h) > MOBILE_HEADLINE_LEN:
        h = _compress_headline(h, MOBILE_HEADLINE_LEN)
    h = h.strip()
    if not h or len(h) < 5 or _has_chinese(h):
        return None
    if any(h.endswith(e) for e in _INCOMPLETE_ENDINGS):   # 불완전 종결
        return None
    # 규칙2: 팁에 없는 수치 생성(환각) 차단 — 제목의 모든 숫자는 팁 숫자에 존재해야 함
    h_nums = re.findall(r'\d+', h.replace(',', ''))
    if h_nums:
        tip_digits = re.sub(r'\D', '', src)
        if any(n not in tip_digits for n in h_nums):
            return None
    if _is_abstract(h):                                   # 규칙2: 구체 사실 아님 → 실패
        return None
    return h


def build_headline(original_title, summary_zh="", summary_ko="", news_id=None,
                   current=None, tip=""):
    """제목 작성 원칙(2026-06-20): Qwen이 쓴 팁(hansanguk_tip)에서 핵심 사실 1개를
    뽑아 24자 이내 제목을 만든다 — ①한 개 사실 ②구체 사실 ③24자 이내.
      - 팁이 없거나(품질 미달 None) 추출 실패 시 요약(summary_ko)에서 사실 추출(폴백).
      - 그래도 실패 시 요약 첫 문장 사실 1개(결정적 폴백).
    어느 경로든 최종 출력은 24자 이내로 강제한다.
    original_title은 고유명사 병기 문맥 용도로만 사용.
    """
    hl = _fact_from_tip(tip, summary_zh, original_title, news_id)
    if not hl:
        hl = _fact_from_news(summary_ko, summary_zh, original_title, news_id)
    if not hl:
        hl = _fact_headline(summary_ko, current)   # 결정적 폴백(요약 첫 문장·사실1개)
    # 규칙3: 모든 경로 최종 24자 이내 강제(수치·행위 보존 압축)
    if hl and len(hl) > MOBILE_HEADLINE_LEN:
        hl = _compress_headline(hl, MOBILE_HEADLINE_LEN)
    return hl


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


# ── 팁 품질 게이트 ─────────────────────────────────────────────────────────
KOREA_BLOCK = ["한국은", "한국의", "한국도", "한국에서", "한국 기업", "한국 경제", "한국 시장"]
# 사전식 종결(통찰 없이 용어 뜻만) 표지
_TIP_DICT_END = re.compile(r"(의미한다|의미합니다|가리킨다|가리킵니다|뜻이다|뜻입니다|"
                           r"말한다|말합니다|를 일컫는다|를 일컫습니다)")
# 'X(풀이)는 …' 정의 도입 표지
_TIP_DEF_OPEN = re.compile(r"^[\"'“‘]?[A-Za-z가-힣]{1,12}\([^)]+\)(은|는|이란|란)\b")
_CJK = re.compile(r"[一-鿿]")


def _tip_has_cjk_residue(tip: str) -> bool:
    """병기 괄호 밖에 한자가 남아 있는가."""
    bare = re.sub(r"\([^)]*\)", "", tip or "")
    return bool(_CJK.search(bare))


def _tip_quality_ok(tip: str) -> tuple:
    """발행 가능한 품질인지 검사. (ok, reason)."""
    if not tip:
        return False, "empty"
    body = tip.replace("💡", "").strip()
    if len(body) < 35:
        return False, "too_short"
    if _tip_has_cjk_residue(tip):
        return False, "cjk_residue"
    has_num = bool(re.search(r"\d", re.sub(r"\([^)]*\)", "", body)))
    # 사전식 일반론: '용어(풀이)는 …~를 의미한다' 형이고 뉴스 고유 수치가 없으면 저품질
    if _TIP_DEF_OPEN.search(body) and _TIP_DICT_END.search(body) and not has_num:
        return False, "dictionary_generic"
    return True, "ok"


def _postprocess_tip(raw: str, news_id, source_zh: str):
    """원시 팁 → 한국어 보정·용어풀이·경어체·QC·고유명사 병기·완결. None=실패."""
    if not raw or any(kw in raw for kw in KOREA_BLOCK):
        return None
    tip = _ensure_tip_complete(raw)
    if not tip or len(tip) < 5:
        return None
    from src.cni.postprocess import (
        replace_company_names as _rcp, explain_terms as _et, ensure_polite_korean,
    )
    tip = _rcp(tip)
    tip = _et(tip)
    tip = ensure_polite_korean(tip)
    tip = _ensure_korean(tip, "tip", news_id)
    from src.cni.translation_qc import run_qc
    tip, _ = run_qc(tip, "hansanguk_tip", news_id, record=True)
    try:
        from src.utils.proper_noun_formatter import format_proper_nouns
        tip = format_proper_nouns(tip, source_zh or "", max_annotations=3)
    except Exception as e:
        logger.warning(f"  팁 proper-noun 병기 실패: {e}")
    tip = _ensure_tip_complete(tip)
    return tip[:500] if tip else None


def build_quality_tip(news_id, title_zh: str, summary_zh: str, source_zh: str = None,
                      retries: int = 2):
    """품질 게이트를 통과하는 팁을 생성. 실패 시 None(빈 '💡' 발행 방지).

    프롬프트 개편(함의 중심) + 재생성으로 사전식·한자잔류·빈 팁을 거른다.
    """
    src = source_zh if source_zh is not None else f"{title_zh or ''} {summary_zh or ''}"
    fallback = None
    for attempt in range(retries + 1):
        raw = _call_ollama(
            _fmt(TIP_PROMPT, title=title_zh or "", summary=(summary_zh or "")[:300]),
            model=MODEL, timeout=TIMEOUT_TIP, num_predict=260,
            temperature=0.3 + 0.2 * attempt)
        tip = _postprocess_tip(raw, news_id, src)
        if not tip:
            continue
        ok, reason = _tip_quality_ok(tip)
        if ok:
            return tip
        logger.info(f"  TIP retry #{news_id} attempt {attempt}: {reason}")
        # 한자잔류/빈 팁이 아닌 '사전식'은 최후 fallback 후보로만 보관
        if reason == "dictionary_generic" and not _tip_has_cjk_residue(tip):
            fallback = fallback or tip
    return fallback  # 전부 실패면 None

# ══════════════════════════════════════
# Prompts
# ══════════════════════════════════════

TIP_PROMPT = """현재 {today}이다. {prev_year}년 이전 데이터는 확정된 과거 사실이다.
너는 한국 기관투자자에게 이 중국 경제뉴스가 '왜 중요한가'를 한 줄로 짚어주는 애널리스트다.
독자가 "그래서 이게 무슨 의미인지"를 바로 파악하게 하라. 용어 사전이 아니라 통찰을 써라.

작성 우선순위 (반드시 순서대로):
1순위: 이 뉴스 핵심 사실의 함의·파급효과 (산업/시장/정책에 어떤 영향인가)
2순위: 사건의 배경·맥락 (왜 지금 일어났는가, 직전 흐름과의 연결)
3순위: 투자자 관점의 시사점 (수혜/리스크, 주목할 지점)

필수 요건:
- 반드시 이 뉴스 본문의 '구체적 수치나 주체(기업·기관·정책명)' 1개 이상을 인용하라.
- 전문 용어는 짧게 괄호로만 풀이하고(예: LPR(대출우대금리)), 정의에 머물지 말고 곧바로 '이번 건에서의 의미'로 넘어가라.

절대 금지:
- 사전식 일반론 금지: "X는 ~를 의미한다/가리킨다/말한다"로 끝나는 용어 설명만 있는 문장 금지.
- "이 뉴스는"으로 시작 금지. "한국" 단어 금지.
- 뉴스 본문에 없는 내용 추측·무관한 기업/기술 언급 금지.
- 영어 문장 금지(약어·고유명사 병기는 허용). 중국어(한자) 단독 표기 금지 — 한국어 음차로 쓰고 필요 시 괄호 병기.

형식:
- 1개만, 200자 이내, 💡 로 시작, 완전한 문장(마침표)으로 끝낼 것. 전체 한국어.

좋은 예시 (함의·맥락 중심):
💡 비야디(比亚迪, BYD)의 2025년 매출 8040억 위안은 전년보다 늘었지만 순이익이 19% 줄어, 가격 경쟁 심화로 박리다매 구조가 굳어지고 있음을 보여준다.
💡 인민은행의 지준율 0.5%p 인하는 약 1조 위안의 장기자금을 푸는 조치로, 부동산·인프라 등 자금난 업종의 숨통을 틔우려는 의도다.
💡 과창판 상장을 신청한 중커우항은 매출이 빠르게 늘었지만 2025년 3분기까지 적자여서, '상업우주 1호주' 경쟁에서 수익성 입증이 관건이다.

나쁜 예시 (금지 — 사전식·일반론):
💡 IPO(기업공개)는 기업이 처음으로 주식을 상장하는 것을 의미한다.
💡 A주(중국 본토 주식)는 중국 본토 증시에서 거래되는 주식을 말한다.

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
今天是{today_zh}。{prev_year}年及以前的数据都是已确认的过去事实，不是预测。

【时态规则·务必严格区分过去与未来】
- 已经发生的事实（{prev_year}年及更早、去年、上季度、已公布的业绩/统计）：
  必须用完成时态，动词后加"了"或用"达到/录得/实现/增长了"。
  绝对禁止"预计""将""有望""拟"等未来表述。翻成韩语后必须是过去时（"기록했습니다""증가했습니다"），不能是"~할 전망이다"。
- 真正尚未发生的计划/预测/目标（{cur_year}年下半年之后、{next_year}年、明年、原文明确写"计划/拟/预计/目标"）：
  保留未来时态（"将""计划""预计"），不要改写成过去。
- 判断依据是原文的时间，不要凭空假设。

【时间信息规则·极其重要，缺失即无新闻价值】
- 原文中每个事实的时间标记（年份、月份、季度，如"今年6月""{prev_year}年""第一季度""上半年"）必须原样保留在摘要里，绝对不能省略。
- 例：原文"今年6月CPI上涨"，摘要必须写"今年6月"，不能只写"CPI上涨"——省略月份就无法判断是某一天还是全年，新闻价值归零。
- 每个数字/事件都要带上它对应的时间。

绝对规则：
1. 只能使用原文中已有的信息，禁止添加任何新内容
2. 原文中没有的数字、机构名、人物名，绝对不能出现在摘要中
3. 300-500字，自然叙述体，不使用任何小标题或标记
4. 保留原文中的所有数字、机构名、人物名、政策名
5. 语气客观中立
6. 货币单位保持原文写法（亿元、万亿元）
7. 句子要短，便于阅读：每句只陈述一个事实；两个或以上的事实必须拆成多个句子，
   不要用逗号或"并""同时""此外"把多个事实塞进一个长句；每句尽量不超过40个汉字；
   全文由5-8个短句组成（翻译成韩语后每句约60字以内）

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
    _summary_prompt = _fmt(SUMMARY_PROMPT,
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
    # ── 팁: 품질 게이트 통과분만 생성 (함의 중심 프롬프트 + 재생성, 실패 시 None) ──
    logger.info(f"  TIP: quality-gated generation...")
    hansanguk_tip = build_quality_tip(
        news_id, original_title, summary_zh,
        source_zh=f"{original_title or ''} {summary_zh or ''}")
    if hansanguk_tip:
        logger.info(f"  TIP: {hansanguk_tip[:50]}...")
    else:
        logger.info(f"  TIP: 품질 미달 → 미발행(빈 팁 방지)")

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
            # 통합 QC 게이트 (시점 我国/우리나라→중국, 정치, 평어체, 문장중단)
            from src.cni.translation_qc import run_qc
            summary_ko, _ = run_qc(summary_ko, "summary_ko", news_id, record=True)
            # 공개 지면 고유명사 병기 — 한국어(汉字) 형태로 최대 3개. 원문 중국어
            # (summary_zh)에 실제 등장하는 엔티티만 병기해 환각 방지.
            try:
                from src.utils.proper_noun_formatter import format_proper_nouns
                summary_ko = format_proper_nouns(summary_ko, summary_zh, max_annotations=3)
            except Exception as e:
                logger.warning(f"  proper-noun 병기 실패: {e}")
            # 가독성: 장문(여러 사실 결합)을 짧은 문장으로 분리
            summary_ko = _split_long_sentences(summary_ko)

        # (팁은 build_quality_tip 단계에서 한자필터·QC·고유명사 병기까지 완료됨)

        # ── Stage 4: 헤드라인 (Qwen 팁에서 사실 1개 추출 — 2026-06-20 원칙: 한사실·구체·24자) ──
        logger.info(f"  [4/4] Headline from TIP (one concrete fact, <=24)...")
        card_headline = build_headline(original_title, summary_zh, summary_ko, news_id,
                                       tip=hansanguk_tip)
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
    # 품질 게이트 통과분만 (함의 중심 프롬프트 + 재생성). 실패 시 저장하지 않음.
    tip_line = build_quality_tip(news_id, title, summary,
                                 source_zh=f"{title} {summary}")
    if not tip_line:
        logger.info(f"  TIP on-demand #{news_id}: 품질 미달 → 미저장")
        return None

    conn = get_connection()
    conn.execute("UPDATE news SET hansanguk_tip=? WHERE id=?", (tip_line[:500], news_id))
    conn.commit()
    conn.close()
    logger.info(f"  TIP saved: {tip_line[:50]}...")
    return tip_line


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

        # Save card_headline if Papago succeeded. Also stamp card_headline_ai
        # (the pristine AI baseline) — expert edits only touch card_headline,
        # so card_headline != card_headline_ai later == the user's net edit.
        if result.get("card_headline"):
            conn.execute(
                "UPDATE news SET card_headline = ?, card_headline_ai = ? WHERE id = ?",
                (result["card_headline"][:72], result["card_headline"][:72], nid))

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

"""CNI Korean Postprocessor.

Rules:
- Remove structure tags ([핵심사실] [배경] etc.)
- Ensure polite Korean (경어체: ~습니다, ~입니다)
- Replace Chinese company names with global English names
- Korean/English first, Chinese in parentheses when unavoidable
"""

import re
import logging
import requests

logger = logging.getLogger("cni_postprocess")

OLLAMA_URL = "http://localhost:11434/api/generate"
REFINE_MODEL = "qwen2.5:7b"
TIMEOUT = 60

# Company/Institution name mapping: Chinese → 한국어(영어/중국어)
COMPANY_NAME_MAP = {
    # 기업: 한국어 표준명(영어)
    "比亚迪": "비야디(BYD)",
    "宁德时代": "CATL(닝더스다이)",
    "小米": "샤오미(Xiaomi)",
    "华为": "화웨이(Huawei)",
    "阿里巴巴": "알리바바(Alibaba)",
    "腾讯": "텐센트(Tencent)",
    "字节跳动": "바이트댄스(Bytedance)",
    "抖音": "더우인(TikTok)",
    "百度": "바이두(Baidu)",
    "京东": "징둥(JD.com)",
    "拼多多": "핀둬둬(Pinduoduo)",
    "蔚来": "니오(NIO)",
    "理想汽车": "리샹(Li Auto)",
    "小鹏汽车": "샤오펑(Xpeng)",
    "特斯拉": "테슬라(Tesla)",
    "苹果": "애플(Apple)",
    "英伟达": "엔비디아(Nvidia)",
    "台积电": "TSMC",
    "三星": "삼성(Samsung)",
    "亿纬锂能": "이웨이리넝(EVE Energy)",
    "中芯国际": "SMIC(중신국제)",
    "云南白药": "윈난바이야오(云南白药)",
    "赛力斯": "세리스(Seres)",
    # 기관: 한국어(중국어)
    "国务院": "국무원(国务院)",
    "人民银行": "인민은행",
    "中国人民银行": "중국인민은행",
    "证监会": "증권감독관리위원회(증감회)",
    "发改委": "국가발전개혁위원회(발개위)",
    "国家发展改革委员会": "국가발전개혁위원회(발개위)",
    "财政部": "재정부",
    "商务部": "상무부",
    "工信部": "공업정보화부",
    "科技部": "과학기술부",
    "金融监管总局": "금융감독총국",
    # 빈출 정책·기술 용어 (Papago/Qwen 오역 보정)
    "算力": "컴퓨팅 파워",
    "新质生产力": "신질생산력",
    "低空经济": "저고도 경제",
    "词元": "토큰",
}

# 화폐 단위 오역 수정 (Papago가 元을 원으로 오역하는 문제)
CURRENCY_FIX = {
    '억 원': '억 위안',
    '조 원': '조 위안',
    '만 원': '만 위안',
    '천억 원': '천억 위안',
    '백억 원': '백억 위안',
    '십억 원': '십억 위안',
}

# 중국 내부 용어 풀이
TERM_EXPLAIN = {
    'A주': 'A주(중국 본토 주식)',
    'A股': 'A주(중국 본토 주식)',
    '港股': '홍콩 주식',
    'LPR': 'LPR(대출우대금리)',
    '지준율': '지급준비율',
    '양회': '전국인민대표대회·정치협상회의(양회)',
    '쌍순환': '쌍순환(국내·국제 이중 경제 순환)',
    '신질생산력': '신질생산력(새로운 품질의 생산력)',
    '십오오': '제15차 5개년 계획',
    '十五五': '제15차 5개년 계획',
    '十四五': '제14차 5개년 계획(2021~2025년)',
    '首发车': '신차(전시회 최초 공개 차량)',
    '事先告知书': '사전 통지서',
    '退市风险警示': '상장폐지 위험 경고',
    '退市': '상장폐지',
    '체인 박람회': '공급망박람회(链博会)',
    '체인박람회': '공급망박람회(链博会)',
    '链博会': '공급망박람회(链博会)',
    '戒股': '주식투자 중단',
    '계주': '주식투자 중단',
    # Papago/Qwen 한국어 오역 보정
    '계산력': '컴퓨팅 파워',
    '계산 능력': '컴퓨팅 파워',
    '새로운 질적 생산력': '신질생산력',
    '새로운 품질의 생산력': '신질생산력',
    '신소재 생산력': '신질생산력',
    '신품질생산력': '신질생산력',
    '저공 경제': '저고도 경제',
    '저공경제': '저고도 경제',
    '엔티티 거래': '토큰 거래',
    '어원 거래': '토큰 거래',
}

# Structure tags to remove
STRUCTURE_TAGS = [
    r'\[핵심\s*사실\]',
    r'\[배경\]',
    r'\[영향과?\s*의미\]',
    r'\[영향\]',
    r'【핵심사실】',
    r'【배경】',
    r'【영향과?\s*의미】',
    r'【영향与意义】',
    r'【核心事实】',
    r'【背景】',
    r'\[핵심\s*사실\]\s*:?',
    r'\[배경\]\s*:?',
    r'핵심\s*사실\s*[:：]',
    r'배경\s*[:：]',
    r'영향과?\s*의미\s*[:：]',
]


def has_chinese_residue(text: str) -> bool:
    """Detect remaining Chinese characters."""
    if not text:
        return False
    return bool(re.search(r'[\u4e00-\u9fff\u3400-\u4dbf]', text))


def remove_structure_tags(text: str) -> str:
    """Remove [핵심사실] [배경] [영향과 의미] etc."""
    if not text:
        return text
    result = text
    for pattern in STRUCTURE_TAGS:
        result = re.sub(pattern, '', result)
    # Clean up extra newlines
    result = re.sub(r'\n{3,}', '\n\n', result)
    return result.strip()


def _registry_keys() -> set:
    """proper_nouns가 소유한 zh 키 집합(고유명사 표기는 canonicalizer가 단일 관리)."""
    try:
        from src.utils.proper_nouns import PEOPLE, COMPANIES, AGENCIES, PLACES
        return set(PEOPLE) | set(COMPANIES) | set(AGENCIES) | set(PLACES)
    except Exception:
        return set()


def replace_company_names(text: str) -> str:
    """정책·기술 용어만 보정한다. 고유명사(기업/인명/기관/지명) 표기 통일은
    proper_noun_formatter.format_proper_nouns()가 단일 권위로 처리하므로,
    proper_nouns 사전에 등재된 키는 여기서 건드리지 않는다(표기·포맷 분열 방지).
    """
    if not text:
        return text
    owned = _registry_keys()
    result = text
    # 긴 이름부터 먼저 치환 (부분 매칭 방지)
    for zh, ko in sorted(COMPANY_NAME_MAP.items(), key=lambda x: -len(x[0])):
        if zh in owned:
            continue  # 정본 사전 소유 → canonicalizer에 위임
        result = result.replace(zh, ko)
    return result


def fix_currency(text: str) -> str:
    """Fix Papago mistranslation of 元 → 원 (should be 위안)."""
    if not text:
        return text
    result = text
    for wrong, correct in CURRENCY_FIX.items():
        # 한국 원화 문맥이 아닌 경우만 수정
        # "한국 원" "원화" 등은 제외
        result = result.replace(wrong, correct)
    return result


# 종목명 뒤에 바로 붙는 한국어 조사 (풀이는 조사 앞에 넣어야 자연스러움)
_ST_JOSA = ("으로서", "으로써", "에서", "에게", "부터", "까지", "으로", "보다",
            "처럼", "만큼", "은", "는", "이", "가", "을", "를", "의", "에",
            "와", "과", "도", "로", "만")


def _strip_trailing_josa(name: str) -> tuple:
    """종목명 끝의 조사를 분리. 남는 이름이 2자 이상일 때만 분리(짧은 이름 보호)."""
    for j in sorted(_ST_JOSA, key=len, reverse=True):
        if name.endswith(j) and len(name) - len(j) >= 2:
            return name[: -len(j)], j
    return name, ""


def explain_st_terms(text: str) -> str:
    """中 A주 상장폐지 위험 종목 표기 *ST / ST를 한국 독자용으로 첫 등장 1회 풀이.

    *ST = 상장폐지 위험 경고(退市风险警示), ST = 특별관리 종목(Special Treatment).
    종목명에 접두로 붙으므로(예: *ST송파) 이름 뒤·조사 앞에 풀이를 단다.
    EAST/FAST 등 단어 내부의 'ST'는 경계 검사로 제외한다.
    """
    if not text:
        return text

    def _mk(expl):
        def _sub(m):
            name, josa = _strip_trailing_josa(m.group(0))
            return f"{name}({expl}){josa}"
        return _sub

    # *ST: 첫 '*ST{종목명}'에 풀이 1회. ST 뒤에 영문자가 오면(STAR 등) 제외 — 종목명은 한글.
    if "*ST" in text and "상장폐지 위험" not in text:
        text = re.sub(r"\*ST(?![A-Za-z])[가-힣]*", _mk("상장폐지 위험 경고"),
                      text, count=1)
    # ST(별표 없음): 앞이 영문자·'*'가 아니고 뒤도 영문자가 아닌 첫 'ST{한글종목명}'에 풀이 1회.
    # EAST/STAR/START 등 영어단어 속 ST 제외, 이미 '특별관리' 풀이 있으면 스킵.
    if "특별관리" not in text and re.search(r"(?<![A-Za-z*])ST(?![A-Za-z])", text):
        text = re.sub(r"(?<![A-Za-z*])ST(?![A-Za-z])[가-힣]*", _mk("특별관리 종목"),
                      text, count=1)
    return text


def explain_terms(text: str) -> str:
    """Expand Chinese internal terms for Korean readers."""
    if not text:
        return text
    result = text
    # 긴 용어부터 치환 (부분 문자열 충돌 방지)
    for term, explanation in sorted(TERM_EXPLAIN.items(), key=lambda x: -len(x[0])):
        # 이미 풀이가 포함된 경우 건너뛰기
        if explanation in result:
            continue
        result = result.replace(term, explanation)
    # 상장폐지 위험 종목(*ST/ST) 풀이 (첫 등장 1회)
    result = explain_st_terms(result)
    return result


# Casual → Polite ending conversion
CASUAL_TO_POLITE = {
    '뚜렷하다.': '뚜렷합니다.',
    '명확하다.': '명확합니다.',
    '분명하다.': '분명합니다.',
    '있다.': '있습니다.',
    '했다.': '했습니다.',
    '됐다.': '됐습니다.',
    '보고 있다.': '보고 있습니다.',
    '말했다.': '말했습니다.',
    '밝혔다.': '밝혔습니다.',
    '나타났다.': '나타났습니다.',
    '전했다.': '전했습니다.',
    '기록했다.': '기록했습니다.',
    '증가했다.': '증가했습니다.',
    '감소했다.': '감소했습니다.',
    '발표했다.': '발표했습니다.',
    '달했다.': '달했습니다.',
    '초과했다.': '초과했습니다.',
    '높았다.': '높았습니다.',
    '낮았다.': '낮았습니다.',
    '된다.': '됩니다.',
    '한다.': '합니다.',
    '간다.': '갑니다.',
    '온다.': '옵니다.',
    '인다.': '입니다.',
    '이다.': '입니다.',
}

# Domain term fixes for headlines
HEADLINE_TERM_FIX = {
    "'신규'를 보다": "'혁신'을 보다",
    "'진보'와": "'전진'과",
    '신규를': '혁신을',
    '활수': '자금 유입',
    '우리나라': '중국',
    '우리 나라': '중국',
}


def ensure_polite_korean(text: str) -> str:
    """Convert casual endings to polite Korean (경어체 통일).

    2단계: 고정 사전 → 정규식 일괄 변환.
    """
    if not text:
        return text
    result = text

    # 1단계: 고정 사전 (특수 패턴 우선 처리)
    for casual, polite in CASUAL_TO_POLITE.items():
        result = result.replace(casual, polite)

    # 2단계: 정규식 일괄 변환 — 문장 끝 비경어체 → 경어체
    # "~았/었/했/됐/갔/났/왔/셨/였/랐/렸/졌 + 다." → "~습니다."
    result = re.sub(r'([았었했됐갔났왔셨였랐렸졌])다([.\s,])', r'\1습니다\2', result)
    result = re.sub(r'([았었했됐갔났왔셨였랐렸졌])다$', r'\1습니다.', result)
    # "~하였다." → "~하였습니다."
    result = re.sub(r'하였다([.\s,])', r'하였습니다\1', result)
    result = re.sub(r'하였다$', '하였습니다.', result)
    # "~ㄴ다/는다." (한다→합니다, 된다→됩니다, 인다→입니다, 이다→입니다)
    result = re.sub(r'한다([.\s,])', r'합니다\1', result)
    result = re.sub(r'된다([.\s,])', r'됩니다\1', result)
    result = re.sub(r'인다([.\s,])', r'입니다\1', result)
    result = re.sub(r'이다([.\s,])', r'입니다\1', result)
    result = re.sub(r'한다$', '합니다.', result)
    result = re.sub(r'된다$', '됩니다.', result)
    result = re.sub(r'인다$', '입니다.', result)
    result = re.sub(r'이다$', '입니다.', result)
    # 형용사 종결: ~밝다, ~크다, ~중요하다 등
    _ADJ = [('밝다','밝습니다'), ('크다','큽니다'), ('높다','높습니다'),
            ('낮다','낮습니다'), ('많다','많습니다'), ('적다','적습니다'),
            ('중요하다','중요합니다'), ('가능하다','가능합니다'), ('원활하다','원활합니다'),
            ('일다','입니다')]
    for casual, polite in _ADJ:
        result = re.sub(re.escape(casual) + r'([.\s,])', polite + r'\1', result)
        result = re.sub(re.escape(casual) + r'$', polite + '.', result)

    # 동사 현재형 종결: ~준다/낸다/친다/는다/진다/든다/킨다 → ~줍니다 등
    result = re.sub(r'([여보높끌당넓밝줍돕])준다([.\s,])', r'\1줍니다\2', result)
    result = re.sub(r'([여보높끌당넓밝줍돕])준다$', r'\1줍니다.', result)
    result = re.sub(r'나타낸다([.\s,])', r'나타냅니다\1', result)
    result = re.sub(r'나타낸다$', '나타냅니다.', result)
    result = re.sub(r'미친다([.\s,])', r'미칩니다\1', result)
    result = re.sub(r'미친다$', '미칩니다.', result)
    result = re.sub(r'보여준다([.\s,])', r'보여줍니다\1', result)
    result = re.sub(r'보여준다$', '보여줍니다.', result)
    result = re.sub(r'여겨진다([.\s,])', r'여겨집니다\1', result)
    result = re.sub(r'여겨진다$', '여겨집니다.', result)
    result = re.sub(r'이루어진다([.\s,])', r'이루어집니다\1', result)
    result = re.sub(r'에 띈다([.\s,])', r'에 띕니다\1', result)
    result = re.sub(r'돕는다([.\s,])', r'돕습니다\1', result)
    result = re.sub(r'갖는다([.\s,])', r'갖습니다\1', result)
    result = re.sub(r'받는다([.\s,])', r'받습니다\1', result)
    result = re.sub(r'가진다([.\s,])', r'가집니다\1', result)
    # 범용: ~ㄴ다/는다 → ~ㅂ니다/습니다 (위에서 안 잡힌 잔여)
    result = re.sub(r'([시키화])킨다([.\s,])', r'\1킵니다\2', result)
    result = re.sub(r'않는다([.\s,])', r'않습니다\1', result)
    result = re.sub(r'알린다([.\s,])', r'알립니다\1', result)
    result = re.sub(r'나타난다([.\s,])', r'나타납니다\1', result)
    # 명사+다 종결 → 명사+입니다 (회의다, 결과다, 지표다, 계기다, 조치다, 제도다)
    result = re.sub(r'(회의|결과|지표|계기|조치|제도|신호|요인|배경)다([.\s,])', r'\1입니다\2', result)
    result = re.sub(r'(회의|결과|지표|계기|조치|제도|신호|요인|배경)다$', r'\1입니다.', result)
    # 형용사 잔여
    result = re.sub(r'가파르다([.\s,])', r'가파릅니다\1', result)
    result = re.sub(r'주요하다([.\s,])', r'주요합니다\1', result)
    result = re.sub(r'만하다([.\s,])', r'만합니다\1', result)
    result = re.sub(r'풍부해진다([.\s,])', r'풍부해집니다\1', result)
    result = re.sub(r'밝혀냈다([.\s,])', r'밝혀냈습니다\1', result)
    result = re.sub(r'다가섰다([.\s,])', r'다가섰습니다\1', result)

    return result


def fix_headline_terms(text: str) -> str:
    """Fix domain-specific mistranslations in headlines."""
    if not text:
        return text
    result = text
    for wrong, correct in HEADLINE_TERM_FIX.items():
        result = result.replace(wrong, correct)
    return result


def refine_korean(text: str) -> str:
    """Full Korean post-processing pipeline.

    1. Remove structure tags
    2. Replace company names
    3. Remove Chinese residue (via Ollama if needed)
    4. Ensure polite Korean
    """
    if not text or not text.strip():
        return text or ""

    # Step 1: Remove structure tags
    result = remove_structure_tags(text)

    # Step 2: Replace company names
    result = replace_company_names(result)

    # Step 3: Chinese residue
    if has_chinese_residue(result):
        # Use Ollama to translate remaining Chinese
        prompt = f"""다음 한국어 텍스트에서 남아있는 중국어를 한국어로 번역하라.
경어체(~습니다)로 통일하라. 의미를 변경하지 마라. 한국어만 출력하라.

{result}

다듬은 한국어:"""
        try:
            resp = requests.post(
                OLLAMA_URL,
                json={"model": REFINE_MODEL, "prompt": prompt,
                      "stream": False,
                      "options": {"temperature": 0.1, "num_predict": 1000}},
                timeout=TIMEOUT)
            resp.raise_for_status()
            refined = resp.json().get("response", "").strip()
            refined = re.sub(r'^```[a-z]*\s*', '', refined)
            refined = re.sub(r'\s*```$', '', refined)
            if refined and len(refined) > 50:
                result = refined.strip()
        except Exception as e:
            logger.error(f"Refine failed: {e}")

    # Step 4: Fix currency (억 원 → 억 위안)
    result = fix_currency(result)

    # Step 5: Explain Chinese terms
    result = explain_terms(result)

    # Step 6: Polite Korean (경어체 통일)
    result = ensure_polite_korean(result)

    # Step 7: Domain term fixes
    result = fix_headline_terms(result)

    return result.strip()

"""중국어(한어병음) → 한국어 음차 (외래어표기법 중국어 표기 기준, 결정적).

Papago/LLM은 중국 고유명사 음차를 신뢰할 수 없어(燧原→쑤이위안/부이위안 혼재),
pypinyin으로 정확한 병음을 얻고 외래어표기법 표로 결정적 변환한다.

핵심 API: transliterate(zh: str) -> str   # 예: '燧原' → '쑤이위안'
검증: tests/ground truth = proper_nouns 사전의 (zh, ko) 쌍.
"""
from __future__ import annotations

try:
    from pypinyin import lazy_pinyin, Style
    _HAS_PYPINYIN = True
except Exception:  # pragma: no cover
    _HAS_PYPINYIN = False

# ── 한글 자모 합성 ──
_CHO = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
_JUNG = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
_JONG = "_ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ"


def _compose(cho: str, jung: str, jong: str = "_") -> str:
    return chr(0xAC00 + _CHO.index(cho) * 588 + _JUNG.index(jung) * 28 + _JONG.index(jong))


# 성모(초성). f→ㅍ, r→ㄹ, z→ㅉ, c→ㅊ, s→ㅆ (외래어표기법).
_INITIALS = {
    "b": "ㅂ", "p": "ㅍ", "m": "ㅁ", "f": "ㅍ",
    "d": "ㄷ", "t": "ㅌ", "n": "ㄴ", "l": "ㄹ",
    "g": "ㄱ", "k": "ㅋ", "h": "ㅎ",
    "j": "ㅈ", "q": "ㅊ", "x": "ㅅ",
    "zh": "ㅈ", "ch": "ㅊ", "sh": "ㅅ", "r": "ㄹ",
    "z": "ㅉ", "c": "ㅊ", "s": "ㅆ",
}

# 운모(중성,종성) 목록. 첫 음절은 초성=성모, 이후 음절은 초성 ㅇ.
_FINALS = {
    "a": [("ㅏ", "_")], "o": [("ㅗ", "_")], "e": [("ㅓ", "_")], "ê": [("ㅔ", "_")],
    "i": [("ㅣ", "_")], "u": [("ㅜ", "_")], "v": [("ㅟ", "_")],
    "ai": [("ㅏ", "_"), ("ㅣ", "_")], "ei": [("ㅔ", "_"), ("ㅣ", "_")],
    "ao": [("ㅏ", "_"), ("ㅗ", "_")], "ou": [("ㅓ", "_"), ("ㅜ", "_")],
    "an": [("ㅏ", "ㄴ")], "en": [("ㅓ", "ㄴ")], "ang": [("ㅏ", "ㅇ")],
    "eng": [("ㅓ", "ㅇ")], "er": [("ㅓ", "ㄹ")], "ong": [("ㅜ", "ㅇ")],
    "ia": [("ㅑ", "_")], "ie": [("ㅖ", "_")], "iao": [("ㅑ", "_"), ("ㅗ", "_")],
    "iu": [("ㅠ", "_")], "iou": [("ㅠ", "_")], "ian": [("ㅖ", "ㄴ")], "in": [("ㅣ", "ㄴ")],
    "iang": [("ㅑ", "ㅇ")], "ing": [("ㅣ", "ㅇ")], "iong": [("ㅠ", "ㅇ")],
    "ua": [("ㅘ", "_")], "uo": [("ㅝ", "_")], "uai": [("ㅘ", "_"), ("ㅣ", "_")],
    "ui": [("ㅜ", "_"), ("ㅣ", "_")], "uei": [("ㅜ", "_"), ("ㅣ", "_")],
    "uan": [("ㅘ", "ㄴ")], "un": [("ㅜ", "ㄴ")], "uen": [("ㅜ", "ㄴ")],
    "uang": [("ㅘ", "ㅇ")], "ueng": [("ㅝ", "ㅇ")],
    "ue": [("ㅞ", "_")], "ve": [("ㅞ", "_")], "van": [("ㅟ", "_"), ("ㅏ", "ㄴ")],
    "vn": [("ㅟ", "ㄴ")],
}

# 성모 zh/ch/sh/r/z/c/s + i (설치/권설음 뒤 i) → ㅡ. 예: 时shi→스, 日ri→르, 子zi→쯔.
_BUZZ_I = {"zh", "ch", "sh", "r", "z", "c", "s"}

# ㅈ/ㅉ/ㅊ 뒤 이중모음 단순화(쟈→자, 쟝→장, 졔→제). ㅅ은 제외(샤먼=厦门 유지).
_GLIDE_SIMPLIFY = {"ㅑ": "ㅏ", "ㅕ": "ㅓ", "ㅖ": "ㅔ", "ㅛ": "ㅗ", "ㅠ": "ㅜ"}

# 영성모(y/w 시작) 음절 — 외래어표기법 통째 매핑.
_ZERO = {
    "yi": "이", "ya": "야", "ye": "예", "yao": "야오", "you": "유", "yan": "옌",
    "yin": "인", "yang": "양", "ying": "잉", "yo": "요", "yong": "융",
    "yu": "위", "yue": "웨", "yuan": "위안", "yun": "윈",
    "wu": "우", "wa": "와", "wo": "워", "wai": "와이", "wei": "웨이",
    "wan": "완", "wang": "왕", "wen": "원", "weng": "웡",
    # 단독 모음 음절(영성모)
    "a": "아", "o": "오", "e": "어", "ai": "아이", "ei": "에이", "ao": "아오",
    "ou": "어우", "an": "안", "en": "언", "ang": "앙", "eng": "엉", "er": "얼",
}


def _split(syl: str):
    """병음 음절 → (성모, 운모). 영성모면 성모=''. ü는 v로 표기."""
    s = syl.replace("ü", "v")
    # j/q/x 뒤의 u는 실제 ü
    for two in ("zh", "ch", "sh"):
        if s.startswith(two):
            return two, s[2:]
    if s and s[0] in "bpmfdtnlgkhjqxrzcs":
        init, fin = s[0], s[1:]
        if init in ("j", "q", "x") and fin.startswith("u"):
            fin = "v" + fin[1:]  # ju→jv(ü), juan→jvan(üan), jun→jvn(ün)
        return init, fin
    return "", s


def _syllable_to_hangul(syl: str) -> str:
    syl = syl.lower().strip()
    if not syl:
        return ""
    if syl in _ZERO:
        return _ZERO[syl]
    init, fin = _split(syl)
    # 설치/권설음 + i → ㅡ
    if init in _BUZZ_I and fin == "i":
        cho = _INITIALS[init]
        return _compose(cho, "ㅡ")
    parts = _FINALS.get(fin)
    if parts is None:
        return ""  # 미지원 음절(드묾) — 호출측에서 처리
    cho0 = _INITIALS.get(init, "ㅇ")
    out = []
    for i, (jung, jong) in enumerate(parts):
        cho = cho0 if i == 0 else "ㅇ"
        if cho in ("ㅈ", "ㅉ", "ㅊ") and jung in _GLIDE_SIMPLIFY:
            jung = _GLIDE_SIMPLIFY[jung]
        out.append(_compose(cho, jung, jong))
    return "".join(out)


def transliterate(zh: str) -> str:
    """한자 문자열 → 한국어 음차. pypinyin 없거나 미지원 음절이면 ''(부분)."""
    if not _HAS_PYPINYIN or not zh:
        return ""
    syls = lazy_pinyin(zh, style=Style.NORMAL, errors="ignore")
    out = []
    for s in syls:
        h = _syllable_to_hangul(s)
        if not h:
            return ""  # 하나라도 변환 실패 시 전체 포기(오음차 방지)
        out.append(h)
    return "".join(out)

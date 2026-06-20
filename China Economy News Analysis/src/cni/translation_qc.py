"""번역 직후 통합 품질 점검 게이트 (단일 진입점).

모든 번역/LLM 한국어 산출물 *직후* run_qc()를 호출해 4개 오류를 일괄 점검·교정한다:
  ① 정치-시점:  我国/我们/本国 및 '우리나라/우리 정부/자국' → '중국'  + 주권 선전 중립화
  ② 중국어-only: 한국어 비율이 낮으면 Papago로 재번역(결과가 더 깨끗할 때만 채택)
  ③ 평어체:      문장형 필드의 비경어 종결 → 경어체
  ④ 문장 중단:   미완결 문장은 마지막 완결 문장까지 절단
  ⑤ 시제:       과거 시점(올해 이전 연도/작년 등)을 가리키는 문장의 미래·추정형
                종결("~할 전망이다")을 과거형("~했습니다")으로 교정. 중국어는 시제
                표지가 없어 Papago가 확정된 과거 실적을 미래형으로 옮기는 사례 대응.

반환: (corrected_text, issues:list[str]). 교정 없으면 issues=[].
설계: leaf 모듈 — 무거운 의존은 함수 내 지연 임포트로 순환참조 방지.
"""
import re
import logging
import datetime
import difflib

logger = logging.getLogger("translation_qc")

_CJK = re.compile(r'[一-鿿]')
# CLAUDE.md §6 고유명사 병기 "(汉字)" 또는 "(汉字, English)" — 한자비율 계산에서 제외
_ANNOT = re.compile(r'\(([一-鿿]{1,8})(?:,\s*[A-Za-z][A-Za-z0-9 .&\-]*)?\)')

# 중국 매체 자기지칭(=중국). 한국어 번역 전/후 모두 대비해 원문 한자형을 직접 치환.
_CN_SELF = [("我国", "중국"), ("我國", "중국"), ("我们", "중국"), ("我們", "중국"),
            ("本国", "중국"), ("本國", "중국"), ("我省", "중국"), ("我市", "중국")]

# 검사 적용 대상 필드군
SENTENCE_FIELDS = {"summary", "summary_ko", "refined_ko", "market_impact",
                   "tip", "hansanguk_tip", "analysis_ko"}
TITLE_FIELDS = {"translated_title", "card_headline", "headline", "title"}

_SENT_END = "다요음함임됨"   # 명사/경어 종결 허용 마지막 글자
_END_PUNCT = ".。!?"


def _strip_annot(t: str) -> str:
    return _ANNOT.sub("", t or "")


def _has_chinese(t: str) -> bool:
    return bool(_CJK.search(_strip_annot(t)))


def _korean_ratio(t: str) -> float:
    s = _strip_annot(t)
    if not s:
        return 1.0
    return len(_CJK.sub("", s)) / len(s)


def _is_complete(t: str) -> bool:
    t = (t or "").rstrip().rstrip("\"'’”)")
    if not t:
        return False
    return t[-1] in _END_PUNCT or t[-1] in _SENT_END


def _trim_to_last_sentence(t: str) -> str:
    """마지막 완결 문장까지 절단 (미완결 꼬리 제거)."""
    t = (t or "").strip()
    if not t:
        return t
    # 종결 부호/어미 위치 탐색
    matches = list(re.finditer(r'[.。!?]|[다요음함임됨](?=\s|$)', t))
    if not matches:
        return t
    end = matches[-1].end()
    trimmed = t[:end].strip()
    return trimmed if len(trimmed) >= 10 else t


# ── ⑤ 시제 보정 ──────────────────────────────────────────────────────────
# 확정된 과거 시점 표지. 이 단어/연도가 든 문장에서만 미래형→과거형 보정해
# 진짜 미래(내년·계획·예정) 문장은 절대 건드리지 않는다(보수적 적용).
_PAST_TIME_WORDS = ("지난해", "작년", "전년", "예년", "지난 분기", "전분기",
                    "지난달", "지난주", "당시", "이전")
_YEAR_RE = re.compile(r'(20\d{2})\s*년')

# 하다-동사 어간(X) + "할" + 미래/추정 종결  →  X + "했습니다"
# 비(非)하다 동사("오를", "늘어날")는 어간이 "할"로 끝나지 않아 매칭되지 않음 → 보수적.
_FUT_PATTERNS = [
    # X할 (것으로) {전망/예상/관측/추정/예측}{이다/입니다/된다/됩니다}
    re.compile(r'([가-힣]{1,12})할\s*(?:것으로\s*)?'
               r'(?:전망|예상|관측|추정|예측)(?:이다|입니다|된다|됩니다)'),
    # X할 것으로 보인다/보입니다
    re.compile(r'([가-힣]{1,12})할\s*것으로\s*보(?:인다|입니다)'),
    # X할 것이다/것입니다
    re.compile(r'([가-힣]{1,12})할\s*것(?:이다|입니다)'),
]


# 시점 표지 바로 뒤에 오면 '비교 기준'(사건 시점 아님)임을 나타내는 말.
# 예: "2025년 수준을 초과할 전망"(=올해 전망), "전년 대비 증가할 것"(=올해 전망)
# → 이런 비교문은 미래 전망일 수 있으므로 과거로 단정하지 않는다.
_COMPARE_MARKERS = ("수준", "대비", "보다", "동기", "에 비", "과 비교", "와 비교", "比")


def _sentence_is_past(s: str, cur_year: int) -> bool:
    def _is_compare(end_idx: int) -> bool:
        return any(cm in s[end_idx:end_idx + 6] for cm in _COMPARE_MARKERS)

    # ① 과거 부사(작년/지난해 등) — 단, 비교 기준으로만 쓰였으면 제외
    for w in _PAST_TIME_WORDS:
        p = s.find(w)
        while p != -1:
            if not _is_compare(p + len(w)):
                return True
            p = s.find(w, p + 1)
    # ② 올해보다 앞선 연도 — 단, 'YYYY년 수준/대비/…' 비교 기준이면 제외
    for m in _YEAR_RE.finditer(s):
        if int(m.group(1)) < cur_year and not _is_compare(m.end()):
            return True
    return False


def _fix_tense(text: str, cur_year: int = None):
    """과거 시점 문장의 미래·추정형 종결을 과거 경어형으로 교정. (corrected, changed)."""
    if cur_year is None:
        cur_year = datetime.date.today().year
    # 종결부호를 보존하며 문장 토큰화 (delim을 별도 토큰으로 유지)
    tokens = re.split(r'([.。!?]+)', text)
    changed = False
    for i in range(0, len(tokens), 2):
        s = tokens[i]
        if not s or not _sentence_is_past(s, cur_year):
            continue
        new = s
        for pat in _FUT_PATTERNS:
            new = pat.sub(lambda m: m.group(1) + '했습니다', new)
        if new != s:
            tokens[i] = new
            changed = True
    return ("".join(tokens) if changed else text), changed


# ── 교정 내역 기록(번역 교정 게시판용) ─────────────────────────────────────
# 발행 경로(record=True)에서 의미 있는 교정만 DB에 적재한다. 평어체/문장절단
# 같은 단순 서식 보정은 제외하고, 시제·시점(我国/우리나라)·정치 중립화만 게시.
_BOARD_ISSUES = ("tense", "perspective", "cn_self", "political")


_DIFF_BOUNDARY = " \n\t.。!?,，·"


def _expand_to_boundary(s: str, lo: int, hi: int, max_ctx: int = 18) -> str:
    """바뀐 구간 [lo,hi)를 좌우 공백/문장부호 경계까지 확장(어절 단위 가독성)."""
    while lo > 0 and (hi - lo) < max_ctx and s[lo - 1] not in _DIFF_BOUNDARY:
        lo -= 1
    while hi < len(s) and (hi - lo) < max_ctx and s[hi] not in _DIFF_BOUNDARY:
        hi += 1
    return s[lo:hi].strip()


def _compact_diff(before: str, after: str):
    """before→after에서 바뀐 조각만 어절 단위로 추출. [(before_frag, after_frag), ...]."""
    sm = difflib.SequenceMatcher(None, before, after)
    out = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        b = _expand_to_boundary(before, i1, i2)
        a = _expand_to_boundary(after, j1, j2)
        if b or a:
            out.append((b, a))
    return out


def _persist_corrections(news_id, field, recs):
    """교정 내역을 translation_corrections 테이블에 적재(best-effort)."""
    if not news_id or not recs:
        return
    try:
        from src.database.models import get_connection
        conn = get_connection()
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS translation_corrections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                news_id INTEGER,
                field TEXT,
                issue_type TEXT,
                before_text TEXT,
                after_text TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_tc_news ON translation_corrections(news_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_tc_created ON translation_corrections(created_at)")
        for issue, before_frag, after_frag in recs:
            c.execute(
                "INSERT INTO translation_corrections "
                "(news_id, field, issue_type, before_text, after_text) VALUES (?,?,?,?,?)",
                (news_id, field, issue, before_frag, after_frag))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"  교정내역 적재 실패 #{news_id}: {e}")


def run_qc(text, field: str = "summary", news_id=None, allow_papago: bool = True,
           record: bool = False):
    """번역 직후 통합 점검. (corrected, issues) 반환.

    allow_papago=False면 중국어-only Papago 재번역을 건너뛴다(유료 호출 절감).
    내부 분석용(claude_analyzer) 산출물은 False로 호출하고, 발행 경로
    (CNI summary_ko/headline, analysis_ko)만 True로 두어 유료 소모를 줄인다.
    record=True면 의미 있는 교정(시제·시점·정치)을 translation_corrections에
    적재해 대시보드 '번역 교정 게시판'에 노출한다.
    """
    if not text or not str(text).strip():
        return text, []
    out = str(text)
    issues = []
    recs = []   # 게시판 적재용 (issue, before_frag, after_frag)

    def _note(issue_key, label, before, after):
        """교정 발생 시 issues 기록 + (게시 대상이면) before/after 조각 수집."""
        issues.append(label)
        if record and issue_key in _BOARD_ISSUES:
            for b, a in _compact_diff(before, after):
                recs.append((label, b, a))

    # ── ① 정치: 중국어 자기지칭 → 중국 (선처리, 무료) ──
    _prev = out
    for a, b in _CN_SELF:
        out = out.replace(a, b)
    if out != _prev:
        _note("cn_self", "cn_self→중국", _prev, out)

    # ── ② 중국어-only: 한국어 비율 낮으면 Papago 재번역(발행 경로만) ──
    if allow_papago and _has_chinese(out) and _korean_ratio(out) < 0.90:
        try:
            from src.cni.translator import papago_translate
            cand = papago_translate(_strip_annot(out).strip())
            if cand and _korean_ratio(cand) > _korean_ratio(out) and len(cand) > 3:
                out = cand
                issues.append("chinese_only→papago")
        except Exception as e:
            logger.warning(f"  QC papago fallback failed: {e}")

    # ── ① 정치: 시점 보정 (우리나라/우리 정부/자국/국내 → 중국) ──
    try:
        from src.utils.title_validator import correct_title
        fixed = correct_title(out)
        if fixed != out:
            _note("perspective", "perspective→중국", out, fixed)
            out = fixed
    except Exception as e:
        logger.warning(f"  QC perspective failed: {e}")

    # ── ① 정치: 주권 선전 중립화 ──
    try:
        from src.cni.political_check import check_and_neutralize
        cleaned, score, reasons = check_and_neutralize(out)
        if cleaned != out:
            _note("political", f"political:{score:.1f}", out, cleaned)
            out = cleaned
    except Exception as e:
        logger.warning(f"  QC political failed: {e}")

    # ── ⑤시제 + ③평어체 + ④문장중단: 문장형 필드만 (제목/헤드라인 제외) ──
    if field in SENTENCE_FIELDS:
        try:
            fixed_t, t_changed = _fix_tense(out)
            if t_changed:
                _note("tense", "tense→past", out, fixed_t)
                out = fixed_t
        except Exception as e:
            logger.warning(f"  QC tense failed: {e}")
        try:
            from src.cni.postprocess import ensure_polite_korean
            polite = ensure_polite_korean(out)
            if polite != out:
                issues.append("polite")
                out = polite
        except Exception as e:
            logger.warning(f"  QC polite failed: {e}")
        if not _is_complete(out):
            trimmed = _trim_to_last_sentence(out)
            if trimmed and trimmed != out:
                issues.append("truncated")
                out = trimmed

    if issues:
        logger.info(f"  QC[{field}]{' #'+str(news_id) if news_id else ''}: {issues}")
    if record and recs:
        _persist_corrections(news_id, field, recs)
    return out, issues


def qc(text, field: str = "summary", news_id=None) -> str:
    """교정 텍스트만 반환하는 편의 래퍼."""
    return run_qc(text, field, news_id)[0]

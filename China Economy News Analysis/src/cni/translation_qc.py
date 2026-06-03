"""번역 직후 통합 품질 점검 게이트 (단일 진입점).

모든 번역/LLM 한국어 산출물 *직후* run_qc()를 호출해 4개 오류를 일괄 점검·교정한다:
  ① 정치-시점:  我国/我们/本国 및 '우리나라/우리 정부/자국' → '중국'  + 주권 선전 중립화
  ② 중국어-only: 한국어 비율이 낮으면 Papago로 재번역(결과가 더 깨끗할 때만 채택)
  ③ 평어체:      문장형 필드의 비경어 종결 → 경어체
  ④ 문장 중단:   미완결 문장은 마지막 완결 문장까지 절단

반환: (corrected_text, issues:list[str]). 교정 없으면 issues=[].
설계: leaf 모듈 — 무거운 의존은 함수 내 지연 임포트로 순환참조 방지.
"""
import re
import logging

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


def run_qc(text, field: str = "summary", news_id=None, allow_papago: bool = True):
    """번역 직후 통합 점검. (corrected, issues) 반환.

    allow_papago=False면 중국어-only Papago 재번역을 건너뛴다(유료 호출 절감).
    내부 분석용(claude_analyzer) 산출물은 False로 호출하고, 발행 경로
    (CNI summary_ko/headline, analysis_ko)만 True로 두어 유료 소모를 줄인다.
    """
    if not text or not str(text).strip():
        return text, []
    out = str(text)
    issues = []

    # ── ① 정치: 중국어 자기지칭 → 중국 (선처리, 무료) ──
    for a, b in _CN_SELF:
        if a in out:
            out = out.replace(a, b)
            if "cn_self" not in issues:
                issues.append("cn_self→중국")

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
            issues.append("perspective→중국")
            out = fixed
    except Exception as e:
        logger.warning(f"  QC perspective failed: {e}")

    # ── ① 정치: 주권 선전 중립화 ──
    try:
        from src.cni.political_check import check_and_neutralize
        cleaned, score, reasons = check_and_neutralize(out)
        if cleaned != out:
            issues.append(f"political:{score:.1f}")
            out = cleaned
    except Exception as e:
        logger.warning(f"  QC political failed: {e}")

    # ── ③평어체 + ④문장중단: 문장형 필드만 (제목/헤드라인 제외) ──
    if field in SENTENCE_FIELDS:
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
    return out, issues


def qc(text, field: str = "summary", news_id=None) -> str:
    """교정 텍스트만 반환하는 편의 래퍼."""
    return run_qc(text, field, news_id)[0]

"""공개 즉시 번역 보완 (On-Publish Translation Refine) — 핵심 로직.

공개된 CNI 뉴스의 요약·헤드라인·팁을 **원문 중국어와 대조**하여 LLM으로 재번역하고,
품질 게이트를 통과한 경우에만 공개본을 자동 교체한다.

- 대상 필드: summary(refined_ko), headline(card_headline), tip(hansanguk_tip)
- 멱등: refine_log에 (news_id, field, refiner_ver) 행이 있으면 재처리 skip
- 감사/롤백: 모든 시도를 refine_log에 before/after/판정/사유로 기록

설계: docs/onpublish_refine_design.md
"""

import sys
import re
import json
import hashlib
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import requests

from src.database.models import get_connection
from src.cni.summary_store import get_summary, update_refined
from src.cni.postprocess import (
    OLLAMA_URL, REFINE_MODEL, TIMEOUT,
    has_chinese_residue, ensure_polite_korean, refine_korean, fix_headline_terms,
)
from src.cni.translation_qc import run_qc, _is_complete
from src.utils.proper_noun_formatter import format_proper_nouns
from src.utils.title_postprocessor import postprocess_title

logger = logging.getLogger(__name__)

REFINER_VER = "r1"
FIELDS = ("summary", "headline", "tip")

# 게이트 파라미터
KOREAN_RATIO_MIN = 0.85       # Hangul / 비공백 최소 비율
LEN_LOW, LEN_HIGH = 0.6, 1.6  # 원본 대비 길이 허용 배수
HEADLINE_MAX_CHARS = 36
HEADLINE_MAX_BYTES = 72
SUMMARY_SRC_MAX = 3000        # 원문 프롬프트 투입 상한


# ────────────────────────────────────────────────────────────
# 스키마 (idempotent migration)
# ────────────────────────────────────────────────────────────
def init_refine_schema():
    """refine_log 테이블 + news.refine_status/refined_done_at 컬럼을 멱등 생성."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS refine_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id      TEXT    NOT NULL,
            news_id     INTEGER NOT NULL,
            field       TEXT    NOT NULL,
            old_text    TEXT,
            new_text    TEXT,
            decision    TEXT    NOT NULL,
            reason      TEXT,
            metrics     TEXT,
            source_hash TEXT,
            refiner_ver TEXT    NOT NULL,
            model       TEXT,
            refined_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_refine_log_news ON refine_log(news_id, field)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_refine_log_run  ON refine_log(run_id)")

    cols = {r["name"] for r in cur.execute("PRAGMA table_info(news)").fetchall()}
    if "refine_status" not in cols:
        cur.execute("ALTER TABLE news ADD COLUMN refine_status TEXT")
    if "refined_done_at" not in cols:
        cur.execute("ALTER TABLE news ADD COLUMN refined_done_at DATETIME")
    if "refine_retries" not in cols:
        cur.execute("ALTER TABLE news ADD COLUMN refine_retries INTEGER DEFAULT 0")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_news_refine_status ON news(refine_status)")
    conn.commit()
    conn.close()


# ────────────────────────────────────────────────────────────
# 품질 게이트 헬퍼
# ────────────────────────────────────────────────────────────
def korean_ratio(text: str) -> float:
    """비공백 문자 중 한글 음절 비율."""
    chars = [c for c in (text or "") if not c.isspace()]
    if not chars:
        return 0.0
    hangul = sum(1 for c in chars if "가" <= c <= "힣")
    return hangul / len(chars)


def _number_tokens(text: str) -> list[str]:
    """콤마 제거 후 숫자 시퀀스(길이≥2 또는 소수점/% 포함)만 추출."""
    t = (text or "").replace(",", "")
    out = []
    for m in re.findall(r"\d+(?:\.\d+)?", t):
        if len(m.replace(".", "")) >= 2 or "." in m:
            out.append(m)
    return out


def numbers_consistent(new_text: str, original_zh: str) -> tuple[bool, list[str]]:
    """new의 모든 숫자 토큰이 원문(콤마 제거)에 substring으로 존재해야 함.

    수치 환각(없는 금액·증가율·날짜 생성)을 막는 최후 방어선.
    반환: (정합 여부, 원문에 없는 숫자 목록)
    """
    src = (original_zh or "").replace(",", "")
    missing = [n for n in _number_tokens(new_text) if n not in src]
    return (len(missing) == 0, missing)


def byte_len(text: str) -> int:
    return len((text or "").encode("utf-8"))


def quality_gate(field: str, old: str, new: str, original_zh: str) -> tuple[bool, str, dict]:
    """8종 품질 게이트. 통과(True)해야만 자동 교체. 실패 시 (False, 사유, metrics)."""
    old = (old or "").strip()
    new = (new or "").strip()
    kr = korean_ratio(new)
    len_ratio = (len(new) / len(old)) if old else (1.0 if new else 0.0)
    num_ok, num_missing = numbers_consistent(new, original_zh)
    metrics = {
        "korean_ratio": round(kr, 3),
        "len_old": len(old), "len_new": len(new),
        "len_ratio": round(len_ratio, 3),
        "num_missing": num_missing,
        "bytes_new": byte_len(new),
    }

    if not new:
        return False, "empty", metrics
    # 8) 실질 변경 없음 → unchanged (별도 처리, 게이트 실패 아님)
    if new == old:
        return False, "unchanged", metrics
    # 1) 한국어 비율
    if kr < KOREAN_RATIO_MIN:
        return False, f"korean_ratio<{KOREAN_RATIO_MIN}", metrics
    # 2) 중국어 잔재
    if has_chinese_residue(new):
        return False, "chinese_residue", metrics
    # 3) 길이 정상범위 (old가 있을 때만)
    if old and not (LEN_LOW <= len_ratio <= LEN_HIGH):
        return False, f"len_ratio_out_of_band({len_ratio:.2f})", metrics
    # 4) 완결 문장 (요약·팁만 — 헤드라인은 종결부호 없음)
    if field in ("summary", "tip") and not _is_complete(new):
        return False, "incomplete_sentence", metrics
    # 5) 경어체 (요약·팁만)
    if field in ("summary", "tip") and ensure_polite_korean(new) != new:
        return False, "not_polite", metrics
    # 6) 헤드라인 길이
    if field == "headline" and (len(new) > HEADLINE_MAX_CHARS or byte_len(new) > HEADLINE_MAX_BYTES):
        return False, "headline_too_long", metrics
    # 7) 숫자 정합성
    if not num_ok:
        return False, f"number_hallucination:{num_missing}", metrics

    return True, "ok", metrics


# ────────────────────────────────────────────────────────────
# LLM 재번역
# ────────────────────────────────────────────────────────────
def _ollama(prompt: str, num_predict: int = 800) -> str:
    resp = requests.post(
        OLLAMA_URL,
        json={"model": REFINE_MODEL, "prompt": prompt, "stream": False,
              "options": {"temperature": 0.1, "num_predict": num_predict}},
        timeout=TIMEOUT)
    resp.raise_for_status()
    out = resp.json().get("response", "").strip()
    out = re.sub(r"^```[a-z]*\s*", "", out)
    out = re.sub(r"\s*```$", "", out)
    return out.strip()


def retranslate_summary(original_title: str, original_zh: str, current_ko: str) -> str:
    src = (original_zh or "")[:SUMMARY_SRC_MAX]
    prompt = f"""다음은 중국 경제 뉴스 원문(중국어)과 현재 한국어 요약이다.
원문과 대조하여 (1) 누락된 핵심 정보를 채우고 (2) 오역을 바로잡고 (3) 자연스러운 한국어 경어체(~습니다)로 다시 작성하라.
- 원문에 없는 내용은 절대 추가하지 마라.
- 숫자, 기업명, 인명, 기관명은 원문과 정확히 일치시켜라.
- 분량은 150~300자. 설명 없이 요약문만 출력하라.

[원문 제목] {original_title or ""}
[원문] {src}
[현재 요약] {current_ko or ""}

다시 작성한 한국어 요약:"""
    return _ollama(prompt, num_predict=800)


def retranslate_tip(original_zh: str, current_tip: str) -> str:
    src = (original_zh or "")[:SUMMARY_SRC_MAX]
    prompt = f"""다음은 중국 경제 뉴스에 대한 한국어 전문가 팁이다.
의미와 관점은 그대로 유지하되, 어색한 표현을 자연스럽게 다듬고 한국어 경어체(~습니다)로 통일하라.
- 새로운 사실이나 의견을 추가하지 마라.
- 200자 이내. 설명 없이 팁 문장만 출력하라.

[참고 원문(중국어)] {src}
[현재 팁] {current_tip or ""}

다듬은 팁:"""
    return _ollama(prompt, num_predict=400)


def retranslate_headline(refined_summary: str, current_hl: str) -> str:
    prompt = f"""다음 한국어 요약을 바탕으로 모바일 카드용 헤드라인을 작성하라.
- 36자 이내(공백 포함), 핵심만 압축.
- 한국어 명사형 종결(예: ~발표, ~확대). 마침표 없이.
- 설명 없이 헤드라인 한 줄만 출력하라.

[요약] {refined_summary or ""}
[현재 헤드라인] {current_hl or ""}

헤드라인:"""
    out = _ollama(prompt, num_predict=60)
    return out.splitlines()[0].strip() if out else ""


# ────────────────────────────────────────────────────────────
# 후처리 (기존 자산 재사용)
# ────────────────────────────────────────────────────────────
def _postprocess(field: str, text: str, original_zh: str, news_id: int) -> str:
    if not text:
        return text
    if field == "headline":
        pp = postprocess_title(text)
        out = pp.processed
        out = fix_headline_terms(out)
        out = format_proper_nouns(out, original_zh) or out
        return out.strip()[:HEADLINE_MAX_CHARS]
    # summary / tip
    out = refine_korean(text)                      # 태그/회사명/통화/용어/경어체/도메인
    out, _ = run_qc(out, field="summary", news_id=news_id, allow_papago=True)
    out = format_proper_nouns(out, original_zh) or out
    return out.strip()


# ────────────────────────────────────────────────────────────
# 멱등 / 로그
# ────────────────────────────────────────────────────────────
def already_refined(news_id: int, field: str, refiner_ver: str) -> bool:
    conn = get_connection()
    row = conn.execute(
        "SELECT 1 FROM refine_log WHERE news_id=? AND field=? AND refiner_ver=? "
        "AND decision IN ('applied','unchanged','rejected') LIMIT 1",
        (news_id, field, refiner_ver)).fetchone()
    conn.close()
    return row is not None


def _log(run_id, news_id, field, old, new, decision, reason, metrics, src_hash, refiner_ver):
    conn = get_connection()
    conn.execute(
        "INSERT INTO refine_log (run_id, news_id, field, old_text, new_text, decision, "
        "reason, metrics, source_hash, refiner_ver, model) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (run_id, news_id, field, old, new, decision, reason,
         json.dumps(metrics, ensure_ascii=False), src_hash, refiner_ver, REFINE_MODEL))
    conn.commit()
    conn.close()


def _source_hash(original_zh: str, baseline: str, field: str) -> str:
    h = hashlib.sha1()
    h.update(f"{original_zh}\x00{baseline}\x00{field}".encode("utf-8"))
    return h.hexdigest()


# ────────────────────────────────────────────────────────────
# 메인 진입점
# ────────────────────────────────────────────────────────────
def refine_news(news_id: int, fields=FIELDS, dry_run: bool = False,
                force: bool = False, refiner_ver: str = REFINER_VER) -> dict:
    """단건 뉴스의 지정 필드를 재번역·게이트·적용. 처리 리포트(dict) 반환."""
    init_refine_schema()
    run_id = f"op-{news_id}-{refiner_ver}"
    report = {"news_id": news_id, "fields": {}}

    conn = get_connection()
    n = conn.execute(
        "SELECT id, original_title, original_content, card_headline, card_headline_ai, "
        "hansanguk_tip, pipeline_status FROM news WHERE id=?", (news_id,)).fetchone()
    conn.close()
    if not n:
        report["error"] = "news_not_found"
        return report

    original_zh = n["original_content"] or ""
    cni = get_summary(news_id) or {}
    summary_ko = cni.get("summary_ko") or ""
    refined_ko = cni.get("refined_ko") or ""

    for field in fields:
        if not force and already_refined(news_id, field, refiner_ver):
            report["fields"][field] = {"decision": "skipped", "reason": "already_refined"}
            continue
        try:
            res = _refine_field(field, news_id, n, original_zh, summary_ko, refined_ko,
                                report, run_id, refiner_ver, dry_run)
            report["fields"][field] = res
            # 요약 적용분은 헤드라인 입력으로 쓰도록 갱신
            if field == "summary" and res.get("decision") == "applied":
                refined_ko = res["new"]
        except Exception as e:
            logger.exception("refine field failed: %s/%s", news_id, field)
            report["fields"][field] = {"decision": "error", "reason": str(e)}

    return report


def _refine_field(field, news_id, n, original_zh, summary_ko, refined_ko,
                  report, run_id, refiner_ver, dry_run) -> dict:
    if field == "summary":
        baseline = summary_ko or refined_ko
        old = refined_ko or summary_ko
        raw = retranslate_summary(n["original_title"], original_zh, baseline)
    elif field == "tip":
        baseline = old = n["hansanguk_tip"] or ""
        if not baseline.strip():
            return {"decision": "skipped", "reason": "no_tip"}
        raw = retranslate_tip(original_zh, baseline)
    elif field == "headline":
        # 요약 보완 결과가 있으면 그걸 입력으로, 없으면 기존 요약
        summ = (report.get("fields", {}).get("summary", {}).get("new")
                or refined_ko or summary_ko)
        baseline = n["card_headline_ai"] or n["card_headline"] or ""
        old = n["card_headline"] or ""
        raw = retranslate_headline(summ, old)
    else:
        return {"decision": "skipped", "reason": "unknown_field"}

    new = _postprocess(field, raw, original_zh, news_id)
    ok, reason, metrics = quality_gate(field, old, new, original_zh)
    src_hash = _source_hash(original_zh, baseline, field)

    decision = "applied" if ok else ("unchanged" if reason == "unchanged" else "rejected")
    result = {"decision": decision, "reason": reason, "old": old, "new": new, "metrics": metrics}

    if dry_run:
        result["decision"] = "dry_run:" + decision
        return result

    if ok:
        _apply(field, news_id, new)
    _log(run_id, news_id, field, old, new, decision, reason, metrics, src_hash, refiner_ver)
    return result


def _apply(field: str, news_id: int, new: str):
    if field == "summary":
        update_refined(news_id, new)
    elif field == "headline":
        conn = get_connection()
        conn.execute("UPDATE news SET card_headline=? WHERE id=?", (new[:72], news_id))
        conn.commit()
        conn.close()
    elif field == "tip":
        conn = get_connection()
        conn.execute("UPDATE news SET hansanguk_tip=? WHERE id=?", (new[:500], news_id))
        conn.commit()
        conn.close()

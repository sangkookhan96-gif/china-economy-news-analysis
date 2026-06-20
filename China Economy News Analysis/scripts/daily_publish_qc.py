"""공개 직후 일일 품질점검(QC).

오번역·형식·스타일을 '개시 원칙'대로 재점검·교정하고, 대시보드 게시판에 점검 일시·
수정 내역을 기록한다. 하루 3회(에디션별: morning/afternoon/evening) 실행 권장.

점검 항목(결정적 패스만 — Ollama 미사용, Papago 비활성으로 무중단·무과금):
  ① 고유명사·중국어 독음 통일 (proper_noun_formatter + 외래어표기법 음차)
  ② 시제(과거/미래)·시점(我国→중국)·정치 중립화 (translation_qc)  → translation_corrections 기록
  ③ 상장폐지 위험종목 표기(*ST/ST) 풀이 (explain_st_terms)
  ④ 평어체→경어체·문장 완결성 (translation_qc 내장)

안전: 직전 점검 이후 신규 공개분만(델타), 행단위 커밋·throttle, 멱등.
사용: python3 scripts/daily_publish_qc.py [morning|afternoon|evening|manual]
"""
import sys
import time
import json

sys.path.insert(0, "/home/jeozeohan/vibe_temp/China Economy News Analysis")
from src.database.models import get_connection
from src.cni.postprocess import explain_st_terms
from src.cni.translation_qc import run_qc, _compact_diff, _persist_corrections
from src.utils.proper_noun_formatter import format_proper_nouns

# 공개 지면 텍스트 필드
CNI_FIELDS = ("summary_ko", "refined_ko")
NEWS_SENT_FIELDS = ("summary", "market_impact", "hansanguk_tip")
NEWS_TITLE_FIELDS = ("translated_title", "card_headline")


def ensure_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS qc_audit_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            edition TEXT,
            window_from TIMESTAMP,
            n_checked INTEGER DEFAULT 0,
            n_corrected INTEGER DEFAULT 0,
            by_type TEXT
        )""")
    conn.commit()


def _qc_value(qc_field, value, source_zh, news_id, title=False):
    """한 필드에 교정 스택 적용. (new_value, changed)."""
    if not value or not value.strip():
        return value, False
    orig = value
    v1 = explain_st_terms(value)                                  # ③ *ST/ST 풀이
    if v1 != value:
        _persist_corrections(news_id, qc_field,
                             [("ST용어풀이", b, a) for b, a in _compact_diff(value, v1)])
    v2, _ = run_qc(v1, qc_field, news_id, allow_papago=False, record=True)  # ②④ (자체 기록)
    try:                                                          # ① 고유명사·음차 통일
        v3 = format_proper_nouns(v2, source_zh or "", max_annotations=(0 if title else 3))
    except Exception:
        v3 = v2
    if v3 != v2:
        _persist_corrections(news_id, qc_field,
                             [("고유명사·음차", b, a) for b, a in _compact_diff(v2, v3)])
    return v3, (v3 != orig)


def main():
    edition = sys.argv[1] if len(sys.argv) > 1 else "manual"
    conn = get_connection()
    c = conn.cursor()
    ensure_table(conn)

    # 윈도우: 마지막 QC 이후 공개분(없으면 최근 8시간)
    last = c.execute("SELECT MAX(run_at) m FROM qc_audit_runs").fetchone()["m"]
    if last:
        win_clause = "datetime(cs.published_at) > datetime(?)"
        win_param = [last]
        window_from = last
    else:
        win_clause = "datetime(cs.published_at) >= datetime('now','-8 hours')"
        win_param = []
        window_from = "(-8h)"

    rows = c.execute(f"""
        SELECT n.id, n.original_title, n.original_content
        FROM news n JOIN cni_summaries cs ON cs.news_id = n.id
        WHERE cs.published_at IS NOT NULL AND {win_clause}
    """, win_param).fetchall()
    print(f"[QC {edition}] 점검 대상(신규 공개): {len(rows)}건", flush=True)

    # 점검 시작 시각(이후 생성된 translation_corrections로 by-type 집계)
    started = c.execute("SELECT datetime('now') t").fetchone()["t"]
    n_corrected = 0
    t0 = time.time()

    for i, r in enumerate(rows):
        nid = r["id"]
        source = f"{r['original_title'] or ''}\n{r['original_content'] or ''}"
        news = c.execute("SELECT translated_title, card_headline, summary, market_impact, hansanguk_tip FROM news WHERE id=?",
                         (nid,)).fetchone()
        cni = c.execute("SELECT id, summary_ko, refined_ko FROM cni_summaries WHERE news_id=? ORDER BY id DESC LIMIT 1",
                        (nid,)).fetchone()
        changed = False

        nu = {}
        for f in NEWS_SENT_FIELDS:
            nu[f], ch = _qc_value("hansanguk_tip" if f == "hansanguk_tip" else "summary_ko",
                                  news[f], source, nid)
            changed = changed or ch
        for f in NEWS_TITLE_FIELDS:
            nu[f], ch = _qc_value("card_headline", news[f], source, nid, title=True)
            changed = changed or ch
        if changed:
            conn.execute("""UPDATE news SET summary=?, market_impact=?, hansanguk_tip=?,
                            translated_title=?, card_headline=? WHERE id=?""",
                         (nu["summary"], nu["market_impact"], nu["hansanguk_tip"],
                          nu["translated_title"], nu["card_headline"], nid))

        ccu = {}
        cchanged = False
        if cni:
            for f in CNI_FIELDS:
                ccu[f], ch = _qc_value("summary_ko", cni[f], source, nid)
                cchanged = cchanged or ch
            if cchanged:
                conn.execute("UPDATE cni_summaries SET summary_ko=?, refined_ko=? WHERE id=?",
                             (ccu["summary_ko"], ccu["refined_ko"], cni["id"]))

        if changed or cchanged:
            conn.commit()      # 행단위 커밋 — 락 장기점유 방지(대시보드 보호)
            n_corrected += 1
        time.sleep(0.03)       # throttle

    # by-type 집계: 이번 점검 중 기록된 교정 유형
    bt = {}
    for row in c.execute(
        "SELECT issue_type, COUNT(*) n FROM translation_corrections WHERE created_at >= ? GROUP BY issue_type",
        (started,)):
        bt[row["issue_type"]] = row["n"]

    conn.execute("""INSERT INTO qc_audit_runs (edition, window_from, n_checked, n_corrected, by_type)
                    VALUES (?,?,?,?,?)""",
                 (edition, str(window_from), len(rows), n_corrected, json.dumps(bt, ensure_ascii=False)))
    conn.commit()
    conn.close()
    print(f"[QC {edition}] 완료: 점검 {len(rows)}건, 수정 {n_corrected}건, 유형 {bt} ({time.time()-t0:.0f}s)",
          flush=True)


if __name__ == "__main__":
    main()

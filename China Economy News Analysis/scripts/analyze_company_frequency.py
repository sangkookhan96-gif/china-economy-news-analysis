"""지난 공개 뉴스에서 기업 출현 빈도를 분석해 표기 통일 대상을 보고한다.

규칙(사용자 지정):
  - 3회 이상 출현 기업만 표기 통일 대상.
  - 영어약칭 상용 기업: 영어약칭(한국어음차, 汉字)   예) CATL(닝더스다이, 宁德时代)
  - 그 외 기업:          한국어음차(汉字)             예) 윈난바이야오(云南白药)

doc_count = 해당 기업이 (원문 중국어 또는 공개 한국어 텍스트에) 등장한 '공개 뉴스 건수'.
"""
import sys
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database.models import get_connection
from src.utils.proper_nouns import COMPANIES
from src.utils.proper_noun_formatter import _render, _is_en_first

DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 60
MIN_COUNT = 3


def main():
    conn = get_connection()
    c = conn.cursor()
    c.execute(f"""
        SELECT n.id,
               COALESCE(n.original_title,'') || ' ' || COALESCE(n.original_content,'') AS src,
               COALESCE(NULLIF(cs.refined_ko,''), cs.summary_ko, '') || ' '
               || COALESCE(n.hansanguk_tip,'') || ' ' || COALESCE(n.translated_title,'') AS ko
        FROM news n
        LEFT JOIN cni_summaries cs ON cs.news_id = n.id
        WHERE (cs.published_at IS NOT NULL
               AND datetime(cs.published_at) >= datetime('now','-{DAYS} days'))
           OR (n.pipeline_status='published'
               AND n.analyzed_at >= datetime('now','-{DAYS} days'))
    """)
    rows = c.fetchall()
    conn.close()

    # 기업별 doc_count: 원문 한자형 또는 한국어/영문/이음차형이 등장한 뉴스 수
    doc_count = defaultdict(int)
    en_hits = defaultdict(int)   # 공개 본문에 영문약칭형이 등장한 뉴스 수
    ko_hits = defaultdict(int)   # 공개 본문에 한국어 음차형이 등장한 뉴스 수
    for r in rows:
        src, ko = r["src"], r["ko"]
        for zh, info in COMPANIES.items():
            ko_form = info["ko"]
            en = info.get("en")
            aliases = info.get("aliases", [])
            in_src = zh in src
            in_ko = (ko_form in ko) or any(a in ko for a in aliases) or (bool(en) and en in ko)
            if in_src or in_ko:
                doc_count[zh] += 1
                if en and en in ko:
                    en_hits[zh] += 1
                if ko_form in ko or any(a in ko for a in aliases):
                    ko_hits[zh] += 1

    qualifying = [(zh, n) for zh, n in doc_count.items() if n >= MIN_COUNT]
    qualifying.sort(key=lambda x: -x[1])

    print(f"=== 지난 {DAYS}일 공개 뉴스 {len(rows)}건 — 3회 이상 출현 기업 표기 통일 대상 "
          f"({len(qualifying)}개) ===\n")
    print(f"{'빈도':>4}  {'영문출현':>6} {'한글출현':>6}  {'형식':<6} 통일 표기 (첫 등장)")
    print("-" * 74)
    for zh, n in qualifying:
        info = COMPANIES[zh]
        fmt = "영문약칭" if _is_en_first(info) else "한국어"
        print(f"{n:>4}  {en_hits[zh]:>6} {ko_hits[zh]:>6}  {fmt:<6} {_render(info, zh, 'company')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

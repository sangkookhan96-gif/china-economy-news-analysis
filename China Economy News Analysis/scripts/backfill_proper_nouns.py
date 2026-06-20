"""Backfill proper-noun dual-script annotations on existing analyzed news.

Two targets are supported:

    --target news        (default) rewrite news.translated_title / summary /
                         market_impact across all analyzed rows. Matches the
                         legacy behavior of this script.

    --target published   rewrite every publicly exposed text field for rows
                         whose news is published via either the expert_reviews
                         path (er.publish_status='published') or the CNI path
                         (news.pipeline_status='published'). Affects:
                           - news.translated_title / summary / market_impact
                           - expert_reviews.expert_comment / ai_comment / ai_final_review
                           - cni_summaries.summary_ko / refined_ko

Usage:
    python3 scripts/backfill_proper_nouns.py --target news --days 30
    python3 scripts/backfill_proper_nouns.py --target published --days 30
    python3 scripts/backfill_proper_nouns.py --target published --days 30 --dry-run --show 8
"""

from __future__ import annotations

import re
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database.models import get_connection
from src.utils.proper_noun_formatter import format_proper_nouns


NEWS_FIELDS = ("translated_title", "summary", "market_impact")
# 공개 지면 교정 대상: 제목·요약·시장영향 + 한상국 팁(중국어 잔류·표기 통일 포함)
PUBLISHED_NEWS_FIELDS = ("translated_title", "summary", "market_impact", "hansanguk_tip")
REVIEW_FIELDS = ("expert_comment", "ai_comment", "ai_final_review")
CNI_FIELDS = ("summary_ko", "refined_ko")


# ---------- target=news (legacy) ---------------------------------------------

def _select_news_rows(cursor, days, limit, ids):
    where: list[str] = ["analyzed_at IS NOT NULL"]
    params: list = []
    if ids:
        placeholders = ",".join("?" for _ in ids)
        where.append(f"id IN ({placeholders})")
        params.extend(ids)
    elif days is not None:
        where.append(f"analyzed_at >= datetime('now', '-{int(days)} days')")
    sql = (
        "SELECT id, original_title, original_content, "
        "translated_title, summary, market_impact "
        "FROM news WHERE " + " AND ".join(where) + " ORDER BY analyzed_at DESC"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    cursor.execute(sql, params)
    return cursor.fetchall()


def _rewrite_news(row, source):
    new = {}
    changed = False
    for f in NEWS_FIELDS:
        old = row[f]
        updated = format_proper_nouns(old, source)
        new[f] = updated
        if updated != old:
            changed = True
    return new if changed else None


def run_news(cursor, args):
    rows = _select_news_rows(cursor, args.days, args.limit,
                             [int(x) for x in args.ids.split(",")] if args.ids else None)
    print(f"[backfill news] candidates: {len(rows)} rows")
    changed = 0
    shown = 0
    for row in rows:
        source = f"{row['original_title'] or ''}\n{row['original_content'] or ''}"
        new = _rewrite_news(row, source)
        if not new:
            continue
        changed += 1
        if args.dry_run and shown < args.show:
            print(f"\n--- news id={row['id']} ---")
            for f in NEWS_FIELDS:
                if new[f] != row[f]:
                    print(f"  {f}:\n    before: {row[f]}\n    after : {new[f]}")
            shown += 1
        if not args.dry_run:
            cursor.execute(
                "UPDATE news SET translated_title=?, summary=?, market_impact=? WHERE id=?",
                (new["translated_title"], new["summary"], new["market_impact"], row["id"]),
            )
    return changed, len(rows)


# ---------- target=published --------------------------------------------------

def _select_published_ids(cursor, days: int) -> list[int]:
    """Publicly exposed news ids = legacy path ∪ CNI path."""
    cursor.execute(
        f"""
        SELECT DISTINCT n.id
        FROM news n
        LEFT JOIN expert_reviews er
               ON er.news_id = n.id
              AND er.publish_status = 'published'
              AND er.expert_comment IS NOT NULL
        LEFT JOIN cni_summaries cs ON cs.news_id = n.id
        WHERE n.analyzed_at >= datetime('now','-{int(days)} days')
          AND (
                er.id IS NOT NULL
             OR (n.pipeline_status = 'published'
                 AND cs.summary_ko IS NOT NULL)
          )
        """
    )
    return [r["id"] for r in cursor.fetchall()]


def _load_published_row(cursor, news_id: int):
    cursor.execute(
        """
        SELECT n.id, n.original_title, n.original_content,
               n.translated_title, n.summary, n.market_impact, n.hansanguk_tip
        FROM news n WHERE n.id = ?
        """,
        (news_id,),
    )
    news = cursor.fetchone()
    cursor.execute(
        "SELECT id, expert_comment, ai_comment, ai_final_review "
        "FROM expert_reviews WHERE news_id = ? AND publish_status='published' "
        "ORDER BY id DESC LIMIT 1",
        (news_id,),
    )
    review = cursor.fetchone()
    cursor.execute(
        "SELECT id, summary_ko, refined_ko FROM cni_summaries WHERE news_id = ? "
        "ORDER BY id DESC LIMIT 1",
        (news_id,),
    )
    cni = cursor.fetchone()
    return news, review, cni


# 병기 "(汉字[, English])" 제거용 — 한자 잔류율 계산에서 기존 병기는 제외한다.
_ANNOT = re.compile(r"\([一-鿿][^)）]*\)")


def _korean_ratio(t: str) -> float:
    # 기존 병기 괄호를 제외한 '본문'의 한국어 비율 — 병기가 많아도 깎이지 않게.
    s = _ANNOT.sub("", t or "")
    bare = re.sub(r"[一-鿿]", "", s)
    return len(bare) / len(s) if s else 1.0


def _rewrite_field(value, source, max_annotations=3):
    # 정본 canonicalizer는 멱등이며 기존 병기(EN-우선 등)를 정본으로 접어 통일하므로,
    # '괄호가 있으면 건너뛰기'(옛 옵션 b)는 제거 — 그래야 CATL(닝더스다이)류가 교정된다.
    if not value:
        return value, False
    # 번역이 깨진 한자투성이 과거 행(병기 제외 본문 한국어<90%)은 가독성 도움 안 되므로 제외
    if _korean_ratio(value) < 0.90:
        return value, False
    updated = format_proper_nouns(value, source, max_annotations=max_annotations)
    return updated, updated != value


def run_published(cursor, args):
    ids = _select_published_ids(cursor, args.days)
    print(f"[backfill published] candidates: {len(ids)} publicly exposed rows in last {args.days}d")
    total_changed = 0
    shown = 0
    per_table = {"news": 0, "expert_reviews": 0, "cni_summaries": 0}

    for news_id in ids:
        news, review, cni = _load_published_row(cursor, news_id)
        if not news:
            continue
        source = f"{news['original_title'] or ''}\n{news['original_content'] or ''}"

        diffs: list[tuple[str, str, str, str]] = []  # (table, field, before, after)

        # news table (제목·요약·시장영향 + 한상국 팁)
        news_updates = {}
        for f in PUBLISHED_NEWS_FIELDS:
            new_v, ch = _rewrite_field(news[f], source)
            news_updates[f] = new_v
            if ch:
                diffs.append(("news", f, news[f], new_v))

        # expert_reviews
        review_updates = {}
        if review:
            for f in REVIEW_FIELDS:
                new_v, ch = _rewrite_field(review[f], source)
                review_updates[f] = new_v
                if ch:
                    diffs.append(("expert_reviews", f, review[f], new_v))

        # cni_summaries
        cni_updates = {}
        if cni:
            for f in CNI_FIELDS:
                new_v, ch = _rewrite_field(cni[f], source)
                cni_updates[f] = new_v
                if ch:
                    diffs.append(("cni_summaries", f, cni[f], new_v))

        if not diffs:
            continue
        total_changed += 1

        if args.dry_run and shown < args.show:
            print(f"\n--- news id={news_id} ---")
            for table, field, before, after in diffs:
                print(f"  [{table}.{field}]")
                print(f"    before: {before}")
                print(f"    after : {after}")
            shown += 1

        if not args.dry_run:
            # news update (always attempt; same-value writes are harmless)
            if any(news_updates[f] != news[f] for f in PUBLISHED_NEWS_FIELDS):
                cursor.execute(
                    "UPDATE news SET translated_title=?, summary=?, market_impact=?, "
                    "hansanguk_tip=? WHERE id=?",
                    (news_updates["translated_title"], news_updates["summary"],
                     news_updates["market_impact"], news_updates["hansanguk_tip"], news_id),
                )
                per_table["news"] += 1
            if review and any(review_updates[f] != review[f] for f in REVIEW_FIELDS):
                cursor.execute(
                    "UPDATE expert_reviews SET expert_comment=?, ai_comment=?, ai_final_review=? "
                    "WHERE id=?",
                    (review_updates["expert_comment"], review_updates["ai_comment"],
                     review_updates["ai_final_review"], review["id"]),
                )
                per_table["expert_reviews"] += 1
            if cni and any(cni_updates[f] != cni[f] for f in CNI_FIELDS):
                cursor.execute(
                    "UPDATE cni_summaries SET summary_ko=?, refined_ko=? WHERE id=?",
                    (cni_updates["summary_ko"], cni_updates["refined_ko"], cni["id"]),
                )
                per_table["cni_summaries"] += 1

    return total_changed, len(ids), per_table


# ---------- entry -------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--target", choices=("news", "published"), default="news")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--ids", type=str, default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--show", type=int, default=10)
    args = p.parse_args()

    conn = get_connection()
    cursor = conn.cursor()

    if args.target == "news":
        changed, total = run_news(cursor, args)
        per_table = None
    else:
        changed, total, per_table = run_published(cursor, args)

    if not args.dry_run:
        conn.commit()
    conn.close()

    verb = "would update" if args.dry_run else "updated"
    print(f"\n[backfill] {verb} {changed}/{total} rows")
    if per_table is not None and not args.dry_run:
        print(f"[backfill] per-table UPDATE counts: {per_table}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

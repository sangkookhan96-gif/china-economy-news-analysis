"""Content-Based Scoring System 테스트 및 검증 스크립트.

사용법:
    python tests/test_content_scorer.py              # 샘플 데이터로 테스트
    python tests/test_content_scorer.py --db          # DB의 최근 뉴스로 테스트
    python tests/test_content_scorer.py --db --limit 50  # DB에서 50개 테스트
"""

import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.collector.content_scorer import ContentScorer


# =============================================================================
# 샘플 뉴스 데이터 (다양한 유형)
# =============================================================================
SAMPLE_NEWS = [
    {
        "title": "习近平主持召开中央经济工作会议 部署2026年经济工作",
        "content": "中央经济工作会议12月11日至12日在北京举行。习近平出席会议并发表重要讲话。"
                   "会议强调，要加快建设以实体经济为支撑的现代化产业体系，推进新型工业化，"
                   "发展数字经济，加快人工智能发展。加大宏观政策调控力度，"
                   "实施更加积极的财政政策和适度宽松的货币政策。",
        "expected": "최고 점수 (최고 지도부 + 국무원급 + 전략산업 + 전국 범위)",
    },
    {
        "title": "国务院办公厅印发《关于加快推进半导体产业高质量发展的意见》",
        "content": "国务院办公厅近日印发意见，提出到2030年我国半导体产业规模达到万亿元级别。"
                   "意见明确，加大对芯片设计、晶圆制造、封装测试等环节的支持力度，"
                   "在北京、上海、深圳等地建设国家级集成电路产业基地。"
                   "财政部将设立500亿元专项基金支持。",
        "expected": "높은 점수 (국무원 + 반도체 핵심전략 + 대규모 금액 + 1선도시)",
    },
    {
        "title": "比亚迪宣布投资100亿元在深圳建设AI智能驾驶研发中心",
        "content": "比亚迪集团今日宣布将投资100亿元人民币在深圳前海建设全球AI智能驾驶研发中心。"
                   "该中心将聚焦自动驾驶、人工智能算法和车规级芯片研发。"
                   "项目预计2027年投入运营，届时将提供超过5000个就业岗位。",
        "expected": "높은 점수 (대형 민영 + AI/반도체 + 100억 + 선전 + 고용)",
    },
    {
        "title": "中美贸易紧张升级：美国宣布对华芯片出口新限制",
        "content": "美国商务部今日宣布新一轮对华半导体出口管制措施。新规将限制向中国出口"
                   "先进AI芯片和半导体制造设备。中国商务部回应称将采取必要反制措施。"
                   "分析人士认为，这将对全球供应链产生重大影响。",
        "expected": "높은 점수 (미중 관계 + 반도체 + 공급망 + 돌발성)",
    },
    {
        "title": "国家电网公司完成1000亿元绿色债券发行",
        "content": "中央企业国家电网公司今日成功发行1000亿元绿色债券，用于支持新能源基础设施建设。"
                   "这是全国最大规模的绿色债券发行。资金将投向光伏、风电和储能等领域。",
        "expected": "높은 점수 (중앙기업 + 신에너지 + 1000억 + 전국)",
    },
    {
        "title": "杭州市出台促进数字经济发展新政策",
        "content": "杭州市政府发布关于促进数字经济高质量发展的若干意见，提出到2025年"
                   "数字经济核心产业增加值突破8000亿元。支持人工智能、区块链等领域创新。",
        "expected": "중간 점수 (시급 정부 + 디지털경제 + 성급 중심도시)",
    },
    {
        "title": "某县食品加工企业获得500万元补贴",
        "content": "某县政府为支持当地食品加工产业发展，向三家中小企业发放共计500万元产业补贴。",
        "expected": "낮은 점수 (현급 + 전통제조 + 소규모 + 중소기업)",
    },
    {
        "title": "CPI同比上涨2.3%，物价保持温和上涨",
        "content": "国家统计局今日发布数据显示，2026年1月全国居民消费价格指数(CPI)"
                   "同比上涨2.3%。其中食品价格上涨3.1%，非食品价格上涨1.8%。"
                   "专家分析认为物价整体可控，就业市场保持稳定。",
        "expected": "중상 점수 (부위급 통계 + 민생/CPI + 전국 + 고용)",
    },
    {
        "title": "中国一带一路项目在东南亚取得重大进展",
        "content": "商务部宣布，一带一路框架下在东盟国家投资的基础设施项目"
                   "总投资额已超过500亿美元。其中铁路、港口等交通基础设施项目占60%。",
        "expected": "중상 점수 (부위급 + 일대일로 + 대규모 + 국제)",
    },
    {
        "title": "环保新规：全国碳排放交易市场扩容",
        "content": "生态环境部发布通知，全国碳排放权交易市场将于下月起扩大覆盖范围，"
                   "新增钢铁、水泥等行业纳入碳交易体系。这将影响全国超过5000家企业，"
                   "预计碳交易市场规模将突破千亿元。",
        "expected": "중상 점수 (부위급 + 환경/탄소 + 전국 + 천억)",
    },
]


def run_sample_test():
    """샘플 데이터로 점수 계산 테스트"""
    scorer = ContentScorer()

    print("=" * 80)
    print("Content-Based Scoring System 테스트 결과")
    print("=" * 80)

    scores = []
    for i, news in enumerate(SAMPLE_NEWS, 1):
        result = scorer.score(news["title"], news["content"])
        scores.append(result["total_score"])

        print(f"\n--- [{i}] {news['title'][:50]}... ---")
        print(f"  총점: {result['total_score']:.1f}점 (가중 원점수: {result['weighted_raw']:.1f})")
        print(f"  기대: {news['expected']}")
        print(f"  근거: {result['explanation']}")
        print(f"  세부 점수:")
        for key, val in result["breakdown"].items():
            weight_pct = {
                "policy_hierarchy": 25, "corporate_hierarchy": 15,
                "strategic_industry": 20, "economic_scale": 15,
                "geographic_significance": 10, "time_sensitivity": 5,
                "international_impact": 5, "social_impact": 5,
            }
            print(f"    {key}: {val}점 (가중치 {weight_pct.get(key, 0)}%)")
        if result["boosters"]:
            print(f"  부스터: {result['boosters']}")

    # 통계
    print("\n" + "=" * 80)
    print("점수 분포 통계")
    print("=" * 80)
    print(f"  샘플 수: {len(scores)}")
    print(f"  평균: {statistics.mean(scores):.1f}")
    print(f"  중앙값: {statistics.median(scores):.1f}")
    print(f"  표준편차: {statistics.stdev(scores):.1f}")
    print(f"  최고: {max(scores):.1f}")
    print(f"  최저: {min(scores):.1f}")

    # 순위
    ranked = sorted(enumerate(SAMPLE_NEWS, 1), key=lambda x: scores[x[0]-1], reverse=True)
    print("\n점수 순위:")
    for rank, (idx, news) in enumerate(ranked, 1):
        print(f"  {rank}위: [{idx}] {scores[idx-1]:.1f}점 - {news['title'][:40]}...")

    return scores


def run_db_test(limit: int = 100):
    """DB의 실제 뉴스로 점수 계산"""
    from src.database.models import get_connection

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, source, original_title, original_content
        FROM news
        WHERE original_title IS NOT NULL AND original_title != ''
        ORDER BY collected_at DESC
        LIMIT ?
    """, (limit,))
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        print("DB에 뉴스 데이터가 없습니다.")
        return

    scorer = ContentScorer()
    scores = []
    results = []

    print(f"\nDB 뉴스 {len(rows)}건 점수 계산 중...")

    for row in rows:
        title = row["original_title"] or ""
        content = row["original_content"] or ""
        result = scorer.score(title, content, row["source"])
        scores.append(result["total_score"])
        results.append({
            "id": row["id"],
            "source": row["source"],
            "title": title[:50],
            "score": result["total_score"],
            "explanation": result["explanation"],
        })

    # 통계
    print("\n" + "=" * 80)
    print(f"DB 뉴스 {len(rows)}건 점수 분포")
    print("=" * 80)
    print(f"  평균: {statistics.mean(scores):.1f}")
    print(f"  중앙값: {statistics.median(scores):.1f}")
    if len(scores) > 1:
        print(f"  표준편차: {statistics.stdev(scores):.1f}")
    print(f"  최고: {max(scores):.1f}")
    print(f"  최저: {min(scores):.1f}")

    # 구간별 분포
    bins = [(0, 20), (20, 40), (40, 60), (60, 80), (80, 100)]
    print("\n  구간별 분포:")
    for low, high in bins:
        count = sum(1 for s in scores if low <= s < high)
        bar = "#" * count
        print(f"    {low:3d}-{high:3d}: {count:3d}건 {bar}")
    count_100 = sum(1 for s in scores if s >= 100)
    if count_100:
        print(f"    100+  : {count_100:3d}건 {'#' * count_100}")

    # Top 10
    results.sort(key=lambda x: x["score"], reverse=True)
    print("\n  Top 10 고득점 뉴스:")
    for i, r in enumerate(results[:10], 1):
        print(f"    {i:2d}. [{r['score']:.1f}점] [{r['source']}] {r['title']}")
        print(f"        {r['explanation'][:80]}")

    # Bottom 5
    print("\n  Bottom 5 저득점 뉴스:")
    for i, r in enumerate(results[-5:], 1):
        print(f"    {i:2d}. [{r['score']:.1f}점] [{r['source']}] {r['title']}")

    # DB에 점수 저장 옵션
    print("\n  점수를 DB에 저장하려면 --save 옵션을 사용하세요.")


def save_scores_to_db(limit: int = 100):
    """계산된 점수를 DB에 저장"""
    from src.database.models import get_connection
    import json as json_mod

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, source, original_title, original_content
        FROM news
        WHERE original_title IS NOT NULL AND original_title != ''
        ORDER BY collected_at DESC
        LIMIT ?
    """, (limit,))
    rows = cursor.fetchall()

    scorer = ContentScorer()
    updated = 0

    for row in rows:
        title = row["original_title"] or ""
        content = row["original_content"] or ""
        result = scorer.score(title, content, row["source"])

        cursor.execute("""
            UPDATE news
            SET content_score = ?,
                score_breakdown = ?,
                score_explanation = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (
            result["total_score"],
            json_mod.dumps(result["breakdown"], ensure_ascii=False),
            result["explanation"],
            row["id"],
        ))
        updated += 1

    conn.commit()
    conn.close()
    print(f"\n{updated}건의 뉴스에 content_score 저장 완료.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Content Scoring 테스트")
    parser.add_argument("--db", action="store_true", help="DB의 실제 뉴스로 테스트")
    parser.add_argument("--limit", type=int, default=100, help="DB 테스트 시 최대 건수")
    parser.add_argument("--save", action="store_true", help="점수를 DB에 저장")
    args = parser.parse_args()

    # 항상 샘플 테스트 실행
    run_sample_test()

    if args.db:
        run_db_test(args.limit)

    if args.save:
        save_scores_to_db(args.limit)

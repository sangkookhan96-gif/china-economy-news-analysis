"""내용적 선정 기준(Content-Based Scoring) 모듈.

8가지 기준으로 뉴스의 실질적 중요도를 평가한다:
1. 정책/제도 계층 (Policy Hierarchy) - 25%
2. 기업/주체 계층 (Corporate Hierarchy) - 15%
3. 산업/기술 전략성 (Strategic Industry) - 20%
4. 경제 규모/영향도 (Economic Scale) - 15%
5. 지리적 중요도 (Geographic Significance) - 10%
6. 시간적 긴급성 (Time Sensitivity) - 5%
7. 국제적 영향도 (International Impact) - 5%
8. 사회적 파급효과 (Social Impact) - 5%
"""

import json
import logging
import re
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from config.content_scoring import (
    POLICY_HIERARCHY,
    CENTRAL_SOES,
    MAJOR_PRIVATE_CORPS,
    CORPORATE_TYPE_KEYWORDS,
    STRATEGIC_SOE_KEYWORDS,
    STRATEGIC_INDUSTRIES,
    FOOD_SECURITY_KEYWORDS,
    AMOUNT_PATTERNS,
    ECONOMIC_SCALE_THRESHOLDS,
    IMPACT_SCOPE_KEYWORDS,
    GEOGRAPHIC_SCORES,
    TIME_SENSITIVITY,
    INTERNATIONAL_IMPACT,
    SOCIAL_IMPACT,
    SCORING_WEIGHTS,
    BOOSTER_KEYWORDS,
    SOE_STRATEGIC_BOOSTER,
)

logger = logging.getLogger(__name__)


class ContentScorer:
    """뉴스 내용 기반 점수 평가기.

    제목과 본문을 분석하여 8가지 기준의 세부 점수와 종합 점수를 계산한다.
    """

    def __init__(self):
        # 중앙기업/민영기업 이름을 세트로 변환 (빠른 검색)
        self._central_soes = set(CENTRAL_SOES)
        self._private_corps = set(MAJOR_PRIVATE_CORPS)
        self._strategic_soe_kw = STRATEGIC_SOE_KEYWORDS

    def score(self, title: str, content: str, source: str = "") -> dict:
        """뉴스 종합 점수 계산.

        Args:
            title: 뉴스 제목 (원문 중국어)
            content: 뉴스 본문 (원문 중국어)
            source: 뉴스 출처 키

        Returns:
            dict with keys:
                - total_score (float): 0~100 종합 점수
                - breakdown (dict): 8개 기준별 세부 점수
                - boosters (list): 적용된 부스터 목록
                - explanation (str): 점수 산출 근거 설명
        """
        text = title + content

        # 8가지 기준별 점수 계산
        policy = self._score_policy_hierarchy(text)
        corporate = self._score_corporate_hierarchy(text)
        industry = self._score_strategic_industry(text)
        economic = self._score_economic_scale(text)
        geographic = self._score_geographic(text)
        time_sens = self._score_time_sensitivity(text)
        international = self._score_international_impact(text)
        social = self._score_social_impact(text)

        breakdown = {
            "policy_hierarchy": policy,
            "corporate_hierarchy": corporate,
            "strategic_industry": industry,
            "economic_scale": economic,
            "geographic_significance": geographic,
            "time_sensitivity": time_sens,
            "international_impact": international,
            "social_impact": social,
        }

        # 가중 평균 계산
        weighted_score = sum(
            breakdown[key]["score"] * SCORING_WEIGHTS[key]
            for key in SCORING_WEIGHTS
        )

        # 부스터 적용
        boosters = self._apply_boosters(text, breakdown)
        multiplier = 1.0
        for b in boosters:
            multiplier *= b["multiplier"]

        total_score = min(weighted_score * multiplier, 100.0)

        # 점수 산출 근거 생성
        explanation = self._build_explanation(breakdown, boosters, total_score)

        return {
            "total_score": round(total_score, 2),
            "weighted_raw": round(weighted_score, 2),
            "breakdown": {k: v["score"] for k, v in breakdown.items()},
            "breakdown_detail": breakdown,
            "boosters": boosters,
            "explanation": explanation,
        }

    # =========================================================================
    # 1. 정책/제도 계층 (Policy Hierarchy)
    # =========================================================================
    def _score_policy_hierarchy(self, text: str) -> dict:
        """정책 계층 점수: 전인대(100) > 국무원(95) > 부위급(80) > 성급(60) > 시급(40) > 현급(20)"""
        best_score = 0
        matched_level = ""
        matched_keywords = []

        for score_val in sorted(POLICY_HIERARCHY.keys(), reverse=True):
            level_info = POLICY_HIERARCHY[score_val]
            for kw in level_info["keywords"]:
                if kw in text:
                    if score_val > best_score:
                        best_score = score_val
                        matched_level = level_info["name"]
                    matched_keywords.append(kw)

            # 최고 점수 이미 확보되면 하위 레벨 키워드는 수집만
            if best_score == 100:
                break

        return {
            "score": best_score,
            "level": matched_level,
            "matched": matched_keywords[:5],
        }

    # =========================================================================
    # 2. 기업/주체 계층 (Corporate Hierarchy)
    # =========================================================================
    def _score_corporate_hierarchy(self, text: str) -> dict:
        """기업 계층 점수: 중앙기업 전략(100) > 중앙기업 일반(85) > 대형 민영(80) > 지방 국기업(60) > 중소(40) > 외자(60)"""
        best_score = 0
        matched_type = ""
        matched_entities = []

        # 중앙기업 확인
        has_central_soe = False
        for name in self._central_soes:
            if name in text:
                has_central_soe = True
                matched_entities.append(name)

        # 중앙기업 키워드 확인
        for kw in CORPORATE_TYPE_KEYWORDS["central_soe"]:
            if kw in text:
                has_central_soe = True
                matched_entities.append(kw)

        if has_central_soe:
            # 전략산업 중앙기업인지 추가 확인
            is_strategic = any(kw in text for kw in self._strategic_soe_kw)
            if is_strategic:
                best_score = 100
                matched_type = "중앙기업(전략산업)"
            else:
                best_score = 85
                matched_type = "중앙기업(일반산업)"

        # 대형 민영기업 확인
        if best_score < 80:
            for name in self._private_corps:
                if name in text:
                    best_score = max(best_score, 80)
                    matched_type = "대형 민영기업"
                    matched_entities.append(name)

            # 상장/유니콘 키워드
            for kw in CORPORATE_TYPE_KEYWORDS["listed"]:
                if kw in text:
                    best_score = max(best_score, 80)
                    if not matched_type:
                        matched_type = "상장기업"
                    matched_entities.append(kw)

            for kw in CORPORATE_TYPE_KEYWORDS["unicorn"]:
                if kw in text:
                    best_score = max(best_score, 80)
                    if not matched_type:
                        matched_type = "유니콘기업"
                    matched_entities.append(kw)

        # 외자기업
        if best_score < 60:
            for kw in CORPORATE_TYPE_KEYWORDS["foreign"]:
                if kw in text:
                    best_score = max(best_score, 60)
                    matched_type = "외자기업"
                    matched_entities.append(kw)

        # 지방 국기업
        if best_score < 60:
            for kw in CORPORATE_TYPE_KEYWORDS["local_soe"]:
                if kw in text:
                    best_score = max(best_score, 60)
                    matched_type = "지방 국유기업"
                    matched_entities.append(kw)

        # 중소기업 (기업 언급은 있으나 위에 해당하지 않는 경우)
        if best_score == 0:
            general_kw = ["企业", "公司", "集团"]
            if any(kw in text for kw in general_kw):
                best_score = 40
                matched_type = "중소 민영기업"

        return {
            "score": best_score,
            "type": matched_type,
            "matched": list(set(matched_entities))[:5],
        }

    # =========================================================================
    # 3. 산업/기술 전략성 (Strategic Industry)
    # =========================================================================
    def _score_strategic_industry(self, text: str) -> dict:
        """산업 전략성 점수: 핵심(100) > 중요(80) > 금융(75) > 부동산(60) > 전통(40)"""
        best_score = 0
        matched_industry = ""
        matched_keywords = []

        # 식량안보 특별 처리
        for kw in FOOD_SECURITY_KEYWORDS:
            if kw in text:
                best_score = max(best_score, 80)
                matched_industry = "식량안보"
                matched_keywords.append(kw)

        for score_val in sorted(STRATEGIC_INDUSTRIES.keys(), reverse=True):
            industry_info = STRATEGIC_INDUSTRIES[score_val]
            for kw in industry_info["keywords"]:
                if kw in text:
                    if score_val > best_score:
                        best_score = score_val
                        matched_industry = industry_info["name"]
                    matched_keywords.append(kw)

        return {
            "score": best_score,
            "industry": matched_industry,
            "matched": list(set(matched_keywords))[:5],
        }

    # =========================================================================
    # 4. 경제 규모/영향도 (Economic Scale)
    # =========================================================================
    def _score_economic_scale(self, text: str) -> dict:
        """경제 규모 점수: 금액 크기와 영향 범위로 산정"""
        max_amount = 0
        amount_text = ""

        # 만억(조) 단위 패턴
        for match in re.finditer(r"(\d+(?:\.\d+)?)\s*万亿", text):
            val = float(match.group(1)) * 1_0000_0000_0000
            if val > max_amount:
                max_amount = val
                amount_text = match.group(0)

        # 천억 단위
        if max_amount == 0:
            m = re.search(r"千亿", text)
            if m:
                max_amount = 100_000_000_000
                amount_text = "千亿"

        # 억 단위 패턴
        if max_amount < 100_000_000:
            for match in re.finditer(r"(\d+(?:\.\d+)?)\s*亿", text):
                val = float(match.group(1)) * 100_000_000
                if val > max_amount:
                    max_amount = val
                    amount_text = match.group(0)

        # 금액 기준 점수
        amount_score = 0
        for threshold, score_val in ECONOMIC_SCALE_THRESHOLDS:
            if max_amount >= threshold:
                amount_score = score_val
                break

        # 영향 범위 보너스
        scope_bonus = 0
        scope_matched = []
        for bonus, keywords in IMPACT_SCOPE_KEYWORDS.items():
            for kw in keywords:
                if kw in text:
                    scope_bonus = max(scope_bonus, bonus)
                    scope_matched.append(kw)

        final_score = min(amount_score + scope_bonus, 100)

        return {
            "score": final_score,
            "amount": amount_text if amount_text else None,
            "amount_score": amount_score,
            "scope_bonus": scope_bonus,
            "matched": scope_matched[:3],
        }

    # =========================================================================
    # 5. 지리적 중요도 (Geographic Significance)
    # =========================================================================
    def _score_geographic(self, text: str) -> dict:
        """지리적 중요도 점수: 베이징/상하이(100) > 선전/광저우(85) > 특구(80) > 성급(60)"""
        best_score = 0
        matched_region = ""
        matched_keywords = []

        for score_val in sorted(GEOGRAPHIC_SCORES.keys(), reverse=True):
            geo_info = GEOGRAPHIC_SCORES[score_val]
            for kw in geo_info["keywords"]:
                if kw in text:
                    if score_val > best_score:
                        best_score = score_val
                        matched_region = geo_info["name"]
                    matched_keywords.append(kw)

        return {
            "score": best_score,
            "region": matched_region,
            "matched": list(set(matched_keywords))[:5],
        }

    # =========================================================================
    # 6. 시간적 긴급성 (Time Sensitivity)
    # =========================================================================
    def _score_time_sensitivity(self, text: str) -> dict:
        """시간적 긴급성 점수: 돌발(100) > 당일(90) > 단기(70) > 중장기(50)"""
        best_score = 0
        matched_level = ""
        matched_keywords = []

        for score_val in sorted(TIME_SENSITIVITY.keys(), reverse=True):
            ts_info = TIME_SENSITIVITY[score_val]
            for kw in ts_info["keywords"]:
                if kw in text:
                    if score_val > best_score:
                        best_score = score_val
                        matched_level = ts_info["name"]
                    matched_keywords.append(kw)

        return {
            "score": best_score,
            "level": matched_level,
            "matched": list(set(matched_keywords))[:3],
        }

    # =========================================================================
    # 7. 국제적 영향도 (International Impact)
    # =========================================================================
    def _score_international_impact(self, text: str) -> dict:
        """국제적 영향도 점수: 미중(100) > 공급망(90) > 일대일로(80) > FDI(75) > 기타(60)"""
        best_score = 0
        matched_type = ""
        matched_keywords = []

        for score_val in sorted(INTERNATIONAL_IMPACT.keys(), reverse=True):
            impact_info = INTERNATIONAL_IMPACT[score_val]
            for kw in impact_info["keywords"]:
                if kw in text:
                    if score_val > best_score:
                        best_score = score_val
                        matched_type = impact_info["name"]
                    matched_keywords.append(kw)

        return {
            "score": best_score,
            "type": matched_type,
            "matched": list(set(matched_keywords))[:3],
        }

    # =========================================================================
    # 8. 사회적 파급효과 (Social Impact)
    # =========================================================================
    def _score_social_impact(self, text: str) -> dict:
        """사회적 파급효과 점수: 고용(100) > 민생(90) > 환경(80) > 공공안전(60)"""
        best_score = 0
        matched_type = ""
        matched_keywords = []

        for score_val in sorted(SOCIAL_IMPACT.keys(), reverse=True):
            si_info = SOCIAL_IMPACT[score_val]
            for kw in si_info["keywords"]:
                if kw in text:
                    if score_val > best_score:
                        best_score = score_val
                        matched_type = si_info["name"]
                    matched_keywords.append(kw)

        return {
            "score": best_score,
            "type": matched_type,
            "matched": list(set(matched_keywords))[:3],
        }

    # =========================================================================
    # 부스터 적용
    # =========================================================================
    def _apply_boosters(self, text: str, breakdown: dict) -> list:
        """부스터 조건 확인 및 적용.

        - 최고 지도부 언급: x1.5
        - 국무원 발표 주체: x1.3
        - 중앙기업 + 전략산업 복합: x1.2
        """
        boosters = []

        # 키워드 기반 부스터
        for booster_key, config in BOOSTER_KEYWORDS.items():
            for kw in config["keywords"]:
                if kw in text:
                    boosters.append({
                        "name": booster_key,
                        "multiplier": config["multiplier"],
                        "matched": kw,
                    })
                    break  # 같은 부스터는 한 번만 적용

        # 중앙기업 + 전략산업 복합 부스터
        corp_score = breakdown.get("corporate_hierarchy", {}).get("score", 0)
        corp_type = breakdown.get("corporate_hierarchy", {}).get("type", "")
        industry_score = breakdown.get("strategic_industry", {}).get("score", 0)

        if "중앙기업" in corp_type and industry_score >= 80:
            boosters.append({
                "name": "soe_strategic",
                "multiplier": SOE_STRATEGIC_BOOSTER,
                "matched": f"{corp_type} + 전략산업",
            })

        return boosters

    # =========================================================================
    # 점수 설명 생성
    # =========================================================================
    def _build_explanation(self, breakdown: dict, boosters: list, total: float) -> str:
        """점수 산출 근거를 한국어 문자열로 생성.

        예: "국무院 발표(23.75점) + 대형 민영기업(12점) + AI산업(20점) = 총 78.5점"
        """
        parts = []
        weight_map = {
            "policy_hierarchy": ("정책계층", 0.25),
            "corporate_hierarchy": ("기업계층", 0.15),
            "strategic_industry": ("산업전략", 0.20),
            "economic_scale": ("경제규모", 0.15),
            "geographic_significance": ("지리중요", 0.10),
            "time_sensitivity": ("시간긴급", 0.05),
            "international_impact": ("국제영향", 0.05),
            "social_impact": ("사회파급", 0.05),
        }

        for key, (label, weight) in weight_map.items():
            detail = breakdown[key]
            raw = detail["score"]
            weighted = raw * weight

            if raw > 0:
                # 상세 정보 추출
                extra = ""
                if "level" in detail and detail["level"]:
                    extra = f"/{detail['level']}"
                elif "type" in detail and detail["type"]:
                    extra = f"/{detail['type']}"
                elif "industry" in detail and detail["industry"]:
                    extra = f"/{detail['industry']}"
                elif "region" in detail and detail["region"]:
                    extra = f"/{detail['region']}"

                parts.append(f"{label}{extra}({weighted:.1f}점)")

        base_text = " + ".join(parts) if parts else "매칭 항목 없음"

        # 부스터 설명
        booster_text = ""
        if boosters:
            booster_names = [f"{b['name']}(x{b['multiplier']})" for b in boosters]
            booster_text = f" [부스터: {', '.join(booster_names)}]"

        return f"{base_text}{booster_text} = 총 {total:.1f}점"


def score_news(title: str, content: str, source: str = "") -> dict:
    """편의 함수: ContentScorer 인스턴스 없이 점수 계산."""
    scorer = ContentScorer()
    return scorer.score(title, content, source)

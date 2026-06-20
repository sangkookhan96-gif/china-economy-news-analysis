"""Canonical dual-script rendering for Korean news summaries.

Given a Korean text (summary / title / market impact) and the original Chinese
source text, this module UNIFIES every surface form of a known proper noun into
a single canonical rendering:

    시진핑 국가주석(习近平)
    닝더스다이(宁德时代, CATL)
    국무원(国务院)

Unification covers ALL the ways the same entity can leak through Papago:
  - the canonical Korean transliteration (닝더스다이)
  - alternate Korean transliterations Papago emits (니오 ↔ 웨이라이)
  - the bare Chinese form left untranslated (宁德时代)
  - the bare English/brand form (CATL, NIO)
  - any pre-existing annotation in either order (CATL(닝더스다이), 비야디(BYD))

Output is house-style **Korean-first**: first occurrence is annotated
`한국어(汉字, English)`, later occurrences stay the plain canonical Korean.
Entities are only considered when their Chinese form actually appears in the
original source, so affiliations are never hallucinated.
"""

from __future__ import annotations

import re

from src.utils.proper_nouns import all_entries


_KOREAN_CHAR = re.compile(r"[가-힣]")
_LATIN_CHAR = re.compile(r"[A-Za-z]")
# 임의 엔티티의 기존 병기 "단어(…汉字…)" 보호용 (알 수 없는 고유명사 대비)
_EXISTING_ANNOT = re.compile(r"[가-힣A-Za-z0-9]+\s*[（(][^)）]*[一-鿿][^)）]*[)）]")

# 병기 금지 목록 — 한국 독자 대부분이 아는 인물·중앙기관·주요 지명·초유명 기업.
# 이들은 병기는 생략하되 누출된 변이형(한자/영문)은 정본 한국어로 정규화한다.
WELL_KNOWN_ZH = {
    # 중국 정치인·관료 — 사용자 규칙: 정치인은 중국어(한자) 병기 생략(음차만).
    "习近平", "李强", "李克强", "毛泽东", "邓小平", "胡锦涛", "江泽民", "温家宝",
    "赵乐际", "王沪宁", "蔡奇", "丁薛祥", "李希", "韩正", "王毅", "何立峰",
    "刘鹤", "易纲", "潘功胜", "蓝佛安", "郑栅洁", "王文涛", "金壮龙",
    # 외국 인물 — 한자는 음역일 뿐이라 병기 생략(음차만): 트럼프/머스크 등.
    "特朗普", "拜登", "马斯克", "黄仁勋", "库克", "鲍威尔", "耶伦", "普京",
    "岸田文雄", "石破茂",
    # 중앙기관
    "国务院", "中国人民银行", "人民银行", "央行", "外交部", "商务部", "财政部",
    "全国人大", "全国人民代表大会", "中国共产党", "共产党", "中共",
    "国家统计局", "国家发展改革委员会", "发改委", "中国人民解放军", "解放军",
    "中国证监会", "证监会", "中国证券监督管理委员会", "北京大学",
    # 주요 지명
    "中国", "北京", "上海", "香港", "广东", "广东省", "广州", "深圳",
    "天津", "重庆", "台湾", "新疆", "西藏", "澳门", "江苏", "浙江", "山东",
    # 기업은 제외 — 3회 이상 출현 기업은 표기 통일 정책에 따라 첫 등장 병기한다.
}


# 영어 약칭(상용 영문명) 판별 — 전부 대문자 영숫자 2~6자. 예: CATL, BYD, SMIC,
# ICBC, NIO, TSMC. Xiaomi/Alibaba/Tencent/Geely 등 일반 브랜드명은 제외(한국어 음차 우선).
_ACRONYM = re.compile(r"^[A-Z][A-Z0-9&]{1,5}$")


def _is_en_first(info: dict, kind: str = None) -> bool:
    # 제품명은 영어약칭-우선 규칙을 쓰지 않는다(음차(영문) 형식).
    if kind == "product":
        return False
    # 명시적 en_first 플래그가 있으면 우선(예: NVIDIA→엔비디아는 음차 우선이라 False).
    if "en_first" in info:
        return bool(info["en_first"])
    en = info.get("en")
    return bool(en) and bool(_ACRONYM.match(en))


def _canonical_bare(info: dict, kind: str = None) -> str:
    """첫 등장 외(또는 정규화 전용)에서 쓰는 정본 표기.
    영어약칭 상용 기업은 영어약칭, 그 외는 한국어 음차."""
    return info["en"] if _is_en_first(info, kind) else info["ko"]


def _render(info: dict, zh: str, kind: str) -> str:
    """첫 등장 병기 형식.
    - 영어약칭 상용 기업: 영어약칭(한국어음차, 汉字)  예) CATL(닝더스다이, 宁德时代)
    - 제품명: 한국어음차(영문 우선, 없으면 汉字)      예) 훙멍(HarmonyOS), 치린(Kirin)
    - 그 외 기업/엔티티/인물: 한국어음차(汉字)        예) 윈난바이야오(云南白药), 산시(山西)
    """
    ko = info["ko"]

    def _paren(head: str, parts: list[str]) -> str:
        seen: list[str] = []
        for p in parts:
            if p and p != head and p not in seen:
                seen.append(p)
        return f"{head}({', '.join(seen)})" if seen else head

    if kind == "product":
        return _paren(ko, [info.get("en") or zh])    # 음차(영문 우선, 없으면 汉字)
    if _is_en_first(info, kind):
        return _paren(info["en"], [ko, zh])          # 영어약칭(한국어음차, 汉字)
    # 인물 포함 그 외: 한국어음차(汉字). 정치인·외국인은 WELL_KNOWN으로 병기 자체를
    # 생략(아래 do_annot=False)하므로 여기 도달하는 인물은 중국 기업인·학자뿐이다.
    return _paren(ko, [zh])                            # 한국어음차(汉字)


def _surface_variants(zh: str, info: dict) -> list[str]:
    """정본 ko를 제외한, 한국어 텍스트에 나타날 수 있는 변이형(한자·영문·이음차)."""
    ko = info["ko"]
    out: list[str] = []
    for f in [zh, info.get("en"), *info.get("aliases", [])]:
        if f and f != ko and f not in out:
            out.append(f)
    return out


def _form_regex(form: str) -> re.Pattern:
    """표면형 종류(한국어/영문/한자)에 맞는 경계 패턴. 괄호 안·단어 중간 매칭 차단."""
    if _KOREAN_CHAR.search(form):
        # 우측은 조사(…와/…는)가 바로 붙으므로 한글을 막지 않음; 좌측만 단어중간 차단.
        return re.compile(r"(?<![가-힣(（])" + re.escape(form) + r"(?![)）])(?!\()")
    if _LATIN_CHAR.search(form):
        return re.compile(r"(?<![A-Za-z0-9(（])" + re.escape(form) + r"(?![A-Za-z0-9)])(?!\()")
    # 한자: 인접 한자가 있으면(더 긴 고유명사의 일부) 매칭하지 않음
    return re.compile(r"(?<![一-鿿(（])" + re.escape(form) + r"(?![一-鿿)）])")


def _nonoverlap_spans(result: str, forms: list[str]) -> list[list[int]]:
    """forms 중 어느 것이든 매칭된 위치를, 겹치지 않게(긴 것 우선) 수집."""
    spans: list[tuple[int, int]] = []
    for form in sorted(set(forms), key=len, reverse=True):
        for m in _form_regex(form).finditer(result):
            spans.append((m.start(), m.end()))
    spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
    chosen: list[list[int]] = []
    last = -1
    for s, e in spans:
        if s >= last:
            chosen.append([s, e])
            last = e
    return chosen


def format_proper_nouns(text: str | None, source_zh: str | None,
                        max_annotations: int | None = None) -> str | None:
    """모든 변이형을 정본 표기로 통일하고 첫 등장 1회만 병기.

    Args:
        text: 통일 대상 한국어 텍스트(요약/제목/시장영향).
        source_zh: 원문 중국어(제목+본문). 실제 등장 엔티티만 처리(환각 방지).
        max_annotations: 병기(괄호) 추가 엔티티 수 상한(None=무제한). 상한과 무관
            하게 변이형→정본 한국어 '정규화'는 항상 수행한다(공개 피드 2~3개 병기).

    Returns:
        통일된 텍스트. 해당 없으면 입력 그대로. None은 그대로 통과.
    """
    if not text or not source_zh:
        return text

    tokens: list[str] = []

    def _mask(s: str) -> str:
        tokens.append(s)
        return f"\x01PN{len(tokens) - 1}\x01"

    result = text

    # 대상 후보: source_zh에 실제 등장하고, 텍스트에 정본/변이형이 보이는 엔티티.
    cands: list[tuple[str, dict, str, list[str]]] = []
    for zh, info, kind in all_entries():
        if zh not in source_zh:
            continue
        ko = info["ko"]
        variants = _surface_variants(zh, info)
        if ko not in result and not any(v in result for v in variants):
            continue
        cands.append((zh, info, kind, variants))
    # 긴 정본명 우선("핑안보험"→"핑안"), 동률은 더 구체적인 한자형 우선.
    cands.sort(key=lambda c: (len(c[1]["ko"]), len(c[0])), reverse=True)

    # ── 0) 기존 병기 "표면형1(표면형2[, …])"을 정본 병기로 접기(순서 무관) ──
    #    "CATL(닝더스다이)", "비야디(BYD)", "닝더스다이(宁德时代, CATL)" → 정본 1형식.
    collapsed: set[str] = set()
    for zh, info, kind, variants in cands:
        ko = info["ko"]
        forms = sorted(set(variants + [ko]), key=len, reverse=True)
        alt = "|".join(re.escape(f) for f in forms)
        formalt = r"(?:" + alt + r")"
        # 병기 괄호 그룹. 괄호 안은 표면형·쉼표·공백, 그리고 '중첩 괄호(표면형)'까지 허용 →
        # 과거 백필이 만든 중첩 병기 "텐센트(텐센트(腾讯))", "NIO(니오(NIO))"도 통째로 접음.
        nested = r"[（(]\s*" + formalt + r"(?:\s*[,，]\s*" + formalt + r")*\s*[)）]"
        inner = r"\s*[（(](?:\s|[,，]|" + formalt + r"|" + nested + r")+[)）]"
        # 인물은 옛 "음차 직책(汉字)" 형식의 직책 infix(정확히 그 직책)도 허용.
        if kind == "person" and info.get("title_ko"):
            inner = r"(?:\s+" + re.escape(info["title_ko"]) + r")?" + inner
        # 그룹을 1회 이상(+) 허용 → 중복 병기("…(汉字) 직책(汉字) …")까지 통째로 접음.
        pat = re.compile(r"(?:" + alt + r")(?:" + inner + r")+")
        # max_annotations==0 = 정규화 전용(병기 없이 정본 표기로 통일) — 제목/헤드라인용
        bare = _canonical_bare(info, kind)
        canon = bare if (max_annotations == 0 or zh in WELL_KNOWN_ZH) else _render(info, zh, kind)
        new, n = pat.subn(lambda _m, c=canon: _mask(c), result)
        if n:
            result = new
            collapsed.add(zh)

    # 알 수 없는 엔티티의 기존 병기는 통째로 보호(재매칭·이중병기 방지)
    result = _EXISTING_ANNOT.sub(lambda m: _mask(m.group(0)), result)

    # ── 1) 남은 변이형을 정본으로 정규화 + 첫 등장 병기 ──
    annot_count = 0
    for zh, info, kind, variants in cands:
        ko = info["ko"]
        bare = _canonical_bare(info, kind)
        spans = _nonoverlap_spans(result, variants + [ko])
        if not spans:
            continue
        well_known = zh in WELL_KNOWN_ZH
        already = zh in collapsed or f"{bare}(" in text or f"{bare}（" in text
        do_annot = (not well_known) and (not already) and \
                   (max_annotations is None or annot_count < max_annotations)
        first_start = min(s[0] for s in spans)
        for s, e in sorted(spans, key=lambda x: -x[0]):
            rep = _render(info, zh, kind) if (do_annot and s == first_start) else bare
            result = result[:s] + _mask(rep) + result[e:]
        if do_annot:
            annot_count += 1

    for i, tok in enumerate(tokens):
        result = result.replace(f"\x01PN{i}\x01", tok)
    return result


def format_fields(
    fields: dict[str, str | None],
    source_zh: str | None,
) -> dict[str, str | None]:
    """Apply formatter to multiple fields sharing one Chinese source."""
    return {k: format_proper_nouns(v, source_zh) for k, v in fields.items()}

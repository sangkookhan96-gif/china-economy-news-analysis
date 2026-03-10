"""GICS-based industry taxonomy for news classification.

Selected 35 GICS codes + 3 extensions (EXT_MACRO, EXT_POLICY, EXT_GEOPOLITICS).
Each entry includes level, English name, Korean name, and baseline importance score.
"""

# GICS_SELECTED: {code: (level, name_en, name_ko, baseline_score)}
GICS_SELECTED = {
    "20101010": ("L4", "Aerospace & Defense",                         "항공우주·방산",         0.89),
    "45301020": ("L4", "Semiconductors",                              "반도체",                0.85),
    "45102010": ("L4", "Application Software",                        "AI·응용소프트웨어",      0.82),
    "45301010": ("L4", "Semiconductor Materials & Equipment",         "반도체 장비·소재",       0.82),
    "25102010": ("L4", "Automobile Manufacturers",                    "자동차·EV",             0.81),
    "25101010": ("L4", "Automotive Parts & Equipment",                "자동차부품(EV배터리)",   0.80),
    "55105020": ("L4", "Renewable Electricity",                       "재생에너지",            0.80),
    "55101010": ("L4", "Electric Utilities",                          "전력유틸리티",          0.79),
    "55105010": ("L4", "Independent Power Producers",                 "발전·에너지저장",       0.79),
    "50203010": ("L4", "Interactive Media & Services",                "인터넷플랫폼",          0.79),
    "45201020": ("L4", "Communications Equipment",                    "통신장비",              0.77),
    "20106020": ("L4", "Industrial Machinery, Supplies & Components", "산업기계·로봇",         0.73),
    "15104020": ("L4", "Diversified Metals & Mining",                 "광업·희토류",           0.72),
    "45101030": ("L4", "Internet Services & Infrastructure",          "인터넷인프라",          0.72),
    "15101050": ("L4", "Specialty Chemicals",                         "특수화학·신소재",       0.71),
    "50102010": ("L4", "Wireless Telecommunication Services",         "무선통신",              0.70),
    "50101020": ("L4", "Integrated Telecommunication Services",       "통신서비스",            0.70),
    "10102010": ("L4", "Integrated Oil & Gas",                        "석유·가스",             0.68),
    "40101010": ("L4", "Diversified Banks",                           "종합은행",              0.68),
    "40201060": ("L4", "Transaction & Payment Processing Services",   "핀테크·결제",           0.67),
    "35201010": ("L4", "Biotechnology",                               "바이오",                0.63),
    "30202030": ("L4", "Packaged Foods & Meats",                      "식품·농업",             0.63),
    "40203020": ("L4", "Investment Banking & Brokerage",              "증권·IPO",              0.62),
    "35202010": ("L4", "Pharmaceuticals",                             "제약",                  0.62),
    "45102020": ("L4", "Systems Software",                            "시스템소프트웨어",       0.61),
    "40301030": ("L4", "Multi-line Insurance",                        "보험",                  0.61),
    "60102030": ("L4", "Real Estate Development",                     "부동산개발",            0.60),
    "35101010": ("L4", "Health Care Equipment",                       "의료기기",              0.60),
    "40203010": ("L4", "Asset Management & Custody Banks",            "자산운용",              0.60),
    "45101010": ("L4", "IT Consulting & Other Services",              "IT서비스",              0.60),
    "25302010": ("L4", "Education Services",                          "교육",                  0.59),
    # L2 fallbacks
    "4530":     ("L2", "Semiconductors & Semiconductor Equipment",    "반도체산업군",          0.75),
    "4510":     ("L2", "Software & Services",                         "소프트웨어·서비스군",   0.65),
    # L1 fallbacks
    "45":       ("L1", "Information Technology",                      "정보기술",              0.63),
    "40":       ("L1", "Financials",                                  "금융",                  0.62),
}

# GICS_EXTENSIONS: {code: (name_ko, name_en, baseline_score)}
GICS_EXTENSIONS = {
    "EXT_POLICY":      ("정부정책·규제",  "Government Policy & Regulation",  0.78),
    "EXT_GEOPOLITICS": ("지정학·무역",    "Geopolitics & Trade",             0.68),
    "EXT_MACRO":       ("거시경제지표",   "Macroeconomic Indicators",        0.67),
    "other":           ("기타",           "Other",                           0.50),
}

ALL_VALID_CODES = set(GICS_SELECTED.keys()) | set(GICS_EXTENSIONS.keys())

CATEGORY_BASELINE_SCORES: dict[str, float] = {
    code: data[3] for code, data in GICS_SELECTED.items()
}
CATEGORY_BASELINE_SCORES.update({
    code: data[2] for code, data in GICS_EXTENSIONS.items()
})


def get_korean_label(code: str) -> str:
    """Return Korean display label for a GICS code or extension code."""
    if code in GICS_SELECTED:
        return GICS_SELECTED[code][2]
    if code in GICS_EXTENSIONS:
        return GICS_EXTENSIONS[code][0]
    return "기타"


def get_baseline_score(code: str) -> float:
    """Return the baseline importance_score floor for a given category code."""
    return CATEGORY_BASELINE_SCORES.get(code, 0.50)

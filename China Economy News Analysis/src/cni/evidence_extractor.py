"""CNI Evidence Extractor — 요약의 근거 문장을 원문에서 추출.

원칙: 요약에 포함된 핵심 정보가 원문 어디에 있는지 표시.
"""

import re
import logging

logger = logging.getLogger("cni_evidence")


def extract_evidence(summary_ko: str, original_content: str, max_sentences: int = 3) -> str:
    """요약의 근거가 되는 원문 문장을 추출.

    Args:
        summary_ko: 한국어 요약
        original_content: 중국어 원문
        max_sentences: 최대 근거 문장 수

    Returns:
        근거 문장 (줄바꿈 구분), 없으면 빈 문자열
    """
    if not summary_ko or not original_content:
        return ""

    # 요약에서 핵심 숫자 추출
    summary_numbers = set(re.findall(r'\d+\.?\d*', summary_ko))

    # 원문을 문장 단위로 분리
    sentences = re.split(r'[。！？\n]', original_content)
    sentences = [s.strip() for s in sentences if s.strip() and len(s.strip()) > 10]

    # 숫자가 포함된 원문 문장 = 근거
    evidence = []
    for sent in sentences:
        sent_nums = set(re.findall(r'\d+\.?\d*', sent))
        # 요약 숫자와 겹치는 문장
        overlap = summary_numbers & sent_nums
        if overlap and len(overlap) >= 1:
            evidence.append(sent[:100])
            if len(evidence) >= max_sentences:
                break

    # 숫자 근거가 부족하면 기관명 기반
    if len(evidence) < max_sentences:
        # 요약에서 한자 기관명 추출
        ko_institutions = re.findall(r'[\uac00-\ud7af]{2,6}(?:은행|위원회|총국|부|원|회)', summary_ko)
        for sent in sentences:
            if any(inst in sent for inst in ko_institutions if len(inst) > 2):
                if sent[:100] not in evidence:
                    evidence.append(sent[:100])
                    if len(evidence) >= max_sentences:
                        break

    if not evidence:
        # 최소 1개: 첫 문장
        if sentences:
            evidence = [sentences[0][:100]]

    return "\n".join(f"[근거{i+1}] {e}" for i, e in enumerate(evidence))

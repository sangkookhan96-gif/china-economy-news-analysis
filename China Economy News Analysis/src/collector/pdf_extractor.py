"""PDF 텍스트 추출 모듈.

중앙정부 사이트의 정책 문건은 PDF 첨부파일로 게시되는 경우가 많다.
이 모듈은 PDF URL에서 텍스트를 추출하고, HTML 페이지에서 PDF 링크를 탐지한다.
"""

import io
import logging
import re
from typing import Optional
from urllib.parse import urljoin

import requests

logger = logging.getLogger(__name__)

MAX_PDF_PAGES = 20
MAX_PDF_CHARS = 15000
PDF_DOWNLOAD_TIMEOUT = 30


def extract_pdf_text(url: str, headers: dict = None) -> Optional[str]:
    """URL에서 PDF를 다운로드하여 텍스트를 추출한다.

    Args:
        url: PDF 파일 URL
        headers: HTTP 요청 헤더 (선택)

    Returns:
        추출된 텍스트 문자열, 실패 시 None
    """
    try:
        import pdfplumber
    except ImportError:
        logger.warning("pdfplumber not installed. Run: pip install pdfplumber")
        return None

    try:
        response = requests.get(url, headers=headers, timeout=PDF_DOWNLOAD_TIMEOUT)
        response.raise_for_status()

        if "application/pdf" not in response.headers.get("Content-Type", "") and not url.endswith(".pdf"):
            logger.debug(f"Not a PDF: {url}")
            return None

        pdf_bytes = io.BytesIO(response.content)
        text_parts = []

        with pdfplumber.open(pdf_bytes) as pdf:
            for i, page in enumerate(pdf.pages):
                if i >= MAX_PDF_PAGES:
                    text_parts.append(f"\n[...페이지 {MAX_PDF_PAGES} 이후 생략...]")
                    break

                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)

        full_text = "\n".join(text_parts)

        if len(full_text) > MAX_PDF_CHARS:
            full_text = full_text[:MAX_PDF_CHARS] + "\n[...텍스트 길이 제한으로 생략...]"

        if len(full_text.strip()) < 50:
            return None

        return full_text

    except requests.RequestException as e:
        logger.error(f"PDF download failed: {url} - {e}")
        return None
    except Exception as e:
        logger.error(f"PDF extraction failed: {url} - {e}")
        return None


def find_pdf_links(html: str, base_url: str) -> list[str]:
    """HTML 페이지에서 PDF 첨부파일 링크를 탐지한다.

    Args:
        html: HTML 문자열
        base_url: 상대 URL 해석을 위한 기본 URL

    Returns:
        PDF URL 리스트
    """
    pdf_urls = []

    # href에서 .pdf 링크 탐색
    pattern = re.compile(r'href=["\']([^"\']*\.pdf)["\']', re.IGNORECASE)
    for match in pattern.finditer(html):
        pdf_path = match.group(1)
        if not pdf_path.startswith("http"):
            pdf_path = urljoin(base_url, pdf_path)
        pdf_urls.append(pdf_path)

    return list(set(pdf_urls))

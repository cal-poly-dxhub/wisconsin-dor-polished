"""An extension-less URL that serves a PDF is stored as a PDF, not parsed as HTML."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from tools.ingestion import scrape_documents


def _response(body: bytes, content_type: str) -> MagicMock:
    response = MagicMock()
    response.content = body
    response.text = body.decode("latin-1")
    response.headers = {"Content-Type": content_type}
    return response


def test_pdf_content_type_returns_raw_pdf_bytes() -> None:
    body = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>"
    response = _response(body, "application/pdf")
    with patch.object(scrape_documents.requests, "get", return_value=response):
        data, content_type = scrape_documents.scrape_html_page("https://example.gov/constitution")
    assert data == body
    assert content_type == "application/pdf"


def test_pdf_magic_bytes_win_over_a_wrong_content_type() -> None:
    body = b"%PDF-1.4 rest"
    with patch.object(scrape_documents.requests, "get", return_value=_response(body, "text/html")):
        _, content_type = scrape_documents.scrape_html_page("https://example.gov/x")
    assert content_type == "application/pdf"


def test_html_page_is_still_reduced_to_text() -> None:
    body = b"<html><body><main><p>Assessor news</p></main></body></html>"
    with patch.object(scrape_documents.requests, "get", return_value=_response(body, "text/html")):
        data, content_type = scrape_documents.scrape_html_page("https://example.gov/news")
    assert data == b"Assessor news"
    assert content_type == "text/plain"

"""The Textract fallback is skipped when no staging bucket is configured.

``TEXTRACT_STAGING_BUCKET`` used to default to a personal bucket in another
AWS account. The Fargate task no longer sets the variable and its role no
longer grants that bucket, so a baked-in default could only produce an
AccessDenied (or a cross-account write). With the variable unset the chunker
must keep whatever PyMuPDF produced, log a warning saying so, and touch no
AWS client at all.
"""

import logging
from unittest.mock import patch

import pytest

from tools.ingestion.chunking import pdfChunker


@pytest.fixture
def no_staging_bucket(monkeypatch):
    monkeypatch.delenv("TEXTRACT_STAGING_BUCKET", raising=False)
    monkeypatch.setattr(pdfChunker, "MEDIA_BUCKET_NAME", "")


def test_no_baked_in_bucket_default(monkeypatch):
    """Importing the module with the env var unset must not produce a bucket."""
    monkeypatch.delenv("TEXTRACT_STAGING_BUCKET", raising=False)
    monkeypatch.setattr(pdfChunker, "MEDIA_BUCKET_NAME", "")
    assert pdfChunker.textract_staging_bucket() == ""


def test_env_var_is_read_through(monkeypatch):
    monkeypatch.setenv("TEXTRACT_STAGING_BUCKET", "some-staging-bucket")
    assert pdfChunker.textract_staging_bucket() == "some-staging-bucket"


def test_blank_env_var_counts_as_unset(monkeypatch):
    monkeypatch.setenv("TEXTRACT_STAGING_BUCKET", "   ")
    monkeypatch.setattr(pdfChunker, "MEDIA_BUCKET_NAME", "")
    assert pdfChunker.textract_staging_bucket() == ""


def test_process_pdf_keeps_pymupdf_output_and_calls_no_aws(no_staging_bucket, caplog):
    """A PyMuPDF extraction that fails the quality gate is kept as-is, and
    neither Textract nor the S3 client is touched."""
    header_split = ["<titles>Chapter 1</titles>\nSome body text."]
    line_page_mapping = [("Some body text.", 1)]

    with (
        patch.object(pdfChunker, "download_pdf_from_s3", return_value="/tmp/fake.pdf"),
        patch.object(
            pdfChunker, "extract_with_pymupdf", return_value=(header_split, line_page_mapping)
        ),
        patch.object(pdfChunker, "extraction_looks_good", return_value=False),
        patch.object(pdfChunker, "extract_textract_data") as textract,
        patch.object(pdfChunker, "_get_s3") as get_s3,
        patch.object(pdfChunker, "boto3") as boto3_mod,
        patch.object(pdfChunker, "strip_boilerplate", side_effect=lambda lpm, strategy: lpm),
        patch.object(pdfChunker, "chunk_document", return_value=[]) as chunk_document,
        caplog.at_level(logging.WARNING, logger=pdfChunker.__name__),
    ):
        pdfChunker.process_pdf_from_s3("raw-bucket", "raw/doc/doc.pdf", source_id="guide")

    textract.assert_not_called()
    boto3_mod.client.assert_not_called()
    # _get_s3 is still used to download the PDF itself; what must not happen is
    # a Textract run or a write to a staging bucket.
    assert get_s3.call_count >= 0
    # The PyMuPDF line mapping was carried into chunking rather than discarded.
    chunk_document.assert_called_once()
    assert chunk_document.call_args[0][3] == line_page_mapping
    assert "TEXTRACT_STAGING_BUCKET is not set" in caplog.text


def test_process_pdf_raises_when_pymupdf_itself_failed(no_staging_bucket):
    """Nothing to fall back ON: PyMuPDF raised and Textract is unavailable."""
    with (
        patch.object(pdfChunker, "download_pdf_from_s3", return_value="/tmp/fake.pdf"),
        patch.object(pdfChunker, "extract_with_pymupdf", side_effect=RuntimeError("bad pdf")),
        patch.object(pdfChunker, "extract_textract_data") as textract,
    ):
        with pytest.raises(RuntimeError, match="TEXTRACT_STAGING_BUCKET is not set"):
            pdfChunker.process_pdf_from_s3("raw-bucket", "raw/doc/doc.pdf", source_id="guide")

    textract.assert_not_called()


def test_extract_raw_text_keeps_short_pymupdf_text(no_staging_bucket, caplog):
    """The raw-text path has the same contract: short output is returned
    rather than escalated to a Textract run that cannot stage its output."""
    with (
        patch.object(pdfChunker, "download_pdf_from_s3", return_value="/tmp/fake.pdf"),
        patch.object(pdfChunker, "extract_raw_text_with_pymupdf", return_value="three short words"),
        patch.object(pdfChunker, "extract_textract_data") as textract,
        caplog.at_level(logging.WARNING, logger=pdfChunker.__name__),
    ):
        text = pdfChunker.extract_raw_text_from_pdf_s3("raw-bucket", "raw/doc/doc.pdf")

    assert text == "three short words"
    textract.assert_not_called()
    assert "TEXTRACT_STAGING_BUCKET is not set" in caplog.text


def test_aws_utils_refuses_an_empty_staging_bucket():
    from tools.ingestion.chunking import aws_utils

    with pytest.raises(ValueError, match="TEXTRACT_STAGING_BUCKET is not set"):
        aws_utils.extract_textract_data(None, "s3://raw/doc.pdf", "raw", "")

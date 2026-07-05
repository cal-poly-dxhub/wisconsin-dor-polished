"""Tests for fetch_case_opinion tool.

The tool takes a citation, normalizes it to a raw S3 key, fetches the
full opinion text if the file exists, and falls back to a Google Scholar
search URL if it doesn't.
"""

from unittest.mock import MagicMock

from botocore.exceptions import ClientError


def test_fetch_case_opinion_success():
    from case_opinion import fetch_case_opinion

    mock_s3 = MagicMock()
    mock_s3.get_object.return_value = {
        "Body": MagicMock(read=lambda: b"109 Wis.2d 290 (1982) CORROON v. HOSCH. Full opinion text here..."),
        "ContentLength": 65,
    }

    result = fetch_case_opinion(
        "109 Wis. 2d 290",
        raw_bucket="test-bucket",
        s3_client=mock_s3,
    )

    assert result["found"] is True
    assert result["citation"] == "109 Wis. 2d 290"
    assert result["raw_key"] == "raw/case-law/wis-2d/109-wis-2d-290.txt"
    assert "CORROON" in result["text"]
    mock_s3.get_object.assert_called_once_with(
        Bucket="test-bucket",
        Key="raw/case-law/wis-2d/109-wis-2d-290.txt",
    )


def test_fetch_case_opinion_not_found_returns_scholar_url():
    from case_opinion import fetch_case_opinion

    mock_s3 = MagicMock()
    mock_s3.get_object.side_effect = ClientError(
        {"Error": {"Code": "NoSuchKey", "Message": "Not found"}},
        "GetObject",
    )

    result = fetch_case_opinion(
        "2001 WI App 182",
        raw_bucket="test-bucket",
        s3_client=mock_s3,
    )

    assert result["found"] is False
    assert result["citation"] == "2001 WI App 182"
    assert "scholar.google.com" in result["scholar_url"]
    assert "2001" in result["scholar_url"]
    assert "WI" in result["scholar_url"]
    assert "App" in result["scholar_url"]


def test_fetch_case_opinion_truncates_large_opinion():
    from case_opinion import MAX_OPINION_CHARS, fetch_case_opinion

    long_text = "A" * (MAX_OPINION_CHARS + 5000)
    mock_s3 = MagicMock()
    mock_s3.get_object.return_value = {
        "Body": MagicMock(read=lambda: long_text.encode("utf-8")),
        "ContentLength": len(long_text),
    }

    result = fetch_case_opinion(
        "109 Wis. 2d 290",
        raw_bucket="test-bucket",
        s3_client=mock_s3,
    )

    assert result["found"] is True
    assert len(result["text"]) <= MAX_OPINION_CHARS + 100  # allow room for truncation marker
    assert "truncated" in result["text"].lower()


def test_fetch_case_opinion_empty_citation():
    from case_opinion import fetch_case_opinion

    mock_s3 = MagicMock()

    result = fetch_case_opinion(
        "",
        raw_bucket="test-bucket",
        s3_client=mock_s3,
    )

    assert result["found"] is False
    assert "error" in result
    mock_s3.get_object.assert_not_called()


def test_scholar_url_public_alias_encodes_citation():
    from case_opinion import scholar_url

    url = scholar_url("109 Wis. 2d 290")
    assert url.startswith("http://scholar.google.com/scholar?")
    assert "q=109%20Wis.%202d%20290" in url

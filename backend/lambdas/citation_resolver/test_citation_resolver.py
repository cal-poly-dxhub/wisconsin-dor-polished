import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))


@patch.dict(os.environ, {"RAW_BUCKET": "test-bucket"})
def test_redirects_with_page_fragment():
    from citation_resolver.main import handler

    with patch("citation_resolver.main.s3") as mock_s3:
        mock_s3.head_object.return_value = {}
        mock_s3.generate_presigned_url.return_value = (
            "https://test-bucket.s3.amazonaws.com/raw/wpam/wpam.pdf?signature=abc"
        )

        event = {"queryStringParameters": {"s3Key": "raw/wpam/wpam.pdf", "page": "12"}}
        response = handler(event, MagicMock())

    assert response["statusCode"] == 302
    assert response["headers"]["Location"].endswith("#page=12")
    assert response["headers"]["Cache-Control"] == "no-store"
    assert response["headers"]["Referrer-Policy"] == "no-referrer"
    mock_s3.head_object.assert_called_once_with(Bucket="test-bucket", Key="raw/wpam/wpam.pdf")


@patch.dict(os.environ, {"RAW_BUCKET": "test-bucket"})
def test_redirects_without_page_fragment():
    from citation_resolver.main import handler

    with patch("citation_resolver.main.s3") as mock_s3:
        mock_s3.head_object.return_value = {}
        mock_s3.generate_presigned_url.return_value = (
            "https://test-bucket.s3.amazonaws.com/raw/case/x.txt?sig=z"
        )

        event = {"queryStringParameters": {"s3Key": "raw/case/x.txt"}}
        response = handler(event, MagicMock())

    assert response["statusCode"] == 302
    assert "#page" not in response["headers"]["Location"]
    assert response["headers"]["Referrer-Policy"] == "no-referrer"


@patch.dict(os.environ, {"RAW_BUCKET": "test-bucket"})
def test_rejects_s3_key_outside_raw_prefix():
    from citation_resolver.main import handler

    event = {"queryStringParameters": {"s3Key": "work/something.pdf"}}
    response = handler(event, MagicMock())

    assert response["statusCode"] == 400


@patch.dict(os.environ, {"RAW_BUCKET": "test-bucket"})
def test_rejects_missing_s3_key():
    from citation_resolver.main import handler

    response = handler({"queryStringParameters": {}}, MagicMock())
    assert response["statusCode"] == 400


@patch.dict(os.environ, {"RAW_BUCKET": "test-bucket"})
def test_rejects_non_integer_page():
    from citation_resolver.main import handler

    event = {"queryStringParameters": {"s3Key": "raw/x.pdf", "page": "twelve"}}
    response = handler(event, MagicMock())
    assert response["statusCode"] == 400


@patch.dict(os.environ, {"RAW_BUCKET": "test-bucket"})
def test_rejects_zero_page():
    from citation_resolver.main import handler

    event = {"queryStringParameters": {"s3Key": "raw/x.pdf", "page": "0"}}
    response = handler(event, MagicMock())
    assert response["statusCode"] == 400


@patch.dict(os.environ, {"RAW_BUCKET": "test-bucket"})
def test_returns_404_when_object_missing():
    from botocore.exceptions import ClientError
    from citation_resolver.main import handler

    with patch("citation_resolver.main.s3") as mock_s3:
        mock_s3.head_object.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject"
        )

        event = {"queryStringParameters": {"s3Key": "raw/wpam/missing.pdf"}}
        response = handler(event, MagicMock())

    assert response["statusCode"] == 404


@patch.dict(os.environ, {"RAW_BUCKET": "test-bucket"})
def test_returns_404_for_no_such_key():
    from botocore.exceptions import ClientError
    from citation_resolver.main import handler

    with patch("citation_resolver.main.s3") as mock_s3:
        mock_s3.head_object.side_effect = ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": "Not Found"}}, "HeadObject"
        )

        event = {"queryStringParameters": {"s3Key": "raw/wpam/missing.pdf"}}
        response = handler(event, MagicMock())

    assert response["statusCode"] == 404


@patch.dict(os.environ, {"RAW_BUCKET": "different-bucket"})
def test_uses_current_env_var_per_invocation():
    """RAW_BUCKET must be read at handler invocation, not module import,
    so a per-test env override is honored."""
    from citation_resolver.main import handler

    with patch("citation_resolver.main.s3") as mock_s3:
        mock_s3.head_object.return_value = {}
        mock_s3.generate_presigned_url.return_value = (
            "https://different-bucket.s3.amazonaws.com/raw/x.pdf?sig=q"
        )

        event = {"queryStringParameters": {"s3Key": "raw/x.pdf"}}
        response = handler(event, MagicMock())

    assert response["statusCode"] == 302
    mock_s3.head_object.assert_called_once_with(Bucket="different-bucket", Key="raw/x.pdf")


@patch.dict(os.environ, {"RAW_BUCKET": ""})
def test_raises_when_raw_bucket_empty():
    from citation_resolver.main import handler

    try:
        handler({"queryStringParameters": {"s3Key": "raw/x.pdf"}}, MagicMock())
    except RuntimeError as e:
        assert "RAW_BUCKET" in str(e)
    else:
        raise AssertionError("expected RuntimeError on empty RAW_BUCKET")

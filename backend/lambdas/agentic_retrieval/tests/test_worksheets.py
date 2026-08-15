"""Tests for the worksheet sidecar loader."""

import io
import json

import pytest
import worksheets
from botocore.exceptions import ClientError


class _FakeS3:
    def __init__(self, objects: dict[str, bytes]):
        self._objects = objects
        self.calls = 0

    def get_object(self, Bucket: str, Key: str):  # noqa: N803 (boto3 kwarg names)
        self.calls += 1
        if Key not in self._objects:
            raise ClientError({"Error": {"Code": "NoSuchKey", "Message": "missing"}}, "GetObject")
        return {"Body": io.BytesIO(self._objects[Key])}


_SAMPLE = {
    "worksheet_id": "worksheets-decrement",
    "title": "TID Base Redetermination (Decrement) Worksheet",
    "source_url": "https://www.revenue.wi.gov/DORForms/decrement.xlsx",
    "sheets": [
        {
            "sheet": "Calculation",
            "labels": [{"cell": "E11", "label": "TID base value"}],
            "formulas": [
                {"cell": "G14", "formula": "=F14/F11", "description": "cell F14/cell F11"}
            ],
            "instructions": ["Base year: Enter the year the TID was created"],
        },
    ],
}


@pytest.fixture(autouse=True)
def _clear_cache():
    worksheets._cache.clear()
    yield
    worksheets._cache.clear()


def test_list_worksheets_returns_registry():
    result = worksheets.list_worksheets()
    ids = {w["worksheet_id"] for w in result}
    assert "worksheets-decrement" in ids
    assert "worksheets-tidbase" in ids
    assert all("title" in w and "summary" in w for w in result)


def test_get_worksheet_loads_and_caches():
    s3 = _FakeS3({"worksheets/worksheets-decrement.json": json.dumps(_SAMPLE).encode()})
    r1 = worksheets.get_worksheet("worksheets-decrement", raw_bucket="b", s3_client=s3)
    assert r1["title"].startswith("TID Base Redetermination")
    assert r1["sheets"][0]["labels"][0]["label"] == "TID base value"
    # Second call served from cache — no extra S3 hit.
    r2 = worksheets.get_worksheet("worksheets-decrement", raw_bucket="b", s3_client=s3)
    assert r2 == r1
    assert s3.calls == 1


def test_get_worksheet_filters_sheet():
    s3 = _FakeS3({"worksheets/worksheets-decrement.json": json.dumps(_SAMPLE).encode()})
    r = worksheets.get_worksheet(
        "worksheets-decrement", raw_bucket="b", sheet="Calculation", s3_client=s3
    )
    assert len(r["sheets"]) == 1
    assert r["sheets"][0]["sheet"] == "Calculation"


def test_get_worksheet_unknown_sheet_reports_available():
    s3 = _FakeS3({"worksheets/worksheets-decrement.json": json.dumps(_SAMPLE).encode()})
    r = worksheets.get_worksheet("worksheets-decrement", raw_bucket="b", sheet="Nope", s3_client=s3)
    assert "error" in r
    assert r["available_sheets"] == ["Calculation"]


def test_get_worksheet_unknown_id():
    r = worksheets.get_worksheet("worksheets-bogus", raw_bucket="b", s3_client=_FakeS3({}))
    assert "error" in r
    assert "available" in r


def test_get_worksheet_missing_sidecar_is_graceful():
    s3 = _FakeS3({})  # known id, but no sidecar uploaded yet
    r = worksheets.get_worksheet("worksheets-decrement", raw_bucket="b", s3_client=s3)
    assert "error" in r
    assert "not yet published" in r["error"]


def test_get_worksheet_requires_bucket():
    r = worksheets.get_worksheet("worksheets-decrement", raw_bucket="", s3_client=_FakeS3({}))
    assert r["error"] == "Raw bucket not configured"

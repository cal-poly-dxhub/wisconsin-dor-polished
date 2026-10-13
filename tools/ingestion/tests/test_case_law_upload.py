"""ingest_case_law.upload_case never writes a "no text" stub over a held opinion."""

from __future__ import annotations

import json

from tools.ingestion.ingest_case_law import upload_case
from tools.ingestion.tests.fake_s3 import FakeS3


def _entry() -> dict:
    return {
        "slug": "100-wis-2d-256",
        "citation": "100 Wis. 2d 256",
        "case_name": "Walker v. Bignell",
        "scholar_url": "http://scholar.example/q",
        "legis_url": "https://docs.legis.wisconsin.gov/x",
        "source_url": "https://www.courtlistener.com/opinion/1/walker-v-bignell/",
        "sources": [{"file": "70.pdf", "pages": [3]}],
    }


def test_stubs_only_keeps_existing_opinion_text() -> None:
    fake = FakeS3()
    fake.put_bytes("raw", "raw/case-law/wis-2d/100-wis-2d-256.txt", b"the opinion")

    status = upload_case(_entry(), "raw", fake, True, None, 0)

    assert fake.keys("raw") == [
        "raw/case-law/wis-2d/100-wis-2d-256.metadata.json",
        "raw/case-law/wis-2d/100-wis-2d-256.txt",
    ]
    assert status["has_text"] is True
    assert status["s3_key"] == "raw/case-law/wis-2d/100-wis-2d-256.txt"


def test_stubs_only_writes_a_stub_when_no_text_is_held() -> None:
    fake = FakeS3()

    status = upload_case(_entry(), "raw", fake, True, None, 0)

    stub = json.loads(fake.objects[("raw", "raw/case-law/wis-2d/100-wis-2d-256.json")])
    assert stub["citation"] == "100 Wis. 2d 256"
    assert status["has_text"] is False

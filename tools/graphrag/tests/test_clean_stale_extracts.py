"""Tests for stale-extract cleanup.

We keep extracts that have a matching raw doc. We delete extracts whose
doc_id has no raw counterpart (the raw file was deleted or renamed).
"""

from unittest.mock import MagicMock

from tools.graphrag.clean_stale_extracts import (
    artifact_source_key,
    delete_artifacts,
    delete_stale_extracts,
    find_stale_artifacts,
    find_stale_extracts,
    source_key_rank,
)


def test_find_stale_extracts_keeps_matched():
    raw_ids = {"case-law-109-wis", "statutes-wi-statute-ch70"}
    extracted_ids = {"case-law-109-wis", "statutes-wi-statute-ch70"}
    stale = find_stale_extracts(raw_ids, extracted_ids)
    assert stale == set()


def test_find_stale_extracts_flags_unmatched():
    raw_ids = {"case-law-109-wis-2d-290"}  # colleague's new full opinion
    extracted_ids = {"case-law-109-wis-2d-290", "case-law-old-stub"}  # extra stale stub
    stale = find_stale_extracts(raw_ids, extracted_ids)
    assert stale == {"case-law-old-stub"}


def test_find_stale_extracts_ignores_nonexistent_extracts():
    raw_ids = {"case-law-109-wis", "case-law-new-2d-290"}
    extracted_ids = {"case-law-109-wis"}  # new one not yet extracted
    stale = find_stale_extracts(raw_ids, extracted_ids)
    assert stale == set()


def test_find_stale_artifacts_flags_source_key_mismatch():
    raw_source_keys = {
        "case-law-2017-wi-79": "raw/case-law/wi/2017-wi-79.txt",
    }
    artifact_source_keys = {
        "case-law-2017-wi-79": "raw/case-law/wi/2017-wi-79.json",
    }

    stale = find_stale_artifacts(raw_source_keys, artifact_source_keys)

    assert stale == {"case-law-2017-wi-79": "source-key-mismatch"}


def test_find_stale_artifacts_flags_missing_raw_doc():
    stale = find_stale_artifacts(
        raw_source_keys={"case-law-current": "raw/case-law/misc/current.txt"},
        artifact_source_keys={"case-law-old": "raw/case-law/misc/old.json"},
    )

    assert stale == {"case-law-old": "missing-raw-doc"}


def test_find_stale_artifacts_keeps_current_artifact():
    source_key = "raw/case-law/wis-2d/109-wis-2d-290.txt"

    stale = find_stale_artifacts(
        raw_source_keys={"case-law-109-wis-2d-290": source_key},
        artifact_source_keys={"case-law-109-wis-2d-290": source_key},
    )

    assert stale == {}


def test_artifact_source_key_falls_back_to_chunk_metadata():
    doc = {
        "chunks": [
            {
                "metadata": {
                    "source": "raw/case-law/wis-2d/109-wis-2d-290.txt",
                }
            }
        ]
    }

    assert artifact_source_key(doc) == "raw/case-law/wis-2d/109-wis-2d-290.txt"


def test_source_key_rank_prefers_txt_over_json_stub():
    keys = [
        "raw/case-law/wi/2017-wi-79.json",
        "raw/case-law/wi/2017-wi-79.txt",
    ]

    assert sorted(keys, key=source_key_rank)[0].endswith(".txt")


def test_delete_stale_extracts_issues_delete_calls_in_batches():
    mock_s3 = MagicMock()
    stale = {f"doc-{i}" for i in range(2500)}  # force multiple batches

    delete_stale_extracts(mock_s3, bucket="work-bucket", stale_ids=stale)

    # S3 delete_objects caps at 1000 keys per call
    assert mock_s3.delete_objects.call_count == 3
    total_deleted = sum(
        len(call_args.kwargs["Delete"]["Objects"])
        for call_args in mock_s3.delete_objects.call_args_list
    )
    assert total_deleted == 2500


def test_delete_stale_extracts_empty_set_is_noop():
    mock_s3 = MagicMock()
    delete_stale_extracts(mock_s3, bucket="work-bucket", stale_ids=set())
    mock_s3.delete_objects.assert_not_called()


def test_delete_artifacts_uses_requested_prefix():
    mock_s3 = MagicMock()

    delete_artifacts(mock_s3, bucket="work-bucket", prefix="embedded/", stale_ids={"doc-a"})

    mock_s3.delete_objects.assert_called_once_with(
        Bucket="work-bucket",
        Delete={"Objects": [{"Key": "embedded/doc-a.json"}], "Quiet": True},
    )

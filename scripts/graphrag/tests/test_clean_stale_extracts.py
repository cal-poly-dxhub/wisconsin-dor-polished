"""Tests for stale-extract cleanup.

We keep extracts that have a matching raw doc. We delete extracts whose
doc_id has no raw counterpart (the raw file was deleted or renamed).
"""

from unittest.mock import MagicMock

from scripts.graphrag.clean_stale_extracts import find_stale_extracts, delete_stale_extracts


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

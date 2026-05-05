"""Tests for the case-law remediation script.

Confirms it only touches case-law keys and correctly batches deletions across
both the extracted/ and embedded/ prefixes.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from scripts.graphrag.remediate_case_law import delete_keys, list_case_law_keys


def _mock_paginator(pages: list[dict]) -> MagicMock:
    paginator = MagicMock()
    paginator.paginate.return_value = pages
    return paginator


def test_list_case_law_keys_only_returns_case_law_prefix() -> None:
    mock_s3 = MagicMock()
    # S3 already filters by Prefix on the server side — the paginator only yields
    # keys under the requested prefix. Verify we're asking for the right prefix.
    mock_s3.get_paginator.return_value = _mock_paginator([
        {"Contents": [
            {"Key": "extracted/case-law-100-wis-2d-256.json"},
            {"Key": "extracted/case-law-2009-wi-app-159.json"},
        ]},
    ])

    result = list_case_law_keys(mock_s3, "work-bucket", "extracted/")

    mock_s3.get_paginator.assert_called_with("list_objects_v2")
    paginate_call = mock_s3.get_paginator.return_value.paginate.call_args
    assert paginate_call.kwargs["Prefix"] == "extracted/case-law-"
    assert result == [
        "extracted/case-law-100-wis-2d-256.json",
        "extracted/case-law-2009-wi-app-159.json",
    ]


def test_list_case_law_keys_skips_manifest() -> None:
    mock_s3 = MagicMock()
    mock_s3.get_paginator.return_value = _mock_paginator([
        {"Contents": [
            {"Key": "extracted/case-law-100-wis-2d-256.json"},
            {"Key": "extracted/case-law-manifest/manifest.json"},  # defensive: we skip any .../manifest.json
        ]},
    ])

    result = list_case_law_keys(mock_s3, "work-bucket", "extracted/")
    assert result == ["extracted/case-law-100-wis-2d-256.json"]


def test_delete_keys_batches_at_1000() -> None:
    mock_s3 = MagicMock()
    keys = [f"extracted/case-law-doc-{i}.json" for i in range(2500)]

    count = delete_keys(mock_s3, "work-bucket", keys)

    assert count == 2500
    assert mock_s3.delete_objects.call_count == 3
    # First two batches have 1000 keys each; third has 500.
    sizes = [
        len(call.kwargs["Delete"]["Objects"])
        for call in mock_s3.delete_objects.call_args_list
    ]
    assert sizes == [1000, 1000, 500]


def test_delete_keys_quiet_mode_enabled() -> None:
    """Quiet=True keeps the S3 response small; we don't parse it."""
    mock_s3 = MagicMock()
    delete_keys(mock_s3, "work-bucket", ["extracted/case-law-foo.json"])
    delete_args = mock_s3.delete_objects.call_args.kwargs["Delete"]
    assert delete_args["Quiet"] is True

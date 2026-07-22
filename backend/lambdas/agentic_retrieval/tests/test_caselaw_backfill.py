"""Tests for batched chunk-level case-law backfill."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from agent_tools.stages import caselaw_backfill
from agent_tools.stages.base import StageContext


def _candidate(chunk_id: str, case_id: str) -> dict:
    return {
        "chunk_id": chunk_id,
        "case_id": case_id,
        "doc_id": case_id,
        "text": f"Analysis from {case_id}",
        "heading": "analysis_holding",
        "embedding": [1.0, 0.0],
        "authority_level": 3,
    }


def test_backfill_batches_all_stubs_and_reuses_query_embedding() -> None:
    neptune = MagicMock()
    neptune.get_case_chunks_for_statutes_with_embeddings.return_value = [
        _candidate("case-1-a", "case-1"),
        _candidate("case-1-b", "case-1"),
        _candidate("case-2-a", "case-2"),
        _candidate("case-3-a", "case-3"),
    ]
    ctx = StageContext(
        query="assessment",
        refined_query="property assessment",
        neptune=neptune,
        embedding=[1.0, 0.0],
        statute_backfill=[
            {"cited_stubs": ["WIS-STAT-70.32", "WIS-STAT-70.47"]},
            {"cited_stubs": ["WIS-STAT-73.03", "WIS-STAT-70.32"]},
        ],
    )

    with (
        patch.dict(
            "os.environ",
            {
                "CASELAW_BACKFILL_CAP": "3",
                "CASELAW_CHUNK_FETCH_K": "200",
                "CASELAW_CHUNK_HARD_CAP": "300",
                "CASELAW_CHUNK_MAX_PER_CASE": "1",
            },
            clear=False,
        ),
        patch("agent_tools.executor.embed_query") as embed_query,
    ):
        caselaw_backfill.run(ctx)

    neptune.get_case_chunks_for_statutes_with_embeddings.assert_called_once_with(
        ["WIS-STAT-70.32", "WIS-STAT-70.47", "WIS-STAT-73.03"], limit=200
    )
    embed_query.assert_not_called()
    assert [chunk["case_id"] for chunk in ctx.caselaw_backfill] == [
        "case-1",
        "case-2",
        "case-3",
    ]


def test_fetch_k_is_globally_bounded_by_hard_cap() -> None:
    neptune = MagicMock()
    neptune.get_case_chunks_for_statutes_with_embeddings.return_value = []
    ctx = StageContext(
        query="assessment",
        refined_query="assessment",
        neptune=neptune,
        statute_backfill=[{"cited_stubs": ["WIS-STAT-70.32"]}],
    )

    with patch.dict(
        "os.environ",
        {
            "CASELAW_CHUNK_FETCH_K": "500",
            "CASELAW_CHUNK_HARD_CAP": "300",
        },
        clear=False,
    ):
        caselaw_backfill.run(ctx)

    neptune.get_case_chunks_for_statutes_with_embeddings.assert_called_once_with(
        ["WIS-STAT-70.32"], limit=300
    )


def test_diversity_second_pass_fills_from_selected_case() -> None:
    ranked = [
        _candidate("case-1-a", "case-1"),
        _candidate("case-1-b", "case-1"),
        _candidate("case-2-a", "case-2"),
    ]

    selected = caselaw_backfill._diversify_by_case(ranked, top_k=3, initial_cap=1)

    assert [chunk["chunk_id"] for chunk in selected] == [
        "case-1-a",
        "case-2-a",
        "case-1-b",
    ]

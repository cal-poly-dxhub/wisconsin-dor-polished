"""Tests for chunk-level citation wiring during graph load."""

from __future__ import annotations

from unittest.mock import patch

from tools.ingestion import load


def test_phase_5_persists_role_and_creates_chunk_statute_cites() -> None:
    calls: list[tuple[str, dict | None]] = []

    def capture(_client, _graph_id, query, parameters=None):
        calls.append((query, parameters))
        if "DETACH DELETE" in query:
            return {"results": [{"deleted": 0}]}
        return {"results": []}

    documents = [
        {
            "doc_id": "case-law-test",
            "s3_key": "raw/case-law/test.txt",
            "framework_id": "FW-CASE-LAW",
            "chunks": [
                {
                    "text": "The court interprets Wis. Stat. § 70.32.",
                    "metadata": {
                        "heading": "analysis_holding ¶8–¶12",
                        "content_role": "analysis_holding",
                        "statute_refs": ["70.32"],
                        "admin_rule_refs": [],
                    },
                }
            ],
        }
    ]

    with patch.object(load, "execute_query", side_effect=capture):
        load.phase_5_chunk_nodes(object(), "graph", documents)

    node_call = next(call for call in calls if "c.content_role = row.content_role" in call[0])
    assert node_call[1]["rows"][0]["content_role"] == "analysis_holding"

    cite_call = next(
        call
        for call in calls
        if "MERGE (c)-[:CITES]->(s)" in call[0]
    )
    assert cite_call[1]["rows"] == [
        {"chunk_id": "case-law-test_chunk_0000", "stub_id": "WIS-STAT-70.32"}
    ]

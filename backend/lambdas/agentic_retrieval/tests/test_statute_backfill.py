"""Tests for vector_search statute backfill.

After vector_search ranks chunks, execute_tool follows the chunk-level
CITES edges of the top-N most-relevant chunks to the statute stub, resolves
the stub to the real statute-text chunk (DEFINED_BY), and surfaces that text
to the model as `statute_backfill`. This lets the agent ground a WPAM/guide
passage in the underlying statute without a separate list_sections +
get_section round-trip.

v1 defaults (data-derived): source gate = top-3 chunks by relevance,
output cap = 3 statute chunks. Both env-configurable.
"""

from unittest.mock import MagicMock, patch
import pytest


@pytest.fixture(autouse=True)
def _passthrough_auto_refine(monkeypatch):
    """Make _auto_refine a no-op so tests don't hit real Bedrock."""
    monkeypatch.setattr(
        "agent_tools.executor._auto_refine",
        lambda query, history: (query, None),
    )


def _mock_neptune(vector_chunks, backfill_rows):
    m = MagicMock()
    m.vector_search.return_value = vector_chunks
    m.get_neighbors.return_value = []  # keep enrichment inert
    m.get_statute_backfill.return_value = backfill_rows
    m.current_wpam_year = 2026
    return m


def test_backfill_surfaces_cited_statute_text():
    from agent_tools import execute_tool

    # A guide chunk (rank 1) that cites a statute, plus filler.
    vector_chunks = [
        {"chunk_id": "guide-1", "text": "BOR rules", "doc_id": "gov-guide", "score": 0.10},
        {"chunk_id": "guide-2", "text": "more", "doc_id": "gov-guide", "score": 0.20},
        {"chunk_id": "faq-1", "text": "faq", "doc_id": "faq-x", "score": 0.30},
    ]
    # Backfill resolves guide-1's CITES -> WIS-STAT-70.47 -> real statute chunk.
    backfill_rows = [
        {
            "source_chunk_id": "guide-1",
            "stub_id": "WIS-STAT-70.47",
            "chunk_id": "statutes-70_c167",
            "text": "70.47 Board of review text",
            "doc_id": "statutes-70",
            "source_url": "https://x",
            "s3_key": "k",
            "start_page": 39,
            "end_page": 42,
            "heading": "70.47",
            "subheading": None,
            "authority_level": 2,
        }
    ]
    m = _mock_neptune(vector_chunks, backfill_rows)
    with patch("agent_tools.executor.embed_query", return_value=[0.1] * 1024):
        result = execute_tool("vector_search", {"query": "board of review recording"}, m)

    assert "statute_backfill" in result
    bf = result["statute_backfill"]
    assert len(bf) == 1
    assert bf[0]["doc_id"] == "statutes-70"
    assert bf[0]["cited_stubs"] == ["WIS-STAT-70.47"]
    assert bf[0]["text"] == "70.47 Board of review text"
    # get_statute_backfill was called with the top-3 relevance chunk_ids.
    called_ids = m.get_statute_backfill.call_args.args[0]
    assert "guide-1" in called_ids


def test_backfill_respects_source_gate():
    """Only the top-N (default 3) chunks are used as backfill sources."""
    from agent_tools import execute_tool

    vector_chunks = [
        {"chunk_id": f"c{i}", "text": "t", "doc_id": f"doc-{i}", "score": 0.1 * i}
        for i in range(6)
    ]
    m = _mock_neptune(vector_chunks, [])
    with patch("agent_tools.executor.embed_query", return_value=[0.1] * 1024):
        execute_tool("vector_search", {"query": "q"}, m)

    called_ids = m.get_statute_backfill.call_args.args[0]
    # default gate = 3
    assert len(called_ids) == 3
    assert called_ids == ["c0", "c1", "c2"]


def test_backfill_capped():
    """No more than STATUTE_BACKFILL_CAP (default 3) statute chunks surface."""
    from agent_tools import execute_tool

    vector_chunks = [
        {"chunk_id": "g1", "text": "t", "doc_id": "gov", "score": 0.1},
        {"chunk_id": "g2", "text": "t", "doc_id": "gov", "score": 0.2},
        {"chunk_id": "g3", "text": "t", "doc_id": "gov", "score": 0.3},
    ]
    # 5 distinct statute chunks resolvable -> must be capped to 3.
    backfill_rows = [
        {
            "source_chunk_id": "g1",
            "stub_id": f"WIS-STAT-70.{40 + i}",
            "chunk_id": f"statutes-70_c{i}",
            "text": f"text {i}",
            "doc_id": "statutes-70",
            "source_url": None,
            "s3_key": None,
            "start_page": i,
            "end_page": i,
            "heading": f"70.{40 + i}",
            "subheading": None,
            "authority_level": 2,
        }
        for i in range(5)
    ]
    m = _mock_neptune(vector_chunks, backfill_rows)
    with patch("agent_tools.executor.embed_query", return_value=[0.1] * 1024):
        result = execute_tool("vector_search", {"query": "q"}, m)

    assert len(result["statute_backfill"]) == 3


def test_backfill_chunk_level_dedup():
    """A statute chunk already in the results is not re-surfaced, but a
    DIFFERENT section of the same chapter still can be."""
    from agent_tools import execute_tool

    vector_chunks = [
        {"chunk_id": "g1", "text": "t", "doc_id": "gov", "score": 0.1},
        # statutes-70 section 70.48 already retrieved directly:
        {
            "chunk_id": "statutes-70_c200",
            "text": "70.48",
            "doc_id": "statutes-70",
            "score": 0.2,
            "authority_level": 2,
        },
    ]
    backfill_rows = [
        # same chunk already present -> must be skipped
        {
            "source_chunk_id": "g1",
            "stub_id": "WIS-STAT-70.48",
            "chunk_id": "statutes-70_c200",
            "text": "70.48",
            "doc_id": "statutes-70",
            "source_url": None,
            "s3_key": None,
            "start_page": 1,
            "end_page": 1,
            "heading": "70.48",
            "subheading": None,
            "authority_level": 2,
        },
        # different section of same chapter -> must be surfaced
        {
            "source_chunk_id": "g1",
            "stub_id": "WIS-STAT-70.47",
            "chunk_id": "statutes-70_c167",
            "text": "70.47",
            "doc_id": "statutes-70",
            "source_url": None,
            "s3_key": None,
            "start_page": 39,
            "end_page": 42,
            "heading": "70.47",
            "subheading": None,
            "authority_level": 2,
        },
    ]
    m = _mock_neptune(vector_chunks, backfill_rows)
    with patch("agent_tools.executor.embed_query", return_value=[0.1] * 1024):
        result = execute_tool("vector_search", {"query": "q"}, m)

    ids = [b["chunk_id"] for b in result["statute_backfill"]]
    assert "statutes-70_c167" in ids  # new section surfaced
    assert "statutes-70_c200" not in ids  # already-present chunk skipped


def test_backfill_skips_statute_source_chunks():
    """Statute chunks are not used as backfill sources (no statute->statute)."""
    from agent_tools import execute_tool

    vector_chunks = [
        {
            "chunk_id": "statutes-70_c1",
            "text": "t",
            "doc_id": "statutes-70",
            "score": 0.1,
            "authority_level": 2,
        },
        {"chunk_id": "g1", "text": "t", "doc_id": "gov", "score": 0.2},
        {"chunk_id": "g2", "text": "t", "doc_id": "gov", "score": 0.3},
    ]
    m = _mock_neptune(vector_chunks, [])
    with patch("agent_tools.executor.embed_query", return_value=[0.1] * 1024):
        execute_tool("vector_search", {"query": "q"}, m)

    called_ids = m.get_statute_backfill.call_args.args[0]
    assert "statutes-70_c1" not in called_ids  # statute source excluded
    assert "g1" in called_ids


def test_backfill_swallows_errors():
    from agent_tools import execute_tool

    m = _mock_neptune(
        [{"chunk_id": "g1", "text": "t", "doc_id": "gov", "score": 0.1}], []
    )
    m.get_statute_backfill.side_effect = RuntimeError("neptune down")
    with patch("agent_tools.executor.embed_query", return_value=[0.1] * 1024):
        result = execute_tool("vector_search", {"query": "q"}, m)

    # chunks still returned, backfill absent, no crash
    assert len(result["chunks"]) == 1
    assert "statute_backfill" not in result


def test_backfill_disabled_via_env():
    from agent_tools import execute_tool

    m = _mock_neptune(
        [{"chunk_id": "g1", "text": "t", "doc_id": "gov", "score": 0.1}],
        [
            {
                "source_chunk_id": "g1",
                "stub_id": "WIS-STAT-70.47",
                "chunk_id": "statutes-70_c1",
                "text": "x",
                "doc_id": "statutes-70",
                "authority_level": 2,
            }
        ],
    )
    with (
        patch("agent_tools.executor.embed_query", return_value=[0.1] * 1024),
        patch.dict("os.environ", {"STATUTE_BACKFILL_SOURCE_GATE": "0"}),
    ):
        result = execute_tool("vector_search", {"query": "q"}, m)

    assert "statute_backfill" not in result
    m.get_statute_backfill.assert_not_called()

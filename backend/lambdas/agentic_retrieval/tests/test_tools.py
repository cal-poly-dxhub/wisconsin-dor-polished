"""Unit tests for agentic retrieval tools."""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _passthrough_auto_refine(request, monkeypatch):
    """Make _auto_refine a no-op passthrough for all tests by default.

    Tests marked with @pytest.mark.real_auto_refine skip this fixture.
    """
    if "real_auto_refine" in request.keywords:
        return
    monkeypatch.setattr(
        "agent_tools.executor._auto_refine",
        lambda query, history: (query, None),
    )


def test_execute_tool_vector_search():
    from agent_tools import execute_tool

    mock_neptune = MagicMock()
    mock_neptune.vector_search.return_value = [
        {"chunk_id": "c1", "text": "test chunk", "score": 0.9}
    ]

    with patch("agent_tools.executor.embed_query", return_value=[0.1] * 1024):
        result = execute_tool("vector_search", {"query": "test query"}, mock_neptune)

    assert "chunks" in result
    assert len(result["chunks"]) == 1
    mock_neptune.vector_search.assert_called_once()


def test_execute_tool_get_document_found():
    from agent_tools import execute_tool

    mock_neptune = MagicMock()
    mock_neptune.get_document.return_value = {"id": "doc-1", "title": "Test Doc"}

    result = execute_tool("get_document", {"doc_id": "doc-1"}, mock_neptune)

    assert "document" in result
    assert result["document"]["id"] == "doc-1"


def test_execute_tool_get_document_not_found():
    from agent_tools import execute_tool

    mock_neptune = MagicMock()
    mock_neptune.get_document.return_value = None

    result = execute_tool("get_document", {"doc_id": "missing"}, mock_neptune)

    assert "error" in result


def test_execute_tool_get_document_accepts_node_id_alias():
    """The model sometimes calls get_document with node_id (the param name
    used by get_neighbors/get_authority_chain) instead of doc_id. Accept it
    rather than raising KeyError, which would crash the whole agent loop."""
    from agent_tools import execute_tool

    mock_neptune = MagicMock()
    mock_neptune.get_document.return_value = {"id": "doc-1", "title": "Test Doc"}

    result = execute_tool("get_document", {"node_id": "doc-1"}, mock_neptune)

    assert "document" in result
    assert result["document"]["id"] == "doc-1"
    mock_neptune.get_document.assert_called_once_with("doc-1")


def test_execute_tool_get_document_missing_id_returns_error_not_keyerror():
    """A get_document call with no id at all must return a tool error, not
    raise — a raise propagates out of the loop and crashes the request."""
    from agent_tools import execute_tool

    mock_neptune = MagicMock()
    mock_neptune.get_document.return_value = None

    result = execute_tool("get_document", {}, mock_neptune)

    assert "error" in result


def test_execute_tool_prepare_answer_is_terminal():
    from agent_tools import execute_tool

    mock_neptune = MagicMock()
    input_data = {"cited_doc_ids": ["doc-1"], "answer_plan": "Explain X"}

    result = execute_tool("prepare_answer", input_data, mock_neptune)

    assert result["cited_doc_ids"] == ["doc-1"]
    assert result["answer_plan"] == "Explain X"


def test_execute_tool_unknown_tool():
    from agent_tools import execute_tool

    mock_neptune = MagicMock()
    result = execute_tool("nonexistent", {}, mock_neptune)

    assert "error" in result


def test_execute_tool_fetch_case_opinion_success():
    from agent_tools import execute_tool

    mock_neptune = MagicMock()

    with (
        patch("agent_tools.executor.fetch_case_opinion") as mock_fetch,
        patch("agent_tools.executor.RAW_BUCKET", "test-bucket"),
    ):
        mock_fetch.return_value = {
            "found": True,
            "citation": "109 Wis. 2d 290",
            "text": "CORROON v. HOSCH opinion body",
            "scholar_url": "http://scholar.google.com/scholar?q=109+Wis+2d+290",
        }
        result = execute_tool(
            "fetch_case_opinion",
            {"citation": "109 Wis. 2d 290"},
            mock_neptune,
        )

    assert result["found"] is True
    assert "CORROON" in result["text"]
    mock_fetch.assert_called_once()


def test_fetch_case_opinion_tool_in_definitions():
    from agent_tools import TOOL_DEFINITIONS

    names = {t["toolSpec"]["name"] for t in TOOL_DEFINITIONS}
    assert "fetch_case_opinion" in names


def test_execute_tool_fetch_case_opinion_no_bucket():
    from agent_tools import execute_tool

    mock_neptune = MagicMock()

    with patch("agent_tools.executor.RAW_BUCKET", ""):
        result = execute_tool(
            "fetch_case_opinion",
            {"citation": "109 Wis. 2d 290"},
            mock_neptune,
        )

    assert "error" in result


def test_get_document_falls_back_to_vector_search_on_not_found():
    from agent_tools import execute_tool

    mock_neptune = MagicMock()
    mock_neptune.get_document.return_value = None
    mock_neptune.vector_search.return_value = [
        {"chunk_id": "c1", "text": "match", "doc_id": "real-doc-id", "score": 0.8},
    ]

    with patch("agent_tools.executor.embed_query", return_value=[0.1] * 1024):
        result = execute_tool("get_document", {"doc_id": "typo-or-format-mismatch"}, mock_neptune)

    # Fallback kicked in; returns a suggestion result, not a bare error
    assert "fallback_matches" in result
    assert len(result["fallback_matches"]) == 1
    assert result["fallback_matches"][0]["doc_id"] == "real-doc-id"
    # Original error context still present
    assert result.get("error", "").startswith("Document")


def test_get_document_no_fallback_matches_returns_error():
    from agent_tools import execute_tool

    mock_neptune = MagicMock()
    mock_neptune.get_document.return_value = None
    mock_neptune.vector_search.return_value = []

    with patch("agent_tools.executor.embed_query", return_value=[0.1] * 1024):
        result = execute_tool("get_document", {"doc_id": "nonsense"}, mock_neptune)

    assert "error" in result
    assert result.get("fallback_matches", []) == []


def test_refine_query_tool_removed_from_definitions():
    from agent_tools import TOOL_DEFINITIONS

    names = {t["toolSpec"]["name"] for t in TOOL_DEFINITIONS}
    assert "refine_query" not in names


# ---------------------------------------------------------------------------
# get_section subsection retrieval (Option A) — the § 70.11(49) fix
# ---------------------------------------------------------------------------

# A realistic slice of § 70.11 as the ingestion chunker packs it: several
# subsections glued into one chunk, plus a mid-sentence cross-reference to
# (49) that must NOT be treated as the subsection's own introduction.
_SEC_7011_CHUNKS = [
    {
        "chunk_id": "c-a",
        "doc_id": "statutes-70",
        "text": (
            "(47) BROADBAND EQUIPMENT. Equipment used to provide broadband...\n"
            "(48) DIGITAL GOODS. ...\n"
            "(49) RECREATIONAL PREFABRICATED STRUCTURES. Any prefabricated "
            "structure originally designed to be towed upon a highway...\n"
            "(50) SOMETHING ELSE. ..."
        ),
        "start_page": 12,
        "heading": "70.11 Property exempted from taxation.",
    },
    {
        "chunk_id": "c-b",
        "doc_id": "statutes-70",
        "text": (
            "(1) PROPERTY OF THE STATE. ... property not otherwise exempt "
            "under s. 66.0435 (3) or 70.11 (49) is taxable as real property."
        ),
        "start_page": 5,
        "heading": "70.11 Property exempted from taxation.",
    },
]


def test_find_subsection_chunks_matches_marker_at_line_start():
    from agent_tools.executor import _find_subsection_chunks

    matched = _find_subsection_chunks(_SEC_7011_CHUNKS, "49")

    # Only the chunk that INTRODUCES (49) at a line start matches — the
    # chunk that merely cross-references "70.11 (49)" mid-sentence does not.
    assert [c["chunk_id"] for c in matched] == ["c-a"]


def test_find_subsection_chunks_normalizes_parenthesized_input():
    from agent_tools.executor import _find_subsection_chunks

    assert _find_subsection_chunks(_SEC_7011_CHUNKS, "(49)")[0]["chunk_id"] == "c-a"


def test_find_subsection_chunks_alnum_marker():
    from agent_tools.executor import _find_subsection_chunks

    chunks = [{"chunk_id": "x", "text": "(4m) SPECIAL CASE. ...\n(5) NEXT. ..."}]
    assert [c["chunk_id"] for c in _find_subsection_chunks(chunks, "4m")] == ["x"]
    # (4) must not match (4m), and vice versa.
    assert _find_subsection_chunks(chunks, "4") == []


def test_execute_tool_get_section_subsection_bypasses_ranking():
    from agent_tools import execute_tool

    mock_neptune = MagicMock()
    mock_neptune.get_section_chunks.return_value = _SEC_7011_CHUNKS

    # embed_query must NOT be called in subsection mode (no ranking).
    with patch("agent_tools.executor.embed_query") as mock_embed:
        result = execute_tool(
            "get_section",
            {
                "doc_id": "statutes-70",
                "heading": "70.11 Property exempted from taxation.",
                "subsection": "49",
            },
            mock_neptune,
        )

    mock_embed.assert_not_called()
    mock_neptune.get_section_chunks_with_embeddings.assert_not_called()
    assert result["subsection"] == "49"
    assert [c["chunk_id"] for c in result["chunks"]] == ["c-a"]
    assert result["chunks"][0]["start_page"] == 12


def test_execute_tool_get_section_subsection_not_found():
    from agent_tools import execute_tool

    mock_neptune = MagicMock()
    mock_neptune.get_section_chunks.return_value = _SEC_7011_CHUNKS

    result = execute_tool(
        "get_section",
        {
            "doc_id": "statutes-70",
            "heading": "70.11 Property exempted from taxation.",
            "subsection": "99",
        },
        mock_neptune,
    )

    assert "error" in result
    assert "(99)" in result["error"]
    assert "chunks" not in result


def test_get_section_subsection_param_in_definitions():
    from agent_tools import TOOL_DEFINITIONS

    spec = next(t["toolSpec"] for t in TOOL_DEFINITIONS if t["toolSpec"]["name"] == "get_section")
    assert "subsection" in spec["inputSchema"]["json"]["properties"]


@pytest.mark.real_auto_refine
def test_auto_refine_uses_history(monkeypatch):
    """_auto_refine passes chat history to Bedrock so follow-ups resolve."""
    from agent_tools.executor import _auto_refine

    fake_response = {
        "output": {
            "message": {
                "content": [
                    {"text": '{"refined_query": "agricultural land classification", "target_wpam_year": null}'}
                ]
            }
        }
    }
    mock_bedrock = MagicMock()
    mock_bedrock.converse.return_value = fake_response
    monkeypatch.setattr("agent_tools.executor.bedrock", mock_bedrock)

    refined, year = _auto_refine(
        "what about agriculture",
        [{"query": "what are land classifications", "answer": "..."}],
    )

    assert refined == "agricultural land classification"
    assert year is None
    user_text = mock_bedrock.converse.call_args.kwargs["messages"][0]["content"][0]["text"]
    assert "what are land classifications" in user_text
    assert "what about agriculture" in user_text


@pytest.mark.real_auto_refine
def test_auto_refine_falls_back_on_error(monkeypatch):
    """If Bedrock throws, return original query unchanged."""
    from agent_tools.executor import _auto_refine

    mock_bedrock = MagicMock()
    mock_bedrock.converse.side_effect = RuntimeError("bedrock down")
    monkeypatch.setattr("agent_tools.executor.bedrock", mock_bedrock)

    refined, year = _auto_refine("original question", None)

    assert refined == "original question"
    assert year is None


@pytest.mark.real_auto_refine
def test_auto_refine_extracts_target_wpam_year(monkeypatch):
    from agent_tools.executor import _auto_refine

    mock_response = {
        "output": {
            "message": {
                "content": [
                    {"text": '{"refined_query": "WPAM agricultural land 2018", "target_wpam_year": 2018}'}
                ]
            }
        }
    }
    mock_bedrock = MagicMock()
    mock_bedrock.converse.return_value = mock_response
    monkeypatch.setattr("agent_tools.executor.bedrock", mock_bedrock)

    refined, year = _auto_refine("what does the 2018 WPAM say about agricultural land?", [])

    assert refined == "WPAM agricultural land 2018"
    assert year == 2018


@pytest.mark.real_auto_refine
def test_auto_refine_no_target_year_when_none(monkeypatch):
    from agent_tools.executor import _auto_refine

    mock_response = {
        "output": {
            "message": {
                "content": [
                    {"text": '{"refined_query": "WPAM agricultural land", "target_wpam_year": null}'}
                ]
            }
        }
    }
    mock_bedrock = MagicMock()
    mock_bedrock.converse.return_value = mock_response
    monkeypatch.setattr("agent_tools.executor.bedrock", mock_bedrock)

    _, year = _auto_refine("what does WPAM say about agricultural land?", [])
    assert year is None


@pytest.mark.real_auto_refine
def test_auto_refine_falls_back_on_invalid_json(monkeypatch):
    from agent_tools.executor import _auto_refine

    mock_response = {"output": {"message": {"content": [{"text": "WPAM agricultural land"}]}}}
    mock_bedrock = MagicMock()
    mock_bedrock.converse.return_value = mock_response
    monkeypatch.setattr("agent_tools.executor.bedrock", mock_bedrock)

    refined, year = _auto_refine("what does WPAM say?", [])
    assert refined == "WPAM agricultural land"
    assert year is None


@pytest.mark.real_auto_refine
def test_auto_refine_falls_back_on_bedrock_error(monkeypatch):
    from agent_tools.executor import _auto_refine

    mock_bedrock = MagicMock()
    mock_bedrock.converse.side_effect = RuntimeError("bedrock unavailable")
    monkeypatch.setattr("agent_tools.executor.bedrock", mock_bedrock)

    refined, year = _auto_refine("what does WPAM say about agricultural land?", None)
    assert refined == "what does WPAM say about agricultural land?"
    assert year is None


def test_vector_search_applies_wpam_dedup():
    """Two near-identical WPAM chunks from different years should collapse."""
    from agent_tools import execute_tool

    mock_neptune = MagicMock()
    mock_neptune.current_wpam_year = 2025
    mock_neptune.vector_search.return_value = [
        {
            "chunk_id": "wpam-2018-c1",
            "doc_id": "wpam-...-2018",
            "framework_id": "FW-WPAM",
            "edition_year": 2018,
            "heading": "Manufactured Homes",
            "text": "old",
        },
        {
            "chunk_id": "wpam-2025-c1",
            "doc_id": "wpam-...-2025",
            "framework_id": "FW-WPAM",
            "edition_year": 2025,
            "heading": "Manufactured Homes",
            "text": "new",
        },
    ]
    mock_neptune.get_neighbors.return_value = []

    with patch("agent_tools.executor.embed_query", return_value=[0.1] * 1024):
        result = execute_tool(
            "vector_search",
            {"query": "manufactured homes"},
            mock_neptune,
        )

    assert len(result["chunks"]) == 1
    assert result["chunks"][0]["edition_year"] == 2025


def test_vector_search_target_year_overrides_max(monkeypatch):
    from agent_tools import execute_tool

    mock_neptune = MagicMock()
    mock_neptune.vector_search.return_value = [
        {
            "chunk_id": "wpam-2018-c1",
            "doc_id": "wpam-...-2018",
            "framework_id": "FW-WPAM",
            "edition_year": 2018,
            "heading": "Manufactured Homes",
            "text": "...",
        },
        {
            "chunk_id": "wpam-2025-c1",
            "doc_id": "wpam-...-2025",
            "framework_id": "FW-WPAM",
            "edition_year": 2025,
            "heading": "Manufactured Homes",
            "text": "...",
        },
    ]
    mock_neptune.get_neighbors.return_value = []
    monkeypatch.setattr(
        "agent_tools.executor._auto_refine",
        lambda q, h: (q, 2018),
    )

    with patch("agent_tools.executor.embed_query", return_value=[0.1] * 1024):
        result = execute_tool(
            "vector_search",
            {"query": "2018 manufactured homes"},
            mock_neptune,
        )

    assert len(result["chunks"]) == 1
    assert result["chunks"][0]["edition_year"] == 2018


def test_get_neighbors_applies_wpam_dedup():
    from agent_tools import execute_tool

    mock_neptune = MagicMock()
    mock_neptune.current_wpam_year = 2025
    mock_neptune.get_neighbors.return_value = [
        {
            "id": "wpam-2018-c1",
            "framework_id": "FW-WPAM",
            "edition_year": 2018,
            "heading": "Manufactured Homes",
            "relationship": "CITES",
        },
        {
            "id": "wpam-2025-c1",
            "framework_id": "FW-WPAM",
            "edition_year": 2025,
            "heading": "Manufactured Homes",
            "relationship": "CITES",
        },
        {
            "id": "stat-70-32",
            "framework_id": "FW-STATUTES",
            "heading": "70.32",
            "relationship": "CITES",
        },
    ]

    result = execute_tool(
        "get_neighbors",
        {"node_id": "stat-70-32", "edge_types": ["CITES"]},
        mock_neptune,
    )

    # WPAM dedup'd to 1, statute passes through.
    assert len(result["neighbors"]) == 2
    wpam = [n for n in result["neighbors"] if n.get("framework_id") == "FW-WPAM"]
    assert len(wpam) == 1
    assert wpam[0]["edition_year"] == 2025


def test_get_neighbors_filters_out_chunk_labels():
    """Chunk-labeled nodes should be excluded from get_neighbors results."""
    from agent_tools import execute_tool

    mock_neptune = MagicMock()
    mock_neptune.get_neighbors.return_value = [
        {"id": "doc-1", "title": "Real Doc", "labels": ["Document"], "relationship": "CITES"},
        {"id": "chunk-1", "title": None, "labels": ["Chunk"], "relationship": "EXTRACTED_FROM"},
        {"id": "chunk-2", "title": None, "labels": ["Chunk"], "relationship": "CITES"},
        {
            "id": "stat-1",
            "title": "Statute 70.32",
            "labels": ["Document", "Statute"],
            "relationship": "IMPLEMENTS",
        },
    ]

    result = execute_tool(
        "get_neighbors",
        {"node_id": "some-node"},
        mock_neptune,
    )

    assert len(result["neighbors"]) == 2
    ids = [n["id"] for n in result["neighbors"]]
    assert "doc-1" in ids
    assert "stat-1" in ids
    assert "chunk-1" not in ids
    assert "chunk-2" not in ids


def test_get_neighbors_ranked_result_includes_trace_context():
    from agent_tools import execute_tool

    mock_neptune = MagicMock()
    mock_neptune.get_neighbor_case_summaries_with_embeddings.return_value = [
        {
            "case_id": f"case-{i}",
            "summary": f"Case summary {i}",
            "title": f"Case {i}",
            "citation": f"{i} Wis. 2d 1",
            "embedding": [1.0, 0.0],
        }
        for i in range(10)
    ]

    with patch("agent_tools.executor.embed_query", return_value=[1.0, 0.0]):
        result = execute_tool(
            "get_neighbors",
            {
                "node_id": "WIS-STAT-70.32",
                "query": "agricultural assessment",
                "top_k": 10,
            },
            mock_neptune,
        )

    assert len(result["neighbors"]) == 10
    assert result["query"] == "agricultural assessment"
    assert result["top_k"] == 10
    assert result["total_cases"] == 10


def test_vector_search_enrichment_runs_but_is_not_surfaced():
    """Auto-enrichment still runs internally (feeding case-law discovery) but
    is NOT surfaced to the model — graph_context is absent from the result
    (Direction 1, Option A)."""
    from agent_tools import execute_tool

    mock_neptune = MagicMock()
    mock_neptune.vector_search.return_value = [
        {"chunk_id": "c1", "text": "test", "score": 0.9, "doc_id": "doc-1"},
    ]
    mock_neptune.get_neighbors.return_value = [
        {"id": "related-doc", "title": "Related", "labels": ["Document"], "relationship": "CITES"},
        {"id": "chunk-99", "title": None, "labels": ["Chunk"], "relationship": "EXTRACTED_FROM"},
    ]
    mock_neptune.resolve_case_citations.return_value = []

    with patch("agent_tools.executor.embed_query", return_value=[0.1] * 1024):
        result = execute_tool("vector_search", {"query": "test"}, mock_neptune)

    # Enrichment still fires internally for the top parent doc.
    mock_neptune.get_neighbors.assert_called_once_with("doc-1")
    # But nothing is surfaced to the model.
    assert "graph_context" not in result


def test_vector_search_extracts_citations_and_resolves_case_law():
    """vector_search should extract citations from chunk text and resolve
    them to CaseLaw nodes via resolve_case_citations."""
    from agent_tools import execute_tool

    mock_neptune = MagicMock()
    mock_neptune.vector_search.return_value = [
        {
            "chunk_id": "c1",
            "doc_id": "wpam-2025",
            "score": 0.9,
            "text": "See Markarian v City of Cudahy, 45 Wis.2d 683 (1970).",
        },
    ]
    mock_neptune.get_neighbors.return_value = []
    mock_neptune.resolve_case_citations.return_value = [
        {
            "id": "case-law-45-wis-2d-683",
            "title": "State Ex Rel. Markarian v. City of Cudahy, 45 Wis. 2d 683",
            "citation": "45 Wis. 2d 683",
            "labels": ["CaseLaw"],
        },
    ]

    with patch("agent_tools.executor.embed_query", return_value=[0.1] * 1024):
        result = execute_tool("vector_search", {"query": "sale of subject"}, mock_neptune)

    assert "related_case_law" in result
    assert len(result["related_case_law"]) == 1
    assert result["related_case_law"][0]["id"] == "case-law-45-wis-2d-683"
    # Verify resolve_case_citations was called with normalized citation
    mock_neptune.resolve_case_citations.assert_called_once()
    citations_arg = mock_neptune.resolve_case_citations.call_args[0][0]
    assert "45 Wis. 2d 683" in citations_arg


def test_vector_search_no_related_case_law_when_no_citations():
    """vector_search should not include related_case_law key when no
    citations are found in chunk text and neighbor discovery finds nothing."""
    from agent_tools import execute_tool

    mock_neptune = MagicMock()
    mock_neptune.vector_search.return_value = [
        {
            "chunk_id": "c1",
            "doc_id": "wpam-2025",
            "score": 0.9,
            "text": "The assessor shall consider all relevant factors.",
        },
    ]
    mock_neptune.get_neighbors.return_value = []
    mock_neptune.resolve_case_citations.return_value = []

    with patch("agent_tools.executor.embed_query", return_value=[0.1] * 1024):
        result = execute_tool("vector_search", {"query": "test"}, mock_neptune)

    assert "related_case_law" not in result


def test_extract_citations():
    """Test citation regex extraction and normalization."""
    from agent_tools import extract_citations

    text = (
        "See Markarian v City of Cudahy, 45 Wis.2d 683, 173 N.W.2d 627 (1970). "
        "Also Lowe's v Delavan, 2023 WI 8, 405 Wis. 2d 616, 985 N.W.2d 69. "
        "And Children's Hospital, 2025 WI App 43, 417 Wis. 2d 629, 24 N.W.3d 601."
    )
    citations = extract_citations(text)

    # Check normalized forms
    assert "45 Wis. 2d 683" in citations
    assert "173 N.W.2d 627" in citations
    assert "405 Wis. 2d 616" in citations
    assert "985 N.W.2d 69" in citations
    assert "2023 WI 8" in citations
    assert "2025 WI App 43" in citations
    assert "417 Wis. 2d 629" in citations
    assert "24 N.W.3d 601" in citations




def test_find_case_law_tool_by_citation():
    """find_case_law should try citation lookup first."""
    from agent_tools import execute_tool

    mock_neptune = MagicMock()
    mock_neptune.resolve_case_citations.return_value = [
        {
            "id": "case-law-45-wis-2d-683",
            "title": "Markarian",
            "citation": "45 Wis. 2d 683",
            "labels": ["CaseLaw"],
        },
    ]

    result = execute_tool(
        "find_case_law",
        {"search_text": "45 Wis. 2d 683"},
        mock_neptune,
    )

    assert len(result["cases"]) == 1
    assert result["cases"][0]["id"] == "case-law-45-wis-2d-683"
    mock_neptune.resolve_case_citations.assert_called_once()
    # Should NOT fall back to find_case_law since citation lookup succeeded
    mock_neptune.find_case_law.assert_not_called()


def test_find_case_law_tool_falls_back_to_title_search():
    """find_case_law should fall back to title search when no citation found."""
    from agent_tools import execute_tool

    mock_neptune = MagicMock()
    mock_neptune.resolve_case_citations.return_value = []
    mock_neptune.find_case_law.return_value = [
        {
            "id": "case-law-45-wis-2d-683",
            "title": "Markarian v. City of Cudahy",
            "citation": "45 Wis. 2d 683",
            "labels": ["CaseLaw"],
        },
    ]

    result = execute_tool(
        "find_case_law",
        {"search_text": "Markarian", "statute_id": "WIS-STAT-70.32"},
        mock_neptune,
    )

    assert len(result["cases"]) == 1
    mock_neptune.find_case_law.assert_called_once_with(
        "Markarian", statute_id="WIS-STAT-70.32", limit=10
    )

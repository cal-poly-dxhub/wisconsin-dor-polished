"""Unit tests for agentic retrieval tools."""

from unittest.mock import MagicMock, patch


def test_execute_tool_vector_search():
    from agent_tools import execute_tool

    mock_neptune = MagicMock()
    mock_neptune.vector_search.return_value = [
        {"chunk_id": "c1", "text": "test chunk", "score": 0.9}
    ]

    with patch("agent_tools.embed_query", return_value=[0.1] * 1024):
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

    with patch("agent_tools.fetch_case_opinion") as mock_fetch, \
         patch("agent_tools.RAW_BUCKET", "test-bucket"):
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

    with patch("agent_tools.RAW_BUCKET", ""):
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

    with patch("agent_tools.embed_query", return_value=[0.1] * 1024):
        result = execute_tool(
            "get_document", {"doc_id": "typo-or-format-mismatch"}, mock_neptune
        )

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

    with patch("agent_tools.embed_query", return_value=[0.1] * 1024):
        result = execute_tool(
            "get_document", {"doc_id": "nonsense"}, mock_neptune
        )

    assert "error" in result
    assert result.get("fallback_matches", []) == []


def test_refine_query_tool_in_definitions():
    from agent_tools import TOOL_DEFINITIONS

    names = {t["toolSpec"]["name"] for t in TOOL_DEFINITIONS}
    assert "refine_query" in names


def test_refine_query_uses_history_when_present():
    """When chat history is supplied, the Bedrock prompt should include it
    so the model can resolve 'what about X' follow-ups."""
    from agent_tools import execute_tool

    fake_response = {
        "output": {
            "message": {
                "content": [
                    {"text": "agricultural land classification requirements"}
                ]
            }
        }
    }
    mock_bedrock = MagicMock()
    mock_bedrock.converse.return_value = fake_response

    with patch("agent_tools.bedrock", mock_bedrock):
        result = execute_tool(
            "refine_query",
            {"query": "what about agriculture"},
            MagicMock(),
            chat_history=[
                {"query": "what are land classifications", "answer": "..."},
            ],
        )

    assert result["refined_query"] == "agricultural land classification requirements"
    # The Bedrock call must have received the history in the user content.
    messages = mock_bedrock.converse.call_args.kwargs["messages"]
    user_text = messages[0]["content"][0]["text"]
    assert "what are land classifications" in user_text
    assert "what about agriculture" in user_text


def test_refine_query_falls_back_on_error():
    """If Bedrock throws, the tool must not break the loop — return original."""
    from agent_tools import execute_tool

    mock_bedrock = MagicMock()
    mock_bedrock.converse.side_effect = RuntimeError("bedrock down")

    with patch("agent_tools.bedrock", mock_bedrock):
        result = execute_tool(
            "refine_query",
            {"query": "original question"},
            MagicMock(),
            chat_history=None,
        )

    assert result["refined_query"] == "original question"


def test_refine_query_extracts_target_wpam_year():
    """LLM should return target_wpam_year when user explicitly mentions a year + WPAM."""
    from agent_tools import execute_tool

    mock_neptune = MagicMock()
    mock_response = {
        "output": {
            "message": {
                "content": [{"text": '{"refined_query": "WPAM agricultural land 2018", "target_wpam_year": 2018}'}]
            }
        }
    }
    with patch("agent_tools.bedrock") as mock_bedrock:
        mock_bedrock.converse.return_value = mock_response
        result = execute_tool(
            "refine_query",
            {"query": "what does the 2018 WPAM say about agricultural land?"},
            mock_neptune,
            chat_history=[],
        )

    assert result["refined_query"] == "WPAM agricultural land 2018"
    assert result["target_wpam_year"] == 2018


def test_refine_query_no_target_year_when_no_year_mentioned():
    from agent_tools import execute_tool

    mock_neptune = MagicMock()
    mock_response = {
        "output": {
            "message": {
                "content": [{"text": '{"refined_query": "WPAM agricultural land", "target_wpam_year": null}'}]
            }
        }
    }
    with patch("agent_tools.bedrock") as mock_bedrock:
        mock_bedrock.converse.return_value = mock_response
        result = execute_tool(
            "refine_query",
            {"query": "what does WPAM say about agricultural land?"},
            mock_neptune,
            chat_history=[],
        )

    assert result["target_wpam_year"] is None


def test_refine_query_falls_back_on_invalid_json():
    """If the LLM doesn't return JSON, treat the entire output as the
    refined query and target_wpam_year as None."""
    from agent_tools import execute_tool

    mock_neptune = MagicMock()
    mock_response = {
        "output": {
            "message": {
                "content": [{"text": "WPAM agricultural land"}]
            }
        }
    }
    with patch("agent_tools.bedrock") as mock_bedrock:
        mock_bedrock.converse.return_value = mock_response
        result = execute_tool(
            "refine_query",
            {"query": "what does WPAM say about agricultural land?"},
            mock_neptune,
            chat_history=[],
        )

    assert result["refined_query"] == "WPAM agricultural land"
    assert result["target_wpam_year"] is None


def test_refine_query_falls_back_on_bedrock_error():
    from agent_tools import execute_tool

    mock_neptune = MagicMock()
    with patch("agent_tools.bedrock") as mock_bedrock:
        mock_bedrock.converse.side_effect = RuntimeError("bedrock unavailable")
        result = execute_tool(
            "refine_query",
            {"query": "what does WPAM say about agricultural land?"},
            mock_neptune,
            chat_history=[],
        )

    assert result["refined_query"] == "what does WPAM say about agricultural land?"
    assert result["target_wpam_year"] is None


def test_vector_search_applies_wpam_dedup():
    """Two near-identical WPAM chunks from different years should collapse."""
    from agent_tools import execute_tool

    mock_neptune = MagicMock()
    mock_neptune.current_wpam_year = 2025
    mock_neptune.vector_search.return_value = [
        {"chunk_id": "wpam-2018-c1", "doc_id": "wpam-...-2018",
         "framework_id": "FW-WPAM", "edition_year": 2018,
         "heading": "Manufactured Homes", "text": "old"},
        {"chunk_id": "wpam-2025-c1", "doc_id": "wpam-...-2025",
         "framework_id": "FW-WPAM", "edition_year": 2025,
         "heading": "Manufactured Homes", "text": "new"},
    ]
    mock_neptune.get_neighbors.return_value = []

    with patch("agent_tools.embed_query", return_value=[0.1] * 1024):
        result = execute_tool(
            "vector_search",
            {"query": "manufactured homes"},
            mock_neptune,
        )

    assert len(result["chunks"]) == 1
    assert result["chunks"][0]["edition_year"] == 2025


def test_vector_search_target_year_overrides_max():
    from agent_tools import execute_tool

    mock_neptune = MagicMock()
    mock_neptune.vector_search.return_value = [
        {"chunk_id": "wpam-2018-c1", "doc_id": "wpam-...-2018",
         "framework_id": "FW-WPAM", "edition_year": 2018,
         "heading": "Manufactured Homes", "text": "..."},
        {"chunk_id": "wpam-2025-c1", "doc_id": "wpam-...-2025",
         "framework_id": "FW-WPAM", "edition_year": 2025,
         "heading": "Manufactured Homes", "text": "..."},
    ]
    mock_neptune.get_neighbors.return_value = []

    with patch("agent_tools.embed_query", return_value=[0.1] * 1024):
        result = execute_tool(
            "vector_search",
            {"query": "2018 manufactured homes", "target_wpam_year": 2018},
            mock_neptune,
        )

    assert len(result["chunks"]) == 1
    assert result["chunks"][0]["edition_year"] == 2018


def test_get_neighbors_applies_wpam_dedup():
    from agent_tools import execute_tool

    mock_neptune = MagicMock()
    mock_neptune.current_wpam_year = 2025
    mock_neptune.get_neighbors.return_value = [
        {"id": "wpam-2018-c1", "framework_id": "FW-WPAM", "edition_year": 2018,
         "heading": "Manufactured Homes", "relationship": "CITES"},
        {"id": "wpam-2025-c1", "framework_id": "FW-WPAM", "edition_year": 2025,
         "heading": "Manufactured Homes", "relationship": "CITES"},
        {"id": "stat-70-32", "framework_id": "FW-STATUTES",
         "heading": "70.32", "relationship": "CITES"},
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
        {"id": "doc-1", "title": "Real Doc", "labels": ["Document"],
         "relationship": "CITES"},
        {"id": "chunk-1", "title": None, "labels": ["Chunk"],
         "relationship": "EXTRACTED_FROM"},
        {"id": "chunk-2", "title": None, "labels": ["Chunk"],
         "relationship": "CITES"},
        {"id": "stat-1", "title": "Statute 70.32", "labels": ["Document", "Statute"],
         "relationship": "IMPLEMENTS"},
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


def test_vector_search_auto_enrichment_filters_chunks():
    """Auto-enrichment in vector_search should not include Chunk-labeled
    neighbors in graph_context."""
    from agent_tools import execute_tool

    mock_neptune = MagicMock()
    mock_neptune.vector_search.return_value = [
        {"chunk_id": "c1", "text": "test", "score": 0.9, "doc_id": "doc-1"},
    ]
    mock_neptune.get_neighbors.return_value = [
        {"id": "related-doc", "title": "Related", "labels": ["Document"],
         "relationship": "CITES"},
        {"id": "chunk-99", "title": None, "labels": ["Chunk"],
         "relationship": "EXTRACTED_FROM"},
    ]
    mock_neptune.resolve_case_citations.return_value = []

    with patch("agent_tools.embed_query", return_value=[0.1] * 1024):
        result = execute_tool("vector_search", {"query": "test"}, mock_neptune)

    assert "graph_context" in result
    neighbors = result["graph_context"].get("doc-1", [])
    assert len(neighbors) == 1
    assert neighbors[0]["id"] == "related-doc"


def test_vector_search_extracts_citations_and_resolves_case_law():
    """vector_search should extract citations from chunk text and resolve
    them to CaseLaw nodes via resolve_case_citations."""
    from agent_tools import execute_tool

    mock_neptune = MagicMock()
    mock_neptune.vector_search.return_value = [
        {"chunk_id": "c1", "doc_id": "wpam-2025", "score": 0.9,
         "text": "See Markarian v City of Cudahy, 45 Wis.2d 683 (1970)."},
    ]
    mock_neptune.get_neighbors.return_value = []
    mock_neptune.resolve_case_citations.return_value = [
        {"id": "case-law-45-wis-2d-683",
         "title": "State Ex Rel. Markarian v. City of Cudahy, 45 Wis. 2d 683",
         "citation": "45 Wis. 2d 683", "labels": ["CaseLaw"]},
    ]

    with patch("agent_tools.embed_query", return_value=[0.1] * 1024):
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
        {"chunk_id": "c1", "doc_id": "wpam-2025", "score": 0.9,
         "text": "The assessor shall consider all relevant factors."},
    ]
    mock_neptune.get_neighbors.return_value = []
    mock_neptune.resolve_case_citations.return_value = []

    with patch("agent_tools.embed_query", return_value=[0.1] * 1024):
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


def test_vector_search_neighbor_citation_discovery():
    """vector_search should discover case law by scanning neighbor doc chunk
    text for citations, using the top-ranked cross-framework neighbors."""
    from agent_tools import execute_tool

    mock_neptune = MagicMock()
    mock_neptune.vector_search.return_value = [
        {"chunk_id": "c1", "doc_id": "wpam-2025-ag", "score": 0.9,
         "text": "Agricultural land classification under sec. 70.32."},
        {"chunk_id": "c2", "doc_id": "wpam-2025-ag", "score": 0.85,
         "text": "Land devoted primarily to agricultural use."},
    ]
    mock_neptune.get_neighbors.return_value = [
        {"id": "gov_publications-ag-guide", "title": "Ag Assessment Guide",
         "labels": ["Document"], "framework_id": "FW-GOV-PUBS",
         "relationship": "SUPPLEMENTS"},
    ]
    # No citations in query chunk text, so resolve_case_citations is only
    # called once — for the neighbor doc citations.
    mock_neptune.resolve_case_citations.return_value = [
        {"id": "case-law-2019-wi-23", "title": "Peter Ogden v. DOR",
         "citation": "2019 WI 23", "doc_type": "case_law",
         "authority_level": 3, "source_url": None, "labels": ["CaseLaw"]},
    ]
    mock_neptune.get_chunk_statute_ids.return_value = ["WIS-STAT-70.32"]
    mock_neptune.rank_neighbors_by_shared_statutes.return_value = [
        "gov_publications-ag-guide",
    ]
    mock_neptune.get_chunks_text_for_docs.return_value = [
        "Agricultural classification per 2019 WI 23 requires land use.",
    ]

    with patch("agent_tools.embed_query", return_value=[0.1] * 1024):
        result = execute_tool("vector_search", {"query": "ag classification"}, mock_neptune)

    assert "related_case_law" in result
    assert len(result["related_case_law"]) == 1
    assert result["related_case_law"][0]["id"] == "case-law-2019-wi-23"
    mock_neptune.get_chunk_statute_ids.assert_called_once_with(["c1", "c2"])
    mock_neptune.rank_neighbors_by_shared_statutes.assert_called_once_with(
        ["gov_publications-ag-guide"], ["WIS-STAT-70.32"], limit=3
    )
    mock_neptune.get_chunks_text_for_docs.assert_called_once_with(
        ["gov_publications-ag-guide"]
    )


def test_vector_search_neighbor_citation_deduplicates():
    """Neighbor citation discovery should not duplicate cases already found
    by direct citation resolution from the query chunks."""
    from agent_tools import execute_tool

    mock_neptune = MagicMock()
    mock_neptune.vector_search.return_value = [
        {"chunk_id": "c1", "doc_id": "wpam-2025", "score": 0.9,
         "text": "See Markarian, 45 Wis. 2d 683."},
    ]
    mock_neptune.get_neighbors.return_value = [
        {"id": "gov-pub-1", "title": "Guide", "labels": ["Document"],
         "framework_id": "FW-GOV-PUBS", "relationship": "RELATED_TO"},
    ]
    # Direct citation resolution finds Markarian from query chunk text
    # Second resolve call returns both Markarian (dupe) and Peter Ogden (new)
    mock_neptune.resolve_case_citations.side_effect = [
        [{"id": "case-law-45-wis-2d-683", "title": "Markarian",
          "citation": "45 Wis. 2d 683", "labels": ["CaseLaw"]}],
        [{"id": "case-law-45-wis-2d-683", "title": "Markarian",
          "citation": "45 Wis. 2d 683", "labels": ["CaseLaw"]},
         {"id": "case-law-2019-wi-23", "title": "Peter Ogden v. DOR",
          "citation": "2019 WI 23", "labels": ["CaseLaw"]}],
    ]
    mock_neptune.get_chunk_statute_ids.return_value = ["WIS-STAT-70.32"]
    mock_neptune.rank_neighbors_by_shared_statutes.return_value = ["gov-pub-1"]
    mock_neptune.get_chunks_text_for_docs.return_value = [
        "See Markarian, 45 Wis. 2d 683, and 2019 WI 23.",
    ]

    with patch("agent_tools.embed_query", return_value=[0.1] * 1024):
        result = execute_tool("vector_search", {"query": "test"}, mock_neptune)

    assert len(result["related_case_law"]) == 2
    ids = [c["id"] for c in result["related_case_law"]]
    assert "case-law-45-wis-2d-683" in ids
    assert "case-law-2019-wi-23" in ids


def test_find_case_law_tool_by_citation():
    """find_case_law should try citation lookup first."""
    from agent_tools import execute_tool

    mock_neptune = MagicMock()
    mock_neptune.resolve_case_citations.return_value = [
        {"id": "case-law-45-wis-2d-683", "title": "Markarian",
         "citation": "45 Wis. 2d 683", "labels": ["CaseLaw"]},
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
        {"id": "case-law-45-wis-2d-683", "title": "Markarian v. City of Cudahy",
         "citation": "45 Wis. 2d 683", "labels": ["CaseLaw"]},
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

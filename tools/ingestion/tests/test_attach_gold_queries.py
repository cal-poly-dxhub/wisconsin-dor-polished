"""ops/attach_gold_queries.py: chunk selection + idempotent attachment (no AWS)."""

from tools.ingestion.ops import attach_gold_queries as agq


def _doc(n):
    return {
        "doc_id": "statutes-70",
        "chunks": [
            {"text": f"chunk {i}", "metadata": {"heading": f"70.{i:02d}"}} for i in range(n)
        ],
    }


def _embed_factory(best_idx: int):
    """Query embeds to e_q; chunk i embeds so that cosine peaks at best_idx."""

    def embed(text):
        if text.startswith("chunk "):
            i = int(text.split()[1])
            return [1.0, 0.0] if i == best_idx else [0.0, 1.0]
        return [1.0, 0.0]

    return embed


def test_explicit_chunk_ids_win():
    doc = _doc(6)
    case = {
        "id": "c1",
        "query": "q",
        "expected_chunk_ids": ["statutes-70_chunk_0004", "other-doc_chunk_0001", 2],
    }
    idx = agq.choose_chunk_indices(
        doc, case, embed_fn=lambda t: [1.0], chunk_embeddings={}, query_embedding=None
    )
    assert idx == [2, 4]


def test_small_doc_attaches_to_all():
    doc = _doc(3)
    idx = agq.choose_chunk_indices(
        doc,
        {"id": "c", "query": "q"},
        embed_fn=lambda t: [1.0],
        chunk_embeddings={},
        query_embedding=None,
    )
    assert idx == [0, 1, 2]


def test_cosine_picks_best_chunk_and_caches_embeddings():
    doc = _doc(6)
    cache: dict = {}
    idx = agq.choose_chunk_indices(
        doc,
        {"id": "c", "query": "q"},
        embed_fn=_embed_factory(3),
        chunk_embeddings=cache,
        query_embedding=None,
    )
    assert idx == [3]
    assert set(cache) == set(range(6))


def test_subsection_narrows_candidates():
    doc = _doc(6)
    case = {"id": "c", "query": "q", "expected_subsection": "70.05"}
    idx = agq.choose_chunk_indices(
        doc, case, embed_fn=_embed_factory(3), chunk_embeddings={}, query_embedding=None
    )
    assert idx == [5]  # single heading match short-circuits cosine


def test_attach_is_idempotent_and_dedups():
    doc = _doc(6)
    cases = [
        {"id": "a", "query": "How do I appeal?"},
        {"id": "b", "query": "how do i APPEAL?"},
        {"id": "c", "query": "Other q"},
    ]
    changed = agq.attach_to_doc(doc, cases, embed_fn=_embed_factory(1))
    assert changed
    assert doc["chunks"][1]["gold_queries"] == ["How do I appeal?", "Other q"]
    assert all("gold_queries" not in c for i, c in enumerate(doc["chunks"]) if i != 1)
    assert not agq.attach_to_doc(doc, cases, embed_fn=_embed_factory(1))
    # reset drops stale attachments from a previous YAML version
    assert agq.attach_to_doc(doc, cases[:1], embed_fn=_embed_factory(1), reset=True)
    assert doc["chunks"][1]["gold_queries"] == ["How do I appeal?"]


def test_chunk_index_parsing():
    assert agq.chunk_index_from_id("statutes-70_chunk_0012", "statutes-70") == 12
    assert agq.chunk_index_from_id("wpam-2026-ch7_chunk_0003", "statutes-70") is None
    assert agq.chunk_index_from_id("7", "x") == 7
    assert agq.chunk_index_from_id("nonsense", "x") is None

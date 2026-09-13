"""embed.py: --embed-input plain|enriched and cache-prefix key threading."""

import json
from unittest.mock import patch

import pytest

from tools.ingestion import embed, load
from tools.ingestion.lib.aliases import compose_embed_input
from tools.ingestion.tests.fake_s3 import FakeS3

WB = "work"


def _doc():
    return {
        "doc_id": "gov_publications-pb060",
        "title": "Guide for Property Owners",
        "summary": "sum",
        "chunks": [
            {
                "text": "Chunk zero text",
                "metadata": {"heading": "Assessment"},
                "aliases": {
                    "questions": ["How is my house valued?"],
                    "aliases": ["assessor = the person who values your house"],
                },
                "gold_queries": ["How are my property taxes determined?"],
            },
            {"text": "Chunk one text", "metadata": {}},
        ],
    }


def test_enriched_mode_embeds_composed_string_and_records_provenance():
    doc = _doc()
    calls = []

    def fake_embed(text, model_id, dimension):
        calls.append(text)
        return [0.1, 0.2]

    with patch.object(embed, "embed_text", side_effect=fake_embed):
        out = embed.embed_chunks(doc, "titan", 2, embed_input="enriched")

    expected0 = compose_embed_input(
        doc, doc["chunks"][0], doc["chunks"][0]["aliases"], doc["chunks"][0]["gold_queries"]
    )
    assert calls[0] == expected0
    assert calls[0].startswith(
        "Guide for Property Owners > Assessment\n"
        "Q: How are my property taxes determined?\n"
        "Q: How is my house valued?\n"
        "assessor = the person who values your house\n\nChunk zero text"
    )
    # Chunk without aliases still gets the header, and stored text is untouched.
    assert calls[1] == "Guide for Property Owners\n\nChunk one text"
    assert out["chunks"][0]["text"] == "Chunk zero text"
    assert out["embed_input_mode"] == "enriched"
    assert out["chunks"][0]["embed_input_chars"] == len(expected0)
    # Doc-level embedding unchanged: title + summary + chunk0 plain text.
    assert calls[2] == "Guide for Property Owners sum Chunk zero text"


def test_plain_mode_embeds_raw_text():
    doc = _doc()
    calls = []
    with patch.object(embed, "embed_text", side_effect=lambda t, m, d: calls.append(t) or [0.0]):
        out = embed.embed_chunks(doc, "titan", 1)  # default plain
    assert calls[:2] == ["Chunk zero text", "Chunk one text"]
    assert out["embed_input_mode"] == "plain"
    assert out["chunks"][1]["embed_input_chars"] == len("Chunk one text")


def test_invalid_mode_rejected():
    with pytest.raises(ValueError):
        embed.embed_chunks(_doc(), "titan", 1, embed_input="fancy")


def test_embed_keys_and_listing_honor_prefix():
    fake = FakeS3()
    fake.put_bytes(WB, "staging/extracted/a.json", json.dumps({"doc_id": "a"}).encode())
    fake.put_bytes(WB, "staging/extracted/manifest.json", b"[]")
    fake.put_bytes(WB, "extracted/prod.json", json.dumps({"doc_id": "prod"}).encode())
    fake.put_bytes(WB, "staging/embedded/b.json", b"{}")
    fake.put_bytes(WB, "embedded/prodb.json", b"{}")
    with patch.object(embed, "s3", fake):
        docs = embed.load_extracted_docs(WB, "staging/")
        assert [d["doc_id"] for d in docs] == ["a"]
        assert embed.list_already_embedded(WB, "staging/") == {"b"}
        assert embed.list_already_embedded(WB) == {"prodb"}
    assert embed.embedded_key("x", "staging/") == "staging/embedded/x.json"
    assert embed.extracted_key("x", "staging/") == "staging/extracted/x.json"


def test_load_embedded_docs_honors_prefix_and_extra_chunk_fields_pass_through():
    fake = FakeS3()
    doc = {
        "doc_id": "a",
        "embed_input_mode": "enriched",
        "chunks": [
            {
                "text": "t",
                "metadata": {},
                "embedding": [0.5],
                "aliases": {"questions": ["q"], "aliases": []},
                "gold_queries": ["g"],
                "embed_input_chars": 12,
            }
        ],
    }
    fake.put_bytes(WB, "staging/embedded/a.json", json.dumps(doc).encode())
    fake.put_bytes(WB, "embedded/prod.json", json.dumps({"doc_id": "prod", "chunks": []}).encode())
    with patch.object(load, "s3", fake):
        docs = load.load_embedded_docs(WB, "staging/")
    assert [d["doc_id"] for d in docs] == ["a"]
    # Phase 8 picks up embeddings regardless of the extra fields.
    jobs = [
        (f"{d['doc_id']}_chunk_{i:04d}", c["embedding"])
        for d in docs
        for i, c in enumerate(d["chunks"])
        if c.get("embedding")
    ]
    assert jobs == [("a_chunk_0000", [0.5])]


def test_phase_9_reads_prefixed_extracted_case_law_set():
    fake = FakeS3()
    fake.put_bytes(WB, "staging/extracted/case-law-x.json", b"{}")
    fake.put_bytes(WB, "extracted/case-law-prod.json", b"{}")
    queries = []

    def fake_exec(client, graph_id, query, params=None):
        queries.append((query, params))
        if "MATCH (c:CaseLaw) RETURN" in query:
            return {"results": [{"id": "case-law-x"}, {"id": "case-law-stale"}]}
        return {"results": [{"deleted": 0}]}

    with patch.object(load, "s3", fake), patch.object(load, "execute_query", side_effect=fake_exec):
        load.phase_9_cleanup(None, "g", WB, None, cache_prefix="staging/")
    purge = [p for q, p in queries if p and "ids" in p]
    assert purge == [{"ids": ["case-law-stale"]}]  # prod-only doc NOT considered live


def test_enriched_mode_embeds_case_law_plain(monkeypatch):
    """Case law is excluded from enrichment by default: plain text is embedded
    and the doc records embed_input_mode == 'plain' even when 'enriched' is asked."""
    from tools.ingestion import embed as embed_mod

    seen = []
    monkeypatch.setattr(
        embed_mod, "embed_text", lambda text, model_id, dimension: seen.append(text) or [0.0]
    )
    doc = {
        "doc_id": "case-law-1-wis-2d-1",
        "doc_type": "case_law",
        "title": "Smith v. Town",
        "chunks": [{"text": "Holding text here.", "aliases": {"questions": ["Q?"], "aliases": []}}],
    }
    out = embed_mod.embed_chunks(doc, "m", 4, embed_input="enriched")
    assert seen[0] == "Holding text here."
    assert out["embed_input_mode"] == "plain"
    # Opt-in: an empty exclusion set enriches case law too.
    seen.clear()
    embed_mod.embed_chunks(
        dict(doc, chunks=[dict(doc["chunks"][0])]), "m", 4, "enriched", frozenset()
    )
    assert seen[0] != "Holding text here." and "Holding text here." in seen[0]


def test_enrich_policy_excludes_iaao_old_wpam_and_index_chunks(monkeypatch):
    from tools.ingestion import embed as embed_mod

    docs = [
        {"doc_id": "wpam-wisconsin-property-assessment-manual-2026", "doc_type": "manual"},
        {"doc_id": "wpam-wisconsin-property-assessment-manual-2019", "doc_type": "manual"},
        {"doc_id": "iaao-standard-on-professional-development", "doc_type": "iaao_standard"},
    ]
    superseded = embed_mod.superseded_wpam_doc_ids(docs)
    assert superseded == {"wpam-wisconsin-property-assessment-manual-2019"}
    policy = embed_mod.EnrichPolicy(exclude_doc_ids=superseded)
    assert policy.doc_enriched(docs[0]) is True
    assert policy.doc_enriched(docs[1]) is False
    assert policy.doc_enriched(docs[2]) is False
    good = {"text": "x", "heading": "Chapter 18 Personal Property"}
    index = {"text": "x", "heading": "Chapter 21 Index of Legal Decisions"}
    toc = {"text": "x", "metadata": {"heading": "Table of Contents"}}
    assert policy.chunk_enriched(docs[0], good) is True
    assert policy.chunk_enriched(docs[0], index) is False
    assert policy.chunk_enriched(docs[0], toc) is False
    # chunk_embed_input honors the policy object directly.
    doc = dict(docs[0], title="WPAM 2026")
    assert embed_mod.chunk_embed_input(doc, index, "enriched", policy) == "x"
    assert (
        embed_mod.chunk_embed_input(
            doc, dict(good, aliases={"questions": ["Q?"], "aliases": []}), "enriched", policy
        )
        != "x"
    )


def test_enrich_policy_include_allowlist():
    from tools.ingestion import embed as embed_mod

    policy = embed_mod.EnrichPolicy(include_doc_types=["statute", "admin_rule"])
    assert policy.doc_enriched({"doc_id": "statutes-70", "doc_type": "statute"}) is True
    assert policy.doc_enriched({"doc_id": "admin_rules-tax-18", "doc_type": "admin_rule"}) is True
    assert policy.doc_enriched({"doc_id": "gov_publications-pb060", "doc_type": "guide"}) is False
    assert policy.doc_enriched({"doc_id": "wpam-x-2026", "doc_type": "assessment_manual"}) is False
    # exclusions still apply inside the allowlist
    policy2 = embed_mod.EnrichPolicy(
        include_doc_types=["statute"], exclude_doc_ids=frozenset({"statutes-70"})
    )
    assert policy2.doc_enriched({"doc_id": "statutes-70", "doc_type": "statute"}) is False

"""Ingestion metadata integrity (docs/tasks.md Task 64 remediation).

Covers the four mechanisms that stop extract-time metadata fixes from being
lost and stop deleted dedup losers from being resurrected:

  - load.load_embedded_docs overlays document-level fields from extracted/
    (never chunks/embeddings), skips ids in dedup/losers.json
  - embed --smart metadata-only refresh: identical chunk inputs → no Titan
    calls, fresh metadata + existing vectors; any chunk delta → full re-embed
  - load Phase 2 case-law title sink guard (three bad shapes)
  - load Phase 10 integrity assertion (doubled titles → non-zero exit)
  - ops/purge_dedup_losers.classify_groups (docket ∪ name, Jaccard-gated)
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from tools.ingestion import embed, load
from tools.ingestion.tests.fake_s3 import FakeS3

WB = "work"


def _put(fake: FakeS3, key: str, doc: dict) -> None:
    fake.put_bytes(WB, key, json.dumps(doc).encode())


def _embedded_case(doc_id="case-law-1-wis-2d-1", title="1 Wis. 2d 1, 1 Wis. 2d 1"):
    return {
        "doc_id": doc_id,
        "doc_type": "case_law",
        "title": title,
        "summary": "old summary",
        "citation": "1 Wis. 2d 1",
        "source_url": "https://www.courtlistener.com/opinion/123/smith-v-city-of-madison/",
        "embed_input_mode": "plain",
        "doc_embedding": [0.9, 0.9],
        "chunks": [
            {"text": "holding text", "metadata": {}, "embedding": [0.1, 0.2]},
            {"text": "analysis text", "metadata": {}, "embedding": [0.3, 0.4]},
        ],
    }


# --------------------------------------------------------------------------- #
# 1. extracted/ → embedded/ metadata overlay in load_embedded_docs
# --------------------------------------------------------------------------- #
def test_overlay_applies_doc_metadata_but_never_chunks_or_embeddings():
    fake = FakeS3()
    emb = _embedded_case()
    _put(fake, "embedded/case-law-1-wis-2d-1.json", emb)
    extracted = {
        "doc_id": "case-law-1-wis-2d-1",
        "doc_type": "case_law",
        "title": "Smith v. City of Madison, 1 Wis. 2d 1",
        "case_name": "Smith v. City of Madison",
        "summary": "new summary",
        "citation": "1 Wis. 2d 1",
        "source_url": "https://www.courtlistener.com/opinion/123/smith-v-city-of-madison/",
        "statute_refs": ["70.32"],
        "full_text": "should never be copied",
        # A re-chunked extraction with DIFFERENT chunk texts and no embeddings:
        "chunks": [{"text": "re-chunked A", "metadata": {}}, {"text": "B"}, {"text": "C"}],
    }
    _put(fake, "extracted/case-law-1-wis-2d-1.json", extracted)

    with patch.object(load, "s3", fake):
        docs = load.load_embedded_docs(WB)

    assert len(docs) == 1
    d = docs[0]
    assert d["title"] == "Smith v. City of Madison, 1 Wis. 2d 1"
    assert d["summary"] == "new summary"
    assert d["case_name"] == "Smith v. City of Madison"
    assert d["statute_refs"] == ["70.32"]
    assert "full_text" not in d
    # Embedding data is the embedded copy's, untouched.
    assert [c["text"] for c in d["chunks"]] == ["holding text", "analysis text"]
    assert d["chunks"][0]["embedding"] == [0.1, 0.2]
    assert d["doc_embedding"] == [0.9, 0.9]
    assert d["embed_input_mode"] == "plain"
    assert "_metadata_overlay" not in d


def test_overlay_skipped_when_extracted_missing_and_when_disabled():
    fake = FakeS3()
    _put(fake, "embedded/a.json", {"doc_id": "a", "title": "old", "chunks": []})
    _put(fake, "embedded/b.json", {"doc_id": "b", "title": "old", "chunks": []})
    _put(fake, "extracted/b.json", {"doc_id": "b", "title": "new"})

    with patch.object(load, "s3", fake):
        docs = {d["doc_id"]: d for d in load.load_embedded_docs(WB)}
        assert docs["a"]["title"] == "old"  # no extracted copy → untouched
        assert docs["b"]["title"] == "new"

        off = {d["doc_id"]: d for d in load.load_embedded_docs(WB, metadata_overlay=False)}
        assert off["b"]["title"] == "old"  # --no-metadata-overlay escape hatch


def test_overlay_honors_cache_prefix():
    fake = FakeS3()
    _put(fake, "staging/embedded/a.json", {"doc_id": "a", "title": "old", "chunks": []})
    _put(fake, "staging/extracted/a.json", {"doc_id": "a", "title": "staged"})
    _put(fake, "extracted/a.json", {"doc_id": "a", "title": "PROD — must not leak"})
    with patch.object(load, "s3", fake):
        docs = load.load_embedded_docs(WB, "staging/")
    assert docs[0]["title"] == "staged"


def test_overlay_helper_reports_title_change_and_ignores_embedding_keys():
    emb = {"title": "t1", "chunks": ["keep"], "doc_embedding": [1.0]}
    assert load.overlay_extracted_metadata(emb, None) is False
    assert load.overlay_extracted_metadata(emb, {"summary": "s"}) is False
    assert emb["summary"] == "s"
    changed = load.overlay_extracted_metadata(
        emb, {"title": "t2", "chunks": ["NO"], "doc_embedding": [0.0]}
    )
    assert changed is True
    assert emb["title"] == "t2"
    assert emb["chunks"] == ["keep"] and emb["doc_embedding"] == [1.0]


# --------------------------------------------------------------------------- #
# 2. dedup/losers.json skip
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "payload",
    [
        {"losers": {"case-law-loser": {"winner": "case-law-winner", "reason": "docket_dedup"}}},
        {"losers": ["case-law-loser"]},
        ["case-law-loser"],
    ],
)
def test_losers_json_skips_listed_doc_ids(payload):
    fake = FakeS3()
    _put(fake, "embedded/case-law-loser.json", {"doc_id": "case-law-loser", "chunks": []})
    _put(fake, "embedded/case-law-winner.json", {"doc_id": "case-law-winner", "chunks": []})
    _put(fake, "dedup/losers.json", payload)
    with patch.object(load, "s3", fake):
        assert load.load_dedup_losers(WB) == {"case-law-loser"}
        docs = [d["doc_id"] for d in load.load_embedded_docs(WB)]
        assert docs == ["case-law-winner"]
        # escape hatch
        both = sorted(d["doc_id"] for d in load.load_embedded_docs(WB, skip_losers=False))
        assert both == ["case-law-loser", "case-law-winner"]


def test_losers_json_absent_is_a_noop():
    fake = FakeS3()
    _put(fake, "embedded/x.json", {"doc_id": "x", "chunks": []})
    with patch.object(load, "s3", fake):
        assert load.load_dedup_losers(WB) == set()
        assert [d["doc_id"] for d in load.load_embedded_docs(WB)] == ["x"]


def test_phase_9_deletes_recorded_losers_of_any_label():
    fake = FakeS3()
    _put(fake, "dedup/losers.json", {"losers": {"statutes-document-73": {"reason": "ghost"}}})
    queries = []

    def fake_exec(client, graph_id, query, params=None):
        queries.append((query, params))
        if "MATCH (c:CaseLaw) RETURN" in query:
            return {"results": []}
        return {"results": [{"deleted": 1}]}

    with patch.object(load, "s3", fake), patch.object(load, "execute_query", side_effect=fake_exec):
        load.phase_9_cleanup(None, "g", WB, None)
    loser_q = [(q, p) for q, p in queries if p and p.get("ids") == ["statutes-document-73"]]
    assert len(loser_q) == 1
    assert "MATCH (d {id: lid})" in loser_q[0][0]  # label-agnostic
    assert "DETACH DELETE ch, d" in loser_q[0][0]


# --------------------------------------------------------------------------- #
# 3. embed --smart metadata-only refresh
# --------------------------------------------------------------------------- #
def _extracted_from(embedded: dict, **overrides) -> dict:
    """The extracted/ copy: same chunk texts, no embeddings, fresh metadata."""
    doc = {k: v for k, v in embedded.items() if k not in ("doc_embedding", "embed_input_mode")}
    doc["chunks"] = [{k: v for k, v in c.items() if k != "embedding"} for c in embedded["chunks"]]
    doc.update(overrides)
    return doc


def test_metadata_refresh_reuses_vectors_when_chunk_inputs_identical():
    emb = _embedded_case()
    ext = _extracted_from(emb, title="Smith v. City of Madison, 1 Wis. 2d 1", summary="fresh")

    fake = FakeS3()
    _put(fake, "embedded/case-law-1-wis-2d-1.json", emb)
    calls = []
    with (
        patch.object(embed, "s3", fake),
        patch.object(embed, "embed_text", side_effect=lambda *a: calls.append(a) or [0.0]),
    ):
        remaining, refreshed = embed.refresh_metadata_only(WB, "", [ext])

    assert calls == []  # no Titan calls
    assert refreshed == 1 and remaining == []
    written = json.loads(fake.objects[(WB, "embedded/case-law-1-wis-2d-1.json")])
    assert written["title"] == "Smith v. City of Madison, 1 Wis. 2d 1"
    assert written["summary"] == "fresh"
    assert [c["embedding"] for c in written["chunks"]] == [[0.1, 0.2], [0.3, 0.4]]
    assert written["doc_embedding"] == [0.9, 0.9]
    assert written["embed_input_mode"] == "plain"


def test_metadata_refresh_falls_through_when_a_chunk_text_differs():
    emb = _embedded_case()
    ext = _extracted_from(emb, title="new title")
    ext["chunks"][1]["text"] = "analysis text (amended)"

    fake = FakeS3()
    _put(fake, "embedded/case-law-1-wis-2d-1.json", emb)
    with patch.object(embed, "s3", fake):
        remaining, refreshed = embed.refresh_metadata_only(WB, "", [ext])
    assert refreshed == 0
    assert remaining == [ext]
    assert fake.puts == []  # nothing rewritten; caller re-embeds


def test_metadata_refresh_falls_through_on_chunk_count_mode_or_missing_embedded():
    emb = _embedded_case()
    # chunk count differs
    ext = _extracted_from(emb)
    ext["chunks"].append({"text": "extra"})
    assert embed.try_metadata_refresh(ext, emb) is None
    # no embedded copy at all
    assert embed.try_metadata_refresh(_extracted_from(emb), None) is None
    # embedded copy lacks an embedding for a chunk
    broken = _embedded_case()
    del broken["chunks"][0]["embedding"]
    assert embed.try_metadata_refresh(_extracted_from(emb), broken) is None
    # effective mode changed: enriched now requested for a doc type that IS enriched
    stat_emb = dict(emb, doc_id="statutes-70", doc_type="statute", embed_input_mode="plain")
    assert embed.try_metadata_refresh(_extracted_from(stat_emb), stat_emb, "enriched") is None
    # ...but case law is embedded plain even under enriched, so it still refreshes
    assert embed.try_metadata_refresh(_extracted_from(emb), emb, "enriched") is not None


def test_metadata_refresh_enriched_mode_detects_alias_change():
    """Under enriched input an alias edit moves the vector even though chunk
    text is identical — must NOT be treated as metadata-only."""
    emb = _embedded_case()
    emb.update(doc_id="statutes-70", doc_type="statute", embed_input_mode="enriched")
    emb["chunks"][0]["aliases"] = {"questions": ["Q old"], "aliases": []}
    same = _extracted_from(emb)
    assert embed.try_metadata_refresh(same, emb, "enriched") is not None
    changed = _extracted_from(emb)
    changed["chunks"][0]["aliases"] = {"questions": ["Q new"], "aliases": []}
    assert embed.try_metadata_refresh(changed, emb, "enriched") is None


def test_chunkless_case_law_stub_refreshes_metadata():
    emb = {"doc_id": "case-law-stub", "doc_type": "case_law", "title": "old", "chunks": []}
    ext = {
        "doc_id": "case-law-stub",
        "doc_type": "case_law",
        "title": "Named, 1 Wis. 2d 1",
        "chunks": [],
    }
    out = embed.try_metadata_refresh(ext, emb)
    assert out is not None and out["title"] == "Named, 1 Wis. 2d 1"


# --------------------------------------------------------------------------- #
# 4. Phase 2 case-law title sink guard
# --------------------------------------------------------------------------- #
CL_URL = "https://www.courtlistener.com/opinion/123/smith-v-city-of-madison/"


@pytest.mark.parametrize(
    "title",
    ["1 Wis. 2d 1", "1 Wis. 2d 1, 1 Wis. 2d 1", "", "1 Wis. 2d 1 (garbage)"],
    ids=["bare", "doubled", "empty", "starts-with-digit"],
)
def test_title_guard_names_bad_shapes_from_case_name(title):
    doc = {
        "doc_type": "case_law",
        "title": title,
        "citation": "1 Wis. 2d 1",
        "case_name": "Smith v. City of Madison",
        "source_url": "",
    }
    assert load.repair_case_law_title(doc) == ("Smith v. City of Madison, 1 Wis. 2d 1", "named")


def test_title_guard_falls_back_to_courtlistener_slug():
    doc = {
        "doc_type": "case_law",
        "title": "1 Wis. 2d 1, 1 Wis. 2d 1",
        "citation": "1 Wis. 2d 1",
        "source_url": CL_URL,
    }
    assert load.repair_case_law_title(doc) == ("Smith v. City of Madison, 1 Wis. 2d 1", "named")


def test_title_guard_collapses_doubled_when_no_name_and_leaves_bare_alone():
    legis = "https://docs.legis.wisconsin.gov/document/courts/1%20Wis.%202d%201"
    doubled = {
        "doc_type": "case_law",
        "title": "1 Wis. 2d 1, 1 Wis. 2d 1",
        "citation": "1 Wis. 2d 1",
        "source_url": legis,
    }
    assert load.repair_case_law_title(doubled) == ("1 Wis. 2d 1", "collapsed")
    bare = dict(doubled, title="1 Wis. 2d 1")
    assert load.repair_case_law_title(bare) == ("1 Wis. 2d 1", None)  # informational only
    good = dict(doubled, title="Smith v. Jones, 1 Wis. 2d 1")
    assert load.repair_case_law_title(good) == ("Smith v. Jones, 1 Wis. 2d 1", None)
    numeric_name = dict(
        doubled, title="123 Main LLC v. City, 1 Wis. 2d 1", case_name="123 Main LLC v. City"
    )
    assert load.repair_case_law_title(numeric_name)[1] is None


def test_phase_2_writes_repaired_title_to_graph():
    docs = [
        {
            "doc_id": "case-law-1-wis-2d-1",
            "doc_type": "case_law",
            "citation": "1 Wis. 2d 1",
            "title": "1 Wis. 2d 1, 1 Wis. 2d 1",
            "source_url": CL_URL,
            "framework_id": "FW-CASE-LAW",
        },
        {
            "doc_id": "gov_publications-x",
            "doc_type": "guide",
            "title": "1 Guide",
            "framework_id": "FW-GOV-PUBS",
        },
    ]
    config = {
        "doc_types": {"case_law": "CaseLaw", "guide": "Guide"},
        "frameworks": [{"id": "FW-CASE-LAW", "authority_level": 3}],
    }
    params = []
    with patch.object(load, "execute_query", side_effect=lambda c, g, q, p=None: params.append(p)):
        load.phase_2_document_nodes(None, "g", docs, config)
    titles = {p["id"]: p["title"] for p in params if p and "title" in p}
    assert titles["case-law-1-wis-2d-1"] == "Smith v. City of Madison, 1 Wis. 2d 1"
    assert titles["gov_publications-x"] == "1 Guide"  # guard is case-law only


# --------------------------------------------------------------------------- #
# 5. Phase 10 integrity checks
# --------------------------------------------------------------------------- #
def _exec_with(doubled: int, bare: int, orphans: int):
    def fake_exec(client, graph_id, query, params=None):
        if "c.citation + ', ' + c.citation" in query:
            return {"results": [{"n": doubled}]}
        if "c.title = c.citation" in query:
            return {"results": [{"n": bare}]}
        if "EXTRACTED_FROM" in query:
            return {"results": [{"n": orphans}]}
        raise AssertionError(query)

    return fake_exec


def test_phase_10_passes_and_reports_counts():
    with patch.object(load, "execute_query", side_effect=_exec_with(0, 115, 0)):
        stats = load.phase_10_integrity_checks(None, "g")
    assert stats == {"doubled_titles": 0, "bare_citation_titles": 115, "orphan_chunks": 0}


def test_phase_10_fails_on_doubled_titles_only():
    with patch.object(load, "execute_query", side_effect=_exec_with(2, 0, 0)):
        with pytest.raises(load.IntegrityCheckFailed):
            load.phase_10_integrity_checks(None, "g")
    # orphans are logged, not fatal
    with patch.object(load, "execute_query", side_effect=_exec_with(0, 0, 7)):
        assert load.phase_10_integrity_checks(None, "g")["orphan_chunks"] == 7


# --------------------------------------------------------------------------- #
# 6. ops/purge_dedup_losers grouping (pure, no AWS)
# --------------------------------------------------------------------------- #
def test_classify_groups_confident_review_and_corruption():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "ops" / "purge_dedup_losers.py"
    spec = importlib.util.spec_from_file_location("purge_dedup_losers", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    body = " ".join(f"word{i}" for i in range(200))
    other = " ".join(f"other{i}" for i in range(200))
    legis = "https://docs.legis.wisconsin.gov/document/courts/"
    nodes = [
        # same docket + same text under two reporters: CL winner, legis loser
        {
            "id": "case-law-2023-wi-8",
            "title": "Lowe's v. Delavan, 2023 WI 8",
            "url": "https://www.courtlistener.com/opinion/1/lowes-v-delavan/",
        },
        {"id": "case-law-379-wis-2d-141", "title": "379 Wis. 2d 141", "url": legis + "379"},
        # same docket, divergent text (appeals vs supreme) → review
        {"id": "case-law-400-wis-2d-1", "title": "Tetra Tech v. DOR, 400 Wis. 2d 1", "url": legis},
        {"id": "case-law-401-wis-2d-1", "title": "Tetra Tech v. DOR, 401 Wis. 2d 1", "url": legis},
        # identical text, two distinct names → corruption suspect
        {"id": "case-law-1-wis-2d-1", "title": "Birge v. Simplicity, 1 Wis. 2d 1", "url": legis},
        {"id": "case-law-2-wis-2d-2", "title": "Kaul v. Legislature, 2 Wis. 2d 2", "url": legis},
    ]
    texts = {
        "case-law-2023-wi-8": "Appeal No. 2019AP1987 " + body,
        "case-law-379-wis-2d-141": "No. 2019AP1987 " + body,
        "case-law-400-wis-2d-1": "No. 2020AP100 " + body,
        "case-law-401-wis-2d-1": "No. 2020AP100 " + other,
        "case-law-1-wis-2d-1": "No. 2024AP567 " + other,
        "case-law-2-wis-2d-2": "No. 2024AP567 " + other,
    }
    confident, corruption, review = mod.classify_groups(nodes, texts, 0.6, frozenset())
    assert [(r["winner"], r["losers"]) for r in confident] == [
        ("case-law-2023-wi-8", ["case-law-379-wis-2d-141"])
    ]
    assert confident[0]["cross_host"] is True and confident[0]["docket"] == "2019AP1987"
    assert len(review) == 1 and review[0]["docket"] == "2020AP100"
    assert len(corruption) == 1 and corruption[0]["docket"] == "2024AP567"

    # Task 49 hazard: once the corrupt node's title goes bare, the name gate no
    # longer fires and reporter priority would crown it. Forced losers can
    # never win.
    bare = [dict(n) for n in nodes]
    bare[5]["title"] = "2 Wis. 2d 2"  # the corrupt node, bare-titled
    bare[4]["title"] = "1 Wis. 2d 1"
    conf2, corr2, _ = mod.classify_groups(bare, texts, 0.6, frozenset())
    naive = next(r for r in conf2 if r["docket"] == "2024AP567")
    # Same reporter tier on both → stable sort keeps list order → node 4 wins naively.
    assert naive["winner"] == "case-law-1-wis-2d-1"
    conf3, _, _ = mod.classify_groups(bare, texts, 0.6, frozenset({"case-law-1-wis-2d-1"}))
    forced = next(r for r in conf3 if r["docket"] == "2024AP567")
    assert forced["winner"] == "case-law-2-wis-2d-2"
    assert forced["losers"] == ["case-law-1-wis-2d-1"]
    assert corr2 == []
    assert mod.FORCED_LOSERS >= {"case-law-414-wis-2d-633", "case-law-395-wis-2d-351"}

    merged = mod.merge_losers({"losers": {"old": {"reason": "manual"}}}, {"new": {"reason": "x"}})
    assert set(merged["losers"]) == {"old", "new"}
    assert mod.cache_keys_for("d", "staging/") == [
        "staging/extracted/d.json",
        "staging/embedded/d.json",
        "staging/aliases/d.json",
    ]

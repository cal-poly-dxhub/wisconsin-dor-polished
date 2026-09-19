"""extract.py: --aliases-only backfill, classification fallback, cache-prefix keys,
and the policy gate that keeps alias generation to the doc types the embed uses."""

import json
from unittest.mock import patch

from tools.ingestion import extract
from tools.ingestion.lib import aliases as al
from tools.ingestion.tests.fake_s3 import FakeS3

WB = "work"
DOC_ID = "news_pages-test-doc"

# Production-shaped alias_enrichment config (see ingest_config.yaml).
CFG = {
    "alias_enrichment": {
        "enabled": True,
        "include_doc_types": ["statute", "admin_rule", "advisory"],
        "exclude_doc_types": ["case_law", "iaao_standard", "uspap_standard"],
        "wpam_latest_only": True,
        "exclude_heading_patterns": ["table of contents"],
    }
}


def _doc(n_long=2, n_short=1, doc_id=DOC_ID, doc_type="advisory"):
    chunks = [
        {
            "text": f"Chunk {i} " + "long content about board of review " * 12,
            "metadata": {"heading": f"H{i}"},
        }
        for i in range(n_long)
    ]
    chunks += [{"text": "tiny", "metadata": {}} for _ in range(n_short)]
    return {"doc_id": doc_id, "title": "Test Doc", "doc_type": doc_type, "chunks": chunks}


def _fake_generate(chunk_text, doc_title, heading, model_id, examples=None, **_):
    return {
        "questions": [f"Question about {heading}?"],
        "aliases": ["board of review = local appeal board", "not present = dropped"],
        "usage": {"input_tokens": 500, "output_tokens": 60},
    }


def test_key_helpers_honor_prefix():
    assert extract.classified_key("d", "staging/") == "staging/classified/d.json"
    assert extract.extracted_key("d", "staging/") == "staging/extracted/d.json"
    assert extract.aliases_key("d", "staging/") == "staging/aliases/d.json"
    assert extract.manifest_key("staging/") == "staging/extracted/manifest.json"
    assert extract.extracted_key("d") == "extracted/d.json"


def test_aliases_only_seeds_from_production_and_writes_prefixed():
    fake = FakeS3()
    fake.put_bytes(WB, f"extracted/{DOC_ID}.json", json.dumps(_doc()).encode())
    ctx = extract.AliasContext(enabled=True, workers=2, cache_prefix="staging/")

    with (
        patch.object(extract, "s3", fake),
        patch.object(al, "generate_aliases", side_effect=_fake_generate) as gen,
    ):
        doc = extract.backfill_aliases_for_doc(DOC_ID, WB, ctx)

    assert gen.call_count == 2  # short chunk skipped
    # Prefixed extracted written; production untouched.
    assert f"staging/extracted/{DOC_ID}.json" in fake.keys(WB)
    assert (WB, f"extracted/{DOC_ID}.json") not in fake.puts
    written = json.loads(fake.objects[(WB, f"staging/extracted/{DOC_ID}.json")])
    long_chunks = [c for c in written["chunks"] if len(c["text"]) >= 200]
    assert all("aliases" in c for c in long_chunks)
    assert "aliases" not in written["chunks"][-1]
    # Ungrounded alias filtered, grounded one kept; text unchanged.
    assert long_chunks[0]["aliases"]["aliases"] == ["board of review = local appeal board"]
    assert long_chunks[0]["aliases"]["questions"] == ["Question about H0?"]
    assert long_chunks[0]["text"] == doc["chunks"][0]["text"]
    # Alias cache keyed by content hash under the prefix.
    cache = json.loads(fake.objects[(WB, f"staging/aliases/{DOC_ID}.json")])
    assert set(cache) == {al.alias_cache_key(c["text"]) for c in long_chunks}
    entry = cache[al.alias_cache_key(long_chunks[0]["text"])]
    assert entry["model"] == al.DEFAULT_ALIAS_MODEL
    assert entry["prompt_version"] == al.PROMPT_VERSION
    assert entry["usage"] == {"input_tokens": 500, "output_tokens": 60}
    assert ctx.totals["generated"] == 2 and ctx.totals["skipped_short"] == 1
    assert ctx.totals["input_tokens"] == 1000


def test_aliases_only_second_run_uses_cache_and_skips_rewrite():
    fake = FakeS3()
    fake.put_bytes(WB, f"extracted/{DOC_ID}.json", json.dumps(_doc()).encode())
    ctx = extract.AliasContext(enabled=True, cache_prefix="staging/")
    with (
        patch.object(extract, "s3", fake),
        patch.object(al, "generate_aliases", side_effect=_fake_generate),
    ):
        extract.backfill_aliases_for_doc(DOC_ID, WB, ctx)
    n_puts = len(fake.puts)

    ctx2 = extract.AliasContext(enabled=True, cache_prefix="staging/")
    with patch.object(extract, "s3", fake), patch.object(al, "generate_aliases") as gen:
        extract.backfill_aliases_for_doc(DOC_ID, WB, ctx2)
    assert gen.call_count == 0
    assert len(fake.puts) == n_puts  # already enriched → no rewrite

    # --force rewrites (bumps LastModified) but still hits the cache.
    before = fake.modified[(WB, f"staging/extracted/{DOC_ID}.json")]
    with patch.object(extract, "s3", fake), patch.object(al, "generate_aliases") as gen:
        extract.backfill_aliases_for_doc(DOC_ID, WB, ctx2, force=True)
    assert gen.call_count == 0
    assert fake.modified[(WB, f"staging/extracted/{DOC_ID}.json")] > before
    assert ctx2.totals["cached"] == 2


def test_rechunked_text_reuses_cache_by_content():
    fake = FakeS3()
    doc = _doc(n_long=2, n_short=0)
    fake.put_bytes(WB, f"extracted/{DOC_ID}.json", json.dumps(doc).encode())
    ctx = extract.AliasContext(enabled=True, cache_prefix="")
    with (
        patch.object(extract, "s3", fake),
        patch.object(al, "generate_aliases", side_effect=_fake_generate),
    ):
        extract.backfill_aliases_for_doc(DOC_ID, WB, ctx)

    # Re-chunk: swap order + add one new chunk. Only the new one is generated.
    doc2 = _doc(n_long=2, n_short=0)
    doc2["chunks"] = [
        doc2["chunks"][1],
        doc2["chunks"][0],
        {"text": "brand new " * 30, "metadata": {"heading": "N"}},
    ]
    with (
        patch.object(extract, "s3", fake),
        patch.object(al, "generate_aliases", side_effect=_fake_generate) as gen,
    ):
        stats = extract.enrich_chunks_with_aliases(doc2, WB, ctx)
    assert gen.call_count == 1
    assert stats == {
        "cached": 2,
        "generated": 1,
        "failed": 0,
        "skipped_short": 0,
        "skipped_policy": 0,
        "input_tokens": 500,
        "output_tokens": 60,
    }


def test_generation_failure_counts_and_leaves_chunk_without_aliases():
    fake = FakeS3()
    doc = _doc(n_long=1, n_short=0)
    ctx = extract.AliasContext(enabled=True)
    with (
        patch.object(extract, "s3", fake),
        patch.object(
            al,
            "generate_aliases",
            return_value={
                "questions": [],
                "aliases": [],
                "usage": {},
                "error": "ThrottlingException",
            },
        ),
    ):
        stats = extract.enrich_chunks_with_aliases(doc, WB, ctx)
    assert stats["failed"] == 1 and stats["generated"] == 0
    assert "aliases" not in doc["chunks"][0]
    assert fake.keys(WB, "aliases/") == []  # nothing cached


def test_classification_fallback_copies_forward():
    fake = FakeS3()
    cls = {"doc_type": "guide", "summary": "s"}
    fake.put_bytes(WB, "classified/d.json", json.dumps(cls).encode())
    with patch.object(extract, "s3", fake):
        got = extract._load_cached_classification(WB, "d", "staging/", "")
    assert got == cls
    assert json.loads(fake.objects[(WB, "staging/classified/d.json")]) == cls

    # Disabled when fallback == prefix.
    fake2 = FakeS3()
    fake2.put_bytes(WB, "classified/d.json", json.dumps(cls).encode())
    with patch.object(extract, "s3", fake2):
        assert extract._load_cached_classification(WB, "d", "staging/", "staging/") is None
    assert "staging/classified/d.json" not in fake2.keys(WB)


def test_list_already_extracted_with_prefix_ignores_manifest_and_other_prefixes():
    fake = FakeS3()
    for k in [
        "extracted/a.json",
        "staging/extracted/b.json",
        "staging/extracted/manifest.json",
        "staging/extracted/c.json",
    ]:
        fake.put_bytes(WB, k, b"{}")
    with patch.object(extract, "s3", fake):
        assert extract.list_already_extracted(WB, "staging/") == {"b", "c"}
        assert extract.list_already_extracted(WB, "") == {"a"}


# --------------------------------------------------------------------------
# Policy gate: generation must match what embed.py will actually use
# --------------------------------------------------------------------------


def test_excluded_doc_type_costs_nothing_at_extract():
    """case_law is never enriched at embed → never generated at extract."""
    fake = FakeS3()
    doc = _doc(n_long=2, n_short=1, doc_id="case-law-state-v-smith", doc_type="case_law")
    ctx = extract.AliasContext.from_config(CFG, enabled=True, workers=2, cache_prefix="")

    with patch.object(extract, "s3", fake), patch.object(al, "generate_aliases") as gen:
        stats = extract.enrich_chunks_with_aliases(doc, WB, ctx)

    assert gen.call_count == 0
    assert stats["skipped_policy"] == 3
    assert stats["generated"] == 0 and stats["cached"] == 0 and stats["skipped_short"] == 0
    assert all("aliases" not in c for c in doc["chunks"])
    assert fake.keys(WB) == []  # the alias cache isn't even read
    assert ctx.totals["skipped_policy"] == 3 and ctx.totals["docs"] == 1


def test_doc_type_outside_include_allowlist_is_skipped():
    fake = FakeS3()
    guide = _doc(n_long=2, n_short=0, doc_id="gov_publications-pb060", doc_type="guide")
    ctx = extract.AliasContext.from_config(CFG, enabled=True, workers=2, cache_prefix="")
    with patch.object(extract, "s3", fake), patch.object(al, "generate_aliases") as gen:
        assert extract.enrich_chunks_with_aliases(guide, WB, ctx)["skipped_policy"] == 2
    assert gen.call_count == 0

    # ...while an allowlisted type still generates.
    statute = _doc(n_long=2, n_short=0, doc_id="statutes-70", doc_type="statute")
    with (
        patch.object(extract, "s3", fake),
        patch.object(al, "generate_aliases", side_effect=_fake_generate) as gen,
    ):
        stats = extract.enrich_chunks_with_aliases(statute, WB, ctx)
    assert gen.call_count == 2
    assert stats["generated"] == 2 and stats["skipped_policy"] == 0


def test_empty_include_doc_types_means_all_but_excluded():
    cfg = {"alias_enrichment": {"include_doc_types": [], "exclude_doc_types": ["case_law"]}}
    ctx = extract.AliasContext.from_config(cfg, enabled=True, workers=1, cache_prefix="")
    assert ctx.doc_allowed({"doc_id": "gov_publications-pb060", "doc_type": "guide"}) is True
    assert ctx.doc_allowed({"doc_id": "wpam-x-2026", "doc_type": "assessment_manual"}) is True
    assert ctx.doc_allowed({"doc_id": "case-law-x", "doc_type": "case_law"}) is False


def test_superseded_wpam_edition_is_skipped_but_latest_is_not():
    cfg = {"alias_enrichment": {"include_doc_types": [], "wpam_latest_only": True}}
    corpus = ["wpam-manual-2019", "wpam-manual-2026", "statutes-70"]
    ctx = extract.AliasContext.from_config(
        cfg, enabled=True, workers=1, cache_prefix="", corpus_doc_ids=corpus
    )
    fake = FakeS3()
    old = _doc(n_long=1, n_short=0, doc_id="wpam-manual-2019", doc_type="assessment_manual")
    new = _doc(n_long=1, n_short=0, doc_id="wpam-manual-2026", doc_type="assessment_manual")

    with patch.object(extract, "s3", fake), patch.object(al, "generate_aliases") as gen:
        assert extract.enrich_chunks_with_aliases(old, WB, ctx)["skipped_policy"] == 1
    assert gen.call_count == 0

    with (
        patch.object(extract, "s3", fake),
        patch.object(al, "generate_aliases", side_effect=_fake_generate) as gen,
    ):
        assert extract.enrich_chunks_with_aliases(new, WB, ctx)["generated"] == 1
    assert gen.call_count == 1

    # Without a corpus the edition rule can't be evaluated — doc types still are.
    ctx_no_corpus = extract.AliasContext.from_config(cfg, enabled=True, workers=1, cache_prefix="")
    assert ctx_no_corpus.doc_allowed(old) is True


def test_index_heading_chunk_is_skipped_inside_an_allowed_doc():
    fake = FakeS3()
    doc = _doc(n_long=2, n_short=0, doc_id="statutes-70", doc_type="statute")
    doc["chunks"][1]["metadata"]["heading"] = "Table of Contents"
    ctx = extract.AliasContext.from_config(CFG, enabled=True, workers=1, cache_prefix="")
    with (
        patch.object(extract, "s3", fake),
        patch.object(al, "generate_aliases", side_effect=_fake_generate) as gen,
    ):
        stats = extract.enrich_chunks_with_aliases(doc, WB, ctx)
    assert gen.call_count == 1
    assert stats["generated"] == 1 and stats["skipped_policy"] == 1
    assert "aliases" in doc["chunks"][0] and "aliases" not in doc["chunks"][1]


def test_backfill_skips_excluded_doc_without_rewriting_it():
    """--aliases-only must not touch (or re-date) a doc the embed won't enrich."""
    fake = FakeS3()
    doc = _doc(n_long=2, n_short=1, doc_id="case-law-x", doc_type="case_law")
    fake.put_bytes(WB, "extracted/case-law-x.json", json.dumps(doc).encode())
    ctx = extract.AliasContext.from_config(CFG, enabled=True, workers=1, cache_prefix="")

    with patch.object(extract, "s3", fake), patch.object(al, "generate_aliases") as gen:
        out = extract.backfill_aliases_for_doc("case-law-x", WB, ctx)

    assert gen.call_count == 0
    assert out["doc_id"] == "case-law-x"
    assert fake.puts == []  # no rewrite → no needless re-embed under --smart
    assert ctx.totals["skipped_policy"] == 3


def test_policy_none_keeps_pre_gate_behavior():
    """A directly constructed AliasContext (no policy) generates for everything."""
    fake = FakeS3()
    doc = _doc(n_long=1, n_short=0, doc_id="case-law-x", doc_type="case_law")
    ctx = extract.AliasContext(enabled=True)
    with (
        patch.object(extract, "s3", fake),
        patch.object(al, "generate_aliases", side_effect=_fake_generate) as gen,
    ):
        stats = extract.enrich_chunks_with_aliases(doc, WB, ctx)
    assert gen.call_count == 1 and stats["generated"] == 1 and stats["skipped_policy"] == 0


def test_log_totals_includes_skipped_policy(caplog):
    ctx = extract.AliasContext(enabled=True)
    ctx.add({"generated": 2, "input_tokens": 10, "output_tokens": 4})
    ctx.add({"skipped_policy": 7})
    with caplog.at_level("INFO"):
        ctx.log_totals()
    assert "skipped_policy=7" in caplog.text
    assert ctx.totals["docs"] == 2 and ctx.totals["generated"] == 2


def test_alias_context_from_config_flag_precedence():
    cfg = {
        "alias_enrichment": {
            "enabled": False,
            "alias_model": "m",
            "min_chunk_chars": 50,
            "workers": 2,
        }
    }
    ctx = extract.AliasContext.from_config(cfg, enabled=None, workers=3, cache_prefix="p/")
    assert (
        not ctx.enabled and ctx.model_id == "m" and ctx.min_chunk_chars == 50 and ctx.workers == 2
    )
    ctx = extract.AliasContext.from_config(cfg, enabled=True, workers=3, cache_prefix="p/")
    assert ctx.enabled and ctx.cache_prefix == "p/"
    ctx = extract.AliasContext.from_config({}, enabled=None, workers=3, cache_prefix="")
    assert not ctx.enabled and ctx.model_id == al.DEFAULT_ALIAS_MODEL and ctx.workers == 3

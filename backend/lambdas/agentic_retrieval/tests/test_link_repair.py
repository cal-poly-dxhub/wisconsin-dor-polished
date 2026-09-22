"""Tests for the deterministic citation-link repair (Task 62).

Covers the pure `repair_citation_links` rules, the streaming hook in
`stream_answer` (link-aware fragment flush + in-stream repair), and the
pre-persist `finalize_answer_links` pass.
"""

import json
from unittest.mock import MagicMock, patch

from loop.link_repair import (
    classify_link_label,
    has_open_link,
    repair_citation_links,
    resolve_section_page,
)

RETRIEVED = {
    "wpam-wisconsin-property-assessment-manual-2026",
    "statutes-70",
    "statutes-62",
    "admin_rules-tax-18",
    "gov_publications-2026-agricultural-assessment-guide",
    "case-law-405-wis-2d-616",
    "case-law-2025-wi-app-43",
}
CHUNKS = {
    "statutes-70": [
        {"doc_id": "statutes-70", "heading": "70.32 Real estate, how valued.", "start_page": 23},
        {"doc_id": "statutes-70", "heading": "70.47 Board of review.", "start_page": 56},
    ],
    "statutes-62": [
        {"doc_id": "statutes-62", "heading": "62.09 Officers.", "start_page": 9},
    ],
}


def _repair(answer, retrieved=RETRIEVED, chunks=CHUNKS, **kw):
    return repair_citation_links(answer, retrieved, chunks, **kw)


class TestRealExamples:
    def test_statute_section_linked_to_wpam_is_repointed_to_chapter(self):
        # Real case from gq-hire-assessor: § 61.19 (villages) linked to the WPAM
        # while statutes-61 was not retrieved. Ch. 61 IS in the corpus, and the
        # frontend resolves any doc:statutes-N link, so repoint (no page: no
        # retrieved ch. 61 chunk can vouch for one).
        answer = (
            "Villages appoint under "
            "[§ 61.19](doc:wpam-wisconsin-property-assessment-manual-2026#page=20) and ..."
        )
        out, stats = _repair(answer)
        assert out == "Villages appoint under [§ 61.19](doc:statutes-61) and ..."
        assert stats["repointed"] == 1 and stats["stripped"] == 0
        assert stats["changes"] == [
            {
                "kind": "repointed",
                "text": "§ 61.19",
                "from": "wpam-wisconsin-property-assessment-manual-2026",
                "to": "statutes-61",
            }
        ]

    def test_out_of_corpus_chapter_is_stripped_not_fabricated(self):
        # Real case: § 340.01(6m) (ch. 340 is not in the corpus) linked to ch. 70.
        answer = "A motor vehicle under [§ 340.01(6m)](doc:statutes-70#page=14) is exempt."
        out, stats = _repair(answer)
        assert out == "A motor vehicle under § 340.01(6m) is exempt."
        assert stats == {
            "repointed": 0,
            "stripped": 1,
            "paged": 0,
            "dual": 0,
            "changes": [
                {"kind": "stripped", "text": "§ 340.01(6m)", "from": "statutes-70", "to": None}
            ],
        }

    def test_rubric_regex_passes_after_repair(self):
        # gq-hire-assessor must_not_contain: "\[§\s*61\.19[^\]]*\]\(doc:statutes-62"
        import re

        answer = (
            "Cities: [§ 62.09](doc:statutes-62#page=9); "
            "villages: [§ 61.19](doc:statutes-62#page=9)."
        )
        out, _ = _repair(answer)
        assert re.search(r"\[§\s*61\.19[^\]]*\]\(doc:statutes-62", answer)
        assert not re.search(r"\[§\s*61\.19[^\]]*\]\(doc:statutes-62", out)
        assert "[§ 62.09](doc:statutes-62#page=9)" in out
        assert "[§ 61.19](doc:statutes-61)" in out


class TestStatuteRules:
    def test_wrong_chapter_repointed_with_page_from_retrieved_heading(self):
        out, stats = _repair("See [§ 70.32(2)(c)1g](doc:statutes-62#page=9).")
        assert out == "See [§ 70.32(2)(c)1g](doc:statutes-70#page=23)."
        assert stats["repointed"] == 1

    def test_page_resolved_from_chunk_text_when_no_heading_matches(self):
        chunks = {
            "statutes-70": [
                {
                    "doc_id": "statutes-70",
                    "heading": "70.11 Property exempted.",
                    "start_page": 5,
                    "text": "... as provided in s. 70.111 (1) ...",
                },
            ]
        }
        out, _ = _repair(
            "[§ 70.111](doc:wpam-wisconsin-property-assessment-manual-2026#page=3)", chunks=chunks
        )
        assert out == "[§ 70.111](doc:statutes-70#page=5)"

    def test_page_dropped_when_unresolvable(self):
        out, _ = _repair("[§ 70.995](doc:statutes-62#page=9)")
        assert out == "[§ 70.995](doc:statutes-70)"

    def test_wis_stat_prefix_and_s_dot_prefix(self):
        out, _ = _repair(
            "[Wis. Stat. § 70.47](doc:gov_publications-2026-agricultural-assessment-guide#page=4) "
            "and [s. 70.32(1)](doc:wpam-wisconsin-property-assessment-manual-2026#page=2)"
        )
        assert out == (
            "[Wis. Stat. § 70.47](doc:statutes-70#page=56) and "
            "[s. 70.32(1)](doc:statutes-70#page=23)"
        )

    def test_chapter_label_on_statute_target_is_repointed(self):
        out, _ = _repair("[ch. 70 Stats.](doc:statutes-62#page=1)")
        assert out == "[ch. 70 Stats.](doc:statutes-70)"

    def test_bare_number_on_statute_target_is_checked(self):
        out, _ = _repair("[70.32(2)](doc:statutes-62#page=9)")
        assert out == "[70.32(2)](doc:statutes-70#page=23)"

    def test_ambiguous_two_chapters_left_alone(self):
        answer = "[§§ 70.32 and 73.03](doc:statutes-70#page=23)"
        out, stats = _repair(answer)
        assert out == answer and stats["repointed"] == 0 and stats["stripped"] == 0

    def test_correct_link_is_noop(self):
        answer = "Under [§ 70.32(2)(c)1g](doc:statutes-70#page=24), agricultural land means..."
        out, stats = _repair(answer)
        assert out == answer
        assert stats == {"repointed": 0, "stripped": 0, "paged": 0, "dual": 0, "changes": []}

    def test_unretrieved_and_unknown_chapter_is_stripped(self):
        out, stats = _repair("[§ 61.19](doc:statutes-62)", known_statute_chapters=frozenset())
        assert out == "§ 61.19" and stats["stripped"] == 1


class TestAdminRules:
    def test_tax_rule_on_secondary_doc_repointed_to_admin_rules_doc(self):
        out, _ = _repair(
            "under [Tax 18.06(1)](doc:gov_publications-2026-agricultural-assessment-guide#page=4), "
            "an assessor"
        )
        assert out == "under [Tax 18.06(1)](doc:admin_rules-tax-18), an assessor"

    def test_tax_rule_on_wrong_admin_chapter_repointed(self):
        out, _ = _repair("[Tax 18.06](doc:admin_rules-tax-16#page=2)")
        assert out == "[Tax 18.06](doc:admin_rules-tax-18)"

    def test_tax_rule_not_retrieved_is_stripped(self):
        out, stats = _repair(
            "[Tax 12.10](doc:wpam-wisconsin-property-assessment-manual-2026#page=8)"
        )
        assert out == "Tax 12.10" and stats["stripped"] == 1

    def test_correct_admin_link_untouched(self):
        answer = "[Tax 18.06(1)](doc:admin_rules-tax-18#page=1)"
        assert _repair(answer)[0] == answer

    def test_tax_number_is_not_mistaken_for_statute_chapter_18(self):
        # "Tax 18.06" must never become doc:statutes-18.
        out, _ = _repair(
            "[Tax 18.06](doc:admin_rules-tax-18)", known_statute_chapters=frozenset({"18"})
        )
        assert "statutes-18" not in out


class TestClassifyLinkLabel:
    """A label that IS the section reference vs. a claim that merely names it."""

    def test_citation_labels(self):
        for text, chapter in [
            ("§ 70.32(2)(c)1g", "70"),
            ("s. 74.37", "74"),
            ("Wis. Stat. § 73.03", "73"),
            ("§ 70.47 Board of review", "70"),
            ("§ 70.11(4m)", "70"),
            ("70.32(2)", "70"),
            ("ch. 70 Stats.", "70"),
            ("Tax 18.06(1)", "18"),
            ("under § 74.37, Wis. Stats.", "74"),
        ]:
            assert classify_link_label(text, chapter) == "citation", text

    def test_claim_labels(self):
        for text, chapter in [
            (
                "a partially constructed building cannot, as a matter of law, be tax exempt "
                "under § 70.11(4m)",
                "70",
            ),
            ("the exemption in § 70.11(4m) reaches only completed structures", "70"),
            ("must classify land under Tax 18.06(1)", "18"),
            ("70.995 State assessment of manufacturing property standards", "70"),
        ]:
            assert classify_link_label(text, chapter) == "claim", text


class TestDualLinks:
    """A claim-length label on a retrieved document keeps its target and gains
    the statute/rule as a second target: `#page=p&ref=<id>#page=n`."""

    HOLDING = (
        "a partially constructed building cannot, as a matter of law, be tax exempt "
        "under § 70.11(4m)"
    )
    HOLDING_2 = "the exemption in § 70.11(4m) reaches only completed structures"
    INDEX = {"70": {"70.11": 5}}

    def test_case_law_holding_gets_statute_as_second_target(self):
        # Real case (prod query b1a047a6): the holding was repointed to the
        # statute and the case link was lost. Now both survive.
        out, stats = _repair(
            f"[{self.HOLDING}](doc:case-law-2025-wi-app-43#page=1)", section_pages=self.INDEX
        )
        assert out == (
            f"[{self.HOLDING}](doc:case-law-2025-wi-app-43#page=1&ref=statutes-70#page=5)"
        )
        assert stats["dual"] == 1 and stats["repointed"] == 0 and stats["stripped"] == 0
        assert stats["changes"] == [
            {
                "kind": "dual",
                "text": self.HOLDING,
                "from": "case-law-2025-wi-app-43",
                "to": "case-law-2025-wi-app-43#page=1&ref=statutes-70#page=5",
                "ref": "statutes-70",
            }
        ]

    def test_second_holding_label_also_dual(self):
        out, stats = _repair(
            f"The court added that [{self.HOLDING_2}](doc:case-law-2025-wi-app-43#page=2).",
            section_pages=self.INDEX,
        )
        assert "(doc:case-law-2025-wi-app-43#page=2&ref=statutes-70#page=5)" in out
        assert stats["dual"] == 1

    def test_primary_without_page_uses_hash_ref(self):
        out, _ = _repair(
            f"[{self.HOLDING}](doc:case-law-2025-wi-app-43)", section_pages=self.INDEX
        )
        assert out == f"[{self.HOLDING}](doc:case-law-2025-wi-app-43#ref=statutes-70#page=5)"

    def test_ref_page_omitted_when_unresolvable(self):
        label = "manufacturing property is assessed by the state under § 70.995"
        out, _ = _repair(f"[{label}](doc:case-law-2025-wi-app-43#page=1)")
        assert out == f"[{label}](doc:case-law-2025-wi-app-43#page=1&ref=statutes-70)"

    def test_ref_page_from_retrieved_chunk_heading(self):
        label = "the assessor values land at full value under § 70.32"
        out, _ = _repair(f"[{label}](doc:case-law-405-wis-2d-616#page=1)")
        assert out == f"[{label}](doc:case-law-405-wis-2d-616#page=1&ref=statutes-70#page=23)"

    def test_bare_section_label_still_repoints(self):
        out, stats = _repair(
            "[§ 74.37](doc:case-law-2025-wi-app-43#page=1)",
            section_pages={"74": {"74.37": 9}},
        )
        assert out == "[§ 74.37](doc:statutes-74#page=9)"
        assert stats["repointed"] == 1 and stats["dual"] == 0

    def test_claim_on_unretrieved_target_keeps_repoint_behavior(self):
        out, stats = _repair(f"[{self.HOLDING}](doc:wpam-2019-superseded#page=3)")
        assert out == f"[{self.HOLDING}](doc:statutes-70)"
        assert stats["repointed"] == 1 and stats["dual"] == 0

    def test_claim_with_out_of_corpus_chapter_is_still_stripped(self):
        label = "a motor vehicle is exempt from property tax under § 340.01(6m)"
        out, stats = _repair(f"[{label}](doc:case-law-2025-wi-app-43#page=1)")
        assert out == label
        assert stats["stripped"] == 1 and stats["dual"] == 0

    def test_admin_rule_claim_becomes_dual_without_ref_page(self):
        label = "must classify land under Tax 18.06(1)"
        out, stats = _repair(
            f"an assessor [{label}](doc:gov_publications-2026-agricultural-assessment-guide#page=4)"
        )
        assert out == (
            f"an assessor [{label}]"
            "(doc:gov_publications-2026-agricultural-assessment-guide"
            "#page=4&ref=admin_rules-tax-18)"
        )
        assert stats["dual"] == 1

    def test_admin_rule_claim_on_unretrieved_rule_is_stripped(self):
        label = "the assessor must follow the conversion charge under Tax 12.10"
        out, stats = _repair(f"[{label}](doc:case-law-2025-wi-app-43#page=1)")
        assert out == label and stats["stripped"] == 1 and stats["dual"] == 0

    def test_already_dual_link_is_untouched(self):
        answer = (
            f"[{self.HOLDING}](doc:case-law-2025-wi-app-43#page=1&ref=statutes-70#page=5) and "
            f"[{self.HOLDING}](doc:case-law-2025-wi-app-43#ref=statutes-70)"
        )
        out, stats = _repair(answer, section_pages=self.INDEX)
        assert out == answer
        assert stats == {"repointed": 0, "stripped": 0, "paged": 0, "dual": 0, "changes": []}

    def test_dual_repair_is_idempotent(self):
        once, _ = _repair(
            f"[{self.HOLDING}](doc:case-law-2025-wi-app-43#page=1)", section_pages=self.INDEX
        )
        twice, stats = _repair(once, section_pages=self.INDEX)
        assert twice == once and stats["dual"] == 0

    def test_grader_doc_regex_extracts_primary_id_only(self):
        # The harness / frontend read the cited doc id with `doc:([^)#]+)`;
        # a `&ref=` tail must not read as a second citation.
        import re

        answer, _ = _repair(
            f"[{self.HOLDING}](doc:case-law-2025-wi-app-43#page=1)", section_pages=self.INDEX
        )
        assert re.findall(r"doc:([^)#]+)", answer) == ["case-law-2025-wi-app-43"]
        no_page, _ = _repair(
            f"[{self.HOLDING}](doc:case-law-2025-wi-app-43)", section_pages=self.INDEX
        )
        assert re.findall(r"doc:([^)#]+)", no_page) == ["case-law-2025-wi-app-43"]
        # And the golden-set chapter-conflation guard still cannot match it.
        assert not re.search(r"\[§\s*61\.19[^\]]*\]\(doc:statutes-62", answer)


class TestUntouched:
    def test_links_without_section_numbers_are_never_touched(self):
        answer = (
            "The WPAM describes the [cost approach for farm buildings]"
            "(doc:wpam-wisconsin-property-assessment-manual-2026#page=502); the Court held that "
            "[comparability exists along a continuum](doc:case-law-405-wis-2d-616#page=1) and "
            "the guide covers [Chapter 13 commercial valuation]"
            "(doc:wpam-wisconsin-property-assessment-manual-2026#page=300) and "
            "[1st Grade Tillable examples]"
            "(doc:gov_publications-2026-agricultural-assessment-guide#page=19) "
            "with [2.5 acres](doc:gov_publications-2026-agricultural-assessment-guide#page=9)."
        )
        out, stats = _repair(answer)
        assert out == answer
        assert stats == {"repointed": 0, "stripped": 0, "paged": 0, "dual": 0, "changes": []}

    def test_plain_text_and_external_links_untouched(self):
        answer = "See § 70.32 (plain) and [DOR](https://www.revenue.wi.gov/x.pdf#page=3)."
        assert _repair(answer)[0] == answer

    def test_empty_answer(self):
        assert _repair("") == (
            "",
            {"repointed": 0, "stripped": 0, "paged": 0, "dual": 0, "changes": []},
        )


class TestHelpers:
    def test_has_open_link(self):
        assert has_open_link("text [§ 70.32")
        assert has_open_link("text [§ 70.32](doc:statu")
        assert not has_open_link("text [§ 70.32](doc:statutes-70#page=3) more")
        assert not has_open_link("plain text")

    def test_resolve_section_page_prefers_heading_over_text(self):
        chunks = [
            {"heading": "Intro", "start_page": 2, "text": "see 70.32 below"},
            {"heading": "70.32 Real estate, how valued.", "start_page": 23, "text": ""},
        ]
        assert resolve_section_page("70", ["70.32"], chunks) == 23
        assert resolve_section_page("70", ["70.99"], chunks) is None
        assert resolve_section_page("70", ["70.32"], None) is None


class TestStreamingHook:
    """stream_answer repairs each fragment before sending and never splits a link."""

    def _run(self, deltas, retrieved=RETRIEVED):
        import loop.phase_b as phase_b

        events = [{"contentBlockDelta": {"delta": {"text": d}}} for d in deltas]
        ws = MagicMock()
        ws.connection_id = "conn"
        with (
            patch.object(phase_b, "converse_stream_with_cache", return_value={"stream": events}),
            patch.object(phase_b, "start_heartbeat", return_value=MagicMock()),
            patch.object(phase_b, "_emit"),
            patch.object(phase_b, "_log") as log,
        ):
            answer = phase_b.stream_answer(
                ws,
                "qid-1",
                "ctx",
                lambda: 1,
                [True],
                retrieved_doc_ids=retrieved,
                cited_chunks=[c for cs in CHUNKS.values() for c in cs],
            )
        fragments = [
            json.loads(call.kwargs["Data"])["body"]["content"]["fragment"]
            for call in ws.client.post_to_connection.call_args_list
            if json.loads(call.kwargs["Data"])["streamId"] == "answer"
        ]
        return answer, fragments, log

    def test_link_split_across_deltas_is_repaired_on_the_wire(self):
        deltas = [
            "Villages appoint an assessor under the village statute ",  # ≥30 chars, flushable
            "[§ 61.19](doc:wpam-wisconsin-",  # open link: must be held back
            "property-assessment-manual-2026#page=20) and cities under ",
            "[§ 62.09](doc:statutes-62#page=9). Done.",
        ]
        answer, fragments, log = self._run(deltas)
        wire = "".join(fragments)
        assert wire == answer
        assert "[§ 61.19](doc:statutes-61)" in wire
        assert "doc:wpam" not in wire
        assert "[§ 62.09](doc:statutes-62#page=9)" in wire
        # No fragment ever carried a half-link.
        assert all(not has_open_link(f) for f in fragments)
        repaired = [c for c in log.call_args_list if c.args[0] == "answer_link_repaired"]
        assert len(repaired) == 1
        assert repaired[0].kwargs["query_id"] == "qid-1"
        assert repaired[0].kwargs["repointed"] == 1
        assert repaired[0].kwargs["stripped"] == 0
        assert repaired[0].kwargs["stage"] == "stream"

    def test_dual_link_split_across_deltas_closes_and_is_logged(self):
        label = "the assessor values land at full value under § 70.32"
        deltas = [
            "The court read the statute narrowly in this appeal, holding that ",
            f"[{label}](doc:case-law-2025-wi-",  # open link: held back
            "app-43#page=1). Done.",
        ]
        answer, fragments, log = self._run(deltas)
        wire = "".join(fragments)
        assert wire == answer
        assert f"[{label}](doc:case-law-2025-wi-app-43#page=1&ref=statutes-70#page=23)" in wire
        assert all(not has_open_link(f) for f in fragments)
        (repaired,) = [c for c in log.call_args_list if c.args[0] == "answer_link_repaired"]
        assert repaired.kwargs["dual"] == 1
        assert repaired.kwargs["repointed"] == 0 and repaired.kwargs["stripped"] == 0
        assert repaired.kwargs["stage"] == "stream"

    def test_no_repair_no_log(self):
        answer, fragments, log = self._run(
            ["Under [§ 70.32](doc:statutes-70#page=23) land is valued."]
        )
        assert "".join(fragments) == answer
        assert not [c for c in log.call_args_list if c.args[0] == "answer_link_repaired"]


class TestFinalizeAnswerLinks:
    def test_repairs_and_logs_final_stage(self):
        import loop.phase_b as phase_b

        chunks = [c for cs in CHUNKS.values() for c in cs]
        with patch.object(phase_b, "_log") as log:
            out = phase_b.finalize_answer_links(
                "[§ 340.01(6m)](doc:statutes-70#page=14)", "qid-2", RETRIEVED, chunks
            )
        assert out == "§ 340.01(6m)"
        (call,) = [c for c in log.call_args_list if c.args[0] == "answer_link_repaired"]
        assert call.kwargs["stripped"] == 1 and call.kwargs["stage"] == "final"

    def test_idempotent_after_stream_repair(self):
        import loop.phase_b as phase_b

        with patch.object(phase_b, "_log") as log:
            out = phase_b.finalize_answer_links(
                "[§ 61.19](doc:statutes-61)", "qid-3", RETRIEVED, []
            )
        assert out == "[§ 61.19](doc:statutes-61)"
        assert not log.call_args_list

    def test_dual_link_is_created_and_logged(self):
        import loop.phase_b as phase_b

        label = "a partially constructed building cannot be tax exempt under § 70.11(4m)"
        with patch.object(phase_b, "_log") as log:
            out = phase_b.finalize_answer_links(
                f"[{label}](doc:case-law-2025-wi-app-43#page=1)",
                "qid-4",
                RETRIEVED,
                [],
                section_pages={"70": {"70.11": 5}},
            )
        assert out == f"[{label}](doc:case-law-2025-wi-app-43#page=1&ref=statutes-70#page=5)"
        (call,) = [c for c in log.call_args_list if c.args[0] == "answer_link_repaired"]
        assert call.kwargs["dual"] == 1 and call.kwargs["stage"] == "final"

    def test_disabled_without_retrieved_set(self):
        import loop.phase_b as phase_b

        assert (
            phase_b.finalize_answer_links("[§ 61.19](doc:statutes-62)", "q", None, [])
            == "[§ 61.19](doc:statutes-62)"
        )


class TestSectionPageFill:
    """A statute link written without a page gets `#page=N` from the chapter's
    section index (phase_b.statute_section_pages) — never invented."""

    INDEX = {"74": {"74.35": 8, "74.37": 9}, "70": {"70.47": 38}}

    def test_bare_statute_link_gets_page_from_index(self):
        out, stats = repair_citation_links(
            "Under [§ 74.37](doc:statutes-74), you may file a claim.",
            RETRIEVED,
            {},
            section_pages=self.INDEX,
        )
        assert "[§ 74.37](doc:statutes-74#page=9)" in out
        assert stats["paged"] == 1
        assert stats["changes"][0]["kind"] == "paged"

    def test_subsection_uses_parent_section_page(self):
        out, _ = repair_citation_links(
            "[s. 70.47(7)(a)](doc:statutes-70)", RETRIEVED, {}, section_pages=self.INDEX
        )
        assert "(doc:statutes-70#page=38)" in out

    def test_existing_page_is_left_alone(self):
        text = "[§ 74.37](doc:statutes-74#page=12)"
        out, stats = repair_citation_links(text, RETRIEVED, {}, section_pages=self.INDEX)
        assert out == text and stats["paged"] == 0

    def test_no_index_entry_means_no_fragment(self):
        text = "[§ 74.99](doc:statutes-74)"
        out, stats = repair_citation_links(text, RETRIEVED, {}, section_pages=self.INDEX)
        assert out == text and stats["paged"] == 0

    def test_retrieved_chunk_heading_beats_index(self):
        chunks = {
            "statutes-74": [
                {"heading": "74.37 Claim on excessive assessment.", "start_page": 11, "text": ""}
            ]
        }
        out, _ = repair_citation_links(
            "[§ 74.37](doc:statutes-74)", RETRIEVED, chunks, section_pages=self.INDEX
        )
        assert "#page=11" in out

    def test_repointed_link_falls_back_to_index_page(self):
        out, stats = repair_citation_links(
            "[§ 74.37](doc:gov_publications-2026-agricultural-assessment-guide#page=4)",
            RETRIEVED,
            {},
            section_pages=self.INDEX,
        )
        assert "(doc:statutes-74#page=9)" in out and stats["repointed"] == 1

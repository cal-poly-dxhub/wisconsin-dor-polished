"""Plan statute grounding (loop/grounding.py): cited-but-unretrieved sections get fetched."""

from loop.grounding import (
    ground_plan_statutes,
    plan_section_refs,
    section_of_heading,
    subheading_covers,
)

HEADING = "12.34 Claim on something."
SECTION_CHUNKS = [
    {"chunk_id": f"c{i}", "doc_id": "statutes-12", "heading": HEADING, "subheading": sub, "text": t}
    for i, (sub, t) in enumerate(
        [
            ("12.34(1)–(2)", "(1) definitions (2) filing"),
            ("12.34(3)", "(3) action on claim"),
            ("12.34(4)", "(4) conditions; this paragraph does not apply if notice was not given"),
            ("12.34(5)–(7)", "(5) interest (6) (7)"),
            ("", "History: ..."),
            ("", "Case note: ..."),
        ]
    )
]


class FakeNeptune:
    def __init__(self):
        self.calls = []

    def get_section_chunks(self, doc_id, heading):
        self.calls.append((doc_id, heading))
        return list(SECTION_CHUNKS) if heading == HEADING else []


def sections(_neptune, doc_id):
    if doc_id != "statutes-12":
        return []
    return [{"heading": "12.30 Something else."}, {"heading": HEADING}]


KNOWN = frozenset({"12", "13"})


def test_refs_keep_order_and_upgrade_to_a_named_subsection():
    plan = "Under § 12.34 you must object first; see also s. 13.01 and § 12.34(4)(a)."
    assert plan_section_refs(plan) == [("12", "12.34", "4"), ("13", "13.01", None)]


def test_section_of_heading_does_not_confuse_prefixes():
    assert section_of_heading("70.11 Property exempted.") == "70.11"
    assert section_of_heading("70.1105 Partial use.") == "70.1105"
    assert section_of_heading("Chapter 20 Board of Review") is None


def test_subheading_ranges():
    assert subheading_covers("12.34(5)–(7)", "12.34", "6")
    assert subheading_covers("12.34(4)", "12.34", "4")
    assert not subheading_covers("12.34(1)–(2)", "12.34", "4")
    assert not subheading_covers("", "12.34", "4")


def test_unretrieved_section_is_fetched_named_subsection_first_without_history():
    neptune = FakeNeptune()
    cited, chunks, report = ground_plan_statutes(
        "Object to the board first under § 12.34(4).", ["guide-a"], [], neptune, sections, KNOWN
    )
    assert cited == ["guide-a", "statutes-12"]
    texts = [c["text"] for c in chunks]
    assert texts[0].startswith("(4) conditions")  # the named subsection leads
    assert not any(t.startswith(("History", "Case note")) for t in texts)
    assert len(texts) == 4
    assert report["fetched"] == [
        {"section": "12.34", "subsection": "4", "chunks": 4, "doc_id": "statutes-12"}
    ]


def test_already_retrieved_section_is_not_refetched_but_its_chapter_is_cited():
    neptune = FakeNeptune()
    have = [dict(SECTION_CHUNKS[2])]
    cited, chunks, report = ground_plan_statutes("See § 12.34.", [], have, neptune, sections, KNOWN)
    assert neptune.calls == []
    assert cited == ["statutes-12"] and chunks == have
    assert report["already_retrieved"] == ["12.34"]


def test_out_of_corpus_and_missing_sections_are_skipped_not_invented():
    neptune = FakeNeptune()
    cited, chunks, report = ground_plan_statutes(
        "§ 340.01 and § 12.99", [], [], neptune, sections, KNOWN
    )
    assert cited == [] and chunks == []
    reasons = {s["section"]: s["reason"] for s in report["skipped"]}
    assert reasons == {"340.01": "chapter not in corpus", "12.99": "no such section"}


def test_graph_errors_never_block_the_answer():
    class Broken(FakeNeptune):
        def get_section_chunks(self, doc_id, heading):
            raise RuntimeError("neptune down")

    cited, chunks, report = ground_plan_statutes("§ 12.34", ["x"], [], Broken(), sections, KNOWN)
    assert cited == ["x"] and chunks == []
    assert report["skipped"] == [{"section": "12.34", "reason": "RuntimeError"}]


def test_inputs_are_not_mutated():
    cited_in, chunks_in = ["guide-a"], []
    ground_plan_statutes("§ 12.34", cited_in, chunks_in, FakeNeptune(), sections, KNOWN)
    assert cited_in == ["guide-a"] and chunks_in == []

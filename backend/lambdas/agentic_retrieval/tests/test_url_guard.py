"""URL grounding guard (loop/url_guard.py, Task 62)."""

from loop.url_guard import guard_external_urls, has_open_url, ungrounded_contacts

CONTEXT = (
    "Chunk: Net new construction figures are published at "
    "https://www.revenue.wi.gov/Pages/EQU/nnc.aspx each August. "
    "Contact the TIF team at tif@wisconsin.gov or (608) 266-7750."
)


def test_invented_markdown_link_whose_label_is_the_url_becomes_the_host():
    # The shape of query 2a4a9aed.
    answer = "👉 [https://www.revenue.wi.gov/Pages/SLF/assessors.aspx](https://www.revenue.wi.gov/Pages/SLF/assessors.aspx)"
    out, stats = guard_external_urls(answer, CONTEXT)
    assert out == "👉 revenue.wi.gov"
    assert stats["reduced"] == 1


def test_invented_markdown_link_with_a_word_label_keeps_the_label():
    answer = "See the [assessor list](https://revenue.wi.gov/x/list.aspx)."
    out, _ = guard_external_urls(answer, CONTEXT)
    assert out == "See the assessor list."


def test_invented_bare_url_is_cut_to_host_and_keeps_punctuation():
    invented = "www.revenue.wi.gov/Pages/SLF/assessors.aspx"
    out, stats = guard_external_urls(f"Look it up at {invented}.", CONTEXT)
    assert out == "Look it up at revenue.wi.gov."
    assert stats["changes"] == [{"from": invented, "to": "revenue.wi.gov"}]


def test_grounded_urls_are_left_alone_in_any_spelling():
    for answer in (
        "Figures are at revenue.wi.gov/Pages/EQU/nnc.aspx.",
        "Figures are at https://revenue.wi.gov/Pages/EQU/nnc.aspx",
        "[NNC report](https://www.revenue.wi.gov/Pages/EQU/nnc.aspx)",
    ):
        out, stats = guard_external_urls(answer, CONTEXT)
        assert out == answer and stats["reduced"] == 0


def test_hosts_emails_and_doc_links_are_not_touched():
    answer = (
        "Search at **wicourts.gov** or email lgs@wisconsin.gov. [§ 70.32](doc:statutes-70#page=3)"
    )
    out, stats = guard_external_urls(answer, CONTEXT)
    assert out == answer and stats["reduced"] == 0


def test_idempotent():
    answer = "At www.revenue.wi.gov/Pages/SLF/assessors.aspx and [x](https://a.gov/b)."
    once, _ = guard_external_urls(answer, CONTEXT)
    twice, stats = guard_external_urls(once, CONTEXT)
    assert twice == once and stats["reduced"] == 0


def test_ungrounded_contacts_flags_only_what_the_context_lacks():
    answer = "Email tif@wisconsin.gov, call 608-266-7750, or try (608) 742-2191 and x@y.gov."
    assert ungrounded_contacts(answer, CONTEXT) == ["x@y.gov", "(608) 742-2191"]


def test_has_open_url_holds_a_url_that_may_still_grow():
    assert has_open_url("visit www.revenue.wi")
    assert has_open_url("visit https:")
    assert has_open_url("at revenue.wi.gov/Pages/SL")
    assert not has_open_url("the assessor sets the value ")
    assert not has_open_url("the assessor sets the value")

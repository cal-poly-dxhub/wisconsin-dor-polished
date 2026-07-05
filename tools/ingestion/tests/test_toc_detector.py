"""Tests for the TOC chunk detector.

TOC chunks pollute vector retrieval: a query like "contact information"
matches the TOC line "XIX. Contact Information . . . . . . 41" verbatim
and outranks the content page. The detector needs to catch those
reliably while NOT flagging citation-heavy case law or bullet lists.

Test inputs are real chunk samples pulled from the deployed graph
(us-east-1) during diagnosis on 2026-05-05.
"""

import pytest

from tools.ingestion.chunking.toc_detector import is_toc_chunk

# --- Positive samples (real TOC chunks from the graph) ---

TOC_MOBHME_X = (
    "X.  Instructions for Monthly Municipality Permit Fee Distribution . .  .  "
    ".  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  . . . . . . . . . . . . . . . . . . . "
)
TOC_MOBHME_I = (
    "I.  General Information . .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  "
    ".  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  . . . . . . . . . . "
)
TOC_POG_XIX = (
    "XIX.  Contact Information  . .  .  .  .  .  .  .  .  .  .  .  .  .  .  "
    ".  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  "
    ".  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .\t41  "
    "Back to table of contents"
)
TOC_PAG_XV = (
    "XV. \tContact Information .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  "
    ".  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  "
    ".  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  "
    ".  .  .  .  .  .  .  .  .  .  \t 57"
)

# Multi-entry TOC fragment (heading isn't a pure roman numeral, but body has
# multiple dot-leader sequences and mostly dots).
TOC_CONSERVATION_E = (
    "D.\t Primary use – must be one of the above agricultural uses  "
    "E.\t History  »\t 1974 . .  .  .  .  . . . . . . . . . \t"
    "State constitution amended to allow\n"
    "»\t 1981 . .  .  .  .  . . . . . . . . . \tAgricultural use value"
)


# --- Negative samples (real content chunks from the graph) ---

CASE_LAW_CITATIONS = (
    "L.P. v. County of Dane, 2012 WI App 28, 340 Wis. 2d 175, 811 N.W.2d 421, 10- "
    "This paragraph does not expand or modify the authority of a town to change a "
    "zoning ordinance. Roberts v. Manitowoc County Bd. of Adjustment, 2006 WI "
    "App 169, 721 N.W.2d 499, 05-2213."
)
STATUTE_DENSE_CITES = (
    "N.W.2d 337 (1977). ments and underpayments of the May 1, 1986, assessment "
    "shall be made by the methods under par. (c). (c) Beginning with calendar year "
    "1986, the apportioned tax rate for any district in each county shall not "
    "exceed the amount determined by the department of revenue."
)
REAL_CONTENT_DOR_CONTACT = (
    "C. Income approach  P.O. Box 8934  Madison, WI 53708-8934  (608) 224-4848  "
    "Contact: Robert J. Battaglia, State Statistician  Internet: "
    "http://www.nass.usda.gov/wi/  Wisconsin Department of Revenue - "
    "Capitalization Rate Components"
)
BULLET_LIST = (
    "The assessor must:\n"
    "•\tInspect the property during the assessment period.\n"
    "•\tRecord the condition on January 1.\n"
    "•\tCompare to sales of similar properties.\n"
    "•\tReport findings to the BOR."
)
ELLIPSIS_QUOTE = (
    'The court held that the statute "shall not, under any circumstance... '
    'operate to impose an affirmative duty" on the assessor. The opinion '
    "distinguishes this from earlier precedent."
)


@pytest.mark.parametrize(
    "text, heading",
    [
        (TOC_MOBHME_X, "X."),
        (TOC_MOBHME_I, "I."),
        (TOC_POG_XIX, "XIX."),
        (TOC_PAG_XV, "XV."),
        (TOC_CONSERVATION_E, "D.\t Primary use – must be one of the above agricultural uses"),
    ],
)
def test_real_toc_chunks_flagged(text: str, heading: str) -> None:
    assert is_toc_chunk(text, heading) is True


@pytest.mark.parametrize(
    "text, heading",
    [
        (CASE_LAW_CITATIONS, ""),
        (STATUTE_DENSE_CITES, ""),
        (REAL_CONTENT_DOR_CONTACT, "C. Income approach | P.O. Box 8934"),
        (BULLET_LIST, ""),
        (ELLIPSIS_QUOTE, ""),
    ],
)
def test_real_content_chunks_not_flagged(text: str, heading: str) -> None:
    assert is_toc_chunk(text, heading) is False


def test_empty_text_not_flagged() -> None:
    assert is_toc_chunk("", "X.") is False
    assert is_toc_chunk(None, "X.") is False  # type: ignore[arg-type]


def test_roman_heading_alone_not_enough() -> None:
    # A pure roman heading with NO leader sequence is not a TOC chunk —
    # e.g., a section labeled "X." that actually starts content.
    assert is_toc_chunk("X. Overview of the process and timeline for appeals.", "X.") is False


def test_leader_with_non_roman_heading() -> None:
    # Even with a real heading, many dot-leaders still signal TOC.
    text = (
        "A. Timeline . .  .  .  .  .  .  .  .  .  .  .  .  . . . . . . . . . . .  12  "
        "B. Procedure . .  .  .  .  .  .  .  .  .  .  .  .  . . . . . . . . . . . 18"
    )
    assert is_toc_chunk(text, "Section A") is True


def test_single_ellipsis_not_toc() -> None:
    # A quoted passage with "..." three dots should never match.
    text = 'The court wrote that "the duty... shall not... exceed." This is dicta.'
    assert is_toc_chunk(text) is False

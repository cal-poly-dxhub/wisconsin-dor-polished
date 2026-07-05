"""Extract case-law annotations from Wisconsin statute PDFs.

Wisconsin publishes a "statutes annotated" edition where case citations are
preceded by 1-4 sentences of editorial summary describing what the case held in
the context of the statute it's annotating. These annotations are far more
useful than the placeholder JSON stubs we upload when a full opinion isn't
available, and they provide real, grounded, per-statute context even when the
opinion IS available (often richer than the opinion's own syllabus).

This module takes a citation + citing-statute metadata and returns the
surrounding annotation paragraph(s), ready to be stored as a Case Law chunk.

Extraction rules (derived empirically from the Wisconsin Statutes format):

    <Body text of statute section.>
    History: YYYY a. NN; YYYY a. NN.
    <Annotation 1 sentence(s). Case Name, Citation, Parallel (Year), NN-NNNN.>
    <Annotation 2 sentence(s). Another Case, ...>

We scan BACKWARD from the citation to find the annotation's start. Candidate
boundaries, from strongest to weakest:

    1. Previous annotation's docket number ending: "NN-NNNNN." then capital.
    2. Previous annotation's year-paren ending: "(...YYYY)." then capital.
    3. History: block terminator ("YYYY a. NN." then capital).
    4. A section heading ending mid-page.

If extraction yields a suspiciously short result (<60 chars), we retry with the
previous PDF page prepended — annotations sometimes span page breaks, and the
Wisconsin Statutes page header ("Updated 23-24 Wis. Stats..." + page number)
interrupts the scan otherwise.
"""

from __future__ import annotations

import re
from pathlib import Path

import fitz  # PyMuPDF

# Page-header boilerplate that appears on every Wisconsin Statutes page.
# Removing it before scanning lets annotations span page breaks cleanly.
_PAGE_HEADER_RE = re.compile(
    r"Updated\s+[\d-]+\s+Wis\.\s+Stats\..*?April\s+\d+,\s+\d{4}\.",
    re.IGNORECASE | re.DOTALL,
)

# Hyphenated line break: "con-\ntext" → "context".
_HYPHEN_BREAK_RE = re.compile(r"(\w)-\s*\n\s*(\w)")

# Docket number ending a prior annotation: "07-1615." or "172439." then capital.
_DOCKET_END_RE = re.compile(r"\b\d{2}-?\d{2,5}[a-z]?\.\s+(?=[A-Z])")

# Year-paren ending a prior annotation: "(Ct. App. 1992)." then capital.
_YEAR_PAREN_END_RE = re.compile(r"\(\s*[^)]*\b\d{4}\s*\)\.\s+(?=[A-Z])")

# "History:" keyword. We walk forward from this to find the block's terminator.
_HISTORY_KEYWORD_RE = re.compile(r"\bHistory:\s+")

# A History block's terminal period, grammar-aware:
# the LAST "YYYY a. NN[, NN]..." clause ending in ". " before an uppercase word.
_HISTORY_END_STRICT_RE = re.compile(
    r"(?:\d{4}\s+a\.\s+[^;.]+[.;])*\s*\d{4}\s+a\.\s+[^.]+\.\s+(?=[A-Z])"
)

# Looser History: end — any ". " then uppercase, used as a fallback.
_HISTORY_END_LOOSE_RE = re.compile(r"\.\s+(?=[A-Z])")

# Section heading mid-page: "70.32 Real estate, how assessed. ..." — mixed-case
# title terminated by `.`. Distinct from all-caps page-top headers (see below).
_SECTION_HEADING_RE = re.compile(r"\b\d{2,3}\.\d{2,4}\s+[A-Z][^.]{3,60}\.\s+")

# All-caps page-top header: "77.52 SALES AND USE TAXES; MANAGED FOREST LANDS;"
# These appear at the top of each PDF page and leak into page-spanning
# annotations. Requires a run of ALL-CAPS tokens (possibly ; or . separated)
# ending where the first mixed-case token begins. Non-greedy on the ALL-CAPS
# run, but anchored on a lookahead so we stop at the first Title-Case word.
_ALLCAPS_PAGE_HEADER_RE = re.compile(
    r"\b\d{2,3}\.\d{2,4}\s+"
    r"(?:[A-Z][A-Z]+(?:\s+[A-Z][A-Z]+)*[;.]?\s+)+"
    r"(?=[A-Za-z][a-z])"  # stop before any word with lowercase
)

# " v. " separator — the defining feature of a case name, used when walking
# backward from a citation to find where the case name starts.
_V_SEPARATOR = re.compile(r"\s+v\.\s+")

# Sentence boundary for case-name scanning: [.!?] optionally followed by a
# closing quote / bracket, then whitespace. Wisconsin Statutes use curly quotes
# (U+201C/U+201D) around quoted terms, so we must match those too.
_SENTENCE_END_RE = re.compile(r"[.!?][\"'”’)\]]?\s+")

# Legal "signal phrases" (Bluebook R1.2) that precede a citation without being
# part of the case name itself: "But see", "See, e.g.,", "Cf.", etc. When the
# extracted case name begins with one of these, strip it.
_SIGNAL_PHRASE_RE = re.compile(
    r"^(?:But\s+see|See\s+also|See,?\s+e\.g\.?,?|See\s+generally|See|Cf\.|Accord|Compare|"
    r"Contra|Contrast)\s+",
    re.IGNORECASE,
)

# Boilerplate residue sometimes left at the start of an extracted annotation:
# "Chapter NN ..." breadcrumb, or a bare section heading like "70.11 Exempt property."
_LEADING_BREADCRUMB_RE = re.compile(
    r"^(?:Chapter\s+\d+\s*[.-]?\s*|\d{2,3}\.\d{2,4}\s+[A-Z][^.]{3,60}\.\s+|\d+\s+)"
)

# Bluebook signal phrases at the START of an annotation body. When an annotation
# begins "See also Foo v. Bar, ..." it is typically a cross-reference stub
# inherited from the prior annotation — stripping the signal exposes that what
# remains is just a case name + citation, which the LLM fallback handles better.
_LEADING_SIGNAL_RE = re.compile(
    r"^(?:But\s+see|See\s+also|See,?\s+e\.g\.?,?|See\s+generally|See|Cf\.|Accord|"
    r"Compare|Contra|Contrast)\s+",
    re.IGNORECASE,
)

# Default window: annotations are typically 1-4 sentences, ≲800 chars.
# 2000 gives headroom for page-break cases without pulling in unrelated annotations.
DEFAULT_MAX_CHARS = 2000

# If extraction is shorter than this, retry with previous page prepended.
MIN_ANNOTATION_CHARS = 60


def _normalize(pdf_text: str) -> str:
    """Prepare PDF text for annotation scanning.

    - Joins hyphenated line breaks ("con-\\nvalent" → "convalent").
    - Collapses whitespace to single spaces.
    - Strips Wisconsin Statutes page-header boilerplate.
    - Collapses whitespace again after header removal.
    """
    text = _HYPHEN_BREAK_RE.sub(r"\1\2", pdf_text)
    text = re.sub(r"\s+", " ", text)
    text = _PAGE_HEADER_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text)


def _find_annotation_start(search_region: str) -> int:
    """Return the offset within search_region where the annotation begins.

    Picks the LATEST (closest to citation) boundary candidate. Falls back to the
    last "period-space-uppercase" break, and finally to offset 0 if the region
    has no sentence-like structure.
    """
    candidates: list[int] = []

    for m in _DOCKET_END_RE.finditer(search_region):
        candidates.append(m.end())

    for m in _YEAR_PAREN_END_RE.finditer(search_region):
        candidates.append(m.end())

    history_starts = [m.start() for m in _HISTORY_KEYWORD_RE.finditer(search_region)]
    if history_starts:
        last_history = history_starts[-1]
        tail = search_region[last_history:]
        match = _HISTORY_END_STRICT_RE.search(tail) or _HISTORY_END_LOOSE_RE.search(tail)
        if match:
            candidates.append(last_history + match.end())

    for m in _SECTION_HEADING_RE.finditer(search_region):
        candidates.append(m.end())

    for m in _ALLCAPS_PAGE_HEADER_RE.finditer(search_region):
        candidates.append(m.end())

    if candidates:
        return max(candidates)

    # No structural boundary found — return the start of the search window
    # rather than the last period break, because "last period" inside a case
    # name like "State v. Smith" would truncate the annotation's body.
    return 0


def _strip_leading_breadcrumb(annotation: str) -> str:
    """Remove residual section headings or chapter breadcrumbs at the annotation start."""
    prev = None
    current = annotation
    # Apply repeatedly — a leading breadcrumb may be followed by another.
    # Also strip all-caps page headers (e.g. "77.52 SALES AND USE TAXES; ...")
    # and leading Bluebook signal phrases (e.g. "See also ...", "But see ...")
    # that mark cross-reference stubs rather than standalone annotations.
    while current != prev:
        prev = current
        match = _ALLCAPS_PAGE_HEADER_RE.match(current)
        if match:
            current = current[match.end() :]
        current = _LEADING_BREADCRUMB_RE.sub("", current, count=1)
        current = _LEADING_SIGNAL_RE.sub("", current, count=1)
    return current.strip()


def extract_case_name(annotation_with_citation: str, citation: str) -> str | None:
    """Return the case name ("State v. Smith") that precedes the given citation.

    Algorithm: walk backward from the citation to find ", " (the separator
    between case name and volume). Then walk back further to find the " v. "
    that splits the two parties. From there, expand backward to the most recent
    sentence boundary — that's where the case name starts.

    Returns None if no " v. " pattern appears in the window before the citation.
    """
    idx = annotation_with_citation.find(citation)
    if idx < 0:
        return None
    # Case names typically occupy the ~200 chars before a citation. 300 gives
    # headroom for long organizational party names.
    window_start = max(0, idx - 300)
    window = annotation_with_citation[window_start:idx]

    # Walk backward from the end of the window to find ", " (name→volume separator).
    # Strip any trailing whitespace first.
    window_trimmed = window.rstrip()
    if not window_trimmed.endswith(","):
        # Citation is preceded by something other than ", <citation>" — unexpected
        # in the Wisconsin Statutes format but possible for manually-edited text.
        return None
    name_end = len(window_trimmed) - 1  # position of the comma

    # Find " v. " within the segment before the comma.
    segment = window_trimmed[:name_end]
    v_matches = list(_V_SEPARATOR.finditer(segment))
    if not v_matches:
        return None
    # The LAST " v. " in the segment is the one binding this case's name.
    v_match = v_matches[-1]

    # Walk backward from v_match.start() to find where the case name begins.
    # Boundary: a sentence-ending punctuation (possibly followed by a closing
    # quote/bracket), or start of segment.
    before_v = segment[: v_match.start()]
    sentence_ends = list(_SENTENCE_END_RE.finditer(before_v))
    if sentence_ends:
        name_start = sentence_ends[-1].end()
    else:
        name_start = 0

    name = segment[name_start:].strip()
    # Strip Bluebook signal phrases ("But see", "Cf.", etc.) that precede a
    # cited case but aren't part of the name.
    name = _SIGNAL_PHRASE_RE.sub("", name).strip()
    # Reject if the captured "name" clearly includes sentence prose, evidenced by
    # characters not permitted in case names (shouldn't happen with our class
    # but defense-in-depth) or by being longer than any reasonable case name.
    if len(name) > 150:
        return None
    return re.sub(r"\s+", " ", name).rstrip(".,;:")


def extract_annotation_from_text(
    pdf_text: str,
    citation: str,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> str | None:
    """Scan `pdf_text` for `citation` and return the preceding annotation paragraph.

    Returns None if the citation isn't found or if no plausible annotation
    precedes it. The returned text INCLUDES the case name that immediately
    precedes the citation (but not the citation itself).
    """
    norm = _normalize(pdf_text)
    idx = norm.find(citation)
    if idx < 0:
        return None

    window_start = max(0, idx - max_chars)
    search_region = norm[window_start:idx]
    relative_start = _find_annotation_start(search_region)
    annotation = norm[window_start + relative_start : idx].strip()
    return _strip_leading_breadcrumb(annotation) or None


# Section-number pattern in the page running header: e.g. "70.32" or "70.327".
# Wisconsin Statutes print this in the banner of every page (alongside the
# page number and the chapter name in all-caps), so the first match in the
# first ~500 chars of page text reliably identifies the section that owns
# the page. Trailing letters (e.g. "70.32m") are statute fragments and ARE
# a valid section identifier — we match them.
_SECTION_HEADER_RE = re.compile(r"\b(\d{1,3}\.\d{2,4}[a-z]?)\b")

# How far into the page text to look for the section banner. Wisconsin
# headers are always within ~250 chars; 500 gives margin for the alternate
# banner ordering ("page | section | chapter" vs "chapter | section | page").
_HEADER_SCAN_CHARS = 500


def extract_section_for_page(
    pdf_path: str | Path,
    page_1idx: int,
    expected_chapter: str | None = None,
) -> str | None:
    """Return the statute section number that owns a given page (1-indexed).

    Reads the running header at the top of the page, which Wisconsin Statutes
    print on every page in the form "GENERAL PROPERTY TAXES | 70.32 | 23" or
    its mirrored variant. The first ``\\d+\\.\\d+`` match in the header region
    is the section.

    When ``expected_chapter`` is provided (e.g. ``"70"`` derived from the PDF
    filename), only matches whose chapter portion equals it are accepted. This
    rejects cross-references that appear in body text but not in the banner —
    useful when a page has more than one section number near the top.

    Returns None when:
    - the page index is out of range,
    - no section pattern is found in the header window,
    - or every match's chapter prefix mismatches ``expected_chapter``.
    """
    doc = fitz.open(str(pdf_path))
    try:
        page_idx = page_1idx - 1
        if not (0 <= page_idx < len(doc)):
            return None
        text = doc[page_idx].get_text()[:_HEADER_SCAN_CHARS]
        for match in _SECTION_HEADER_RE.finditer(text):
            section = match.group(1)
            if expected_chapter is None:
                return section
            if section.split(".")[0] == expected_chapter:
                return section
        return None
    finally:
        doc.close()


def extract_annotation_from_pdf(
    pdf_path: str | Path,
    citation: str,
    page_numbers: list[int],
    max_chars: int = DEFAULT_MAX_CHARS,
) -> str | None:
    """Extract annotation for `citation` from `pdf_path`.

    Tries each page in `page_numbers` (1-indexed). When a single-page extraction
    yields a suspiciously short result, retries with the previous page prepended
    to handle annotations that span page breaks.

    Returns None if no page produces a usable annotation.
    """
    doc = fitz.open(str(pdf_path))
    try:
        best: str | None = None
        for page_1idx in page_numbers:
            page_idx = page_1idx - 1
            if not (0 <= page_idx < len(doc)):
                continue
            page_text = doc[page_idx].get_text()
            ann = extract_annotation_from_text(page_text, citation, max_chars)

            if not ann or len(ann) < MIN_ANNOTATION_CHARS:
                if page_idx > 0:
                    combined = doc[page_idx - 1].get_text() + "\n\n" + page_text
                    retry = extract_annotation_from_text(combined, citation, max_chars)
                    if retry and (not ann or len(retry) > len(ann)):
                        ann = retry

            if ann and (best is None or len(ann) > len(best)):
                best = ann

        return best
    finally:
        doc.close()


def gather_case_annotations(
    citation: str,
    citing_statutes: list[dict],
    pdf_dir: str | Path,
    max_chars_per: int = DEFAULT_MAX_CHARS,
) -> list[dict]:
    """Collect annotation blocks for `citation` across all citing statute PDFs.

    Args:
        citation: The citation we're building an annotation for (e.g. "2009 WI App 159").
        citing_statutes: List of {"file": "70.pdf", "pages": [25]} dicts from the
            case-law metadata `citing_statutes` field.
        pdf_dir: Directory containing the statute PDFs (typically docs/state-laws).
        max_chars_per: Max annotation window per (file, page) location.

    Returns:
        One dict per successfully-extracted annotation:
            {
                "text": "<annotation paragraph(s) ending with case name>",
                "source_file": "70.pdf",
                "pages": [25],
                "case_name": "Nestle USA, Inc. v. DOR",  # may be None
            }

        Missing PDFs, missing pages, and unmatched citations are silently
        skipped. Callers can check the returned list's length to detect misses.
    """
    pdf_dir = Path(pdf_dir)
    out: list[dict] = []
    for src in citing_statutes:
        pdf_path = pdf_dir / src["file"]
        if not pdf_path.exists():
            continue
        pages = src.get("pages", [])
        if not pages:
            continue
        ann = extract_annotation_from_pdf(pdf_path, citation, pages, max_chars_per)
        if not ann:
            continue
        out.append(
            {
                "text": ann,
                "source_file": src["file"],
                "pages": pages,
                "case_name": extract_case_name(ann + " " + citation, citation),
            }
        )
    return out

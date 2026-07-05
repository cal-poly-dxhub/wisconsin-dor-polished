"""Tests for the false-positive table filter in pymupdf_extractor.

PyMuPDF's ``find_tables()`` frequently flags multi-column prose layouts as
tables. When that happens, ``_extract_table_text`` row-joins paragraph-length
cells with ``" | "`` and scrambles the reading order. Real case: Property
Owners Guide p.41 (contact info) comes out as a 4-row × 2-col "table" whose
cells contain entire paragraphs.

We reject such false positives by looking at the extracted rows. A genuine
grid has short, roughly-uniform cells. A prose-layout false positive has
few rows with very long cells that contain newlines.
"""

import pytest

from tools.ingestion.chunking.pymupdf_extractor import looks_like_real_table

# --- Positive: real tables (should stay tables) ---

WISCONSIN_COUNTY_GRID = [
    ["Wisconsin Counties - Alphabetical List", "", "", "", "", "", "", "", ""],
    ["", "County", "District\nOffice", "", "County", "District\nOffice", "", "County", "District\nOffice"],
    ["Code", "Name", "", "Code", "Name", "", "Code", "Name", ""],
    ["01", "Adams", "80", "25", "Iowa", "76", "48", "Polk", "79"],
    ["02", "Ashland", "80", "26", "Iron", "80", "49", "Portage", "80"],
    ["03", "Barron", "79", "27", "Jackson", "79", "50", "Price", "80"],
    ["04", "Bayfield", "79", "28", "Jefferson", "76", "51", "Racine", "77"],
    ["05", "Brown", "81", "29", "Juneau", "80", "52", "Richland", "76"],
    ["06", "Buffalo", "79", "30", "Kenosha", "77", "53", "Rock", "76"],
]

SMALL_RATE_TABLE = [
    ["Year", "Rate", "Notes"],
    ["2023", "3.5%", "Initial"],
    ["2024", "4.0%", "Adjusted"],
    ["2025", "4.25%", "Final"],
]


# --- Negative: false-positive tables from multi-column prose ---

POG_P41_FAKE_TABLE = [
    ["", ""],
    ["Department of Revenue – Equalizati", "on District Offices"],
    [
        "Equalization Districts\nDistricts\nEau Claire (79)\nGreen Bay (81)\n"
        "Madison (76)\nMilwaukee (77)\nWausau (80)\nWisconsin Counties - "
        "Alphabetical List\nCounty District County District County District\n"
        "Code Name Office Code Name Office Code Name Office\n01 Adams 80 25 "
        "Iowa 76 48 Polk 79\n02 Ashland 80 26 Iron 80 49 Portage 80",
        "Contact Information\nEau Claire District Office (79)\n221 W. Madison "
        "St., Suite 203\nEau Claire, WI 54703\neqleau@wisconsin.gov\nPh: "
        "715-836-2866 Fax: 715-836-6690\nGreen Bay District Office (81)\n200 N. "
        "Jefferson St, Ste. 126\nGreen Bay, WI 54301-5100\neqlgrb@wisconsin.gov"
    ],
    ["", ""],
]

TWO_COL_PROSE_LAYOUT = [
    [
        "The assessor shall review the property on or before January 1 "
        "and determine the classification under section 70.32(2) of the "
        "statutes. The review shall consider the highest and best use.",
        "If the property owner disagrees with the classification, the "
        "owner may file an objection with the Board of Review within "
        "the statutory window established by sec. 70.47."
    ]
]


@pytest.mark.parametrize(
    "rows",
    [WISCONSIN_COUNTY_GRID, SMALL_RATE_TABLE],
)
def test_real_tables_kept(rows: list[list[str]]) -> None:
    assert looks_like_real_table(rows) is True


@pytest.mark.parametrize(
    "rows",
    [POG_P41_FAKE_TABLE, TWO_COL_PROSE_LAYOUT],
)
def test_prose_layout_rejected(rows: list[list[str]]) -> None:
    assert looks_like_real_table(rows) is False


def test_empty_rows_rejected() -> None:
    assert looks_like_real_table([]) is False
    assert looks_like_real_table([[]]) is False
    assert looks_like_real_table([["", "", ""]]) is False


def test_single_row_rejected() -> None:
    # A one-row "table" is almost always a header line misdetected.
    assert looks_like_real_table([["Year", "Rate", "Notes"]]) is False


def test_two_row_kept_when_cells_short() -> None:
    # Edge case: header + single data row. Short cells = real.
    rows = [["Code", "Name", "Dist"], ["01", "Adams", "80"]]
    assert looks_like_real_table(rows) is True


def test_few_rows_with_long_prose_cell_rejected() -> None:
    # Signature of multi-column prose detected as table: few rows, one cell
    # is paragraph-length. This is the POG p.41 pattern (4 rows, 1244ch cell).
    rows = [
        ["Label", "Value"],
        ["Short", "Short"],
        [
            "Overview",
            "This is a long paragraph of prose that runs on for many "
            "characters and contains sentence punctuation. It is clearly "
            "narrative text rather than a data cell and should disqualify "
            "the whole block from being treated as a table."
        ],
    ]
    assert looks_like_real_table(rows) is False


def test_many_rows_with_long_description_cells_kept() -> None:
    # Real revision-log tables (WPAM p.12) have many rows and long description
    # cells. The 120-char rule used to reject these. The row-count + length
    # rule keeps them.
    rows = [
        ["Chapter", "Revisions"],
        ["1", "Updated statutory references throughout; corrected citation formats."],
        ["2", "Expanded discussion of market value; added examples from 2023 sales data."],
        ["3", "Reorganized sections 3.1 through 3.5; moved appraisal formulas to appendix."],
        ["4", "Added new subsection on agricultural classification per recent case law."],
        ["5", "Revised income approach capitalization rate table with updated market data."],
        ["6", "Minor editorial corrections; no substantive changes to policy guidance."],
    ]
    assert looks_like_real_table(rows) is True


def test_very_long_cell_requires_more_rows() -> None:
    # 500+ char cells are strong prose signal; need 8+ rows to accept as table.
    huge_cell = "x" * 600
    # 6 rows with 600-char cell → rejected (looks like prose)
    rows_short = [["a", huge_cell]] + [["b", "c"]] * 5
    assert looks_like_real_table(rows_short) is False
    # 10 rows with 600-char cell → kept (real tax-allocation-style table)
    rows_long = [["a", huge_cell]] + [["b", "c"]] * 9
    assert looks_like_real_table(rows_long) is True

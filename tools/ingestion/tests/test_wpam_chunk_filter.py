"""Tests for WPAM post-chunking quality filters.

Validates two fixes:
1. filter_wpam_chunks — drops garbled, too-short, and table-cell chunks
2. repair_wpam_subheadings — clears leaked subheadings appearing on >5 chunks
"""


from tools.ingestion.chunking.wpam_chunk_filter import (
    _body_text,
    _is_garbled,
    _is_table_cells,
    filter_wpam_chunks,
    repair_wpam_subheadings,
)

# ---------------------------------------------------------------------------
# Helpers to build test chunks
# ---------------------------------------------------------------------------


def _make_chunk(text: str, heading: str = "Chapter 20", subheading: str = "") -> dict:
    return {
        "text": text,
        "metadata": {
            "doc_id": "wpam-test",
            "heading": heading,
            "subheading": subheading,
            "start_page": 1,
            "end_page": 1,
        },
    }


# ---------------------------------------------------------------------------
# _body_text extraction
# ---------------------------------------------------------------------------


class TestBodyText:
    def test_strips_heading_and_subheading(self):
        chunk = _make_chunk(
            "Chapter 20\n1. The record\nActual body content here.",
            heading="Chapter 20",
            subheading="1. The record",
        )
        body = _body_text(chunk)
        assert body == "Actual body content here."

    def test_heading_only(self):
        chunk = _make_chunk(
            "Chapter 14 Agricultural Valuation\nContent about agriculture.",
            heading="Chapter 14 Agricultural Valuation",
        )
        body = _body_text(chunk)
        assert body == "Content about agriculture."

    def test_empty_body(self):
        chunk = _make_chunk("Chapter 20\n", heading="Chapter 20")
        body = _body_text(chunk)
        assert body.strip() == ""


# ---------------------------------------------------------------------------
# Garbled text detection
# ---------------------------------------------------------------------------


class TestIsGarbled:
    def test_column_interleaved_text(self):
        # Real garbled chunk from WPAM 2025 (chunk 415)
        text = (
            "om | itted property, w | here they appeare | d generally before | "
            "the Boa | rd of Review, purs | uant to the\n"
            "no | tice. |  | Schedule E |  |  |\n"
            "|  | BUIL | DINGS ON LEASED L | AND |  |\n"
            "Report buildi\n"
            "the same ma\n"
            "M | ngs, structures, and oth\n"
            "nner as improvements l\n"
            "ilwaukee Count | er improvements which\n"
            "ocated on land that is o\n"
        )
        assert _is_garbled(text) is True

    def test_normal_text_not_flagged(self):
        text = (
            "The board of review hears objections to assessments made by the local "
            "assessor. Any person who objects to the assessed value placed on their "
            "property may appear before the board of review."
        )
        assert _is_garbled(text) is False

    def test_text_with_few_pipes_not_flagged(self):
        # Legal text sometimes uses | for statute citations
        text = (
            "See Wis. Stat. § 70.47(7)(aa) | The board shall hear upon oath all "
            "persons who appear before it.\n"
            "The procedure is straightforward and applies uniformly across all municipalities."
        )
        assert _is_garbled(text) is False

    def test_heavily_piped_but_long_lines_not_flagged(self):
        # Tables rendered as markdown-style (lines are long, not fragmented)
        text = (
            "Category | Description | Rate\n"
            "Residential | Single family homes and condominiums | 1.0\n"
            "Commercial | Retail, office, and industrial properties | 1.2\n"
            "Agricultural | Farmland and agricultural buildings | 0.8\n"
        )
        assert _is_garbled(text) is False


# ---------------------------------------------------------------------------
# Table-cell detection
# ---------------------------------------------------------------------------


class TestIsTableCells:
    def test_vertical_code_table(self):
        # Real chunk pattern (C-O-D-E form headers)
        text = "DESCRIPTION OF PROPERTY\nC\nO\nD\nE\nIMPROVEMEN\nT VALUE"
        assert _is_table_cells(text) is True

    def test_rating_list(self):
        # Real chunk from WPAM 2025 (chunk 280 body)
        text = (
            "V. Poor\nGood\nGood\nGood\nFair\nGood\nGood\nGood\n"
            "Good\nGood\nGood\nGood\nGood\nGood\nGood\nGood\n"
            "Good\n—\n—\n—\n—\nFair\nPoor"
        )
        assert _is_table_cells(text) is True

    def test_normal_content_not_flagged(self):
        text = (
            "The assessor must consider all three approaches to value when "
            "determining the assessed value of a property.\n"
            "These approaches include the sales comparison approach, the cost "
            "approach, and the income approach.\n"
            "Each approach provides an independent indication of value."
        )
        assert _is_table_cells(text) is False

    def test_short_numbered_list_not_flagged(self):
        # Legitimate short content with numbered items
        text = (
            "Requirements for exemption:\n"
            "1. Must be owned by a nonprofit\n"
            "2. Must be used exclusively for exempt purposes\n"
            "3. Land must not exceed 10 acres"
        )
        assert _is_table_cells(text) is False

    def test_two_lines_not_flagged(self):
        # Too few lines to trigger
        text = "A\nB"
        assert _is_table_cells(text) is False


# ---------------------------------------------------------------------------
# filter_wpam_chunks integration
# ---------------------------------------------------------------------------


class TestFilterWpamChunks:
    def test_keeps_normal_chunks(self):
        chunks = [
            _make_chunk(
                "Chapter 20\nThe board of review hears objections to assessments. "
                "Any person who objects to the assessed value placed on their property "
                "may appear before the board of review and present evidence.",
                heading="Chapter 20",
            ),
        ]
        kept, removed = filter_wpam_chunks(chunks)
        assert len(kept) == 1
        assert len(removed) == 0

    def test_removes_short_body(self):
        chunks = [_make_chunk("Chapter 8\nC O D E", heading="Chapter 8")]
        kept, removed = filter_wpam_chunks(chunks)
        assert len(kept) == 0
        assert len(removed) == 1
        assert removed[0]["_filter_reason"] == "body_too_short"

    def test_removes_garbled(self):
        garbled_text = (
            "Chapter 20\n"
            "om | itted property, w | here they appeare | d generally\n"
            "no | tice. |  | Schedule E |  |  |\n"
            "|  | BUIL | DINGS ON LEASED L | AND |  |\n"
            "Report buildi\n"
            "the same ma\n"
            "M | ngs, structures, and oth\n"
            "nner as improvements l\n"
            "ilwaukee Count | er improvements which\n"
            "ocated on land that is o\n"
            "Wis. 637, 242 N | located on\n"
        )
        chunks = [_make_chunk(garbled_text, heading="Chapter 20")]
        kept, removed = filter_wpam_chunks(chunks)
        assert len(kept) == 0
        assert removed[0]["_filter_reason"] == "garbled_columns"

    def test_removes_table_cells(self):
        text = (
            "Chapter 14 Agricultural Valuation\n"
            "V. Poor\nGood\nGood\nGood\nFair\nGood\nGood\nGood\n"
            "Good\nGood\nGood\nGood\nGood\nGood\nGood\nGood\n"
            "Good\n—\n—\n—\n—\nFair\nPoor"
        )
        chunks = [_make_chunk(text, heading="Chapter 14 Agricultural Valuation")]
        kept, removed = filter_wpam_chunks(chunks)
        assert len(kept) == 0
        assert removed[0]["_filter_reason"] == "table_cells"

    def test_mixed_batch(self):
        good = _make_chunk(
            "Chapter 9\nThe cost approach estimates value by calculating the cost "
            "to reproduce or replace the improvements, minus depreciation, plus land value.",
            heading="Chapter 9",
        )
        bad_short = _make_chunk("Chapter 8\nX", heading="Chapter 8")
        bad_garbled = _make_chunk(
            "Chapter 20\n"
            "lture, Tr\n"
            "l Resour | Column 6\n"
            "Indexed Net | Value\n"
            "(Full Value) | on\n"
            "January 1 | 2011\n"
            "(Column 4 | x Column 5)\n"
            "the tax | year 20\n"
            "0.11(4) | Wis. Stats\n"
            "ade | something\n"
            "more | pipes here\n",
            heading="Chapter 20",
        )
        kept, removed = filter_wpam_chunks([good, bad_short, bad_garbled])
        assert len(kept) == 1
        assert kept[0] is good
        assert len(removed) == 2


# ---------------------------------------------------------------------------
# repair_wpam_subheadings
# ---------------------------------------------------------------------------


class TestRepairWpamSubheadings:
    def test_clears_leaked_subheading(self):
        # Simulate 10 chunks all sharing the same leaked subheading
        chunks = [
            _make_chunk(
                f"Chapter 20\n1. The record requested does not exist.\nContent {i}.",
                heading="Chapter 20",
                subheading="1. The record requested does not exist.",
            )
            for i in range(10)
        ]
        repaired = repair_wpam_subheadings(chunks)
        assert all(c["metadata"]["subheading"] == "" for c in repaired)

    def test_preserves_unique_subheadings(self):
        chunks = [
            _make_chunk(
                "Chapter 9\nA. Sales Comparison Approach\nBody text.",
                heading="Chapter 9",
                subheading="A. Sales Comparison Approach",
            ),
            _make_chunk(
                "Chapter 9\nB. Cost Approach\nBody text.",
                heading="Chapter 9",
                subheading="B. Cost Approach",
            ),
            _make_chunk(
                "Chapter 9\nC. Income Approach\nBody text.",
                heading="Chapter 9",
                subheading="C. Income Approach",
            ),
        ]
        repaired = repair_wpam_subheadings(chunks)
        subs = [c["metadata"]["subheading"] for c in repaired]
        assert subs == ["A. Sales Comparison Approach", "B. Cost Approach", "C. Income Approach"]

    def test_threshold_boundary(self):
        # 5 occurrences = not leaked (at boundary)
        chunks = [
            _make_chunk(
                f"Chapter 20\nDOR\nContent {i}.",
                heading="Chapter 20",
                subheading="DOR",
            )
            for i in range(5)
        ]
        repaired = repair_wpam_subheadings(chunks)
        assert all(c["metadata"]["subheading"] == "DOR" for c in repaired)

    def test_threshold_exceeded(self):
        # 6 occurrences = leaked
        chunks = [
            _make_chunk(
                f"Chapter 20\nDOR\nContent {i}.",
                heading="Chapter 20",
                subheading="DOR",
            )
            for i in range(6)
        ]
        repaired = repair_wpam_subheadings(chunks)
        assert all(c["metadata"]["subheading"] == "" for c in repaired)

    def test_mixed_leaked_and_unique(self):
        leaked_chunks = [
            _make_chunk(
                f"Chapter 20\n1. It must be an educational association.\nCase {i}.",
                heading="Chapter 20",
                subheading="1. It must be an educational association.",
            )
            for i in range(8)
        ]
        unique_chunk = _make_chunk(
            "Chapter 9\nA. Market Data\nDetails here.",
            heading="Chapter 9",
            subheading="A. Market Data",
        )
        repaired = repair_wpam_subheadings(leaked_chunks + [unique_chunk])
        # Leaked ones cleared
        assert all(c["metadata"]["subheading"] == "" for c in repaired[:8])
        # Unique one preserved
        assert repaired[8]["metadata"]["subheading"] == "A. Market Data"

    def test_empty_subheadings_ignored(self):
        chunks = [
            _make_chunk("Chapter 9\nContent.", heading="Chapter 9", subheading=""),
            _make_chunk("Chapter 9\nMore content.", heading="Chapter 9", subheading=""),
        ]
        repaired = repair_wpam_subheadings(chunks)
        assert len(repaired) == 2

    def test_none_subheadings_handled(self):
        chunk = {
            "text": "Chapter 9\nContent.",
            "metadata": {
                "doc_id": "test",
                "heading": "Chapter 9",
                "subheading": None,
                "start_page": 1,
                "end_page": 1,
            },
        }
        repaired = repair_wpam_subheadings([chunk] * 10)
        assert len(repaired) == 10


# ---------------------------------------------------------------------------
# Real data samples (from WPAM 2025 S3 extract)
# ---------------------------------------------------------------------------


class TestRealDataSamples:
    """Test against actual chunk text patterns observed in WPAM 2025."""

    def test_real_garbled_chunk_415(self):
        """Chunk 415 from WPAM 2025 — column-interleaved Board of Review table."""
        text = (
            "Chapter 20 Board of Review and Assessment Appeals\n"
            "1. The record requested does not exist.\n"
            "om | itted property, w | here they appeare | d generally before | "
            "the Boa | rd of Review, purs | uant to the\n"
            "no | tice. |  | Schedule E |  |  |\n"
            "|  | BUIL | DINGS ON LEASED L | AND |  |\n"
            "Report buildi\n"
            "the same ma\n"
            "M | ngs, structures, and oth\n"
            "nner as improvements l\n"
            "ilwaukee Count | er improvements which\n"
            "ocated on land that is o\n"
            "Wis. 637, 242 N | located on\n"
            "opinion of\n"
            ".W. 515 | land that you do not ow\n"
            "value in column 4.\n"
            "(1932). A taxpa | n. They will be valued in\n"
            "yer is not\n"
            "en | Column 1\n"
            "titled to specific | notice\n"
        )
        chunk = _make_chunk(
            text,
            heading="Chapter 20 Board of Review and Assessment Appeals",
            subheading="1. The record requested does not exist.",
        )
        kept, removed = filter_wpam_chunks([chunk])
        assert len(kept) == 0
        assert removed[0]["_filter_reason"] == "garbled_columns"

    def test_real_table_cell_chunk_280(self):
        """Chunk 280 from WPAM 2025 — pure rating values."""
        text = (
            "Chapter 14 Agricultural Valuation\n"
            "V. Poor\n"
            "Good\nGood\nGood\nFair\nGood\nGood\nGood\nGood\n"
            "Good\nGood\nGood\nGood\nGood\nGood\nGood\nGood\n"
            "—\n—\n—\n—\nFair\nPoor\n"
            "Chapter 14 Agricultural Valuation\n"
            "V. Poor\nFair"
        )
        chunk = _make_chunk(
            text,
            heading="Chapter 14 Agricultural Valuation",
            subheading="V. Poor",
        )
        kept, removed = filter_wpam_chunks([chunk])
        assert len(kept) == 0

    def test_real_form_header_chunk(self):
        """Repeated form header chunks from Ch 8 Data Collection."""
        text = (
            "Chapter 8 Data Collection and Reporting\n"
            "DESCRIPTION OF PROPERTY\n"
            "C\nO\nD\nE\n"
            "Chapter 8 Data Collection and Reporting\n"
            "IMPROVEMEN\nT VALUE"
        )
        chunk = _make_chunk(
            text,
            heading="Chapter 8 Data Collection and Reporting",
            subheading="DESCRIPTION OF PROPERTY",
        )
        kept, removed = filter_wpam_chunks([chunk])
        assert len(kept) == 0

    def test_real_good_chunk_preserved(self):
        """A real useful chunk from WPAM 2025 — assessment appeals content."""
        text = (
            "Chapter 20 Board of Review and Assessment Appeals\n"
            "1. The record requested does not exist.\n"
            "2. The record exists, but statutes or court decisions prohibit disclosure of all or part of the\n"
            "record.  A good example of the latter is a real estate transfer form - sec. 77.265(9) prohibits\n"
            "release of that data except to certain authorized individuals.\n"
            "3. The information is protected by federal law or regulation.\n"
            "4. The information is protected by state law."
        )
        chunk = _make_chunk(
            text,
            heading="Chapter 20 Board of Review and Assessment Appeals",
            subheading="1. The record requested does not exist.",
        )
        kept, removed = filter_wpam_chunks([chunk])
        assert len(kept) == 1
        assert len(removed) == 0

    def test_real_case_law_chunk_preserved(self):
        """Case law discussion chunk — should never be filtered."""
        text = (
            "Chapter 20 Board of Review and Assessment Appeals\n"
            "1. The record requested does not exist.\n"
            "Educational\n"
            "Engineers and Scientists of Milwaukee, Inc. v City of Milwaukee, 38 Wis.2d 550, 157\n"
            "N.W.2d 572 (1968). The property was owned by a nonprofit, nonstock corporation. The\n"
            "purpose of the organization was the continuing education and professional advancement of\n"
            "engineers and scientists. The court held that the property was exempt from taxation."
        )
        chunk = _make_chunk(
            text,
            heading="Chapter 20 Board of Review and Assessment Appeals",
            subheading="1. The record requested does not exist.",
        )
        kept, removed = filter_wpam_chunks([chunk])
        assert len(kept) == 1

    def test_real_subheading_leak_44_chunks(self):
        """The leaked subheading pattern from real WPAM 2025 data."""
        chunks = []
        for i in range(44):
            chunks.append(
                _make_chunk(
                    f"Chapter 20 Board of Review and Assessment Appeals\n"
                    f"1. The record requested does not exist.\n"
                    f"Case law content about totally different topics, chunk {i}. "
                    f"The court held that the assessment was valid and the taxpayer "
                    f"failed to meet the burden of proof.",
                    heading="Chapter 20 Board of Review and Assessment Appeals",
                    subheading="1. The record requested does not exist.",
                )
            )
        repaired = repair_wpam_subheadings(chunks)
        assert all(c["metadata"]["subheading"] == "" for c in repaired)

    def test_real_subheading_leak_does_not_affect_content(self):
        """Subheading repair doesn't modify chunk text, only metadata."""
        original_text = (
            "Chapter 20 Board of Review and Assessment Appeals\n"
            "1. The record requested does not exist.\n"
            "The court held that the property was exempt."
        )
        chunks = [
            _make_chunk(
                original_text,
                heading="Chapter 20 Board of Review and Assessment Appeals",
                subheading="1. The record requested does not exist.",
            )
            for _ in range(10)
        ]
        repaired = repair_wpam_subheadings(chunks)
        for c in repaired:
            assert c["text"] == original_text

"""WPAM edition_year extraction.

Two strategies, called in order by the loader:
  1. Parse the LAST 4-digit group from the doc_id (S3 prefix). Cheap,
     deterministic, handles `wpam-...-2025` and `wpam-...-vol-1-2011`.
  2. Scan the first ~3 pages of PDF text for a 4-digit year in a
     plausible range. Used when the prefix lacks a year.

Both failing returns None — the loader then writes the doc without
edition_year and logs a warning. Downstream dedup treats absent
edition_year as "ineligible for dedup, pass through."
"""

import re
from datetime import UTC, datetime

# Plausible range: WPAM editions exist from 2011 onward; allow next year
# for the December-published edition that's effective the following year.
_MIN_YEAR = 2010
_MAX_YEAR_OFFSET = 1

_DOC_ID_YEAR_RE = re.compile(r"(\d{4})(?!.*\d{4})")
_PDF_YEAR_RE = re.compile(r"\b(20\d{2})\b")


def _max_year() -> int:
    return datetime.now(UTC).year + _MAX_YEAR_OFFSET


def _is_plausible(year: int) -> bool:
    return _MIN_YEAR <= year <= _max_year()


def extract_wpam_year_from_doc_id(doc_id: str) -> int | None:
    """Extract edition year from the doc_id (S3 prefix). Returns None
    if doc_id is not a WPAM prefix or has no plausible 4-digit year."""
    if not doc_id.startswith("wpam-"):
        return None
    match = _DOC_ID_YEAR_RE.search(doc_id)
    if not match:
        return None
    year = int(match.group(1))
    return year if _is_plausible(year) else None


def extract_wpam_year_from_pdf_text(text: str) -> int | None:
    """Scan PDF text for the most-frequent plausible 4-digit year.
    The cover/first pages of WPAM mention the edition year multiple times;
    we use frequency to disambiguate from incidentally cited years
    (statute enactment dates, historical references)."""
    if not text:
        return None
    counts: dict[int, int] = {}
    for match in _PDF_YEAR_RE.finditer(text):
        year = int(match.group(1))
        if _is_plausible(year):
            counts[year] = counts.get(year, 0) + 1
    if not counts:
        return None
    # Require at least 2 mentions to count as the edition year — guards
    # against single incidental mentions like "originally enacted 1879".
    most_common_year, count = max(counts.items(), key=lambda kv: kv[1])
    if count < 2:
        return None
    return most_common_year

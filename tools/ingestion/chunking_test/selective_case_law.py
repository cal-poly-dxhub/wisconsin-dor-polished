"""Selective, analysis-focused chunking for Wisconsin case-law opinions.

This experiment deliberately omits low-value text rather than merely assigning it a
lower retrieval tier.  It retains:

* the majority opinion's opening issue/holding synopsis;
* the majority legal analysis, beginning at a confidently detected transition; and
* the majority disposition.

It normally omits captions, counsel lists, detailed facts/procedure, trailing notes,
and separate opinions.  A per-opinion majority-text fallback is used only when the
analysis transition cannot be located reliably.

Run against the local test corpus with:

    python tools/ingestion/chunking_test/selective_case_law.py
"""

from __future__ import annotations

import argparse
import html
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

TARGET_SIZE = 3500
HARD_CAP = 4200
OVERLAP = 160
MIN_TRAILING_CHUNK = 900

# CourtListener text occasionally OCRs the pilcrow as "ś".  Restrict the repair
# to characters immediately preceding a paragraph number.
_OCR_PARA_RE = re.compile(r"[śŚ]\s*(?=\d{1,3}\b)")
_PARA_RE = re.compile(r"¶\s*(\d{1,3})\.?\s*")

_RESULT_RE = re.compile(
    r"\b(?:we\s+)?(?:affirm|reverse|remand|vacate|dismiss|modify)(?:ed|ing)?\b",
    re.IGNORECASE,
)
_HOLDING_RE = re.compile(
    r"\b(?:we\s+(?:hold|conclude|determine|decide|affirm|reverse)|"
    r"for (?:the|these) foregoing reasons)\b",
    re.IGNORECASE,
)

# These are true analysis labels, not words merely occurring in prose.
_ANALYSIS_HEADING_RE = re.compile(
    r"(?:\b[IVX]+\s*[.\-—]\s*"
    r"(Analysis|Discussion|Standard of Review|Legal Analysis|Argument)|"
    r"(?<![a-z])(ANALYSIS|DISCUSSION|STANDARD OF REVIEW|LEGAL ANALYSIS|ARGUMENT))"
    r"(?=\s|[.:\-—])"
)
_LOW_VALUE_HEADING_RE = re.compile(
    r"(?<![a-z])(?:[IVX]+\s*[.\-—]?\s*)?"
    r"(BACKGROUND|FACTS|FACTUAL BACKGROUND|PROCEDURAL HISTORY)"
    r"(?=\s|[.:\-—])"
)

# Older opinions often use issue-specific all-caps headings instead of "ANALYSIS".
# Exclude labels that clearly introduce facts or end matter.
_GENERIC_HEADING_RE = re.compile(
    r"(?:(?<=\.)|(?<=\?)|(?<=\!)|^)\s+"
    r"([A-Z][A-Z0-9'&()\- ]{4,55})\s+(?=[A-Z\[\"“])"
)
_GENERIC_HEADING_BLOCKLIST = {
    "FACTS",
    "BACKGROUND",
    "FACTUAL BACKGROUND",
    "PROCEDURAL HISTORY",
    "NOTES",
    "FOOTNOTES",
}

_ANALYSIS_PHRASES: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(r"\bwe (?:begin|turn(?: next)?|now turn) (?:our )?(?:analysis|discussion)", re.I), 7),
    (re.compile(r"\b(?:standard|scope) of review\b", re.I), 5),
    (re.compile(r"\b(?:interpretation|construction) of (?:a |the )?statute\b", re.I), 4),
    (re.compile(r"\b(?:statutory|constitutional) interpretation\b", re.I), 4),
    (re.compile(r"\b(?:question|issue) (?:presented|before (?:us|this court)|is whether|of law)\b", re.I), 4),
    (re.compile(r"\b(?:pivotal|central|controlling) issue\b", re.I), 4),
    (re.compile(r"\b(?:question|matter) of law\b", re.I), 3),
    (re.compile(r"\bwe (?:hold|conclude|determine|decide)\b", re.I), 3),
    (re.compile(r"\bour (?:first|next|final) inquiry\b", re.I), 3),
    (re.compile(r"\b(?:the statute|section \d|wisconsin stat(?:ute|\.)).*\b(?:provides|requires|means|permits)\b", re.I), 2),
    (re.compile(r"\b(?:appellant|respondent|petitioner|plaintiff|defendant)s? (?:argues?|contends?|asserts?|maintains?)\b", re.I), 2),
)

_FACT_PHRASES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\btestified\b", re.I),
    re.compile(r"\bat approximately \d", re.I),
    re.compile(r"\bon (?:january|february|march|april|may|june|july|august|september|october|november|december) \d", re.I),
    re.compile(r"\b(?:was|were) charged with\b", re.I),
    re.compile(r"\bthe (?:jury|trial court) found\b", re.I),
)

_SEPARATE_OPINION_RE = re.compile(
    r"¶\s*\d+\.?\s+.{0,110}?\b(?:J\.|JUSTICE|CHIEF JUSTICE)\s*"
    r"\((?:concurring|dissenting|concurring in part|dissenting in part)[^)]*\)",
    re.IGNORECASE,
)
_BY_COURT_RE = re.compile(r"\bBy the Court\s*[.\-—:]+", re.IGNORECASE)
_AUTHOR_RE = re.compile(
    r"\b(?:[A-Z][A-Z.'\-]+\s+){0,4}[A-Z][A-Z.'\-]+,\s*(?:CHIEF\s+)?(?:J\.|P\.J\.|JUSTICE)\s+",
)


@dataclass(frozen=True)
class Unit:
    start: int
    end: int
    text: str
    marker: str = ""


@dataclass(frozen=True)
class SelectedRegion:
    start: int
    end: int
    role: str
    reason: str


@dataclass
class SelectiveChunk:
    index: int
    text: str
    role: str
    heading: str
    source_start: int
    source_end: int

    @property
    def char_count(self) -> int:
        return len(self.text)


@dataclass
class SelectionResult:
    normalized_text: str
    majority_text: str
    chunks: list[SelectiveChunk]
    regions: list[SelectedRegion]
    fallback_used: bool
    confidence: float
    analysis_reason: str
    omitted_high_signal: list[str] = field(default_factory=list)

    @property
    def retained_source_chars(self) -> int:
        return sum(region.end - region.start for region in self.regions)

    @property
    def retained_ratio(self) -> float:
        if not self.normalized_text:
            return 0.0
        return self.retained_source_chars / len(self.normalized_text)


@dataclass(frozen=True)
class AnalysisStart:
    offset: int
    confidence: float
    reason: str


def normalize_opinion(text: str) -> str:
    """Normalize entities, whitespace, and known paragraph-marker OCR damage."""
    text = html.unescape(text).replace("\xa0", " ")
    text = _OCR_PARA_RE.sub("¶ ", text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def strip_caption(text: str) -> str:
    """Strip reporter/caption/counsel text without deleting the opinion author."""
    first_para = _PARA_RE.search(text)
    if first_para:
        return text[first_para.start():].strip()

    # Older opinions lack numbered paragraphs.  Their first judicial-author marker
    # is much safer than guessing from caption length.
    author = _AUTHOR_RE.search(text[:9000])
    if author:
        return text[author.start():].strip()
    return text


def _sentence_end(text: str, start: int, limit: int = 700) -> int:
    """Return the end of the first sentence after start, bounded by limit."""
    segment = text[start : start + limit]
    match = re.search(r"[.!?](?:[\"'”’])?(?=\s|$)", segment)
    return start + match.end() if match else min(len(text), start + limit)


def find_majority_end(text: str) -> int:
    """Find the end of the majority disposition, excluding notes/separate opinions."""
    separate = _SEPARATE_OPINION_RE.search(text)
    search_end = separate.start() if separate else len(text)
    searchable = text[:search_end]

    by_court_matches = list(_BY_COURT_RE.finditer(searchable))
    if by_court_matches:
        marker = by_court_matches[-1]
        # Disposition follows the marker and is ordinarily one sentence.
        return _sentence_end(text, marker.end(), 550)

    # Modern opinions sometimes state the final judgment immediately before a
    # separately authored opinion without a "By the Court" line.
    if separate:
        return separate.start()

    # Trim explicit notes/footnotes blocks only when they occur late in the text.
    notes = re.search(r"\b(?:NOTES|FOOTNOTES)\s+\[?1\]?", text, re.IGNORECASE)
    if notes and notes.start() > len(text) * 0.55:
        return notes.start()
    return len(text)


def split_units(text: str) -> list[Unit]:
    """Create numbered-paragraph units or sentence-group units for older cases."""
    markers = list(_PARA_RE.finditer(text))
    if markers:
        return [
            Unit(
                start=marker.start(),
                end=markers[i + 1].start() if i + 1 < len(markers) else len(text),
                text=text[marker.start() : markers[i + 1].start() if i + 1 < len(markers) else len(text)].strip(),
                marker=f"¶{marker.group(1)}",
            )
            for i, marker in enumerate(markers)
        ]

    # Older opinions arrive as a nearly continuous line.  Group complete sentences
    # into roughly paragraph-sized units while preserving source offsets.
    boundaries = [0]
    abbreviation = re.compile(
        r"(?:\bWis|\bStat|\bStats|\bsec|\bSecs|\bart|\bId|\bNo|\bInc|\bCo|\bCt|\bApp|\bJ|\bP\.J)\.$",
        re.IGNORECASE,
    )
    for match in re.finditer(r"[.!?](?:[\"'”’])?(?=\s+[A-Z\[\"“*])", text):
        prefix = text[max(0, match.start() - 12) : match.end()]
        if match.group(0).startswith(".") and abbreviation.search(prefix):
            continue
        boundaries.append(match.end())
    if boundaries[-1] != len(text):
        boundaries.append(len(text))

    units: list[Unit] = []
    group_start = boundaries[0]
    for boundary in boundaries[1:]:
        if boundary - group_start >= 700:
            units.append(Unit(group_start, boundary, text[group_start:boundary].strip()))
            group_start = boundary
    if group_start < len(text):
        units.append(Unit(group_start, len(text), text[group_start:].strip()))
    return [u for u in units if u.text]


def _analysis_score(text: str) -> int:
    score = sum(weight for pattern, weight in _ANALYSIS_PHRASES if pattern.search(text))
    score += min(2, len(re.findall(r"\b(?:Wis\.?\s+Stat|sec\.|§)\s*\d", text, re.I)))
    score += min(2, len(re.findall(r"\b\d{1,3}\s+Wis\.\s*2d\b|\b\d+\s+N\.W\.2d\b", text)))
    score -= min(3, sum(bool(pattern.search(text)) for pattern in _FACT_PHRASES))
    return score


def _intro_end(text: str, units: list[Unit]) -> int:
    """Keep the compact opening issue/holding synopsis but stop before facts."""
    if not units:
        return min(len(text), 2200)

    opening_signal = re.compile(
        r"\b(?:we (?:hold|conclude|determine|decide|affirm|reverse)|"
        r"(?:issue|issues) (?:presented|before (?:us|this court)|is whether)|"
        r"at issue|questions? of law|question of law before (?:us|the court))\b",
        re.IGNORECASE,
    )

    if units[0].marker:
        # The synopsis varies from one to four paragraphs. Keep through the last
        # issue/holding paragraph among ¶¶1-4, rather than blindly retaining four
        # paragraphs (which often includes detailed facts).
        candidates = units[: min(4, len(units))]
        signal_units = [unit for unit in candidates if opening_signal.search(unit.text)]
        if signal_units:
            return signal_units[-1].end
        return candidates[0].end

    # For unnumbered opinions, keep through the last court-voice issue/holding
    # sentence in the opening. Avoid generic words such as "affirming," which
    # usually describe a lower court and previously caused 100-character synopses.
    opening = text[:5000]
    signals = list(opening_signal.finditer(opening))
    if signals:
        return _sentence_end(text, signals[-1].start(), 700)

    issue = re.search(r"\b(?:issue|question).{0,220}?\?", opening, re.I)
    if issue:
        return issue.end()
    return min(len(text), 2200)


def _generic_heading_candidates(text: str, start: int) -> list[tuple[int, str]]:
    candidates: list[tuple[int, str]] = []
    for match in _GENERIC_HEADING_RE.finditer(text, start):
        label = " ".join(match.group(1).split()).strip(" .:-—")
        if label in _GENERIC_HEADING_BLOCKLIST or len(label.split()) > 10:
            continue
        # A substantive heading should be followed by legally dense prose.
        preview = text[match.end() : match.end() + 1200]
        if _analysis_score(preview) >= 2 or re.search(r"\b(?:amendment|tax|restitution|search|jurisdiction|liability|statute)\b", label, re.I):
            candidates.append((match.start(1), label))
    return candidates


def find_analysis_start(text: str, units: list[Unit], intro_end: int) -> AnalysisStart | None:
    """Locate the earliest reliable majority-analysis transition."""
    for match in _ANALYSIS_HEADING_RE.finditer(text, intro_end):
        if match.start() < len(text) * 0.82:
            label = match.group(1) or match.group(2)
            return AnalysisStart(match.start(), 0.99, f"explicit heading: {label}")

    numbered = bool(units and units[0].marker)
    if not numbered:
        generic = _generic_heading_candidates(text, intro_end)
        if generic:
            offset, label = generic[0]
            return AnalysisStart(offset, 0.91, f"substantive heading: {label}")

    strong_patterns = (
        (re.compile(r"\bwe (?:begin|turn(?: next)?|now turn) (?:our )?(?:analysis|discussion)", re.I), 0.96, "analysis transition phrase"),
        (re.compile(r"\bin part [IVX0-9]+.{0,100}?\bwe analyze\b", re.I), 0.95, "opinion roadmap to analysis"),
        (re.compile(r"\b(?:we perceive|the pivotal|the central|the controlling) issue\b", re.I), 0.90, "issue-framing transition"),
        (re.compile(r"\b(?:at the outset,? we|we first address|we now address)", re.I), 0.88, "court transition phrase"),
        (re.compile(r"\b(?:the issue|the question) (?:presented|before (?:us|this court)|is whether)\b", re.I), 0.84, "issue statement"),
    )

    eligible = [unit for unit in units if unit.end > intro_end]
    scores = [_analysis_score(unit.text) for unit in eligible]
    for i, (unit, score) in enumerate(zip(eligible, scores)):
        # Evaluate in document order so a preliminary issue is not skipped in favor
        # of a later paragraph literally saying "we begin our analysis."
        for pattern, confidence, reason in strong_patterns:
            if pattern.search(unit.text):
                return AnalysisStart(unit.start, confidence, reason)
        if score >= 7:
            return AnalysisStart(unit.start, 0.86, f"strong legal-density score {score}")
        neighborhood = scores[i : i + 3]
        if score >= 4 and sum(value >= 2 for value in neighborhood) >= 2:
            return AnalysisStart(unit.start, 0.78, f"sustained legal-density score {score}")
    return None


def _merge_regions(regions: list[SelectedRegion]) -> list[SelectedRegion]:
    ordered = sorted((r for r in regions if r.end > r.start), key=lambda r: r.start)
    merged: list[SelectedRegion] = []
    for region in ordered:
        if merged and region.start <= merged[-1].end + 80:
            prior = merged[-1]
            merged[-1] = SelectedRegion(
                prior.start,
                max(prior.end, region.end),
                f"{prior.role}+{region.role}" if region.role not in prior.role else prior.role,
                f"{prior.reason}; {region.reason}",
            )
        else:
            merged.append(region)
    return merged


def _covered(offset: int, regions: list[SelectedRegion]) -> bool:
    return any(region.start <= offset < region.end for region in regions)


def _high_signal_omissions(text: str, regions: list[SelectedRegion], end: int) -> list[str]:
    omissions: list[str] = []
    for match in _HOLDING_RE.finditer(text[:end]):
        if _covered(match.start(), regions):
            continue
        sentence_start = max(text.rfind(". ", 0, match.start()) + 2, 0)
        sentence_end = _sentence_end(text, match.start(), 450)
        snippet = " ".join(text[sentence_start:sentence_end].split())
        omissions.append(snippet[:300])
    return omissions


def _split_for_chunking(text: str, source_start: int) -> list[Unit]:
    units = split_units(text)
    return [Unit(u.start + source_start, u.end + source_start, u.text, u.marker) for u in units]


def _clean_overlap(text: str, overlap: int) -> str:
    if overlap <= 0 or not text:
        return ""
    tail = text[-overlap:]
    sentence = re.search(r"(?<=[.!?])\s+", tail)
    return tail[sentence.end() :] if sentence else tail


def _force_split_text(text: str, target: int, hard_cap: int) -> list[str]:
    pieces: list[str] = []
    remaining = text.strip()
    while len(remaining) > hard_cap:
        window = remaining[:hard_cap]
        breaks = list(re.finditer(r"(?<=[.!?])\s+", window[max(0, target - 350) :]))
        if breaks:
            cut = max(0, target - 350) + min(
                breaks, key=lambda m: abs((max(0, target - 350) + m.end()) - target)
            ).end()
        else:
            cut = target
        pieces.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        pieces.append(remaining)
    return pieces


def chunk_regions(
    text: str,
    regions: list[SelectedRegion],
    target_size: int = TARGET_SIZE,
    hard_cap: int = HARD_CAP,
    overlap: int = OVERLAP,
) -> list[SelectiveChunk]:
    """Chunk selected regions at paragraph/sentence boundaries."""
    chunks: list[SelectiveChunk] = []
    for region in regions:
        region_text = text[region.start : region.end].strip()
        units = _split_for_chunking(region_text, region.start)
        current: list[Unit] = []
        current_text = ""

        def emit() -> None:
            nonlocal current, current_text
            if not current_text:
                return
            marker_values = [u.marker for u in current if u.marker]
            heading = region.role
            if marker_values:
                heading += f" {marker_values[0]}–{marker_values[-1]}"
            for piece in _force_split_text(current_text, target_size, hard_cap):
                chunks.append(
                    SelectiveChunk(
                        index=len(chunks),
                        text=piece,
                        role=region.role,
                        heading=heading,
                        source_start=current[0].start if current else region.start,
                        source_end=current[-1].end if current else region.end,
                    )
                )
            current = []
            current_text = ""

        for unit in units:
            candidate = f"{current_text}\n\n{unit.text}".strip() if current_text else unit.text
            if current and len(candidate) > target_size:
                previous = current_text
                emit()
                overlap_text = _clean_overlap(previous, overlap)
                current_text = f"{overlap_text}\n\n{unit.text}".strip() if overlap_text else unit.text
                current = [unit]
            else:
                current_text = candidate
                current.append(unit)
        emit()

    # Compact small boundary chunks. This keeps short exact holding synopses, but
    # pairs them with the beginning of the legal analysis instead of creating weak
    # 200-700 character embeddings. It also rejoins target-size splits when the
    # resulting chunk still fits under the hard cap.
    index = 0
    while index < len(chunks) - 1:
        left, right = chunks[index], chunks[index + 1]
        compatible = left.role == right.role or (
            left.role == "opening_holding" and "analysis_holding" in right.role
        )
        one_is_small = min(left.char_count, right.char_count) < MIN_TRAILING_CHUNK
        if compatible and one_is_small and left.char_count + 2 + right.char_count <= hard_cap:
            left.text = f"{left.text}\n\n{right.text}"
            left.source_end = right.source_end
            if left.role != right.role:
                left.role = f"{left.role}+{right.role}"
                left.heading = left.role
            chunks.pop(index + 1)
            continue
        index += 1

    for index, chunk in enumerate(chunks):
        chunk.index = index
    return chunks


def select_and_chunk(text: str) -> SelectionResult:
    normalized = strip_caption(normalize_opinion(text))
    majority_end = find_majority_end(normalized)
    majority = normalized[:majority_end].strip()
    units = split_units(majority)
    intro_end = min(_intro_end(majority, units), len(majority))
    analysis = find_analysis_start(majority, units, intro_end)

    fallback = False
    if analysis is None:
        fallback = True
        confidence = 0.0
        reason = "no reliable analysis transition"
        regions = [SelectedRegion(0, len(majority), "fallback_majority", reason)]
    else:
        confidence = analysis.confidence
        reason = analysis.reason
        regions = _merge_regions(
            [
                SelectedRegion(0, intro_end, "opening_holding", "opening issue/holding synopsis"),
                SelectedRegion(analysis.offset, len(majority), "analysis_holding", analysis.reason),
            ]
        )
        # A very late heuristic transition is demonstrably unsafe: it would retain
        # too little of a substantial majority opinion. Explicit headings are exempt.
        selected_ratio = sum(r.end - r.start for r in regions) / max(1, len(majority))
        if confidence < 0.84 and selected_ratio < 0.38 and len(majority) > 9000:
            fallback = True
            reason = f"unsafe late transition ({reason}, retained {selected_ratio:.0%})"
            regions = [SelectedRegion(0, len(majority), "fallback_majority", reason)]

    omissions = _high_signal_omissions(majority, regions, len(majority))
    # Missing majority-voice holding language is direct evidence that selection is
    # unsafe, so use the fallback instead of accepting degraded recall.
    if omissions and not fallback:
        fallback = True
        reason = f"guardrail: {len(omissions)} holding-signal sentence(s) omitted"
        regions = [SelectedRegion(0, len(majority), "fallback_majority", reason)]
        omissions = []

    chunks = chunk_regions(majority, regions)
    return SelectionResult(
        normalized_text=normalized,
        majority_text=majority,
        chunks=chunks,
        regions=regions,
        fallback_used=fallback,
        confidence=confidence,
        analysis_reason=reason,
        omitted_high_signal=omissions,
    )


def _baseline_chunk_count(text: str, target: int = TARGET_SIZE, overlap: int = OVERLAP) -> int:
    """Approximate whole-opinion paragraph-aware baseline with the same target."""
    units = split_units(text)
    if not units:
        return 0
    count = 1
    size = 0
    for unit in units:
        addition = len(unit.text) + (2 if size else 0)
        if size and size + addition > target:
            count += 1
            size = min(overlap, size) + addition
        else:
            size += addition
    return count


def evaluate_file(path: Path) -> dict:
    raw = path.read_text(errors="replace")
    result = select_and_chunk(raw)
    baseline = _baseline_chunk_count(result.normalized_text)
    selected_count = len(result.chunks)
    return {
        "file": path.name,
        "raw_chars": len(raw),
        "normalized_chars": len(result.normalized_text),
        "majority_chars": len(result.majority_text),
        "retained_chars": result.retained_source_chars,
        "retained_pct": round(result.retained_ratio * 100, 1),
        "baseline_chunks": baseline,
        "selected_chunks": selected_count,
        "chunk_reduction_pct": round((1 - selected_count / baseline) * 100, 1) if baseline else 0,
        "fallback": result.fallback_used,
        "confidence": result.confidence,
        "analysis_reason": result.analysis_reason,
        "regions": [asdict(region) for region in result.regions],
        "chunk_sizes": [chunk.char_count for chunk in result.chunks],
        "first_selected": " ".join(result.chunks[0].text[:180].split()) if result.chunks else "",
        "first_analysis": next(
            (" ".join(chunk.text[:240].split()) for chunk in result.chunks if "analysis" in chunk.role),
            "",
        ),
        "last_selected": " ".join(result.chunks[-1].text[-220:].split()) if result.chunks else "",
        "omitted_high_signal": result.omitted_high_signal,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate selective case-law chunking")
    parser.add_argument(
        "--samples-dir",
        type=Path,
        default=Path(__file__).with_name("samples"),
    )
    parser.add_argument("--file", type=Path)
    parser.add_argument("--json", type=Path, help="Write complete evaluation JSON")
    args = parser.parse_args()

    files = [args.file] if args.file else sorted(args.samples_dir.glob("*.txt"))
    reports = [evaluate_file(path) for path in files]

    print(
        f"{'opinion':22} {'keep':>6} {'chunks':>9} {'reduce':>7} "
        f"{'fallback':>8}  transition"
    )
    print("-" * 100)
    for report in reports:
        print(
            f"{report['file']:22} {report['retained_pct']:5.1f}% "
            f"{report['selected_chunks']:3}/{report['baseline_chunks']:<3} "
            f"{report['chunk_reduction_pct']:6.1f}% "
            f"{str(report['fallback']):>8}  {report['analysis_reason']}"
        )

    total_baseline = sum(r["baseline_chunks"] for r in reports)
    total_selected = sum(r["selected_chunks"] for r in reports)
    total_chars = sum(r["normalized_chars"] for r in reports)
    total_retained = sum(r["retained_chars"] for r in reports)
    print("-" * 100)
    print(
        f"TOTAL: retained {total_retained / max(1, total_chars):.1%} text; "
        f"chunks {total_baseline} → {total_selected} "
        f"({1 - total_selected / max(1, total_baseline):.1%} reduction); "
        f"fallbacks {sum(r['fallback'] for r in reports)}/{len(reports)}"
    )

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(reports, indent=2))
        print(f"Wrote {args.json}")


if __name__ == "__main__":
    main()

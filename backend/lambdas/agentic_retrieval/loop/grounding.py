"""Ground the answer plan's statute citations before the judge and writer.

The research prompt already says that a section the answer will cite must have
been retrieved, but nothing enforced it: a plan can cite a section it only saw
described in a guide, so the section's own text (its conditions, exceptions,
"does not apply if" clauses) never reaches the adequacy judge or the writer,
and the writer states the rule without its qualifiers. This step makes the
rule mechanical and general: for each statute section the plan cites whose
text was not retrieved, fetch that section's substantive subsections from the
graph and add the chapter to the cited documents, so the section is in the
judge's and the writer's context like any section the agent fetched itself.

No section, chapter or topic is special-cased. A failure never blocks the
answer: anything that cannot be resolved is skipped and reported.
"""

from __future__ import annotations

import re

from .link_repair import KNOWN_STATUTE_CHAPTERS

# "§ 74.37(4)(a)", "s. 70.995", "sec. 70.1105", "§§ 70.47" -> chapter, section, subsections
_SECTION_REF_RE = re.compile(
    r"(?:§§?|\bss?\.|\bsec(?:tion)?s?\.?)\s*(\d{1,3})\.(\d{2,4}[a-z]?)((?:\s*\(\w+\))*)",
    re.IGNORECASE,
)
_SUBSECTION_RE = re.compile(r"\((\w+)\)")
_LEADING_INT_RE = re.compile(r"^(\d+)")

# Bounds: a plan naming many sections should not balloon the context.
MAX_SECTIONS = 3
MAX_CHUNKS_PER_SECTION = 4


def plan_section_refs(plan: str) -> list[tuple[str, str, str | None]]:
    """``(chapter, section, top-level subsection or None)`` in plan order, deduplicated.

    A section cited both bare and with a subsection keeps the first form seen,
    upgraded to the subsection when one appears.
    """
    seen: dict[str, tuple[str, str, str | None]] = {}
    for m in _SECTION_REF_RE.finditer(plan or ""):
        chapter, num, tail = m.group(1), m.group(2), m.group(3) or ""
        section = f"{chapter}.{num}"
        subs = _SUBSECTION_RE.findall(tail)
        sub = subs[0] if subs else None
        if section not in seen:
            seen[section] = (chapter, section, sub)
        elif sub and seen[section][2] is None:
            seen[section] = (chapter, section, sub)
    return list(seen.values())


def section_of_heading(heading: str | None) -> str | None:
    """``"74.37 Claim on excessive assessment."`` -> ``"74.37"``."""
    m = re.match(r"^(\d{1,3}\.\d{2,4}[a-z]?)(?:\s|$)", (heading or "").strip())
    return m.group(1) if m else None


def _int_prefix(token: str) -> int | None:
    m = _LEADING_INT_RE.match(token)
    return int(m.group(1)) if m else None


def subheading_covers(subheading: str | None, section: str, sub: str) -> bool:
    """Whether a chunk's subheading (``"74.37(1)–(2)"``, ``"74.37(4)"``) covers ``sub``."""
    if not subheading or not subheading.startswith(section):
        return False
    rest = subheading[len(section) :]
    tokens = _SUBSECTION_RE.findall(rest)
    if not tokens:
        return False
    if len(tokens) >= 2 and re.search(r"[–-]", rest):
        lo, hi, want = _int_prefix(tokens[0]), _int_prefix(tokens[-1]), _int_prefix(sub)
        if lo is not None and hi is not None and want is not None:
            return lo <= want <= hi
    return tokens[0] == sub


def _pick_chunks(chunks: list[dict], section: str, sub: str | None) -> list[dict]:
    """The section's substantive subsections (not history or case notes), the named one first."""
    substantive = [c for c in chunks if (c.get("subheading") or "").startswith(section)]
    pool = substantive or chunks
    if sub:
        named = [c for c in pool if subheading_covers(c.get("subheading"), section, sub)]
        if named:
            rest = [c for c in pool if c not in named]
            pool = named + rest
    return pool[:MAX_CHUNKS_PER_SECTION]


def ground_plan_statutes(
    plan: str,
    cited_doc_ids: list[str],
    all_chunks: list[dict],
    neptune,
    list_sections,
    known_chapters: frozenset[str] = KNOWN_STATUTE_CHAPTERS,
) -> tuple[list[str], list[dict], dict]:
    """Return ``(cited_doc_ids, all_chunks, report)`` with the plan's cited sections grounded.

    ``list_sections(neptune, doc_id)`` returns a document's headings (cached by
    the caller). Inputs are not mutated.
    """
    cited = list(cited_doc_ids)
    chunks = list(all_chunks)
    report: dict = {"fetched": [], "already_retrieved": [], "skipped": []}
    have_ids = {c.get("chunk_id") for c in chunks if c.get("chunk_id")}
    fetched_sections = 0

    for chapter, section, sub in plan_section_refs(plan):
        doc_id = f"statutes-{chapter}"
        if chapter not in known_chapters:
            report["skipped"].append({"section": section, "reason": "chapter not in corpus"})
            continue
        if any(
            c.get("doc_id") == doc_id and section_of_heading(c.get("heading")) == section
            for c in chunks
        ):
            report["already_retrieved"].append(section)
            if doc_id not in cited:
                cited.append(doc_id)
            continue
        if fetched_sections >= MAX_SECTIONS:
            report["skipped"].append({"section": section, "reason": "section cap"})
            continue
        try:
            heading = next(
                (
                    s.get("heading")
                    for s in list_sections(neptune, doc_id)
                    if section_of_heading(s.get("heading")) == section
                ),
                None,
            )
            if not heading:
                report["skipped"].append({"section": section, "reason": "no such section"})
                continue
            section_chunks = neptune.get_section_chunks(doc_id, heading) or []
        except Exception as exc:  # noqa: BLE001 — grounding must never block the answer
            report["skipped"].append({"section": section, "reason": type(exc).__name__})
            continue

        picked = [
            c
            for c in _pick_chunks(section_chunks, section, sub)
            if c.get("chunk_id") not in have_ids
        ]
        if not picked:
            report["skipped"].append({"section": section, "reason": "no chunks"})
            continue
        for c in picked:
            chunks.append({**c, "doc_id": c.get("doc_id") or doc_id})
            have_ids.add(c.get("chunk_id"))
        if doc_id not in cited:
            cited.append(doc_id)
        fetched_sections += 1
        report["fetched"].append(
            {"section": section, "subsection": sub, "chunks": len(picked), "doc_id": doc_id}
        )

    return cited, chunks, report

"""Deterministic post-generation repair of inline `doc:` citation links.

Phase B occasionally conflates a section number with the wrong document:
``[§ 61.19](doc:wpam-...#page=20)`` (a statute section linked to the WPAM)
or ``[§ 340.01(6m)](doc:statutes-70#page=14)`` (a chapter not in the corpus
linked to ch. 70). The LLM-judge rubric fails such answers ("chapter-conflated
citation"). This module fixes them mechanically — it never invents a link.

Rules (only links whose text carries a section number are touched):

* Statute text (``§ 61.19``, ``s. 70.32(2)``, ``Wis. Stat. § 73.03``, or a
  ``ch. N`` / ``chapter N`` label on a link that already targets a statute):
  the target must be ``statutes-N``. If it is not, repoint when ``statutes-N``
  is retrieved or is a known corpus chapter (the frontend resolves any
  ``statutes-N`` link to the official legislature PDF); otherwise strip the
  link and keep the plain text. ``#page=`` is kept only when a retrieved chunk
  of ``statutes-N`` resolves that section; otherwise the fragment is dropped.
* Admin-rule text (``Tax 18.06``): the target must be ``admin_rules-tax-N``.
  Repoint when that doc is retrieved; otherwise strip (the frontend cannot
  resolve a non-card, non-statute doc link anyway).

Pure and unit-testable: :func:`repair_citation_links` does no I/O and no
logging; callers log the returned stats under ``answer_link_repaired``.
"""

from __future__ import annotations

import re

# Mirrors the `statutes` category of tools/ingestion/config/document_manifest.yaml.
# Used only to decide repoint-vs-strip for chapters that were not retrieved:
# the frontend resolves `doc:statutes-N` to the legislature PDF for any N, so
# repointing to an in-corpus chapter is safe; an out-of-corpus chapter (e.g.
# 340) is stripped instead of fabricated.
KNOWN_STATUTE_CHAPTERS: frozenset[str] = frozenset(
    {
        "17", "19", "33", "38", "59", "60", "61", "62", "66", "69", "70", "73",
        "74", "75", "76", "77", "79", "120", "121", "165", "200", "706", "757",
        "943",
    }
)  # fmt: skip

_LINK_RE = re.compile(r"\[([^\]]+)\]\(doc:([^)#\s]+)(#page=(\d+))?\)")
_STATUTES_DOC_RE = re.compile(r"^statutes-(\d+)$")
_ADMIN_DOC_RE = re.compile(r"^admin_rules-tax-(\d+)$")

# A statute section number with an explicit statute marker. Bare "N.NN"
# is deliberately NOT matched here (it would hit "Tax 18.06", "2.5 acres",
# "$1.50"); see _STATUTE_BARE_RE for links that already target a statute.
_STATUTE_MARKED_RE = re.compile(
    r"(?:§§?|\bss?\.|\bsec(?:tion)?s?\.?|\bWis\.?\s*Stats?\.?(?:\s*§§?)?)\s*(\d+)\.\d+",
    re.IGNORECASE,
)
_STATUTE_BARE_RE = re.compile(r"(?<![\d.$])(\d+)\.\d+")
_CHAPTER_RE = re.compile(r"\b(?:ch\.|chapter)\s*(\d+)\b", re.IGNORECASE)
_ADMIN_RULE_RE = re.compile(r"\bTax\s+(\d+)\.\d+")

# Canonical statute section heading, as produced by the statute chunker
# ("70.32 Real estate, how valued.").
_SECTION_HEADING_RE = re.compile(r"^(\d+\.\d+[A-Za-z\-]*)(?:\s|$)")


def _statute_chapter_in_text(text: str, target_is_statute: bool) -> str | None:
    """Return the single statute chapter named by a link's text, else None.

    None also when the text names two different chapters (ambiguous — leave
    the link alone rather than guess).
    """
    if _ADMIN_RULE_RE.search(text):
        return None
    chapters = {m.group(1) for m in _STATUTE_MARKED_RE.finditer(text)}
    if not chapters and target_is_statute:
        chapters = {m.group(1) for m in _STATUTE_BARE_RE.finditer(text)}
    if not chapters:
        # "ch. N" / "chapter N" is only a statute reference when the link
        # already targets a statute or the text says Wis. Stat.; a WPAM link
        # labelled "Chapter 13" is a manual chapter, not ch. 13 Stats.
        if target_is_statute or re.search(r"\bWis\.?\s*Stats?\b", text, re.IGNORECASE):
            chapters = {m.group(1) for m in _CHAPTER_RE.finditer(text)}
    if len(chapters) != 1:
        return None
    return chapters.pop()


def _section_numbers_in_text(text: str, chapter: str) -> list[str]:
    """All `chapter.section` numbers in the link text, e.g. ['61.19']."""
    return re.findall(rf"\b({re.escape(chapter)}\.\d+)", text)


def resolve_section_page(
    chapter: str, sections: list[str], chunks: list[dict] | None
) -> int | None:
    """Best-effort page for one of `sections` from the retrieved statute chunks.

    Prefers a chunk whose canonical heading IS the section; falls back to a
    chunk whose text mentions it. Returns None when nothing resolves — the
    caller then drops `#page=` rather than keep a wrong page.
    """
    if not chunks or not sections:
        return None
    wanted = set(sections)
    by_heading: int | None = None
    by_text: int | None = None
    for chunk in chunks:
        page = chunk.get("start_page")
        if not page:
            continue
        m = _SECTION_HEADING_RE.match(chunk.get("heading") or "")
        if m and m.group(1) in wanted:
            if by_heading is None or page < by_heading:
                by_heading = page
            continue
        if by_text is None:
            text = chunk.get("text") or ""
            if any(re.search(rf"(?<![\d.]){re.escape(s)}(?![\d])", text) for s in wanted):
                by_text = page
    return by_heading if by_heading is not None else by_text


def repair_citation_links(
    answer: str,
    retrieved_doc_ids: set[str],
    chunks_by_doc: dict[str, list[dict]],
    *,
    known_statute_chapters: frozenset[str] = KNOWN_STATUTE_CHAPTERS,
) -> tuple[str, dict]:
    """Repair conflated statute / admin-rule links in a Markdown answer.

    Returns ``(repaired_answer, stats)`` where stats is
    ``{"repointed": int, "stripped": int, "changes": [{...}, ...]}`` and each
    change records ``kind``, ``text``, ``from`` and ``to`` (``to`` is None for
    a strip). Links whose text has no section number are never touched.
    """
    changes: list[dict] = []

    def _fix(m: re.Match) -> str:
        text, doc_id = m.group(1), m.group(2)
        original = m.group(0)

        admin = _ADMIN_RULE_RE.search(text)
        if admin:
            chapter = admin.group(1)
            target = _ADMIN_DOC_RE.match(doc_id)
            if target and target.group(1) == chapter:
                return original
            want = f"admin_rules-tax-{chapter}"
            if want in retrieved_doc_ids:
                changes.append({"kind": "repointed", "text": text, "from": doc_id, "to": want})
                return f"[{text}](doc:{want})"
            changes.append({"kind": "stripped", "text": text, "from": doc_id, "to": None})
            return text

        target = _STATUTES_DOC_RE.match(doc_id)
        chapter = _statute_chapter_in_text(text, target_is_statute=target is not None)
        if chapter is None:
            return original
        if target and target.group(1) == chapter:
            return original

        want = f"statutes-{chapter}"
        if want in retrieved_doc_ids or chapter in known_statute_chapters:
            resolved = resolve_section_page(
                chapter, _section_numbers_in_text(text, chapter), chunks_by_doc.get(want)
            )
            fragment = f"#page={resolved}" if resolved else ""
            changes.append({"kind": "repointed", "text": text, "from": doc_id, "to": want})
            return f"[{text}](doc:{want}{fragment})"

        changes.append({"kind": "stripped", "text": text, "from": doc_id, "to": None})
        return text

    repaired = _LINK_RE.sub(_fix, answer)
    stats = {
        "repointed": sum(1 for c in changes if c["kind"] == "repointed"),
        "stripped": sum(1 for c in changes if c["kind"] == "stripped"),
        "changes": changes,
    }
    return repaired, stats


_OPEN_LINK_RE = re.compile(r"\[[^\]]*$|\]\([^)]*$")


def has_open_link(text: str) -> bool:
    """True when `text` ends inside an unfinished ``[label](target)`` link.

    Used by the streaming flush so a link is never split across fragments —
    each complete link can then be repaired before it goes over the wire.
    """
    return _OPEN_LINK_RE.search(text) is not None

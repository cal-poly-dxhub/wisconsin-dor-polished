"""Deterministic post-generation repair of inline `doc:` citation links.

Phase B occasionally conflates a section number with the wrong document:
``[§ 61.19](doc:wpam-...#page=20)`` (a statute section linked to the WPAM)
or ``[§ 340.01(6m)](doc:statutes-70#page=14)`` (a chapter not in the corpus
linked to ch. 70). The LLM-judge rubric fails such answers ("chapter-conflated
citation"). This module fixes them mechanically — it never invents a link.

Rules (only links whose text carries a section number are touched). The first
question is what the *label* is: a bare citation ("§ 70.32(2)(c)1g") or a
claim-length sentence that merely names the section it interprets ("a partially
constructed building cannot, as a matter of law, be tax exempt under
§ 70.11(4m)"). :func:`classify_link_label` decides; the two get different
treatment because the ``answerStream`` prompt asks for exactly that split.

* Statute text (``§ 61.19``, ``s. 70.32(2)``, ``Wis. Stat. § 73.03``, or a
  ``ch. N`` / ``chapter N`` label on a link that already targets a statute):

  - label classified ``"citation"``: the target must be ``statutes-N``. If it
    is not, repoint when ``statutes-N`` is retrieved or is a known corpus
    chapter (the frontend resolves any ``statutes-N`` link to the official
    legislature PDF); otherwise strip the link and keep the plain text.
    ``#page=`` is kept only when a retrieved chunk of ``statutes-N`` resolves
    that section; otherwise the fragment is dropped.
  - label classified ``"claim"`` on a target that IS a retrieved document: the
    claim belongs to the quoting document, so the target is kept and the
    statute is added as a SECOND target (``kind="dual"``). The frontend renders
    a dual link in its own tone and offers both destinations on click.
* Admin-rule text (``Tax 18.06``): the target must be ``admin_rules-tax-N``.
  Repoint when that doc is retrieved; otherwise strip (the frontend cannot
  resolve a non-card, non-statute doc link anyway). A claim-length label on a
  retrieved document becomes a dual link the same way.

Dual-link href grammar (one ``ref`` only, appended inside the fragment)::

    [label](doc:<primary>#page=<p>&ref=<secondary>#page=<n>)   primary has a page
    [label](doc:<primary>#ref=<secondary>#page=<n>)            primary has none

``#page=<n>`` on the ref is omitted when no retrieved chunk and no section
index resolves the section. A link that already carries ``ref=`` is left
untouched, so the pass stays idempotent across the stream and final stages.

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

# The fragment is captured whole (not just `#page=N`) so a dual link's
# `&ref=...#page=N` tail is seen by the matcher instead of making the link
# invisible to it.
_LINK_RE = re.compile(r"\[([^\]]+)\]\(doc:([^)#\s]+)((?:#[^)\s]*)?)\)")
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

# ── Label classification ─────────────────────────────────────────────────────
# A section reference as it appears inside a link label: an optional marker
# (§, s., sec., Wis. Stat., Tax, ch.) then `N.NN` with any subsection tail
# ("(2)(c)1g", "(4m)"). Used only to subtract the citation from the label.
_LABEL_REF_RE = re.compile(
    r"(?:§§?|\bss?\.|\bsec(?:tion)?s?\.?|\bWis\.?\s*Stats?\.?(?:\s*§§?)?|\bTax\b|\bch\.|\bchapter)?"
    r"\s*\d+\.\s*\d+[A-Za-z\-]*(?:\s*\([^()]*\))*(?:\s*\d+[a-z]*)?",
    re.IGNORECASE,
)
# A bare chapter form ("ch. 70", "chapter 70") with no section number.
_LABEL_CHAPTER_RE = re.compile(r"(?:§§?\s*)?\b(?:ch\.|chapters?)\s*\d+", re.IGNORECASE)
# Glue that carries no meaning of its own once the citation is subtracted.
_LABEL_GLUE: frozenset[str] = frozenset(
    {
        "under", "see", "per", "of", "the", "a", "an", "in", "at", "to", "on",
        "pursuant", "wis", "wisc", "wisconsin", "stat", "stats", "statute",
        "statutes", "s", "ss", "sec", "secs", "section", "sections", "subsec",
        "subsection", "subsections", "sub", "subs", "ch", "chapter", "chapters",
        "admin", "administrative", "code", "para", "paragraph",
    }
)  # fmt: skip
# Words left after subtraction; at most this many still reads as a citation
# ("§ 70.47 Board of review" -> "Board review").
_LABEL_CITATION_MAX_WORDS = 2
_LABEL_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'\-]*")


def classify_link_label(text: str, chapter: str) -> str:
    """``"citation"`` when the label is essentially just the section reference.

    Subtract the statute/admin-rule reference(s), any bare ``ch. N`` form and
    common glue from the label; what remains is the label's own content. Two
    words or fewer means the label IS the citation ("§ 70.32(2)(c)1g",
    "s. 74.37", "Wis. Stat. § 73.03", "§ 70.47 Board of review") and the link
    must point at the statute. Anything longer is a ``"claim"`` — a sentence
    the quoting document supports, which merely names the section it
    interprets, and which therefore keeps its own target.

    ``chapter`` is the chapter the caller resolved from the label; its
    references are subtracted first so OCR-spaced forms ("70. 32") count too.
    """
    stripped = re.sub(
        rf"(?<![\d.]){re.escape(chapter)}\.\s*\d+[A-Za-z\-]*(?:\s*\([^()]*\))*(?:\s*\d+[a-z]*)?",
        " ",
        text,
    )
    stripped = _LABEL_REF_RE.sub(" ", stripped)
    stripped = _LABEL_CHAPTER_RE.sub(" ", stripped)
    words = [w for w in _LABEL_WORD_RE.findall(stripped) if w.lower() not in _LABEL_GLUE]
    return "citation" if len(words) <= _LABEL_CITATION_MAX_WORDS else "claim"


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
    section_pages: dict[str, dict[str, int]] | None = None,
) -> tuple[str, dict]:
    """Repair conflated statute / admin-rule links in a Markdown answer.

    Returns ``(repaired_answer, stats)`` where stats is
    ``{"repointed": int, "stripped": int, "paged": int, "dual": int,
    "changes": [{...}, ...]}`` and each change records ``kind``, ``text``,
    ``from`` and ``to`` (``to`` is None for a strip, and for a ``dual`` it is
    the whole new target including the ``&ref=`` tail). Links whose text has no
    section number are never touched.

    ``section_pages`` (chapter -> {section -> first page}, from
    ``phase_b.statute_section_pages``) lets a statute link written WITHOUT a
    page, e.g. ``[§ 74.37](doc:statutes-74)``, get its ``#page=N`` filled in
    (``kind="paged"``) so the reader lands on the section instead of page 1 of
    the chapter PDF. Pages are never invented: no index entry, no fragment.
    """
    changes: list[dict] = []
    section_pages = section_pages or {}

    def _index_page(chapter: str, sections: list[str]) -> int | None:
        pages = section_pages.get(chapter) or {}
        for sec in sections:
            if sec in pages:
                return pages[sec]
        return None

    def _dual_target(doc_id: str, fragment: str, ref_doc: str, ref_page: int | None) -> str:
        """`<primary>#page=<p>&ref=<secondary>#page=<n>` (see module docstring)."""
        tail = f"&ref={ref_doc}" if fragment else f"#ref={ref_doc}"
        if ref_page:
            tail += f"#page={ref_page}"
        return f"{doc_id}{fragment}{tail}"

    def _fix(m: re.Match) -> str:
        text, doc_id, fragment = m.group(1), m.group(2), m.group(3) or ""
        original = m.group(0)

        # Already a dual link (this pass ran on the fragment during streaming).
        if "ref=" in fragment:
            return original

        admin = _ADMIN_RULE_RE.search(text)
        if admin:
            chapter = admin.group(1)
            target = _ADMIN_DOC_RE.match(doc_id)
            if target and target.group(1) == chapter:
                return original
            want = f"admin_rules-tax-{chapter}"
            if want not in retrieved_doc_ids:
                changes.append({"kind": "stripped", "text": text, "from": doc_id, "to": None})
                return text
            if classify_link_label(text, chapter) == "claim" and doc_id in retrieved_doc_ids:
                # The claim belongs to the quoting document; the rule rides
                # along as a second target (admin-rule refs carry no page).
                to = _dual_target(doc_id, fragment, want, None)
                changes.append(
                    {"kind": "dual", "text": text, "from": doc_id, "to": to, "ref": want}
                )
                return f"[{text}](doc:{to})"
            changes.append({"kind": "repointed", "text": text, "from": doc_id, "to": want})
            return f"[{text}](doc:{want})"

        target = _STATUTES_DOC_RE.match(doc_id)
        chapter = _statute_chapter_in_text(text, target_is_statute=target is not None)
        if chapter is None:
            return original
        if target and target.group(1) == chapter:
            if fragment:
                return original
            # Right chapter, no page: fill it from the retrieved chunks or the
            # chapter's section index (the writer was shown the same index).
            sections = _section_numbers_in_text(text, chapter)
            page = resolve_section_page(chapter, sections, chunks_by_doc.get(doc_id))
            if page is None:
                page = _index_page(chapter, sections)
            if page is None:
                return original
            changes.append(
                {"kind": "paged", "text": text, "from": doc_id, "to": f"{doc_id}#page={page}"}
            )
            return f"[{text}](doc:{doc_id}#page={page})"

        want = f"statutes-{chapter}"
        if want in retrieved_doc_ids or chapter in known_statute_chapters:
            sections = _section_numbers_in_text(text, chapter)
            resolved = resolve_section_page(chapter, sections, chunks_by_doc.get(want))
            if resolved is None:
                resolved = _index_page(chapter, sections)
            if classify_link_label(text, chapter) == "claim" and doc_id in retrieved_doc_ids:
                # A claim-length label naming the section it interprets: keep
                # the quoting document and add the statute as a second target.
                to = _dual_target(doc_id, fragment, want, resolved)
                changes.append(
                    {"kind": "dual", "text": text, "from": doc_id, "to": to, "ref": want}
                )
                return f"[{text}](doc:{to})"
            page_fragment = f"#page={resolved}" if resolved else ""
            changes.append({"kind": "repointed", "text": text, "from": doc_id, "to": want})
            return f"[{text}](doc:{want}{page_fragment})"

        changes.append({"kind": "stripped", "text": text, "from": doc_id, "to": None})
        return text

    repaired = _LINK_RE.sub(_fix, answer)
    stats = {
        "repointed": sum(1 for c in changes if c["kind"] == "repointed"),
        "stripped": sum(1 for c in changes if c["kind"] == "stripped"),
        "paged": sum(1 for c in changes if c["kind"] == "paged"),
        "dual": sum(1 for c in changes if c["kind"] == "dual"),
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

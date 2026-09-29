"""Keep the answer from handing the reader a URL it was never given (Task 62).

The writer sees only the answer context: retrieved chunk text, the plan and
the finding. A URL that is not in that text came from the model's priors, as
in query 2a4a9aed, which invented `revenue.wi.gov/Pages/SLF/assessors.aspx`
(a dead page) for an assessor lookup. Such a link looks authoritative and is
the worst kind of wrong, so it is cut back to the bare site name, which keeps
the sentence readable and points somewhere real:

* ``[label](https://host/path)`` not in the context: the link is dropped;
  the label stays, or becomes ``host`` when the label is itself the URL.
* a bare ``https://…``, ``www.…`` or ``host.tld/path`` not in the context:
  replaced by ``host``.

A URL that does appear in the context is left alone, as is a bare host with
no path ("revenue.wi.gov"). Email addresses and phone numbers are NOT edited
(rewriting contact details mid-sentence does more harm than good); they are
reported by :func:`ungrounded_contacts` so they can be logged and reviewed.

Pure and idempotent: it runs on each streamed fragment and again on the
persisted answer, and a second pass changes nothing.
"""

from __future__ import annotations

import re

_TLDS = r"(?:gov|com|org|net|edu|us|info|io)"
# One regex, two alternatives, so a markdown link is consumed whole and its
# target is never re-matched as a bare URL.
_URL_RE = re.compile(
    r"\[(?P<label>[^\]]+)\]\((?P<target>https?://[^)\s]+)\)"
    r"|(?P<bare>(?:https?://|www\.)[^\s<>()\[\]]+"
    rf"|(?<![@\w.\-/])[a-z0-9\-]+(?:\.[a-z0-9\-]+)*\.{_TLDS}/[^\s<>()\[\]]*)",
    re.IGNORECASE,
)
_TRAILING_PUNCT = ".,;:!?*'\""

_EMAIL_RE = re.compile(r"\b[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+\b")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?1[\s.\-]?)?\(?(\d{3})\)?[\s.\-]?(\d{3})[\s.\-](\d{4})(?!\d)")

# A trailing token that could still be growing into a URL. The streamer holds
# a fragment while this matches so a URL is never split across two fragments.
_OPEN_URL_RE = re.compile(r"(?:^|\s|\()(?:https?:|www\.|[\w\-]+\.[\w.\-/%?=&#]*)$", re.IGNORECASE)


def _norm(url: str) -> str:
    u = url.lower().strip()
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^www\.", "", u)
    u = u.split("#", 1)[0]
    return u.rstrip("/")


def _norm_text(text: str) -> str:
    t = text.lower()
    t = re.sub(r"https?://", "", t)
    return re.sub(r"(?<![\w.])www\.", "", t)


def _host(url: str) -> str:
    return _norm(url).split("/", 1)[0].split("?", 1)[0]


def has_open_url(text: str) -> bool:
    """True when ``text`` ends in a token that may still be an unfinished URL."""
    return _OPEN_URL_RE.search(text) is not None


def guard_external_urls(answer: str, grounding_text: str) -> tuple[str, dict]:
    """Cut URLs the writer was never shown back to their host.

    Returns ``(answer, {"reduced": n, "changes": [{"from", "to"}]})``.
    """
    grounded = _norm_text(grounding_text)
    changes: list[dict] = []

    def _fix(m: re.Match) -> str:
        if m.group("target"):
            url, label = m.group("target"), m.group("label")
            if _norm(url) in grounded:
                return m.group(0)
            label_is_url = _norm(label) == _norm(url) or bool(
                re.match(r"^(?:https?://|www\.)", label.strip(), re.IGNORECASE)
            )
            to = _host(url) if label_is_url else label
            changes.append({"from": url, "to": to})
            return to

        raw = m.group("bare")
        url = raw.rstrip(_TRAILING_PUNCT)
        tail = raw[len(url) :]
        norm = _norm(url)
        if "/" not in norm or norm in grounded:
            return raw
        host = _host(url)
        changes.append({"from": url, "to": host})
        return host + tail

    guarded = _URL_RE.sub(_fix, answer)
    return guarded, {"reduced": len(changes), "changes": changes}


def ungrounded_contacts(answer: str, grounding_text: str) -> list[str]:
    """Email addresses and phone numbers in ``answer`` that the context never showed."""
    grounded_emails = {e.lower() for e in _EMAIL_RE.findall(grounding_text)}
    grounded_phones = {"".join(p) for p in _PHONE_RE.findall(grounding_text)}
    found: list[str] = []
    for email in _EMAIL_RE.findall(answer):
        if email.lower() not in grounded_emails and email not in found:
            found.append(email)
    for m in _PHONE_RE.finditer(answer):
        if "".join(m.groups()) not in grounded_phones and m.group(0) not in found:
            found.append(m.group(0))
    return found

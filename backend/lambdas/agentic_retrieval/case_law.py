"""Case-law support: stub detection, dedup, opinion fetch/cards, link resolution.

Merges the former case_law_handling.py and case_opinion.py modules.
Case-law opinions are stored in s3://{RAW_BUCKET}/raw/case-law/{reporter}/{slug}.txt.
Stubs (1-chunk metadata nodes) stay in the Neptune graph; the agent fetches
full opinion text only when a question needs the court's actual analysis.
"""

import hashlib
import logging
import re
import time
import urllib.parse
from collections.abc import Callable

import boto3
from botocore.exceptions import ClientError
from step_function_types.models import RAGDocument

logger = logging.getLogger(__name__)

MAX_OPINION_CHARS = 40_000

_REPORTER_PATTERNS = [
    ("f-supp-3d", re.compile(r"\d+-f-supp-3d-\d+")),
    ("f-supp-2d", re.compile(r"\d+-f-supp-2d-\d+")),
    ("f-supp", re.compile(r"\d+-f-supp-\d+")),
    ("f-4th", re.compile(r"\d+-f-4th-\d+")),
    ("f-3d", re.compile(r"\d+-f-3d-\d+")),
    ("f-2d", re.compile(r"\d+-f-2d-\d+")),
    ("l-ed-2d", re.compile(r"\d+-l-ed-2d-\d+")),
    ("n-w-3d", re.compile(r"\d+-n-w-3d-\d+")),
    ("n-w-2d", re.compile(r"\d+-n-w-2d-\d+")),
    ("wis-2d", re.compile(r"\d+-wis-2d-\d+")),
    ("wi-app", re.compile(r"\d+-wi-app-\d+")),
    ("wi", re.compile(r"\d+-wi-\d+")),
    ("s-ct", re.compile(r"\d+-s-ct-\d+")),
    ("u-s", re.compile(r"\d+-u-s-\d+")),
]


def _reporter_for_slug(slug: str) -> str:
    for group, pattern in _REPORTER_PATTERNS:
        if pattern.fullmatch(slug):
            return group
    return "misc"


def _citation_to_slug(citation: str) -> str:
    lowered = citation.lower()
    normalized = re.sub(r"[^a-z0-9]", " ", lowered)
    return "-".join(normalized.split())


def citation_to_doc_id(citation: str) -> str:
    """Normalize a legal citation to its Neptune doc_id.

    Examples:
        '109 Wis. 2d 290' -> 'case-law-109-wis-2d-290'
        '766 F.3d 648'    -> 'case-law-766-f-3d-648'
    """
    return f"case-law-{_citation_to_slug(citation)}"


def citation_to_raw_key(citation: str) -> str:
    """Normalize a legal citation to its S3 key.

    Examples:
        '109 Wis. 2d 290' -> 'raw/case-law/wis-2d/109-wis-2d-290.txt'
        '766 F.3d 648'    -> 'raw/case-law/f-3d/766-f-3d-648.txt'
        '2000 WI App 182' -> 'raw/case-law/wi-app/2000-wi-app-182.txt'
    """
    slug = _citation_to_slug(citation)
    reporter = _reporter_for_slug(slug)
    return f"raw/case-law/{reporter}/{slug}.txt"


def scholar_url(citation: str) -> str:
    """Google Scholar search URL for a case citation."""
    q = urllib.parse.quote(citation)
    return f"http://scholar.google.com/scholar?hl=en&as_sdt=4&as_sdts=50&as_vis=1&q={q}"


def fetch_case_opinion(
    citation: str,
    raw_bucket: str,
    s3_client=None,
) -> dict:
    """Fetch the full text of a court opinion by citation.

    Args:
        citation: Legal citation, e.g. '109 Wis. 2d 290'.
        raw_bucket: S3 bucket name where raw opinions live.
        s3_client: Optional boto3 S3 client (injected for tests).

    Returns:
        dict with:
            found: True if opinion was fetched, False otherwise.
            citation: Echo of input.
            raw_key: S3 key that was queried.
            text: Opinion text (truncated if >MAX_OPINION_CHARS).
            scholar_url: Google Scholar search URL (always populated).
            error: Present only when the call could not proceed.
    """
    if not citation or not citation.strip():
        return {
            "found": False,
            "citation": citation,
            "error": "empty citation",
            "scholar_url": scholar_url(citation),
        }

    s3 = s3_client or boto3.client("s3")
    raw_key = citation_to_raw_key(citation)

    try:
        obj = s3.get_object(Bucket=raw_bucket, Key=raw_key)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("NoSuchKey", "404", "AccessDenied"):
            logger.info(f"No raw opinion for citation '{citation}' (key={raw_key}, code={code})")
            return {
                "found": False,
                "citation": citation,
                "raw_key": raw_key,
                "scholar_url": scholar_url(citation),
            }
        logger.warning(f"S3 error fetching opinion for '{citation}': {e}")
        return {
            "found": False,
            "citation": citation,
            "raw_key": raw_key,
            "scholar_url": scholar_url(citation),
            "error": f"s3 error: {code}",
        }

    text = obj["Body"].read().decode("utf-8", errors="replace")
    if len(text) > MAX_OPINION_CHARS:
        text = (
            text[:MAX_OPINION_CHARS]
            + "\n\n[Opinion truncated to fit context; full text available at the source link.]"
        )

    return {
        "found": True,
        "citation": citation,
        "raw_key": raw_key,
        "text": text,
        "scholar_url": scholar_url(citation),
    }


CASE_LAW_STUB_PREFIX = "case-law-"

DISCOVERY_TAG_PRIORITY = [
    "opinion-fetched",
    "fetched",
    "vector-search",
    "graph-neighbor",
    "auto-enrichment",
    "framework-list",
    "unknown",
]

_CASE_NAME_SUFFIX_RE = re.compile(r"\s*(?:[–—:]|-\s)|,\s*(?=\d+\b)")

_YEAR_RE = re.compile(r"\b(\d{4})\b")


def is_case_law_stub(doc_id: str) -> bool:
    """True when a doc_id is a case-law citation stub."""
    return doc_id.startswith(CASE_LAW_STUB_PREFIX)


def extract_case_name(title: str) -> str:
    """Canonical case-name portion of a title, lowercased and whitespace-collapsed."""
    match = _CASE_NAME_SUFFIX_RE.search(title)
    core = title[: match.start()] if match else title
    return " ".join(core.lower().split())


def extract_year(title: str) -> str | None:
    """First 4-digit year in the title, or None if absent."""
    match = _YEAR_RE.search(title)
    return match.group(1) if match else None


def collapse_case_law_by_title(
    docs_by_id: dict[str, RAGDocument],
) -> dict[str, RAGDocument]:
    """Merge case-law RAGDocuments that share a normalized title.

    Parallel citations of the same decision become separate Neptune Document
    nodes during ingest; this collapses them back into one sidebar card.
    """
    by_name: dict[str, dict[str | None, list[tuple[str, RAGDocument]]]] = {}
    passthrough: dict[str, RAGDocument] = {}

    for doc_id, rag_doc in docs_by_id.items():
        if not is_case_law_stub(doc_id):
            passthrough[doc_id] = rag_doc
            continue
        name_key = extract_case_name(rag_doc.title)
        year_key = extract_year(rag_doc.title)
        by_name.setdefault(name_key, {}).setdefault(year_key, []).append((doc_id, rag_doc))

    groups: list[list[tuple[str, RAGDocument]]] = []
    for year_buckets in by_name.values():
        yearless = year_buckets.pop(None, [])
        if not year_buckets:
            if yearless:
                groups.append(yearless)
            continue

        dominant_year = max(
            year_buckets.keys(),
            key=lambda y: (len(year_buckets[y]), y),
        )
        year_buckets[dominant_year].extend(yearless)

        for bucket in year_buckets.values():
            groups.append(bucket)

    merged: dict[str, RAGDocument] = dict(passthrough)
    for group in groups:
        if len(group) == 1:
            doc_id, rag_doc = group[0]
            merged[doc_id] = rag_doc
            continue

        def sort_key(item: tuple[str, RAGDocument]) -> tuple[int, int]:
            _, doc = item
            tag_rank = (
                DISCOVERY_TAG_PRIORITY.index(doc.discovery_tag)
                if doc.discovery_tag in DISCOVERY_TAG_PRIORITY
                else len(DISCOVERY_TAG_PRIORITY)
            )
            return (tag_rank, -len(doc.content or ""))

        group.sort(key=sort_key)
        primary_id, primary_doc = group[0]

        seen_texts: set[str] = set()
        merged_parts: list[str] = []
        for _, doc in group:
            text = (doc.content or "").strip()
            if text and text not in seen_texts:
                seen_texts.add(text)
                merged_parts.append(text)
        merged_content = "\n\n".join(merged_parts)

        merged[primary_id] = RAGDocument(
            document_id=primary_doc.document_id,
            title=primary_doc.title,
            content=merged_content or primary_doc.content,
            source=primary_doc.source,
            source_url=primary_doc.source_url,
            s3_key=primary_doc.s3_key,
            start_page=primary_doc.start_page,
            end_page=primary_doc.end_page,
            discovery_tag=primary_doc.discovery_tag,
            authority_level=primary_doc.authority_level,
            edition_year=primary_doc.edition_year,
        )
        logger.info(
            f"Collapsed {len(group)} case-law parallel citations into "
            f"'{primary_doc.title[:60]}' (ids: {[g[0] for g in group]})"
        )

    return merged


def build_opinion_card(stub_doc_id: str, payload: dict, neptune_client) -> RAGDocument:
    """Build a RAGDocument for a fetched full court opinion."""
    citation = payload.get("citation", "")
    opinion_text = payload.get("text", "")
    payload_scholar_url = payload.get("scholar_url", "")

    doc_info = neptune_client.get_document(stub_doc_id) or {}
    title = doc_info.get("title") or citation or stub_doc_id
    content_hash = hashlib.sha256(stub_doc_id.encode()).hexdigest()[:7]

    node_url = doc_info.get("source_url")
    public_url = node_url or payload_scholar_url or (scholar_url(citation) if citation else None)

    return RAGDocument(
        document_id=f"{stub_doc_id}-{content_hash}",
        title=title,
        content=opinion_text,
        source=citation or title,
        source_url=public_url,
        s3_key=None,
        start_page=None,
        end_page=None,
        discovery_tag="opinion-fetched",
        authority_level=doc_info.get("authority_level") if doc_info else 3,
        edition_year=None,
    )


# ---------------------------------------------------------------------------
# find_case_law resolution: in-Lambda index of (id, title, citation) triples.
#
# Neptune openCypher has no full-text index, so every title search is a full
# CaseLaw label scan anyway (~1.2k nodes). Pulling the whole index once per
# warm container (~100 KB of id/title/citation, ~0.5 s) and matching here
# lets us normalize both sides (drop "v.", "LLC", "City of", reporter
# suffixes), rank by match quality, and enforce a strict end boundary on
# neutral cites -- none of which a `CONTAINS` chain can express.
# ---------------------------------------------------------------------------

CASE_INDEX_TTL_SECONDS = 15 * 60
FIND_CASE_LAW_MAX_RESULTS = 5

_case_index_cache: dict = {"loaded_at": 0.0, "nodes": []}

# "2018 WI 45", "2025 WI App 43"  (public-domain / neutral cites)
_NEUTRAL_CITE_RE = re.compile(r"\b(\d{4})\s+WI\s+(?:App\s+)?(\d+)\b", re.IGNORECASE)

# Reporter cites: "381 Wis. 2d 311", "985 N.W.2d 69", "766 F.3d 648", "102 S. Ct. 2613"
_REPORTER_CITE_RE = re.compile(
    r"\b(\d+)\s+"
    r"(Wis\.?\s*2d|N\.?\s*W\.?\s*[23]d|F\.?\s*(?:2d|3d|4th)|F\.?\s*Supp\.?(?:\s*[23]d)?"
    r"|S\.?\s*Ct\.?|U\.?\s*S\.?|L\.?\s*Ed\.?\s*2d)"
    r"\s+(\d+)\b",
    re.IGNORECASE,
)

# Tokens that carry no identifying weight in a case name. Party-type words
# ("City of", "LLC") and reporter fragments are dropped from BOTH the query
# and the title so "Nudo Holdings LLC v. City of Kenosha" reduces to
# {nudo, holdings, kenosha} on each side.
# fmt: off
_NAME_STOP_TOKENS = frozenset(
    {
        # connectors
        "v", "vs", "of", "the", "in", "re", "ex", "rel", "et", "al", "and", "for", "a", "an", "on",
        # party-type / entity words
        "llc", "inc", "co", "corp", "corporation", "company", "ltd", "lp", "llp",
        "city", "village", "town", "county", "state", "board", "bd", "review", "dept", "department",
        "wisconsin",
        # reporter fragments (in case a cite survives the regex strip)
        "wis", "wi", "app", "2d", "3d", "4th", "n", "w", "f", "supp", "s", "ct", "u", "l", "ed",
    }
)
# fmt: on


def _name_tokens(text: str) -> list[str]:
    """Normalized identifying tokens of a case name (order preserved, deduped)."""
    lowered = re.sub(r"[^a-z0-9]+", " ", text.lower())
    seen: set[str] = set()
    out: list[str] = []
    for tok in lowered.split():
        if len(tok) < 2 or tok.isdigit() or tok in _NAME_STOP_TOKENS or tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
    return out


def _strip_cites(text: str) -> str:
    text = _REPORTER_CITE_RE.sub(" ", text)
    return _NEUTRAL_CITE_RE.sub(" ", text)


def parse_case_search(search_text: str) -> dict:
    """Split a find_case_law query into reporter slugs, neutral slugs, and name tokens.

    Slugs are the exact `case-law-...` doc_ids the cites resolve to, so lookup
    is equality on id -- "2018 WI 45" can never match `case-law-2018-wi-4`.
    """
    reporter = [citation_to_doc_id(m.group(0)) for m in _REPORTER_CITE_RE.finditer(search_text)]
    neutral = [citation_to_doc_id(m.group(0)) for m in _NEUTRAL_CITE_RE.finditer(search_text)]
    name_tokens = _name_tokens(_strip_cites(search_text))
    return {"reporter": reporter, "neutral": neutral, "name_tokens": name_tokens}


def _name_match_quality(query_tokens: list[str], title_tokens: list[str]) -> float | None:
    """Score a name match in (0, 1]; 1.0 is an exact normalized match; None is no match.

    Match rule: every query token of >= 4 chars appears in the title, OR at
    least 2 query tokens appear in the title. Token equality (not substring)
    is deliberate: "Thoma" must not hit "Thomas G Miller".
    """
    qs, ts = set(query_tokens), set(title_tokens)
    if not qs or not ts:
        return None
    overlap = qs & ts
    long_q = {t for t in qs if len(t) >= 4}
    if not ((long_q and long_q <= ts) or len(overlap) >= 2):
        return None
    if qs == ts:
        return 1.0
    # Partial: how much of the query matched, and how much of the title it explains.
    return min(0.99, round(0.5 * len(overlap) / len(qs) + 0.5 * len(overlap) / len(ts), 3))


def search_case_law(
    search_text: str,
    nodes: list[dict],
    limit: int = FIND_CASE_LAW_MAX_RESULTS,
    allowed_ids: set[str] | None = None,
) -> list[dict]:
    """Resolve a case name / neutral cite / reporter cite against the CaseLaw index.

    Returns at most `limit` node dicts, each annotated with `match_kind`
    (reporter | neutral | name) and `match_score`, ranked best-first:
    exact cite hits, then exact-normalized name hits, then partial names.
    """
    parsed = parse_case_search(search_text)
    reporter_ids = set(parsed["reporter"])
    neutral_ids = set(parsed["neutral"])
    q_tokens = parsed["name_tokens"]
    if not (reporter_ids or neutral_ids or q_tokens):
        return []

    best: dict[str, tuple[tuple, dict]] = {}

    def consider(node: dict, kind: str, score: float, rank: int) -> None:
        node_id = node.get("id") or ""
        key = (rank, -score, node_id)
        current = best.get(node_id)
        if current is None or key < current[0]:
            best[node_id] = (key, {**node, "match_kind": kind, "match_score": score})

    for node in nodes:
        node_id = node.get("id") or ""
        if not node_id or (allowed_ids is not None and node_id not in allowed_ids):
            continue
        cite_slug = citation_to_doc_id(node.get("citation") or "") if node.get("citation") else ""
        if node_id in reporter_ids or cite_slug in reporter_ids:
            consider(node, "reporter", 1.0, 0)
            continue
        if node_id in neutral_ids or cite_slug in neutral_ids:
            consider(node, "neutral", 1.0, 0)
            continue
        if q_tokens:
            title = node.get("title") or ""
            title_tokens = _name_tokens(extract_case_name(title))
            quality = _name_match_quality(q_tokens, title_tokens)
            if quality is not None:
                consider(node, "name", quality, 1 if quality >= 1.0 else 2)

    ranked = sorted(best.values(), key=lambda item: item[0])
    return [hit for _, hit in ranked[:limit]]


def get_case_law_index(loader: Callable[[], list[dict]], now: float | None = None) -> list[dict]:
    """The cached (id, title, citation, ...) index of every CaseLaw node.

    Loaded once per warm container and refreshed after CASE_INDEX_TTL_SECONDS
    so a graph reload shows up without a cold start. If a refresh fails and a
    previous copy exists, the stale copy is served.
    """
    now = time.monotonic() if now is None else now
    if _case_index_cache["nodes"] and now - _case_index_cache["loaded_at"] < CASE_INDEX_TTL_SECONDS:
        return _case_index_cache["nodes"]
    try:
        nodes = loader()
    except Exception:
        if _case_index_cache["nodes"]:
            logger.warning("CaseLaw index refresh failed; serving stale copy", exc_info=True)
            return _case_index_cache["nodes"]
        raise
    _case_index_cache["nodes"] = nodes
    _case_index_cache["loaded_at"] = now
    logger.info(f"CaseLaw index loaded: {len(nodes)} nodes")
    return nodes


def reset_case_law_index_cache() -> None:
    _case_index_cache["nodes"] = []
    _case_index_cache["loaded_at"] = 0.0


def apply_case_law_links(
    docs_by_id: dict[str, RAGDocument], doc_infos: dict[str, dict]
) -> dict[str, RAGDocument]:
    """Ensure case-law stub cards link to a public URL and drop S3 refs."""
    for doc_id, card in docs_by_id.items():
        if not is_case_law_stub(doc_id):
            continue
        doc_info = doc_infos.get(doc_id) or {}
        node_url = doc_info.get("source_url")
        citation = doc_info.get("citation")
        public_url = node_url or (scholar_url(citation) if citation else None)
        if not public_url:
            continue
        docs_by_id[doc_id] = card.model_copy(
            update={
                "source_url": public_url,
                "s3_key": None,
                "start_page": None,
                "end_page": None,
            }
        )
    return docs_by_id

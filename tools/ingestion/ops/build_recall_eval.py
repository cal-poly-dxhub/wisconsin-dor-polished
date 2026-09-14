"""Mine the ChatHistory DynamoDB table into a raw-vector-recall golden set.

Produces tools/ingestion/tests/recall_eval_queries.yaml — the labelled query set
consumed by run_recall_probe.py to measure, per real user query, where the
expected chunk/document ranks in *raw* Neptune vector search (no agent loop,
no re-ranking). Built to evaluate the "document expansion" change (prepending
generated plain-language questions/synonyms to chunk text before embedding).

Two gold tiers:

  gold_cited    rows the reviewer rated "up" (or legacy thumbUp=true with no
                rating). Expected docs = the documents cited in the answer
                (resources[].data.documentId with the -<sha7> suffix stripped,
                cross-checked against the persisted trace's un-suffixed docIds).
                If the trace has get_section / search_document events with
                metadata.chunkIds, those chunk ids (restricted to cited docs)
                are recorded as expected_chunk_ids — a stronger label.

  gold_missing  rows where richFeedback.sourceNotes is non-empty, or where an
                accuracy / relevance / sources comment names a specific missing
                source. Hand-coded heuristics (MISSING_SOURCE_RULES) map the
                note text to graph doc ids + an optional expected_subsection.
                Unmapped rows keep expected_doc_ids: [] and
                needs_manual_label: true, with regex-extracted `note_hints`
                (statute cites, form numbers, revenue.wi.gov URLs) so a human
                can finish the label.

Excluded: disambiguation prompts, out-of-scope / topic-shift answers, empty
answers, and property-type chip labels submitted as queries.

PRIVACY — the YAML is committed to a public repo. Rows get synthetic ids
(re-0001, ...). No queryId, sessionId, tester identity, or verbatim feedback
text is written: `notes` is generated from the matched rule's neutral
description, never copied from the comment. Query text itself is kept.

Usage:
    AWS_PROFILE=<your-profile> AWS_REGION=us-east-1 \\
      uv run python tools/ingestion/ops/build_recall_eval.py
    # optionally validate expected doc ids against a graph:
    ... build_recall_eval.py --graph-id g-xxxxxxxx
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field

import boto3
import yaml

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
OUTPUT_YAML = os.path.join(_REPO_ROOT, "tools", "ingestion", "tests", "recall_eval_queries.yaml")

DEFAULT_TABLE = (
    "WisconsinBotGraphRAG-WisconsinSessionsStackNestedStackWisconsinSessionsStackNestedStac"
    "-1P3H46X50M51H-ChatHistoryTableA22BA13C-GTH2UH9SGD0W"
)

# rag_documents.py builds card ids as f"{doc_id}-{sha256(doc_id)[:7]}".
_SUFFIX_RE = re.compile(r"-[0-9a-f]{7}$")

EXCLUDED_ANSWER_PREFIXES = (
    "To give you the most relevant answer",
    "The question is unclear",
    "It looks like you're asking",
    "That question is outside the scope",
)

# Property-type chip labels from the disambiguation pane (frontend
# disambiguation-pane.tsx PROPERTY_TYPE_CHOICES).
CHIP_LABELS = {
    "residential",
    "commercial",
    "manufacturing",
    "agricultural",
    "undeveloped",
    "agricultural forest",
    "forest land",
    "farm improvements (other)",
    "not certain — general information",
    "not certain - general information",
}

WPAM_2026 = "wpam-wisconsin-property-assessment-manual-2026"


@dataclass(frozen=True)
class MissingRule:
    """Maps a reviewer note pattern to graph doc ids (hand-coded)."""

    pattern: str
    doc_ids: tuple[str, ...]
    subsection: str | None
    note: str  # neutral paraphrase written to the YAML

    def matches(self, text: str) -> bool:
        return re.search(self.pattern, text, re.IGNORECASE) is not None


# Order matters only for readability of `notes`; all matching rules apply.
MISSING_SOURCE_RULES: tuple[MissingRule, ...] = (
    MissingRule(
        r"70\.11\s*\(49\)",
        ("statutes-70",),
        "70.11(49)",
        "Expected the new recreational/prefabricated structure exemption under s. 70.11(49)",
    ),
    MissingRule(
        r"(4/29/26|april 29,? 2026)",
        ("news_pages-cotvc-news-2026-04-29",),
        None,
        "Expected the 2026-04-29 DOR news page announcing the s. 70.11(49) exemption",
    ),
    MissingRule(
        r"napt-treaty-update|native american taxation|taxation of native americans",
        ("gov_publications-napt-treaty-update",),
        None,
        "Expected the DOR Native American property taxation / treaty publication",
    ),
    MissingRule(
        r"70\.56\b",
        ("statutes-70",),
        "70.56",
        "Expected a citation of s. 70.56 (prior-year roll used for the current year)",
    ),
    MissingRule(
        r"70\.47\s*\(8m\)",
        ("statutes-70",),
        "70.47(8m)",
        "Expected the BOR waiver process under s. 70.47(8m)",
    ),
    MissingRule(
        r"70\.85\b",
        ("statutes-70",),
        "70.85",
        "Expected the appeal-to-DOR option under s. 70.85",
    ),
    MissingRule(
        r"70\.365\b",
        ("statutes-70",),
        "70.365",
        "Expected the notice exception under s. 70.365 (BOR not required without notice)",
    ),
    MissingRule(
        r"part of 70\.47",
        ("statutes-70",),
        "70.47",
        "Expected the BOR timing rule to be attributed to s. 70.47, not 70.46",
    ),
    MissingRule(
        r"close of (the )?day on jan(uary)? ?1|close of day is midnight",
        ("statutes-70",),
        "70.10",
        "Expected the s. 70.10 assessment-as-of-close-of-January-1 rule",
    ),
    MissingRule(
        r"chapter 4 of the wpam|missing wpam",
        (WPAM_2026,),
        None,
        "Expected WPAM Chapter 4 (maintenance vs. revaluation assessments)",
    ),
    MissingRule(
        r"municipal official guide",
        ("gov_publications-pb062",),
        None,
        "Expected the Guide for Municipal Officials (PB-062)",
    ),
    MissingRule(
        r"\b61\.19\b",
        ("statutes-61",),
        "61.19",
        "Expected s. 61.19 (village assessor) rather than a ch. 62 citation",
    ),
    MissingRule(
        r"flow ?chart on page 21",
        ("gov_publications-2026-board-of-review-guide",),
        None,
        "Expected the BOR process flowchart in the Board of Review Guide",
    ),
    MissingRule(
        r"innovation grant common question",
        ("form_instructions-ig-inst",),
        None,
        "Expected the full Innovation Grant contract-requirements list (IG instructions / FAQ)",
    ),
    MissingRule(
        r"agricultural, undeveloped, agricultural forest",
        ("statutes-70", "gov_publications-2026-agricultural-assessment-guide"),
        "70.32(2)",
        "Expected the ag / undeveloped / ag-forest classification rules (s. 70.32(2), ag guide)",
    ),
)

# Signals that a comment is describing a MISSING source (vs. a wrong answer).
_MISSING_SIGNAL_RE = re.compile(
    r"missing|missed|expected to see|needs inclusion|not mention|no direct link|went to"
    r"|should have|would have been to direct",
    re.IGNORECASE,
)

# Hints extracted from unmapped notes so a human can finish the label.
_HINT_PATTERNS = (
    re.compile(r"\b\d{2,3}\.\d{2,4}(?:\s*\(\w+\))*"),  # statute cites 70.11 (49)
    re.compile(r"\b(?:PA|PC|PR|PE|SL|PB)-\d{3}\b", re.IGNORECASE),  # form / pub numbers
    re.compile(r"https?://\S*revenue\.wi\.gov\S*"),
    re.compile(r"\bWPAM\b", re.IGNORECASE),
    re.compile(r"\bchapter \d+\b", re.IGNORECASE),
)


@dataclass
class Case:
    query: str
    tiers: set[str] = field(default_factory=set)
    expected_doc_ids: set[str] = field(default_factory=set)
    expected_chunk_ids: set[str] = field(default_factory=set)
    expected_subsections: set[str] = field(default_factory=set)
    notes: list[str] = field(default_factory=list)
    note_hints: set[str] = field(default_factory=set)
    needs_manual_label: bool = False
    source_rows: int = 0


# --------------------------------------------------------------------------- #
# DynamoDB
# --------------------------------------------------------------------------- #


def scan_table(table_name: str) -> list[dict]:
    table = boto3.resource("dynamodb", region_name=AWS_REGION).Table(table_name)
    items: list[dict] = []
    kwargs: dict = {}
    while True:
        page = table.scan(**kwargs)
        items.extend(page.get("Items", []))
        lek = page.get("LastEvaluatedKey")
        if not lek:
            break
        kwargs["ExclusiveStartKey"] = lek
    logger.info("scanned %d chat-history rows", len(items))
    return items


def load_items(table_name: str, cache_path: str | None) -> list[dict]:
    if cache_path and os.path.exists(cache_path):
        with open(cache_path) as f:
            items = json.load(f)
        logger.info("loaded %d rows from cache %s", len(items), cache_path)
        return items
    items = scan_table(table_name)
    if cache_path:
        with open(cache_path, "w") as f:
            json.dump(items, f, default=str)
    return items


# --------------------------------------------------------------------------- #
# Row helpers
# --------------------------------------------------------------------------- #


def _norm_query(q: str) -> str:
    return re.sub(r"\s+", " ", q or "").strip().lower()


def is_excluded(item: dict) -> bool:
    query = _norm_query(item.get("query", ""))
    answer = (item.get("answer") or "").lstrip()
    if not query or len(query) < 4:
        return True
    if query in CHIP_LABELS:
        return True
    if not answer:
        return True
    return answer.startswith(EXCLUDED_ANSWER_PREFIXES)


def parse_trace(item: dict) -> list[dict]:
    raw = item.get("trace")
    if not raw:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    return raw if isinstance(raw, list) else []


def trace_doc_ids(trace: list[dict]) -> set[str]:
    out: set[str] = set()
    for ev in trace:
        for d in ev.get("docIds") or []:
            if isinstance(d, str):
                out.add(d)
    return out


def strip_suffix(document_id: str, known: set[str]) -> str:
    """Strip the -<sha7> card suffix; prefer whichever form the trace knows."""
    stripped = _SUFFIX_RE.sub("", document_id)
    if stripped in known:
        return stripped
    if document_id in known:
        return document_id
    return stripped


def cited_doc_ids(item: dict, known: set[str]) -> set[str]:
    out: set[str] = set()
    for res in item.get("resources") or []:
        if res.get("type") != "document":
            continue
        did = (res.get("data") or {}).get("documentId")
        if did:
            out.add(strip_suffix(did, known))
    docs = item.get("documents")
    if isinstance(docs, str):
        try:
            docs = json.loads(docs)
        except json.JSONDecodeError:
            docs = []
    for d in docs or []:
        did = d.get("document_id") or d.get("documentId")
        if did:
            out.add(strip_suffix(did, known))
    return out


def trace_chunk_ids(trace: list[dict], doc_ids: set[str]) -> set[str]:
    out: set[str] = set()
    for ev in trace:
        if ev.get("toolName") not in ("get_section", "search_document"):
            continue
        for cid in (ev.get("metadata") or {}).get("chunkIds") or []:
            if not isinstance(cid, str):
                continue
            doc = cid.rsplit("_chunk_", 1)[0]
            if doc in doc_ids:
                out.add(cid)
    return out


def is_gold_cited(item: dict) -> bool:
    rating = item.get("rating")
    if rating == "up":
        return True
    return rating is None and item.get("thumbUp") is True


def feedback_texts(item: dict) -> tuple[list[str], list[str]]:
    """Return (source_note_texts, comment_texts) for a row."""
    rf = item.get("richFeedback") or {}
    notes: list[str] = []
    for sn in rf.get("sourceNotes") or []:
        if not isinstance(sn, dict):
            continue
        txt = " ".join(str(sn.get(k) or "") for k in ("comment", "missedDetail")).strip()
        if txt:
            notes.append(txt)
    comments: list[str] = []
    for key in ("accuracy", "relevance", "sources"):
        c = ((rf.get("response") or {}).get(key) or {}).get("comment")
        if c:
            comments.append(str(c))
    fb = item.get("feedback")
    if fb and fb not in comments:
        comments.append(str(fb))
    return notes, comments


def extract_hints(text: str) -> set[str]:
    hints: set[str] = set()
    for pat in _HINT_PATTERNS:
        for m in pat.finditer(text):
            hints.add(re.sub(r"\s+", " ", m.group(0)).strip())
    return hints


# --------------------------------------------------------------------------- #
# Build
# --------------------------------------------------------------------------- #


def build_cases(items: list[dict]) -> dict[str, Case]:
    cases: dict[str, Case] = {}
    n_cited = n_missing = n_excluded = 0

    for item in items:
        excluded = is_excluded(item)
        notes, comments = feedback_texts(item)
        has_source_notes = bool(notes)
        missing_text = " \n ".join(notes + comments)
        matched_rules = [r for r in MISSING_SOURCE_RULES if r.matches(missing_text)]
        # A refusal / disambiguation answer is excluded UNLESS the reviewer's
        # note maps to a concrete source — then the row is a gold_missing case
        # (raw recall is exactly what we want to know for those).
        if excluded and not matched_rules:
            n_excluded += 1
            continue
        key = _norm_query(item["query"])
        trace = parse_trace(item)
        known = trace_doc_ids(trace)

        gold_cited = is_gold_cited(item) and not excluded
        comment_names_source = any(_MISSING_SIGNAL_RE.search(c) for c in comments) and any(
            extract_hints(c) for c in comments
        )
        gold_missing = has_source_notes or bool(matched_rules) or comment_names_source

        if not (gold_cited or gold_missing):
            continue

        case = cases.setdefault(key, Case(query=item["query"].strip()))
        case.source_rows += 1

        if gold_cited:
            n_cited += 1
            docs = cited_doc_ids(item, known)
            case.tiers.add("gold_cited")
            case.expected_doc_ids |= docs
            case.expected_chunk_ids |= trace_chunk_ids(trace, docs)

        if gold_missing:
            n_missing += 1
            case.tiers.add("gold_missing")
            if matched_rules:
                for r in matched_rules:
                    case.expected_doc_ids |= set(r.doc_ids)
                    if r.subsection:
                        case.expected_subsections.add(r.subsection)
                    if r.note not in case.notes:
                        case.notes.append(r.note)
            else:
                case.needs_manual_label = True
                case.note_hints |= extract_hints(missing_text)

    # A gold_cited-only case with no cited docs carries no label — drop it.
    unlabeled = [
        k for k, c in cases.items() if c.tiers == {"gold_cited"} and not c.expected_doc_ids
    ]
    for k in unlabeled:
        del cases[k]
    logger.info(
        "rows: excluded=%d gold_cited=%d gold_missing=%d -> %d queries (%d unlabeled dropped)",
        n_excluded,
        n_cited,
        n_missing,
        len(cases),
        len(unlabeled),
    )
    return cases


def validate_against_graph(cases: dict[str, Case], graph_id: str) -> None:
    client = boto3.client("neptune-graph", region_name=AWS_REGION)
    resp = client.execute_query(
        graphIdentifier=graph_id,
        queryString="MATCH (c:Chunk) WITH DISTINCT c.doc_id AS id RETURN id",
        language="OPEN_CYPHER",
    )
    graph_ids = {r["id"] for r in json.loads(resp["payload"].read())["results"]}
    logger.info("graph %s has %d distinct chunk doc_ids", graph_id, len(graph_ids))
    for case in cases.values():
        unknown = sorted(d for d in case.expected_doc_ids if d not in graph_ids)
        if unknown:
            logger.warning("%r: expected docs not in graph: %s", case.query[:60], unknown)
            case.expected_doc_ids -= set(unknown)
            case.note_hints |= {f"not-in-graph:{d}" for d in unknown}


def to_yaml_rows(cases: dict[str, Case]) -> list[dict]:
    ordered = sorted(cases.values(), key=lambda c: (c.tiers != {"gold_cited"}, c.query.lower()))
    rows: list[dict] = []
    for i, c in enumerate(ordered, 1):
        tier = "gold_missing" if "gold_missing" in c.tiers else "gold_cited"
        row: dict = {
            "id": f"re-{i:04d}",
            "tier": tier,
            "query": c.query,
            "expected_doc_ids": sorted(c.expected_doc_ids),
        }
        if len(c.tiers) > 1:
            row["also_in"] = sorted(c.tiers - {tier})
        if c.expected_chunk_ids:
            row["expected_chunk_ids"] = sorted(c.expected_chunk_ids)
        if c.expected_subsections:
            row["expected_subsection"] = sorted(c.expected_subsections)[0]
            if len(c.expected_subsections) > 1:
                row["expected_subsections"] = sorted(c.expected_subsections)
        if c.notes:
            row["notes"] = "; ".join(c.notes)
        if c.needs_manual_label:
            row["needs_manual_label"] = True
        if c.note_hints:
            row["note_hints"] = sorted(c.note_hints)
        if c.source_rows > 1:
            row["source_rows"] = c.source_rows
        rows.append(row)
    return rows


HEADER = """\
# Raw vector-recall golden set — GENERATED by tools/ingestion/ops/build_recall_eval.py.
# Consumed by tools/ingestion/ops/run_recall_probe.py (see its docstring).
#
# Each row is a real user query mined from chat history with an expected
# source label:
#   tier: gold_cited    reviewer rated the answer "up"; expected_doc_ids are
#                       the docs the answer cited. expected_chunk_ids (when
#                       present) are the exact chunks the agent read via
#                       get_section / search_document — a stronger label.
#   tier: gold_missing  reviewer flagged a missing / mis-cited source; the
#                       expected doc (and expected_subsection) was mapped from
#                       the note by hand-coded rules. needs_manual_label rows
#                       could not be mapped automatically — note_hints holds
#                       the cites / URLs pulled from the note.
#
# ids are synthetic (re-NNNN). No production identifiers or verbatim feedback
# text are stored here; `notes` is a neutral paraphrase.
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Build recall_eval_queries.yaml")
    parser.add_argument("--table", default=DEFAULT_TABLE)
    parser.add_argument("--output", default=OUTPUT_YAML)
    parser.add_argument(
        "--cache",
        default=None,
        help="Optional JSON path to cache the raw scan (keep OUT of the repo).",
    )
    parser.add_argument(
        "--graph-id",
        default=os.environ.get("NEPTUNE_GRAPH_ID"),
        help="If set, drop expected doc ids that do not exist in this graph.",
    )
    args = parser.parse_args()

    items = load_items(args.table, args.cache)
    cases = build_cases(items)
    if args.graph_id:
        validate_against_graph(cases, args.graph_id)
    rows = to_yaml_rows(cases)

    with open(args.output, "w") as f:
        f.write(HEADER)
        yaml.safe_dump({"cases": rows}, f, sort_keys=False, allow_unicode=True, width=100)

    tiers = defaultdict(int)
    for r in rows:
        tiers[r["tier"]] += 1
    n_chunk = sum(1 for r in rows if r.get("expected_chunk_ids"))
    n_manual = sum(1 for r in rows if r.get("needs_manual_label"))
    n_sub = sum(1 for r in rows if r.get("expected_subsection"))
    print(f"wrote {len(rows)} cases -> {args.output}")
    print(f"  gold_cited:   {tiers['gold_cited']}  (with chunk labels: {n_chunk})")
    print(
        f"  gold_missing: {tiers['gold_missing']}  "
        f"(with subsection: {n_sub}, needs_manual_label: {n_manual})"
    )


if __name__ == "__main__":
    main()

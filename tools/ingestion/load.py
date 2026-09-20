"""
Phase 5: Load embedded documents into Neptune Analytics graph.

Implements 10 sequential sub-phases:
1. Scaffold (frameworks + hierarchy)
2. Document nodes (with the case-law title sink guard)
3. Statute hierarchy (PART_OF)
4. Hierarchy links (stub Statute / AdminRule to framework)
5. Chunk nodes
6. Case Law CITES (Statute→CaseLaw reverse edges)
7. Stub resolution (DEFINED_BY edges from stubs to statute chunks)
8. Vector upserts
9. Orphan cleanup
10. Integrity checks (doubled case-law titles → non-zero exit; orphan chunks logged)

Document-level metadata (title, summary, citation, ...) is overlaid from
``extracted/`` onto each ``embedded/`` doc at load time (``DOC_METADATA_KEYS``),
and doc_ids listed in ``{cache_prefix}dedup/losers.json`` are skipped.

Usage:
    python -m tools.ingestion.load \
        --work-bucket <work-bucket> \
        --config tools/ingestion/config/ingest_config.yaml

The target graph defaults to ``$NEPTUNE_GRAPH_ID`` (set on the Fargate task
definition from the ``neptuneGraphId`` CDK context, so a routine load lands on
the graph the retrieval Lambda reads). Pass ``--graph-id`` to override it, e.g.
for a staging graph paired with ``--cache-prefix staging/``.
"""

import argparse
import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3
import yaml

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

s3 = boto3.client("s3")


def load_config(config_path: str) -> dict:
    with open(config_path) as f:
        return yaml.safe_load(f)


def get_neptune_client(graph_id: str):
    return boto3.client(
        "neptune-graph", region_name=os.environ.get("AWS_REGION", "us-east-1")
    ), graph_id


def execute_query(client, graph_id: str, query: str, parameters: dict | None = None) -> dict:
    """Run an OpenCypher query and return the parsed payload.

    The boto3 ``neptune-graph.execute_query`` API returns ``{"payload": <StreamingBody>}``;
    the actual ``{"results": [...]}`` JSON lives inside that streaming body. We
    decode here so callers can reliably do ``result.get("results", [])``. Any
    mutation-only phase that ignores the return value still works the same
    because the parse cost is one ``read()`` per query.
    """
    kwargs = {
        "graphIdentifier": graph_id,
        "language": "OPEN_CYPHER",
        "queryString": query,
    }
    if parameters:
        kwargs["parameters"] = parameters

    for attempt in range(8):
        try:
            resp = client.execute_query(**kwargs)
            payload = resp.get("payload")
            if payload is None:
                return {}
            return json.loads(payload.read())
        except Exception as e:
            # Neptune Analytics signals throttling via ThrottlingException OR via
            # UnprocessableException with message "Retry for SDK query requests is
            # suppressed, please resubmit the query."
            name = type(e).__name__
            msg = str(e)
            is_throttle = (
                "ThrottlingException" in name
                or "resubmit the query" in msg
                or "retry is suppressed" in msg.lower()
            )
            if is_throttle and attempt < 7:
                wait = min(60, 2**attempt)
                logger.warning(f"Neptune throttled ({name}), retrying in {wait}s...")
                time.sleep(wait)
            else:
                raise


# Document-level fields that ``extract.py`` writes and phases 2/4/6 read. The
# ``embedded/`` copy of these is a snapshot taken when the doc was last
# EMBEDDED, so any metadata fix made at extract time (title repair, new
# source_url, reclassified doc_type, ...) never reaches the graph unless the
# doc is also re-embedded. ``extracted/`` is therefore authoritative for these
# keys and is overlaid onto the embedded doc at load time. Embedding data
# (``chunks``, ``doc_embedding``, ``embed_input_mode``) is NEVER overlaid —
# chunk text and vectors must stay the pair that was actually embedded.
DOC_METADATA_KEYS: tuple[str, ...] = (
    "title",
    "summary",
    "citation",
    "case_name",
    "source_url",
    "doc_type",
    "framework_id",
    "authority_level",
    "effective_date",
    "statute_refs",
    "admin_rule_refs",
    "implements_refs",
    "topics",
    "s3_key",
)

_EMBEDDING_KEYS: frozenset[str] = frozenset({"chunks", "doc_embedding", "embed_input_mode"})
assert not _EMBEDDING_KEYS & set(DOC_METADATA_KEYS)


def losers_key(cache_prefix: str = "") -> str:
    """Where ``ops/purge_dedup_losers.py`` records doc_ids that must never load."""
    return f"{cache_prefix}dedup/losers.json"


def load_dedup_losers(work_bucket: str, cache_prefix: str = "") -> set[str]:
    """Return the set of loser doc_ids recorded by the dedup purge, if any.

    The file is ``{"losers": {doc_id: {...}}}`` (or a bare list of ids). A
    missing file means no losers — the pre-purge world.
    """
    try:
        body = s3.get_object(Bucket=work_bucket, Key=losers_key(cache_prefix))["Body"].read()
    except Exception as e:  # noqa: BLE001 — NoSuchKey shapes differ across clients/fakes
        if "NoSuchKey" in type(e).__name__ or "NoSuchKey" in str(e) or "404" in str(e):
            return set()
        raise
    data = json.loads(body)
    losers = data.get("losers", data) if isinstance(data, dict) else data
    return set(losers.keys()) if isinstance(losers, dict) else set(losers)


def overlay_extracted_metadata(embedded: dict, extracted: dict | None) -> bool:
    """Copy ``DOC_METADATA_KEYS`` from the extracted doc onto the embedded doc.

    Only keys present in ``extracted`` are copied; embedding data is untouched.
    Returns True when the title changed (the regression this guards against).
    """
    if not extracted:
        return False
    old_title = embedded.get("title")
    for key in DOC_METADATA_KEYS:
        if key in extracted:
            embedded[key] = extracted[key]
    return embedded.get("title") != old_title


def load_embedded_docs(
    work_bucket: str,
    cache_prefix: str = "",
    metadata_overlay: bool = True,
    skip_losers: bool = True,
) -> list[dict]:
    """Load every ``{cache_prefix}embedded/*.json`` from the work bucket.

    Chunks may carry extra fields from document expansion (``aliases``,
    ``gold_queries``, ``embed_input_chars``) and docs ``embed_input_mode``;
    phases 5/8 only read the specific fields they need, so these pass through
    harmlessly and are never written to the graph.

    ``metadata_overlay`` (default on): document-level fields are refreshed from
    ``{cache_prefix}extracted/{doc_id}.json`` when it exists — see
    ``DOC_METADATA_KEYS``. ``skip_losers`` (default on): doc_ids recorded in
    ``{cache_prefix}dedup/losers.json`` are dropped so a full load cannot
    resurrect nodes an ops dedup pass deleted.
    """
    keys: list[str] = []
    prefix = f"{cache_prefix}embedded/"
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=work_bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith(".json"):
                keys.append(key)
    logger.info(f"Found {len(keys)} embedded JSONs under '{prefix}'; downloading in parallel...")

    losers = load_dedup_losers(work_bucket, cache_prefix) if skip_losers else set()
    if losers:
        before = len(keys)
        keys = [k for k in keys if k.removeprefix(prefix).removesuffix(".json") not in losers]
        logger.info(
            f"Dedup losers: skipping {before - len(keys)} embedded JSON(s) listed in "
            f"'{losers_key(cache_prefix)}' ({len(losers)} ids recorded)"
        )

    extracted_prefix = f"{cache_prefix}extracted/"

    def fetch(key: str) -> dict:
        doc = json.loads(s3.get_object(Bucket=work_bucket, Key=key)["Body"].read())
        if not metadata_overlay:
            return doc
        doc_id = doc.get("doc_id") or key.removeprefix(prefix).removesuffix(".json")
        try:
            raw = s3.get_object(Bucket=work_bucket, Key=f"{extracted_prefix}{doc_id}.json")
        except Exception as e:  # noqa: BLE001
            if "NoSuchKey" in type(e).__name__ or "NoSuchKey" in str(e) or "404" in str(e):
                doc["_metadata_overlay"] = "missing"
                return doc
            raise
        extracted = json.loads(raw["Body"].read())
        doc["_metadata_overlay"] = (
            "title_changed" if overlay_extracted_metadata(doc, extracted) else "applied"
        )
        return doc

    docs: list[dict] = []
    with ThreadPoolExecutor(max_workers=32) as pool:
        for i, doc in enumerate(pool.map(fetch, keys), start=1):
            docs.append(doc)
            if i % 500 == 0 or i == len(keys):
                logger.info(f"  Loaded {i}/{len(keys)} embedded JSONs")

    if metadata_overlay:
        states = [d.pop("_metadata_overlay", "missing") for d in docs]
        applied = sum(1 for s in states if s != "missing")
        title_changed = sum(1 for s in states if s == "title_changed")
        logger.info(
            f"Metadata overlay from '{extracted_prefix}': applied to {applied}/{len(docs)} docs, "
            f"title changed for {title_changed}, no extracted copy for {len(docs) - applied}"
        )
    else:
        logger.info(
            "Metadata overlay DISABLED (--no-metadata-overlay): embedded/ metadata is used as-is"
        )
    return docs


def dedup_case_law_docs(documents: list[dict]) -> list[dict]:
    """Collapse parallel-citation duplicate case-law docs before loading.

    The same opinion can appear in the embedded/ cache under multiple reporter
    citations (e.g. '405 Wis. 2d 616' and '2023 WI 8' / '985 N.W.2d 69'), all
    sharing one CourtListener source_url. ``ingest_case_law.py`` dedups these
    before raw upload, but stale cache entries from older runs can survive and
    reintroduce duplicates. Collapse them here — keyed on source_url, keeping
    the highest-priority reporter — so no phase ever loads a duplicate opinion.
    """
    from tools.ingestion.ingest_case_law import _reporter_priority

    by_url: dict[str, list[dict]] = {}
    passthrough: list[dict] = []
    for doc in documents:
        url = doc.get("source_url", "")
        if doc.get("doc_type") == "case_law" and url:
            by_url.setdefault(url, []).append(doc)
        else:
            passthrough.append(doc)

    kept: list[dict] = []
    dropped = 0
    for group in by_url.values():
        if len(group) == 1:
            kept.append(group[0])
            continue
        group.sort(key=lambda d: _reporter_priority(d.get("doc_id", "").replace("case-law-", "")))
        kept.append(group[0])
        dropped += len(group) - 1
        losers = [d.get("doc_id") for d in group[1:]]
        logger.info(f"  Dedup case law: keep {group[0].get('doc_id')} <- drop {losers}")

    if dropped:
        logger.info(f"Case-law dedup: dropped {dropped} parallel-citation duplicate(s)")
    return passthrough + kept


def phase_1_scaffold(client, graph_id: str, config: dict):
    logger.info("Phase 1: Creating framework scaffold...")

    frameworks = config.get("frameworks", [])
    for fw in frameworks:
        execute_query(
            client,
            graph_id,
            "MERGE (f:Framework {id: $id}) SET f.title = $title, f.authority_level = $level",
            {"id": fw["id"], "title": fw["title"], "level": fw.get("authority_level", 99)},
        )

    for fw in frameworks:
        if "parent" in fw:
            execute_query(
                client,
                graph_id,
                "MATCH (child:Framework {id: $child_id}), (parent:Framework {id: $parent_id}) "
                "MERGE (child)-[:DERIVED_FROM]->(parent)",
                {"child_id": fw["id"], "parent_id": fw["parent"]},
            )

    for family in config.get("statute_families", []):
        execute_query(
            client,
            graph_id,
            "MERGE (s:Statute {id: $id}) SET s.title = $title",
            {"id": family["code"], "title": family["title"]},
        )
        execute_query(
            client,
            graph_id,
            "MATCH (s:Statute {id: $id}), (f:Framework {id: 'FW-STATUTES'}) "
            "MERGE (s)-[:BELONGS_TO]->(f)",
            {"id": family["code"]},
        )

    logger.info(
        f"  Created {len(frameworks)} frameworks, {len(config.get('statute_families', []))} statute families"
    )


def resolve_authority_level(doc: dict, config: dict) -> int | None:
    """Resolve a document's authority level without defaulting to FAQ.

    Precedence:
      1. An explicit ``authority_level`` on the doc.
      2. The canonical level of the doc's framework (single source of truth
         in ingest_config.yaml).
      3. None — render no authority badge rather than a misleading one.

    The old behaviour silently defaulted a missing level to 6 (FAQ), which
    is how 607 gov-pub/advisory nodes ended up badged as FAQs in the UI.
    """
    explicit = doc.get("authority_level")
    if explicit is not None:
        return int(explicit)

    framework_levels = {fw["id"]: fw["authority_level"] for fw in config.get("frameworks", [])}
    return framework_levels.get(doc.get("framework_id"))


def _case_name_for(doc: dict) -> str:
    """Best available case name: persisted ``case_name``, else the CourtListener URL slug."""
    from tools.ingestion.ingest_case_law import _case_name_from_url

    name = (doc.get("case_name") or "").strip()
    if not name or name == (doc.get("citation") or "").strip():
        name = _case_name_from_url(doc.get("source_url") or "")
    return name.strip()


def repair_case_law_title(doc: dict) -> tuple[str, str | None]:
    """Sink guard for case-law titles written by Phase 2.

    Returns ``(title, repair_kind)`` where ``repair_kind`` is None when the
    title was fine, ``"named"`` when a bare/doubled/empty/numeric title was
    replaced by ``"{case_name}, {citation}"``, or ``"collapsed"`` when a
    doubled ``"{citation}, {citation}"`` title had no name available and was
    collapsed to the bare citation (strictly better, and it keeps the Phase 10
    doubled-title assertion honest).

    Bad shapes (all seen in production, see docs/tasks.md Task 46/64):
      - ``title == citation``            (bare reporter cite, no name)
      - ``title == "{cit}, {cit}"``      (legacy doubled cite)
      - empty title / title starts with a digit (a cite, or a cite with junk)
    """
    title = (doc.get("title") or "").strip()
    citation = (doc.get("citation") or "").strip()
    doubled = f"{citation}, {citation}" if citation else None

    bad = (
        not title
        or (citation and title == citation)
        or (doubled is not None and title == doubled)
        or title[:1].isdigit()
    )
    if not bad:
        return title, None

    case_name = _case_name_for(doc)
    if case_name and case_name != citation and not case_name[:1].isdigit():
        if title.startswith(case_name):
            return title, None  # e.g. "123 Main St LLC v. City, 5 Wis. 2d 1" — already named
        return (f"{case_name}, {citation}" if citation else case_name), "named"
    if doubled is not None and title == doubled:
        return citation, "collapsed"
    return title or doc.get("doc_id", ""), None


def phase_2_document_nodes(client, graph_id: str, documents: list[dict], config: dict):
    logger.info("Phase 2: Creating document nodes...")

    from tools.ingestion.lib.wpam_year import extract_wpam_year_from_doc_id

    doc_type_to_label = config.get("doc_types", {})
    count = 0
    wpam_year_misses = 0
    case_law_titles_repaired = 0
    case_law_titles_collapsed = 0

    for doc in documents:
        doc_type = doc.get("doc_type", "guide")
        label = doc_type_to_label.get(doc_type, "Guide")

        if doc_type == "case_law":
            new_title, kind = repair_case_law_title(doc)
            if kind == "named":
                case_law_titles_repaired += 1
                logger.info(
                    f"  Phase 2 title guard: {doc['doc_id']}: {doc.get('title')!r} -> {new_title!r}"
                )
            elif kind == "collapsed":
                case_law_titles_collapsed += 1
            doc["title"] = new_title

        edition_year = None
        if doc.get("framework_id") == "FW-WPAM":
            edition_year = extract_wpam_year_from_doc_id(doc["doc_id"])
            if edition_year is None:
                wpam_year_misses += 1
                logger.warning(
                    f"Phase 2: WPAM doc '{doc['doc_id']}' has no extractable edition_year; "
                    "loading without the property"
                )

        execute_query(
            client,
            graph_id,
            f"MERGE (d:{label} {{id: $id}}) "
            f"SET d.title = $title, d.source_key = $source_key, "
            f"d.summary = $summary, d.source_url = $source_url, "
            f"d.doc_type = $doc_type, d.authority_level = $auth_level, "
            f"d.citation = $citation, d.effective_date = $effective_date, "
            f"d.edition_year = $edition_year",
            {
                "id": doc["doc_id"],
                "title": doc.get("title", doc["doc_id"]),
                "source_key": doc.get("s3_key", ""),
                "summary": doc.get("summary", ""),
                "source_url": doc.get("source_url", ""),
                "doc_type": doc_type,
                "auth_level": resolve_authority_level(doc, config),
                "citation": doc.get("citation", ""),
                "effective_date": doc.get("effective_date", ""),
                "edition_year": edition_year,
            },
        )

        fw_id = doc.get("framework_id", "FW-GOV-PUBS")
        execute_query(
            client,
            graph_id,
            f"MATCH (d:{label} {{id: $doc_id}}), (f:Framework {{id: $fw_id}}) "
            "MERGE (d)-[:BELONGS_TO]->(f)",
            {"doc_id": doc["doc_id"], "fw_id": fw_id},
        )
        count += 1
        if count % 200 == 0:
            logger.info(f"  Phase 2 progress: {count}/{len(documents)} document nodes")

    if wpam_year_misses:
        logger.warning(f"Phase 2: {wpam_year_misses} WPAM docs loaded without edition_year")
    logger.info(
        f"  Phase 2 title guard: case_law_titles_repaired={case_law_titles_repaired} "
        f"case_law_titles_collapsed={case_law_titles_collapsed}"
    )
    logger.info(f"  Created {count} document nodes")


def phase_3_statute_hierarchy(client, graph_id: str):
    """Build (Section)-[:PART_OF]->(Chapter) and (Sub)-[:PART_OF]->(Section).

    Wisconsin Statute IDs look like:
      - WIS-STAT-70           — chapter (no dot)
      - WIS-STAT-70.32        — section (chapter.section)
      - WIS-STAT-66.021(5)(g) — subsection (further nested by parens)

    For each section/subsection, we emit one edge to its immediate parent
    (rsplit on '(' for parens, otherwise the chapter). The chapter node
    itself never gets a PART_OF — that would self-loop.

    Both the section→chapter and subsection→section edges go through one
    UNWIND each, instead of a per-statute execute_query — matches the
    batching pattern used in phases 3, 5, 8.
    """
    logger.info("Phase 3: Building statute hierarchy (PART_OF)...")

    result = execute_query(client, graph_id, "MATCH (s:Statute) RETURN s.id AS id")
    statutes = result.get("results", [])

    section_to_chapter: list[dict] = []
    sub_to_parent: list[dict] = []
    for stat in statutes:
        stat_id = stat["id"]
        # Only "WIS-STAT-{digits}{. or (}..." are sections/subsections.
        # The trailing punctuation guard prevents chapter IDs from matching.
        match = re.match(r"WIS-STAT-(\d+)(?:[.(])", stat_id)
        if match:
            chapter_id = f"WIS-STAT-{match.group(1)}"
            if chapter_id != stat_id:
                section_to_chapter.append({"child_id": stat_id, "parent_id": chapter_id})

        # Subsection → its immediate paren-stripped parent (e.g.
        # "WIS-STAT-66.021(5)(g)" → "WIS-STAT-66.021(5)"). The parent may
        # not yet exist as a real node — created as a stub on demand.
        if "(" in stat_id:
            parent_id = stat_id.rsplit("(", 1)[0]
            sub_to_parent.append({"child_id": stat_id, "parent_id": parent_id})

    flush_cap = 400

    # MERGE parent chapters on demand. At phase 4 time only 7 chapter nodes
    # exist (the hardcoded ``statute_families`` config). Sections from
    # chapters outside that list (e.g., ``WIS-STAT-292.31`` from chapter 292)
    # have no parent to MATCH, so the original MATCH-MATCH-MERGE pattern
    # produced 0 edges. We now MERGE the chapter as a stub if it doesn't
    # exist before wiring the edge — same pattern as the subsection path.
    for start in range(0, len(section_to_chapter), flush_cap):
        chunk = section_to_chapter[start : start + flush_cap]
        execute_query(
            client,
            graph_id,
            "UNWIND $rows AS row MERGE (p:Statute {id: row.parent_id}) ON CREATE SET p.stub = true",
            {"rows": chunk},
        )
        execute_query(
            client,
            graph_id,
            "UNWIND $rows AS row "
            "MATCH (child:Statute {id: row.child_id}), (parent:Statute {id: row.parent_id}) "
            "MERGE (child)-[:PART_OF]->(parent)",
            {"rows": chunk},
        )

    for start in range(0, len(sub_to_parent), flush_cap):
        chunk = sub_to_parent[start : start + flush_cap]
        # Two-step: ensure parent stub exists, then wire the edge. We can't
        # combine into one UNWIND cleanly because the MERGE-of-parent must
        # commit before the section MATCH can pick it up.
        execute_query(
            client,
            graph_id,
            "UNWIND $rows AS row MERGE (p:Statute {id: row.parent_id}) ON CREATE SET p.stub = true",
            {"rows": chunk},
        )
        execute_query(
            client,
            graph_id,
            "UNWIND $rows AS row "
            "MATCH (child:Statute {id: row.child_id}), (parent:Statute {id: row.parent_id}) "
            "MERGE (child)-[:PART_OF]->(parent)",
            {"rows": chunk},
        )

    logger.info(
        f"  Created {len(section_to_chapter)} section→chapter and "
        f"{len(sub_to_parent)} subsection→parent edges"
    )


def phase_4_hierarchy_links(client, graph_id: str):
    """Attach stub Statute / AdminRule nodes to their framework.

    This phase used to also write HAS_SUBSECTION edges from a document's
    ``_parent_id``, but nothing in the pipeline ever set that key, so the
    edge list was always empty. Removed 2026-09-19.
    """
    logger.info("Phase 4: Universal hierarchy...")

    execute_query(
        client,
        graph_id,
        "MATCH (s:Statute) WHERE s.stub = true AND NOT (s)-[:BELONGS_TO]->() "
        "MATCH (f:Framework {id: 'FW-STATUTES'}) "
        "MERGE (s)-[:BELONGS_TO]->(f)",
    )
    execute_query(
        client,
        graph_id,
        "MATCH (r:AdminRule) WHERE r.stub = true AND NOT (r)-[:BELONGS_TO]->() "
        "MATCH (f:Framework {id: 'FW-ADMIN-RULES'}) "
        "MERGE (r)-[:BELONGS_TO]->(f)",
    )

    logger.info("  Hierarchy links complete")


def phase_6_case_law_cites(client, graph_id: str, documents: list[dict]):
    """Create (Statute)-[:CITES]->(CaseLaw) edges for case law documents.

    Case law docs have statute_refs derived from citing-statute PDF page headers
    (deterministic regex, not LLM). This wires the reverse edge so the agent can
    traverse from a Statute stub outward to discover interpreting cases.
    """
    logger.info("Creating case law reverse CITES edges...")

    rows = [
        {"stub_id": f"WIS-STAT-{ref}", "doc_id": doc["doc_id"]}
        for doc in documents
        if doc.get("doc_type") == "case_law"
        for ref in doc.get("statute_refs", [])
    ]

    if not rows:
        logger.info("  No case law documents with statute_refs found")
        return

    flush_cap = 400
    edges_created = 0
    for start in range(0, len(rows), flush_cap):
        batch = rows[start : start + flush_cap]
        execute_query(
            client,
            graph_id,
            "UNWIND $rows AS row "
            "MATCH (s:Statute {id: row.stub_id}), (c:CaseLaw {id: row.doc_id}) "
            "MERGE (s)-[:CITES]->(c)",
            {"rows": batch},
        )
        edges_created += len(batch)

    logger.info(f"  Created {edges_created} Statute→CaseLaw CITES edges")


PHASE_5_BATCH_SIZE = 10
PHASE_5_MAX_PAIRS_PER_FLUSH = 80
# Chunk text dominates the UNWIND payload memory cost. Case-law opinion
# chunks can be up to OPINION_CHUNK_SIZE (2000) chars each; 50 of those in
# one flush sends ~100KB of text through Neptune's per-query memory budget,
# which deterministically OOMs. Cap cumulative text bytes per flush to keep
# payloads within the budget.
PHASE_5_MAX_BYTES_PER_FLUSH = 50_000


def _chunk_pair_count(entry: dict) -> int:
    return len(entry.get("statute_refs", [])) + len(entry.get("admin_rule_refs", []))


def _chunk_byte_count(entry: dict) -> int:
    return len(entry.get("text", ""))


def _flush_phase_8_batch(client, graph_id: str, batch: list[dict]) -> int:
    """Write one batch of chunks using UNWIND for ~10x throughput vs per-chunk queries.

    Returns count of CITES edges created in the batch.
    """
    if not batch:
        return 0

    # 1. Chunk nodes (single UNWIND MERGE, sets all scalar props).
    execute_query(
        client,
        graph_id,
        "UNWIND $rows AS row "
        "MERGE (c:Chunk {id: row.id}) "
        "SET c.text = row.text, c.doc_id = row.doc_id, "
        "c.source_url = row.source_url, c.chunk_index = row.idx, "
        "c.s3_key = row.s3_key, c.start_page = row.start_page, "
        "c.end_page = row.end_page, c.heading = row.heading, "
        "c.subheading = row.subheading, c.content_role = row.content_role, "
        "c.edition_year = row.edition_year",
        {
            "rows": [
                {
                    "id": b["chunk_id"],
                    "text": b["text"],
                    "doc_id": b["doc_id"],
                    "source_url": b["source_url"],
                    "idx": b["idx"],
                    "s3_key": b["s3_key"],
                    "start_page": b["start_page"],
                    "end_page": b["end_page"],
                    "heading": b["heading"],
                    "subheading": b["subheading"],
                    "content_role": b.get("content_role", ""),
                    "edition_year": b.get("edition_year"),
                }
                for b in batch
            ]
        },
    )

    # 2. EXTRACTED_FROM edges (batch MATCH+MERGE).
    execute_query(
        client,
        graph_id,
        "UNWIND $rows AS row "
        "MATCH (c:Chunk {id: row.chunk_id}), (d {id: row.doc_id}) "
        "MERGE (c)-[:EXTRACTED_FROM]->(d)",
        {"rows": [{"chunk_id": b["chunk_id"], "doc_id": b["doc_id"]} for b in batch]},
    )

    cite_edges = 0

    # 3. Statute stubs + chunk CITES Statute (two UNWINDs).
    statute_refs = sorted({ref for b in batch for ref in b["statute_refs"]})
    if statute_refs:
        execute_query(
            client,
            graph_id,
            "UNWIND $rows AS row "
            "MERGE (s:Statute {id: row.id}) "
            "ON CREATE SET s.title = row.title, s.stub = true",
            {"rows": [{"id": f"WIS-STAT-{r}", "title": f"Wis. Stat. {r}"} for r in statute_refs]},
        )
        cite_pairs = [
            {"chunk_id": b["chunk_id"], "stub_id": f"WIS-STAT-{ref}"}
            for b in batch
            for ref in b["statute_refs"]
        ]
        execute_query(
            client,
            graph_id,
            "UNWIND $rows AS row "
            "MATCH (c:Chunk {id: row.chunk_id}), (s:Statute {id: row.stub_id}) "
            "MERGE (c)-[:CITES]->(s)",
            {"rows": cite_pairs},
        )
        cite_edges += len(cite_pairs)

    # 4. AdminRule stubs + chunk CITES AdminRule.
    admin_refs = sorted({ref for b in batch for ref in b["admin_rule_refs"]})
    if admin_refs:
        execute_query(
            client,
            graph_id,
            "UNWIND $rows AS row "
            "MERGE (r:AdminRule {id: row.id}) "
            "ON CREATE SET r.title = row.title, r.stub = true",
            {"rows": [{"id": f"ADMIN-{r.replace(' ', '-')}", "title": r} for r in admin_refs]},
        )
        cite_pairs = [
            {"chunk_id": b["chunk_id"], "stub_id": f"ADMIN-{ref.replace(' ', '-')}"}
            for b in batch
            for ref in b["admin_rule_refs"]
        ]
        execute_query(
            client,
            graph_id,
            "UNWIND $rows AS row "
            "MATCH (c:Chunk {id: row.chunk_id}), (r:AdminRule {id: row.stub_id}) "
            "MERGE (c)-[:CITES]->(r)",
            {"rows": cite_pairs},
        )
        cite_edges += len(cite_pairs)

    return cite_edges


def _purge_stale_chunks(client, graph_id: str, documents: list[dict]):
    """Delete all existing Chunk nodes for documents being reloaded.

    When chunks are filtered or renumbered (e.g., after quality filtering),
    old high-numbered chunks become orphans. This purge ensures a clean slate
    before re-creating them from the current embedded JSONs.
    """
    doc_ids = [d["doc_id"] for d in documents]
    logger.info(f"Purging existing chunks for {len(doc_ids)} documents...")

    total_deleted = 0
    batch_size = 50
    for i in range(0, len(doc_ids), batch_size):
        batch = doc_ids[i : i + batch_size]
        result = execute_query(
            client,
            graph_id,
            "UNWIND $doc_ids AS did "
            "MATCH (c:Chunk {doc_id: did}) "
            "DETACH DELETE c "
            "RETURN count(c) AS deleted",
            {"doc_ids": batch},
        )
        deleted = result.get("results", [{}])[0].get("deleted", 0)
        total_deleted += deleted

    logger.info(f"  Purged {total_deleted} stale chunks across {len(doc_ids)} documents")


def phase_5_chunk_nodes(client, graph_id: str, documents: list[dict]):
    _purge_stale_chunks(client, graph_id, documents)
    logger.info(
        f"Phase 5: Creating chunk nodes with headings + chunk-level CITES edges "
        f"(batch size {PHASE_5_BATCH_SIZE})..."
    )
    from tools.ingestion.lib.wpam_year import extract_wpam_year_from_doc_id

    batch: list[dict] = []
    batch_pairs = 0
    batch_bytes = 0
    total_chunks = 0
    cite_edges = 0

    for doc in documents:
        doc_id = doc["doc_id"]
        s3_key = doc.get("s3_key", "")
        edition_year = None
        if doc.get("framework_id") == "FW-WPAM":
            edition_year = extract_wpam_year_from_doc_id(doc_id)
        for i, chunk in enumerate(doc.get("chunks", [])):
            meta = chunk.get("metadata", {})
            entry = {
                "chunk_id": f"{doc_id}_chunk_{i:04d}",
                "text": chunk["text"],
                "doc_id": doc_id,
                "source_url": meta.get("source_url", ""),
                "idx": i,
                "s3_key": meta.get("source", s3_key),
                "start_page": meta.get("start_page"),
                "end_page": meta.get("end_page"),
                "heading": meta.get("heading", ""),
                "subheading": meta.get("subheading", ""),
                "content_role": meta.get("content_role", ""),
                "edition_year": edition_year,
                "statute_refs": meta.get("statute_refs", []),
                "admin_rule_refs": meta.get("admin_rule_refs", []),
            }
            pairs = _chunk_pair_count(entry)
            byte_len = _chunk_byte_count(entry)

            # Flush BEFORE adding if this chunk would blow any of: count, pair, or byte cap.
            if batch and (
                len(batch) >= PHASE_5_BATCH_SIZE
                or batch_pairs + pairs > PHASE_5_MAX_PAIRS_PER_FLUSH
                or batch_bytes + byte_len > PHASE_5_MAX_BYTES_PER_FLUSH
            ):
                cite_edges += _flush_phase_8_batch(client, graph_id, batch)
                batch = []
                batch_pairs = 0
                batch_bytes = 0
                if total_chunks % 1000 == 0:
                    logger.info(
                        f"  Phase 5 progress: {total_chunks} chunks, {cite_edges} CITES edges"
                    )

            batch.append(entry)
            batch_pairs += pairs
            batch_bytes += byte_len
            total_chunks += 1

    if batch:
        cite_edges += _flush_phase_8_batch(client, graph_id, batch)

    logger.info(f"  Created {total_chunks} chunk nodes, {cite_edges} chunk-level CITES edges")


def phase_7_stub_resolution(client, graph_id: str, only_doc_ids: set[str] | None = None):
    """Wire DEFINED_BY edges from statute stubs to their matching statute chunks.

    Stubs like WIS-STAT-70.32 become routing nodes that point to the actual
    chunk(s) containing that section's text, matched by chunk heading prefix.

    ``only_doc_ids`` (set on a ``--source-filter`` load) restricts resolution to
    stubs that target one of the documents being loaded. Phase 5 only purges and
    recreates chunks of those documents, so DEFINED_BY edges into every other
    document are still valid — re-resolving all ~8.5k stubs (one query each)
    is what made a single-document load take 15 minutes from a laptop.
    """
    logger.info("Phase 7: Wiring DEFINED_BY edges from stubs to statute chunks...")
    if only_doc_ids is not None:
        logger.info(f"  Restricted to stubs targeting {len(only_doc_ids)} loaded document(s)")

    result = execute_query(
        client, graph_id, "MATCH (s:Statute) WHERE s.stub = true RETURN s.id AS id"
    )
    stubs = result.get("results", [])
    logger.info(f"  {len(stubs)} statute stubs found")

    parsed = []
    for stub in stubs:
        stub_id = stub["id"]
        m = re.match(r"WIS-STAT-(.+)", stub_id)
        if not m:
            continue
        section = m.group(1)
        chapter = section.split(".")[0]
        if not chapter.isdigit():
            continue
        if "." not in section:
            continue
        # Strip subsection qualifiers — chunk headings are section-level only
        # e.g. "70.47(1)" → "70.47", "70.111 (27)" → "70.111"
        match_section = re.split(r"[\s(]", section, maxsplit=1)[0]
        if only_doc_ids is not None and f"statutes-{chapter}" not in only_doc_ids:
            continue
        parsed.append(
            {
                "stub_id": stub_id,
                "doc_id": f"statutes-{chapter}",
                "section": match_section,
            }
        )

    if not parsed:
        logger.info("  No section-level stubs to resolve")
    else:
        logger.info(f"  {len(parsed)} section-level stubs to resolve")

        # Group by chapter doc to reduce query count
        by_doc: dict[str, list[dict]] = {}
        for entry in parsed:
            by_doc.setdefault(entry["doc_id"], []).append(entry)

        pairs: list[dict] = []
        for doc_id, entries in by_doc.items():
            for entry in entries:
                result = execute_query(
                    client,
                    graph_id,
                    "MATCH (c:Chunk)-[:EXTRACTED_FROM]->(d {id: $doc_id}) "
                    "WHERE c.heading STARTS WITH $section "
                    "RETURN c.id AS chunk_id",
                    {"doc_id": doc_id, "section": entry["section"]},
                )
                for row in result.get("results", []):
                    pairs.append({"stub_id": entry["stub_id"], "chunk_id": row["chunk_id"]})

        resolved_count = len({p["stub_id"] for p in pairs})

        flush_cap = 200
        edges_created = 0
        for start in range(0, len(pairs), flush_cap):
            batch = pairs[start : start + flush_cap]
            execute_query(
                client,
                graph_id,
                "UNWIND $rows AS row "
                "MATCH (s:Statute {id: row.stub_id}), (c:Chunk {id: row.chunk_id}) "
                "MERGE (s)-[:DEFINED_BY]->(c)",
                {"rows": batch},
            )
            edges_created += len(batch)

        logger.info(
            f"  Wired {edges_created} DEFINED_BY edges for {resolved_count}/{len(parsed)} stubs"
        )

    # --- AdminRule stub resolution ---
    # Same pattern: normalize dirty stub IDs to section level, match against
    # admin-rule chunk headings. Stubs like ADMIN-Tax-18.05(1)(a) normalize to
    # "Tax 18.05" which matches the chunk heading.
    logger.info("  Resolving AdminRule stubs...")

    ar_result = execute_query(
        client, graph_id, "MATCH (r:AdminRule) WHERE r.stub = true RETURN r.id AS id"
    )
    ar_stubs = ar_result.get("results", [])
    logger.info(f"  {len(ar_stubs)} AdminRule stubs found")

    def _normalize_admin_stub(stub_id: str) -> tuple[str, str] | None:
        """Parse ADMIN-Tax-18.05(1)(a) → (doc_id='admin_rules-tax-18', section='Tax 18.05')."""
        m = re.match(r"ADMIN-(.+)", stub_id)
        if not m:
            return None
        raw = m.group(1)
        # Fix OCR: lowercase L → 1 in subsection refs
        raw = raw.replace("(l)", "(1)")
        # Normalize embedded newlines/spaces → hyphen (preserves Tax-18 structure)
        raw = re.sub(r"[\n\r]+", "-", raw)
        # Strip trailing punctuation artifacts
        raw = raw.rstrip(".)],;:")
        raw = raw.strip()
        # Collapse double hyphens from newline fix
        raw = re.sub(r"-+", "-", raw)
        # Extract section: "Tax-18.05(1)(a)" → "Tax-18.05"
        section_match = re.match(r"(Tax-\d+\.\d+)", raw)
        if not section_match:
            # Try chapter-only: "Tax-18" — skip, no section to resolve
            return None
        section_slug = section_match.group(1)  # "Tax-18.05"
        # Derive doc_id: "Tax-18.05" → chapter "tax-18" → "admin_rules-tax-18"
        chapter_match = re.match(r"Tax-(\d+)", section_slug)
        if not chapter_match:
            return None
        chapter_num = chapter_match.group(1)
        doc_id = f"admin_rules-tax-{chapter_num}"
        # Heading format in chunks: "Tax 18.05" (space, not hyphen)
        heading_section = section_slug.replace("-", " ")  # "Tax 18.05"
        return doc_id, heading_section

    ar_parsed = []
    seen_pairs: set[tuple[str, str]] = set()
    for stub in ar_stubs:
        result_pair = _normalize_admin_stub(stub["id"])
        if not result_pair:
            continue
        doc_id, section = result_pair
        if only_doc_ids is not None and doc_id not in only_doc_ids:
            continue
        # Dedup: multiple dirty stubs (18.05(1)(a), 18.05(1)(b)) normalize to same section
        if (doc_id, section) not in seen_pairs:
            seen_pairs.add((doc_id, section))
        ar_parsed.append(
            {
                "stub_id": stub["id"],
                "doc_id": doc_id,
                "section": section,
            }
        )

    if not ar_parsed:
        logger.info("  No AdminRule section-level stubs to resolve")
    else:
        logger.info(f"  {len(ar_parsed)} AdminRule stubs to resolve (from {len(ar_stubs)} total)")

        # Group by doc
        ar_by_doc: dict[str, list[dict]] = {}
        for entry in ar_parsed:
            ar_by_doc.setdefault(entry["doc_id"], []).append(entry)

        ar_pairs: list[dict] = []
        for doc_id, entries in ar_by_doc.items():
            for entry in entries:
                result = execute_query(
                    client,
                    graph_id,
                    "MATCH (c:Chunk)-[:EXTRACTED_FROM]->(d {id: $doc_id}) "
                    "WHERE c.heading STARTS WITH $section "
                    "RETURN c.id AS chunk_id",
                    {"doc_id": doc_id, "section": entry["section"]},
                )
                for row in result.get("results", []):
                    ar_pairs.append({"stub_id": entry["stub_id"], "chunk_id": row["chunk_id"]})

        ar_resolved = len({p["stub_id"] for p in ar_pairs})

        for start in range(0, len(ar_pairs), flush_cap):
            batch = ar_pairs[start : start + flush_cap]
            execute_query(
                client,
                graph_id,
                "UNWIND $rows AS row "
                "MATCH (r:AdminRule {id: row.stub_id}), (c:Chunk {id: row.chunk_id}) "
                "MERGE (r)-[:DEFINED_BY]->(c)",
                {"rows": batch},
            )

        logger.info(
            f"  Wired {len(ar_pairs)} DEFINED_BY edges for {ar_resolved}/{len(ar_parsed)} AdminRule stubs"
        )


PHASE_8_WORKERS = 8


def _upsert_vector(client, graph_id: str, chunk_id: str, embedding: list[float]) -> None:
    # Neptune's vector upsert is a CALL procedure — cannot UNWIND a batch.
    # Inline the embedding literal to avoid parameterized-CALL limits.
    embedding_literal = "[" + ",".join(str(v) for v in embedding) + "]"
    execute_query(
        client,
        graph_id,
        f"MATCH (c:Chunk {{id: $id}}) "
        f"CALL neptune.algo.vectors.upsert(c, {embedding_literal}) "
        f"RETURN c.id",
        {"id": chunk_id},
    )


def phase_8_vector_upserts(client, graph_id: str, documents: list[dict]):
    logger.info(f"Phase 8: Upserting chunk vectors (parallel workers={PHASE_8_WORKERS})...")

    jobs: list[tuple[str, list[float]]] = []
    for doc in documents:
        doc_id = doc["doc_id"]
        for i, chunk in enumerate(doc.get("chunks", [])):
            embedding = chunk.get("embedding")
            if not embedding:
                continue
            jobs.append((f"{doc_id}_chunk_{i:04d}", embedding))

    logger.info(f"  {len(jobs)} vectors to upsert")

    total = 0
    with ThreadPoolExecutor(max_workers=PHASE_8_WORKERS) as pool:
        futures = [pool.submit(_upsert_vector, client, graph_id, cid, emb) for cid, emb in jobs]
        for fut in as_completed(futures):
            fut.result()
            total += 1
            if total % 500 == 0 or total == len(jobs):
                logger.info(f"  Upserted {total}/{len(jobs)} vectors...")

    logger.info(f"  Upserted {total} chunk vectors")


def phase_9_cleanup(
    client,
    graph_id: str,
    work_bucket: str,
    documents: list[dict] | None = None,
    cache_prefix: str = "",
):
    """Garbage-collect orphan nodes left over from prior loads.

    Two specific classes:
      1. Orphan Statute STUBs — created on demand by phase 3 / phase 8 when
         a chunk's regex matches a section we never indexed full text for.
         If after the whole load the stub has zero incoming AND zero
         outgoing relationships, it's a regex hallucination (e.g., a
         partial number that looked like a statute ref). Safe to delete.
      2. Stale CaseLaw nodes — parallel-reporter duplicates from prior loads
         that were later deduplicated from S3. Their extracted cache no
         longer exists, so they'll never be updated. Delete any CaseLaw
         node whose ID isn't in the current document set.

    A third class, orphan Topic nodes, was GC'd here until 2026-09-19. No
    phase has created a Topic node or a COVERS_TOPIC edge for a long time,
    so the query only ever deleted zero rows.

    This phase is run-once-and-safe. MERGE-idempotent phases above will
    not re-create the orphans because the underlying conditions for stub
    creation (unmatched regex, LLM canonical drift) only fire during
    extract/embed. So running this phase doesn't fight the rest of the
    pipeline.

    We DELIBERATELY do not GC:
      - Stub Statutes that have ANY edge — they're real placeholders.
      - Stub AdminRules — much smaller volume; defer until we see the
        same pattern.
    """
    logger.info("Phase 9: Cleaning up orphan stubs...")

    stub_orphans = execute_query(
        client,
        graph_id,
        "MATCH (s:Statute) "
        "WHERE s.stub = true "
        "  AND NOT (s)-[]-() "
        "WITH s LIMIT 5000 "
        "DETACH DELETE s "
        "RETURN count(s) AS deleted",
    )
    deleted_stubs = stub_orphans.get("results", [{}])[0].get("deleted", 0)
    logger.info(f"  Deleted {deleted_stubs} orphan Statute stubs (no incoming/outgoing edges)")

    # Stale CaseLaw nodes: compare graph against the extracted/ prefix in S3
    # (the authoritative document set), not against what was loaded this run.
    # This makes GC self-healing regardless of which subset was loaded.
    extracted_case_ids = set()
    extracted_prefix = f"{cache_prefix}extracted/"
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=work_bucket, Prefix=f"{extracted_prefix}case-law-"):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith(".json"):
                doc_id = key.removeprefix(extracted_prefix).removesuffix(".json")
                extracted_case_ids.add(doc_id)

    if extracted_case_ids:
        all_graph_cases = execute_query(
            client,
            graph_id,
            "MATCH (c:CaseLaw) RETURN c.id AS id",
        )
        graph_case_ids = {r["id"] for r in all_graph_cases.get("results", [])}
        stale_ids = sorted(graph_case_ids - extracted_case_ids)
        if stale_ids:
            logger.info(
                f"  Found {len(stale_ids)} stale CaseLaw nodes "
                f"(in graph but not in extracted/ set of {len(extracted_case_ids)})"
            )
            batch_size = 100
            total_deleted = 0
            for i in range(0, len(stale_ids), batch_size):
                batch = stale_ids[i : i + batch_size]
                result = execute_query(
                    client,
                    graph_id,
                    "UNWIND $ids AS cid "
                    "MATCH (c:CaseLaw {id: cid}) "
                    # Delete the node's chunks too — a bare DETACH DELETE on the
                    # doc node leaves its Chunk nodes parentless, and orphan
                    # chunks stay vector-searchable with no citation card.
                    "OPTIONAL MATCH (ch:Chunk)-[:EXTRACTED_FROM]->(c) "
                    "DETACH DELETE ch, c "
                    "RETURN count(DISTINCT c) AS deleted",
                    {"ids": batch},
                )
                total_deleted += result.get("results", [{}])[0].get("deleted", 0)
            logger.info(f"  Purged {total_deleted} stale CaseLaw nodes")
        else:
            logger.info("  No stale CaseLaw nodes found")

    # 4. Recorded dedup losers of ANY label (ops/purge_dedup_losers.py writes
    #    {prefix}dedup/losers.json). The CaseLaw sweep above already covers
    #    case-law losers once their extracted/ copy is gone; this catches the
    #    rest (e.g. ghost statutes-document-* ids) and is a no-op when the
    #    file is absent.
    losers = sorted(load_dedup_losers(work_bucket, cache_prefix))
    if losers:
        removed = 0
        for i in range(0, len(losers), 100):
            batch = losers[i : i + 100]
            result = execute_query(
                client,
                graph_id,
                "UNWIND $ids AS lid "
                "MATCH (d {id: lid}) "
                "OPTIONAL MATCH (ch:Chunk)-[:EXTRACTED_FROM]->(d) "
                "DETACH DELETE ch, d "
                "RETURN count(DISTINCT d) AS deleted",
                {"ids": batch},
            )
            removed += result.get("results", [{}])[0].get("deleted", 0)
        logger.info(
            f"  Dedup losers: removed {removed} node(s) (+ chunks) of the {len(losers)} ids "
            f"recorded in '{losers_key(cache_prefix)}'"
        )


class IntegrityCheckFailed(RuntimeError):
    """Raised by Phase 10 when a hard post-load invariant is violated."""


def phase_10_integrity_checks(client, graph_id: str) -> dict:
    """Post-load assertions over the live graph.

    Hard failure (non-zero exit): any CaseLaw node whose title is the legacy
    doubled ``"{cit}, {cit}"`` shape — the Phase 2 guard makes this impossible
    for docs loaded this run, so a hit means a stale node or a regression.
    Informational: bare-citation titles (``title == citation``; ~115
    Scholar-sourced nodes legitimately have no recoverable name) and orphan
    chunks (no EXTRACTED_FROM parent — should be 0 after Phase 9).
    """
    logger.info("Phase 10: Integrity checks...")

    def _count(query: str, field: str) -> int:
        res = execute_query(client, graph_id, query)
        rows = res.get("results", [])
        return int(rows[0].get(field, 0)) if rows else 0

    doubled = _count(
        "MATCH (c:CaseLaw) WHERE c.title = c.citation + ', ' + c.citation RETURN count(c) AS n",
        "n",
    )
    bare = _count("MATCH (c:CaseLaw) WHERE c.title = c.citation RETURN count(c) AS n", "n")
    orphans = _count(
        "MATCH (c:Chunk) WHERE NOT (c)-[:EXTRACTED_FROM]->() RETURN count(c) AS n", "n"
    )
    stats = {"doubled_titles": doubled, "bare_citation_titles": bare, "orphan_chunks": orphans}
    logger.info(
        f"  case-law doubled titles={doubled} (must be 0); bare-citation titles={bare} "
        f"(informational); orphan chunks={orphans}"
    )
    if orphans:
        logger.warning(f"  {orphans} orphan Chunk nodes have no EXTRACTED_FROM parent")
    if doubled:
        raise IntegrityCheckFailed(
            f"{doubled} CaseLaw node(s) carry a doubled '{{citation}}, {{citation}}' title"
        )
    return stats


GRAPH_ID_ENV_VAR = "NEPTUNE_GRAPH_ID"


def resolve_graph_id(cli_graph_id: str | None, env: dict[str, str] | None = None) -> str:
    """Pick the graph to load: explicit --graph-id, else $NEPTUNE_GRAPH_ID.

    The Fargate task definition carries NEPTUNE_GRAPH_ID (from the pinned
    ``neptuneGraphId`` CDK context), so an operator running `run_fargate.sh load`
    with no flags hits the same graph the retrieval Lambda queries. Passing
    --graph-id is the deliberate opt-out for a staging/blue-green graph.
    """
    if cli_graph_id:
        return cli_graph_id
    from_env = (env if env is not None else os.environ).get(GRAPH_ID_ENV_VAR, "").strip()
    if not from_env:
        raise SystemExit(
            f"No graph selected: pass --graph-id or set {GRAPH_ID_ENV_VAR}. "
            "On Fargate this env var comes from the task definition (CDK context "
            "'neptuneGraphId'); locally, export it before running."
        )
    return from_env


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Load documents into Neptune Analytics graph")
    parser.add_argument("--work-bucket", required=True)
    parser.add_argument(
        "--graph-id",
        default=None,
        help=(
            "Neptune Analytics graph identifier. Defaults to $NEPTUNE_GRAPH_ID (the "
            "pinned production graph on Fargate); pass explicitly to load a staging graph."
        ),
    )
    parser.add_argument("--config", default="tools/ingestion/config/ingest_config.yaml")
    parser.add_argument("--start-phase", type=int, default=1, help="Resume from specific phase")
    parser.add_argument(
        "--stop-after-phase", type=int, default=None, help="Exit cleanly after this phase completes"
    )
    parser.add_argument(
        "--source-filter",
        default="",
        help=(
            "Only load doc_ids matching this prefix (e.g., 'wpam-' for a WPAM-only "
            "re-load). Graph-wide phases (scaffold, hierarchy, stub resolution) "
            "still run but are MERGE-idempotent."
        ),
    )
    parser.add_argument(
        "--cache-prefix",
        default="",
        help="Prefix for every work-bucket key (embedded/, extracted/). e.g. 'staging/' loads "
        "staging/embedded/ — pair with a staging --graph-id so production is untouched.",
    )
    parser.add_argument(
        "--no-metadata-overlay",
        action="store_true",
        help="Do NOT refresh document-level metadata (title, summary, citation, ...) from "
        "extracted/ — use the snapshot inside embedded/ as-is. Default overlays, because "
        "embedded/ is only rewritten on re-embed and stale titles regressed the graph 4x.",
    )
    parser.add_argument(
        "--no-skip-losers",
        action="store_true",
        help="Load doc_ids recorded in {cache-prefix}dedup/losers.json anyway (default skips "
        "them so a full load cannot resurrect nodes a dedup pass deleted).",
    )
    return parser


def main():
    args = build_arg_parser().parse_args()

    config = load_config(args.config)
    client, graph_id = get_neptune_client(resolve_graph_id(args.graph_id))
    logger.info(f"Graph: {graph_id}; cache prefix: '{args.cache_prefix}'")
    documents = load_embedded_docs(
        args.work_bucket,
        args.cache_prefix,
        metadata_overlay=not args.no_metadata_overlay,
        skip_losers=not args.no_skip_losers,
    )
    logger.info(f"Loaded {len(documents)} documents for graph loading")

    documents = dedup_case_law_docs(documents)

    if args.source_filter:
        before = len(documents)
        documents = [d for d in documents if d.get("doc_id", "").startswith(args.source_filter)]
        logger.info(f"Source filter '{args.source_filter}': {before} → {len(documents)} documents")

    phases = [
        (1, "Scaffold", lambda: phase_1_scaffold(client, graph_id, config)),
        (2, "Document Nodes", lambda: phase_2_document_nodes(client, graph_id, documents, config)),
        (3, "Statute Hierarchy", lambda: phase_3_statute_hierarchy(client, graph_id)),
        (4, "Hierarchy Links", lambda: phase_4_hierarchy_links(client, graph_id)),
        (5, "Chunk Nodes", lambda: phase_5_chunk_nodes(client, graph_id, documents)),
        (6, "Case Law CITES", lambda: phase_6_case_law_cites(client, graph_id, documents)),
        (
            7,
            "Stub Resolution",
            lambda: phase_7_stub_resolution(
                client,
                graph_id,
                only_doc_ids={d["doc_id"] for d in documents} if args.source_filter else None,
            ),
        ),
        (8, "Vector Upserts", lambda: phase_8_vector_upserts(client, graph_id, documents)),
        (
            9,
            "Orphan Cleanup",
            lambda: phase_9_cleanup(
                client, graph_id, args.work_bucket, documents, args.cache_prefix
            ),
        ),
        (10, "Integrity Checks", lambda: phase_10_integrity_checks(client, graph_id)),
    ]

    for phase_num, name, fn in phases:
        if phase_num < args.start_phase:
            logger.info(f"Skipping Phase {phase_num}: {name}")
            continue
        logger.info(f"\n{'=' * 60}\nPhase {phase_num}: {name}\n{'=' * 60}")
        try:
            fn()
        except IntegrityCheckFailed as e:
            logger.error(f"Phase {phase_num} FAILED: {e}")
            raise SystemExit(1) from e
        if args.stop_after_phase is not None and phase_num >= args.stop_after_phase:
            logger.info(f"Stopping after Phase {phase_num} per --stop-after-phase.")
            return

    logger.info("\nGraph loading complete!")


if __name__ == "__main__":
    main()

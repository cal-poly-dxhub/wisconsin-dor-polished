"""
Phase 5: Load embedded documents into Neptune Analytics graph.

Implements 10 sequential sub-phases:
1. Scaffold (frameworks + hierarchy)
2. Document nodes
3. Statute hierarchy (PART_OF)
4. Hierarchy links (sub-document + universal)
5. Chunk nodes
6. Case Law CITES (Statute→CaseLaw reverse edges)
7. Stub resolution (DEFINED_BY edges from stubs to statute chunks)
8. Vector upserts
9. Semantic edges
10. Orphan cleanup

Usage:
    python -m tools.ingestion.load \
        --work-bucket <work-bucket> \
        --graph-id <neptune-graph-id> \
        --config tools/ingestion/config/ingest_config.yaml
"""

import argparse
import hashlib
import json
import logging
import math
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3
import yaml

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

s3 = boto3.client("s3")
# Tighter timeouts so Phase 5/9 LLM calls don't hang indefinitely on a stalled socket.
_bedrock_cfg = boto3.session.Config(
    read_timeout=180,
    connect_timeout=30,
    retries={"max_attempts": 5, "mode": "adaptive"},
)
bedrock = boto3.client(
    "bedrock-runtime",
    region_name=os.environ.get("AWS_REGION", "us-east-1"),
    config=_bedrock_cfg,
)


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


def load_embedded_docs(work_bucket: str) -> list[dict]:
    keys: list[str] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=work_bucket, Prefix="embedded/"):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith(".json"):
                keys.append(key)
    logger.info(f"Found {len(keys)} embedded JSONs; downloading in parallel...")

    def fetch(key: str) -> dict:
        return json.loads(s3.get_object(Bucket=work_bucket, Key=key)["Body"].read())

    docs: list[dict] = []
    with ThreadPoolExecutor(max_workers=32) as pool:
        for i, doc in enumerate(pool.map(fetch, keys), start=1):
            docs.append(doc)
            if i % 500 == 0 or i == len(keys):
                logger.info(f"  Loaded {i}/{len(keys)} embedded JSONs")
    return docs


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


def phase_2_document_nodes(client, graph_id: str, documents: list[dict], config: dict):
    logger.info("Phase 2: Creating document nodes...")

    from tools.ingestion.lib.wpam_year import extract_wpam_year_from_doc_id

    doc_type_to_label = config.get("doc_types", {})
    count = 0
    wpam_year_misses = 0

    for doc in documents:
        doc_type = doc.get("doc_type", "guide")
        label = doc_type_to_label.get(doc_type, "Guide")

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




def phase_4_hierarchy_links(client, graph_id: str, documents: list[dict]):
    logger.info("Phase 4: Sub-document links + universal hierarchy...")

    pairs = [
        {"parent_id": doc["_parent_id"], "child_id": doc["doc_id"]}
        for doc in documents
        if doc.get("_parent_id")
    ]
    logger.info(f"  {len(pairs)} HAS_SUBSECTION links")

    flush_cap = 400
    for start in range(0, len(pairs), flush_cap):
        chunk = pairs[start : start + flush_cap]
        execute_query(
            client,
            graph_id,
            "UNWIND $rows AS row "
            "MATCH (parent {id: row.parent_id}), (child {id: row.child_id}) "
            "MERGE (parent)-[:HAS_SUBSECTION]->(child)",
            {"rows": chunk},
        )

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
        "c.subheading = row.subheading, c.edition_year = row.edition_year",
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


def phase_7_stub_resolution(client, graph_id: str):
    """Wire DEFINED_BY edges from statute stubs to their matching statute chunks.

    Stubs like WIS-STAT-70.32 become routing nodes that point to the actual
    chunk(s) containing that section's text, matched by chunk heading prefix.
    """
    logger.info("Phase 7: Wiring DEFINED_BY edges from stubs to statute chunks...")

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
        parsed.append({
            "stub_id": stub_id,
            "doc_id": f"statutes-{chapter}",
            "section": match_section,
        })

    if not parsed:
        logger.info("  No section-level stubs to resolve")
        return

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

    logger.info(f"  Wired {edges_created} DEFINED_BY edges for {resolved_count}/{len(parsed)} stubs")


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


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


PHASE_9_LLM_WORKERS = 8
PHASE_9_ALLOWED_TYPES = {"RELATED_TO", "SUPPLEMENTS", "SUPERSEDES", "CONFLICTS_WITH"}


def _extract_json_array(text: str) -> list:
    """Extract a JSON array from LLM output that may be wrapped in prose or markdown fences."""
    # Strip markdown fences first
    stripped = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.MULTILINE)
    stripped = re.sub(r"```\s*$", "", stripped.strip(), flags=re.MULTILINE)
    # Find the first top-level JSON array
    m = re.search(r"\[.*\]", stripped, re.DOTALL)
    if not m:
        raise ValueError(f"no JSON array found in response (first 200 chars): {text[:200]!r}")
    return json.loads(m.group(0))


def _llm_classify_semantic_batch(
    batch: list[tuple[dict, dict, float]],
    llm_model: str,
) -> list[dict]:
    """Run one LLM batch and return list of edge specs.

    Each edge spec: {"a_id", "b_id", "type", "sim", "reason"}.
    Raises on failure — caller logs + moves on.

    Prompt design notes:
      - Title + type alone is too thin for CONFLICTS_WITH detection. We
        include the doc summary (first ~400 chars) so the LLM can see
        substantive positions, not just titles.
      - We define each edge type explicitly with positive AND negative
        examples; without this the LLM defaults to RELATED_TO for
        anything ambiguous, which is why CONFLICTS_WITH was 1/11k+.
      - "related": false is still a valid output — many cosine-similar
        pairs are coincidental (shared boilerplate, same chapter banner).
    """
    pairs_text = "\n".join(
        [
            (
                f"Pair {k + 1}:\n"
                f"  Doc A: '{a['title']}' (type={a['doc_type']})\n"
                f"    Summary: {(a.get('summary') or '')[:400]}\n"
                f"  Doc B: '{b['title']}' (type={b['doc_type']})\n"
                f"    Summary: {(b.get('summary') or '')[:400]}\n"
                f"  Cosine similarity: {sim:.3f}"
            )
            for k, (a, b, sim) in enumerate(batch)
        ]
    )
    prompt = (
        "Classify each pair of Wisconsin DOR property tax documents. "
        "Return one JSON array, no prose, no markdown.\n\n"
        'Each result: {"pair": N, "related": true|false, '
        '"type": "RELATED_TO"|"SUPPLEMENTS"|"SUPERSEDES"|"CONFLICTS_WITH", '
        '"reason": "<one sentence>"}\n\n'
        "EDGE TYPE DEFINITIONS\n\n"
        "RELATED_TO — same topic, mutually consistent, neither extends nor "
        "replaces the other. Default for compatible docs that just cover "
        "overlapping subject matter.\n"
        "  POSITIVE: WPAM section on residential valuation + Advisory on "
        "    new construction valuation in residential class.\n"
        "  NEGATIVE: Two unrelated FAQs that happen to share boilerplate.\n\n"
        "SUPPLEMENTS — Doc A provides additional detail or worked examples "
        "for a rule that Doc B states. A → B (A supplements B).\n"
        "  POSITIVE: Advisory worked example → WPAM section it illustrates.\n"
        "  NEGATIVE: Two equally-detailed treatments of the same rule.\n\n"
        "SUPERSEDES — Doc A is a newer version of Doc B and replaces it. "
        "A → B (A supersedes B). Look for explicit version/year markers "
        "(WPAM 2020 → WPAM 2017) or 'replaces' language.\n"
        "  POSITIVE: WPAM 2024 chapter on uniformity → WPAM 2020 same chapter.\n"
        "  NEGATIVE: Two contemporaneous Advisories from different quarters. "
        "    Different scope is NOT supersession.\n\n"
        "CONFLICTS_WITH — same topic, INCOMPATIBLE positions or guidance "
        "that cannot both be followed simultaneously. This is a serious "
        "label — the agent will surface this to users as a tension. Use "
        "ONLY when the docs make affirmative claims that contradict each "
        "other on substance, not when they just emphasize different things.\n"
        "  POSITIVE: Advisory A says 'agricultural classification requires "
        "    primary use'; Advisory B says 'any qualifying use suffices'.\n"
        "  POSITIVE: WPAM section says assessor must use cost approach for X; "
        "    admin rule allows market approach for the same X.\n"
        "  NEGATIVE: Older guidance later softened — that is SUPERSEDES.\n"
        "  NEGATIVE: Different topics that happen to use shared terminology.\n"
        "  NEGATIVE: Same position phrased differently.\n\n"
        "DECISION ORDER: SUPERSEDES > CONFLICTS_WITH > SUPPLEMENTS > RELATED_TO. "
        "If Doc A explicitly replaces Doc B, label SUPERSEDES even if their "
        "positions differ. CONFLICTS_WITH is only for unresolved contradiction "
        "between docs that are both presently in force.\n\n"
        "When in doubt, prefer 'related: false' over a wrong RELATED_TO.\n\n"
        f"Pairs:\n{pairs_text}"
    )
    response = bedrock.converse(
        modelId=llm_model,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": 4096, "temperature": 0.0},
    )
    result_text = response["output"]["message"]["content"][0]["text"]
    results = _extract_json_array(result_text)
    if not isinstance(results, list):
        results = [results]

    edges = []
    for item in results:
        pair_idx = item.get("pair", 0) - 1
        if not (0 <= pair_idx < len(batch)) or not item.get("related"):
            continue
        doc_a, doc_b, sim = batch[pair_idx]
        edge_type = item.get("type", "RELATED_TO")
        if edge_type not in PHASE_9_ALLOWED_TYPES:
            edge_type = "RELATED_TO"
        edges.append(
            {
                "a_id": doc_a["doc_id"],
                "b_id": doc_b["doc_id"],
                "type": edge_type,
                "sim": sim,
                "reason": item.get("reason", ""),
            }
        )
    return edges


def _flush_semantic_edges(client, graph_id: str, edges: list[dict]) -> int:
    """Write a chunk of semantic edges, grouped by edge type (one UNWIND per type)."""
    if not edges:
        return 0
    by_type: dict[str, list[dict]] = {}
    for e in edges:
        by_type.setdefault(e["type"], []).append(
            {
                "a_id": e["a_id"],
                "b_id": e["b_id"],
                "sim": e["sim"],
                "reason": e["reason"],
            }
        )
    written = 0
    for edge_type, rows in by_type.items():
        # Edge type is injected as a label (can't be parameterized in Cypher)
        # but rows are validated against PHASE_9_ALLOWED_TYPES above.
        execute_query(
            client,
            graph_id,
            "UNWIND $rows AS row "
            "MATCH (a {id: row.a_id}), (b {id: row.b_id}) "
            f"MERGE (a)-[r:{edge_type}]->(b) "
            "SET r.similarity = row.sim, r.reason = row.reason",
            {"rows": rows},
        )
        written += len(rows)
    return written


SEMANTIC_CACHE_KEY = "semantic_edges_cache.json"


def _embedding_fingerprint(embedding: list[float]) -> str:
    """Short hash of a doc embedding for cache invalidation."""
    sig = ",".join(f"{v:.6f}" for v in embedding[:16])
    return hashlib.sha256(sig.encode()).hexdigest()[:12]


def _pair_cache_key(doc_a_id: str, doc_b_id: str) -> str:
    """Deterministic cache key for a doc pair (order-independent)."""
    a, b = sorted([doc_a_id, doc_b_id])
    return f"{a}|{b}"


def _load_semantic_cache(work_bucket: str) -> dict:
    """Load semantic edge cache from S3. Returns empty dict on miss/error."""
    try:
        obj = s3.get_object(Bucket=work_bucket, Key=SEMANTIC_CACHE_KEY)
        cache = json.loads(obj["Body"].read())
        logger.info(f"  Loaded semantic cache from s3://{work_bucket}/{SEMANTIC_CACHE_KEY} ({len(cache)} entries)")
        return cache
    except s3.exceptions.NoSuchKey:
        logger.info("  No existing semantic cache found — starting fresh")
        return {}
    except Exception as e:
        logger.warning(f"  Failed to load semantic cache: {e} — proceeding without cache")
        return {}


def _save_semantic_cache(work_bucket: str, cache: dict):
    """Write updated semantic edge cache to S3."""
    try:
        body = json.dumps(cache, separators=(",", ":"))
        s3.put_object(Bucket=work_bucket, Key=SEMANTIC_CACHE_KEY, Body=body.encode())
        logger.info(f"  Saved semantic cache ({len(cache)} entries, {len(body) // 1024}KB) to s3://{work_bucket}/{SEMANTIC_CACHE_KEY}")
    except Exception as e:
        logger.warning(f"  Failed to save semantic cache: {e}")


def phase_9_semantic_edges(client, graph_id: str, documents: list[dict], config: dict, work_bucket: str = ""):
    """Discover and write semantic edges between documents.

    Pipeline: cosine similarity filtering → S3 cache check → LLM classification → Neptune writes.
    """
    logger.info("Phase 9: Discovering semantic edges...")

    similarity_threshold = config.get("semantic_similarity_threshold", 0.55)
    llm_batch_size = config.get("semantic_batch_size", 15)
    llm_model = config.get("bedrock_llm_model", "us.anthropic.claude-sonnet-4-20250514")

    # --- Stage 1: Find candidate pairs via cosine similarity ---
    docs_with_embeddings = [d for d in documents if d.get("doc_embedding")]
    logger.info(f"  {len(docs_with_embeddings)} documents with embeddings")

    candidate_pairs = []
    for i, doc_a in enumerate(docs_with_embeddings):
        for doc_b in docs_with_embeddings[i + 1 :]:
            similarity = cosine_similarity(doc_a["doc_embedding"], doc_b["doc_embedding"])
            if similarity >= similarity_threshold:
                candidate_pairs.append((doc_a, doc_b, similarity))

    logger.info(f"  {len(candidate_pairs)} candidate pairs above {similarity_threshold} threshold")

    # --- Stage 1.5: S3 cache — skip LLM for pairs whose embeddings haven't changed ---
    cache = _load_semantic_cache(work_bucket) if work_bucket else {}

    emb_fingerprints: dict[str, str] = {}
    for doc in docs_with_embeddings:
        emb_fingerprints[doc["doc_id"]] = _embedding_fingerprint(doc["doc_embedding"])

    cached_edges: list[dict] = []
    uncached_pairs: list[tuple] = []

    for doc_a, doc_b, sim in candidate_pairs:
        pk = _pair_cache_key(doc_a["doc_id"], doc_b["doc_id"])
        entry = cache.get(pk)
        if entry:
            sorted_ids = sorted([doc_a["doc_id"], doc_b["doc_id"]])
            fp_a = emb_fingerprints.get(sorted_ids[0], "")
            fp_b = emb_fingerprints.get(sorted_ids[1], "")
            if entry.get("fp_a") == fp_a and entry.get("fp_b") == fp_b:
                if entry.get("related"):
                    cached_edges.append({
                        "a_id": doc_a["doc_id"],
                        "b_id": doc_b["doc_id"],
                        "type": entry["type"],
                        "sim": sim,
                        "reason": entry.get("reason", ""),
                    })
                continue
        uncached_pairs.append((doc_a, doc_b, sim))

    logger.info(
        f"  Cache: {len(candidate_pairs) - len(uncached_pairs)} hits, "
        f"{len(uncached_pairs)} pairs need LLM classification"
    )

    # --- Stage 2: Classify uncached pairs via LLM in parallel batches ---
    llm_batches = [
        uncached_pairs[i : i + llm_batch_size]
        for i in range(0, len(uncached_pairs), llm_batch_size)
    ]
    logger.info(f"  {len(llm_batches)} LLM batches, {PHASE_9_LLM_WORKERS} parallel workers")

    total_edges_written = 0
    batches_completed = 0
    edge_write_buffer: list[dict] = []
    flush_threshold = 200
    edge_type_counts: dict[str, int] = dict.fromkeys(PHASE_9_ALLOWED_TYPES, 0)

    # Count and flush cached edges first
    for e in cached_edges:
        edge_type_counts[e["type"]] = edge_type_counts.get(e["type"], 0) + 1
    if cached_edges:
        written = _flush_semantic_edges(client, graph_id, cached_edges)
        total_edges_written += written
        logger.info(f"  Wrote {written} cached edges to graph")

    new_cache_entries: dict[str, dict] = {}

    with ThreadPoolExecutor(max_workers=PHASE_9_LLM_WORKERS) as pool:
        future_to_batch_index = {
            pool.submit(_llm_classify_semantic_batch, batch, llm_model): batch_index
            for batch_index, batch in enumerate(llm_batches)
        }
        for future in as_completed(future_to_batch_index):
            batch_index = future_to_batch_index[future]
            try:
                classified_edges = future.result()
                batch = llm_batches[batch_index]
                classified_pair_keys: set[str] = set()
                for edge in classified_edges:
                    edge_type_counts[edge["type"]] = edge_type_counts.get(edge["type"], 0) + 1
                    pk = _pair_cache_key(edge["a_id"], edge["b_id"])
                    sorted_ids = sorted([edge["a_id"], edge["b_id"]])
                    new_cache_entries[pk] = {
                        "fp_a": emb_fingerprints.get(sorted_ids[0], ""),
                        "fp_b": emb_fingerprints.get(sorted_ids[1], ""),
                        "related": True,
                        "type": edge["type"],
                        "reason": edge.get("reason", ""),
                    }
                    classified_pair_keys.add(pk)
                # Cache "not related" for pairs the LLM didn't flag
                for doc_a, doc_b, _sim in batch:
                    pk = _pair_cache_key(doc_a["doc_id"], doc_b["doc_id"])
                    if pk not in classified_pair_keys:
                        sorted_ids = sorted([doc_a["doc_id"], doc_b["doc_id"]])
                        new_cache_entries[pk] = {
                            "fp_a": emb_fingerprints.get(sorted_ids[0], ""),
                            "fp_b": emb_fingerprints.get(sorted_ids[1], ""),
                            "related": False,
                        }
                edge_write_buffer.extend(classified_edges)
            except Exception as exc:
                logger.warning(f"  Batch {batch_index} failed: {exc}")

            batches_completed += 1

            # --- Stage 3: Flush accumulated edges to Neptune periodically ---
            if len(edge_write_buffer) >= flush_threshold:
                written = _flush_semantic_edges(client, graph_id, edge_write_buffer)
                total_edges_written += written
                edge_write_buffer = []

            if batches_completed % 20 == 0 or batches_completed == len(llm_batches):
                logger.info(
                    f"  Phase 9 progress: {batches_completed}/{len(llm_batches)} batches, "
                    f"{total_edges_written} edges written, by type: "
                    + ", ".join(f"{t}={n}" for t, n in sorted(edge_type_counts.items()))
                )

    # Flush any remaining edges
    if edge_write_buffer:
        written = _flush_semantic_edges(client, graph_id, edge_write_buffer)
        total_edges_written += written

    # Save updated cache
    if work_bucket and (new_cache_entries or not cache):
        cache.update(new_cache_entries)
        _save_semantic_cache(work_bucket, cache)

    logger.info(
        f"  Created {total_edges_written} semantic edges; final by type: "
        + ", ".join(f"{t}={n}" for t, n in sorted(edge_type_counts.items()))
    )


def phase_10_cleanup(client, graph_id: str):
    """Garbage-collect orphan nodes left over from prior loads.

    Two specific classes:
      1. Orphan Statute STUBs — created on demand by phase 3 / phase 8 when
         a chunk's regex matches a section we never indexed full text for.
         If after the whole load the stub has zero incoming AND zero
         outgoing relationships, it's a regex hallucination (e.g., a
         partial number that looked like a statute ref). Safe to delete.
      2. Orphan Topic nodes — phase 5 sometimes creates canonical Topics
         from LLM cluster output that no document maps to (the LLM names a
         canonical that isn't a "member" of any cluster, so the doc→topic
         edge is never wired). Topics with zero COVERS_TOPIC are dead
         weight in the index.

    This phase is run-once-and-safe. MERGE-idempotent phases above will
    not re-create the orphans because the underlying conditions for stub
    creation (unmatched regex, LLM canonical drift) only fire during
    extract/embed. So running phase 12 doesn't fight the rest of the
    pipeline.

    We DELIBERATELY do not GC:
      - Stub Statutes that have ANY edge — they're real placeholders.
      - Topics with at least one COVERS_TOPIC — the agent uses these.
      - Stub AdminRules — much smaller volume; defer until we see the
        same pattern.
    """
    logger.info("Phase 10: Cleaning up orphan stubs and topics...")

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

    topic_orphans = execute_query(
        client,
        graph_id,
        "MATCH (t:Topic) "
        "WHERE NOT (t)<-[:COVERS_TOPIC]-() "
        "WITH t LIMIT 5000 "
        "DETACH DELETE t "
        "RETURN count(t) AS deleted",
    )
    deleted_topics = topic_orphans.get("results", [{}])[0].get("deleted", 0)
    logger.info(f"  Deleted {deleted_topics} orphan Topic nodes (no incoming COVERS_TOPIC)")


def main():
    parser = argparse.ArgumentParser(description="Load documents into Neptune Analytics graph")
    parser.add_argument("--work-bucket", required=True)
    parser.add_argument("--graph-id", required=True, help="Neptune Analytics graph identifier")
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
    args = parser.parse_args()

    config = load_config(args.config)
    client, graph_id = get_neptune_client(args.graph_id)
    documents = load_embedded_docs(args.work_bucket)
    logger.info(f"Loaded {len(documents)} documents for graph loading")

    if args.source_filter:
        before = len(documents)
        documents = [d for d in documents if d.get("doc_id", "").startswith(args.source_filter)]
        logger.info(f"Source filter '{args.source_filter}': {before} → {len(documents)} documents")

    phases = [
        (1, "Scaffold", lambda: phase_1_scaffold(client, graph_id, config)),
        (2, "Document Nodes", lambda: phase_2_document_nodes(client, graph_id, documents, config)),
        (3, "Statute Hierarchy", lambda: phase_3_statute_hierarchy(client, graph_id)),
        (4, "Hierarchy Links", lambda: phase_4_hierarchy_links(client, graph_id, documents)),
        (5, "Chunk Nodes", lambda: phase_5_chunk_nodes(client, graph_id, documents)),
        (6, "Case Law CITES", lambda: phase_6_case_law_cites(client, graph_id, documents)),
        (7, "Stub Resolution", lambda: phase_7_stub_resolution(client, graph_id)),
        (8, "Vector Upserts", lambda: phase_8_vector_upserts(client, graph_id, documents)),
        (9, "Semantic Edges", lambda: phase_9_semantic_edges(client, graph_id, documents, config, args.work_bucket)),
        (10, "Orphan Cleanup", lambda: phase_10_cleanup(client, graph_id)),
    ]

    for phase_num, name, fn in phases:
        if phase_num < args.start_phase:
            logger.info(f"Skipping Phase {phase_num}: {name}")
            continue
        logger.info(f"\n{'=' * 60}\nPhase {phase_num}: {name}\n{'=' * 60}")
        fn()
        if args.stop_after_phase is not None and phase_num >= args.stop_after_phase:
            logger.info(f"Stopping after Phase {phase_num} per --stop-after-phase.")
            return

    logger.info("\nGraph loading complete!")


if __name__ == "__main__":
    main()

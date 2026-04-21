"""
Phase 5: Load embedded documents into Neptune Analytics graph.

Implements 11 sequential sub-phases:
1. Scaffold (frameworks + hierarchy)
2. Document nodes
3. Cross-reference edges (CITES + IMPLEMENTS)
4. Statute hierarchy (PART_OF)
5. Topic merging via LLM
6. Sub-document links
7. Universal hierarchy post-pass
8. Chunk nodes
9. Stub resolution
10. Vector upserts
11. Semantic edges

Usage:
    python scripts/graphrag/load.py \
        --work-bucket <work-bucket> \
        --graph-id <neptune-graph-id> \
        --config scripts/graphrag/ingest_config.yaml
"""

import argparse
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
bedrock = boto3.client("bedrock-runtime", region_name=os.environ.get("AWS_REGION", "us-east-1"))


def load_config(config_path: str) -> dict:
    with open(config_path) as f:
        return yaml.safe_load(f)


def get_neptune_client(graph_id: str):
    return boto3.client("neptune-graph", region_name=os.environ.get("AWS_REGION", "us-east-1")), graph_id


def execute_query(client, graph_id: str, query: str, parameters: dict | None = None) -> dict:
    kwargs = {
        "graphIdentifier": graph_id,
        "language": "OPEN_CYPHER",
        "queryString": query,
    }
    if parameters:
        kwargs["parameters"] = parameters

    for attempt in range(8):
        try:
            return client.execute_query(**kwargs)
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
                wait = min(60, 2 ** attempt)
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
        execute_query(client, graph_id,
            "MERGE (f:Framework {id: $id}) SET f.title = $title, f.authority_level = $level",
            {"id": fw["id"], "title": fw["title"], "level": fw.get("authority_level", 99)},
        )

    for fw in frameworks:
        if "parent" in fw:
            execute_query(client, graph_id,
                "MATCH (child:Framework {id: $child_id}), (parent:Framework {id: $parent_id}) "
                "MERGE (child)-[:DERIVED_FROM]->(parent)",
                {"child_id": fw["id"], "parent_id": fw["parent"]},
            )

    for family in config.get("statute_families", []):
        execute_query(client, graph_id,
            "MERGE (s:Statute {id: $id}) SET s.title = $title",
            {"id": family["code"], "title": family["title"]},
        )
        execute_query(client, graph_id,
            "MATCH (s:Statute {id: $id}), (f:Framework {id: 'FW-STATUTES'}) "
            "MERGE (s)-[:BELONGS_TO]->(f)",
            {"id": family["code"]},
        )

    logger.info(f"  Created {len(frameworks)} frameworks, {len(config.get('statute_families', []))} statute families")


def phase_2_document_nodes(client, graph_id: str, documents: list[dict], config: dict):
    logger.info("Phase 2: Creating document nodes...")

    doc_type_to_label = config.get("doc_types", {})
    count = 0

    for doc in documents:
        doc_type = doc.get("doc_type", "guide")
        label = doc_type_to_label.get(doc_type, "Guide")

        execute_query(client, graph_id,
            f"MERGE (d:{label} {{id: $id}}) "
            f"SET d.title = $title, d.source_key = $source_key, "
            f"d.summary = $summary, d.source_url = $source_url, "
            f"d.doc_type = $doc_type, d.authority_level = $auth_level",
            {
                "id": doc["doc_id"],
                "title": doc.get("title", doc["doc_id"]),
                "source_key": doc.get("s3_key", ""),
                "summary": doc.get("summary", ""),
                "source_url": doc.get("source_url", ""),
                "doc_type": doc_type,
                "auth_level": doc.get("authority_level", 6),
            },
        )

        fw_id = doc.get("framework_id", "FW-GOV-PUBS")
        execute_query(client, graph_id,
            f"MATCH (d:{label} {{id: $doc_id}}), (f:Framework {{id: $fw_id}}) "
            "MERGE (d)-[:BELONGS_TO]->(f)",
            {"doc_id": doc["doc_id"], "fw_id": fw_id},
        )
        count += 1
        if count % 200 == 0:
            logger.info(f"  Phase 2 progress: {count}/{len(documents)} document nodes")

    logger.info(f"  Created {count} document nodes")


def phase_3_cross_references(client, graph_id: str, documents: list[dict]):
    logger.info("Phase 3: Creating cross-reference edges (CITES + IMPLEMENTS)...")

    edges_created = 0
    stubs_created = 0

    for doc in documents:
        doc_id = doc["doc_id"]

        for ref in doc.get("statute_refs", []):
            stub_id = f"WIS-STAT-{ref}"
            execute_query(client, graph_id,
                "MERGE (s:Statute {id: $id}) ON CREATE SET s.title = $title, s.stub = true",
                {"id": stub_id, "title": f"Wis. Stat. {ref}"},
            )
            stubs_created += 1
            execute_query(client, graph_id,
                "MATCH (d {id: $doc_id}), (s:Statute {id: $stub_id}) MERGE (d)-[:CITES]->(s)",
                {"doc_id": doc_id, "stub_id": stub_id},
            )
            edges_created += 1

        for ref in doc.get("implements_refs", []):
            stub_id = f"WIS-STAT-{ref}"
            execute_query(client, graph_id,
                "MERGE (s:Statute {id: $id}) ON CREATE SET s.title = $title, s.stub = true",
                {"id": stub_id, "title": f"Wis. Stat. {ref}"},
            )
            execute_query(client, graph_id,
                "MATCH (d {id: $doc_id}), (s:Statute {id: $stub_id}) MERGE (d)-[:IMPLEMENTS]->(s)",
                {"doc_id": doc_id, "stub_id": stub_id},
            )
            edges_created += 1

        for ref in doc.get("admin_rule_refs", []):
            stub_id = f"ADMIN-{ref.replace(' ', '-')}"
            execute_query(client, graph_id,
                "MERGE (r:AdminRule {id: $id}) ON CREATE SET r.title = $title, r.stub = true",
                {"id": stub_id, "title": ref},
            )
            stubs_created += 1
            execute_query(client, graph_id,
                "MATCH (d {id: $doc_id}), (r:AdminRule {id: $stub_id}) MERGE (d)-[:CITES]->(r)",
                {"doc_id": doc_id, "stub_id": stub_id},
            )
            edges_created += 1

    logger.info(f"  Created {edges_created} cross-reference edges, {stubs_created} stubs")


def phase_4_statute_hierarchy(client, graph_id: str):
    logger.info("Phase 4: Building statute hierarchy (PART_OF)...")

    result = execute_query(client, graph_id,
        "MATCH (s:Statute) RETURN s.id AS id, s.title AS title"
    )

    statutes = result.get("results", [])
    edges = 0

    for stat in statutes:
        stat_id = stat["id"]
        match = re.match(r"WIS-STAT-(\d+)", stat_id)
        if match:
            chapter_id = f"CH-{match.group(1)}"
            execute_query(client, graph_id,
                "MATCH (child:Statute {id: $child_id}), (parent:Statute {id: $parent_id}) "
                "MERGE (child)-[:PART_OF]->(parent)",
                {"child_id": stat_id, "parent_id": chapter_id},
            )
            edges += 1

        if "(" in stat_id:
            parent_id = stat_id.rsplit("(", 1)[0]
            execute_query(client, graph_id,
                "MERGE (p:Statute {id: $parent_id}) ON CREATE SET p.stub = true "
                "WITH p "
                "MATCH (child:Statute {id: $child_id}) "
                "MERGE (child)-[:PART_OF]->(p)",
                {"child_id": stat_id, "parent_id": parent_id},
            )
            edges += 1

    logger.info(f"  Created {edges} hierarchy edges")


def phase_5_topic_merging(client, graph_id: str, documents: list[dict], config: dict):
    logger.info("Phase 5: Merging topics via LLM...")

    all_topics = set()
    for doc in documents:
        for topic in doc.get("topics", []):
            all_topics.add(topic.strip().lower())

    all_topics = sorted(all_topics)
    logger.info(f"  {len(all_topics)} unique raw topics")

    if not all_topics:
        return

    batch_size = 200
    canonical_map = {}
    llm_model = config.get("bedrock_llm_model", "us.anthropic.claude-sonnet-4-20250514")

    for i in range(0, len(all_topics), batch_size):
        batch = all_topics[i : i + batch_size]
        prompt = (
            "Given these raw topic labels from Wisconsin DOR property tax documents, "
            "merge synonyms into canonical names. Return JSON only:\n"
            '{"clusters": [{"canonical": "...", "members": ["...", "..."]}]}\n\n'
            f"Topics: {json.dumps(batch)}"
        )

        response = bedrock.converse(
            modelId=llm_model,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 4096, "temperature": 0.0},
        )

        result_text = response["output"]["message"]["content"][0]["text"]
        result_text = re.sub(r"^```(?:json)?\n?", "", result_text.strip())
        result_text = re.sub(r"\n?```$", "", result_text.strip())

        try:
            clusters = json.loads(result_text).get("clusters", [])
            for cluster in clusters:
                canonical = cluster["canonical"]
                for member in cluster["members"]:
                    canonical_map[member.lower()] = canonical
        except json.JSONDecodeError:
            logger.warning(f"  Failed to parse LLM topic response for batch {i}")

    canonical_topics = set(canonical_map.values())
    for topic in canonical_topics:
        execute_query(client, graph_id,
            "MERGE (t:Topic {id: $id}) SET t.title = $title",
            {"id": topic.lower().replace(" ", "-"), "title": topic},
        )

    for doc in documents:
        for raw_topic in doc.get("topics", []):
            canonical = canonical_map.get(raw_topic.strip().lower(), raw_topic.strip())
            topic_id = canonical.lower().replace(" ", "-")
            execute_query(client, graph_id,
                "MATCH (d {id: $doc_id}), (t:Topic {id: $topic_id}) "
                "MERGE (d)-[:COVERS_TOPIC]->(t)",
                {"doc_id": doc["doc_id"], "topic_id": topic_id},
            )

    logger.info(f"  Created {len(canonical_topics)} canonical topics from {len(all_topics)} raw topics")


def phase_6_7_hierarchy(client, graph_id: str, documents: list[dict]):
    logger.info("Phase 6-7: Sub-document links + universal hierarchy...")

    for doc in documents:
        parent_id = doc.get("_parent_id")
        if parent_id:
            execute_query(client, graph_id,
                "MATCH (parent {id: $parent_id}), (child {id: $child_id}) "
                "MERGE (parent)-[:HAS_SUBSECTION]->(child)",
                {"parent_id": parent_id, "child_id": doc["doc_id"]},
            )

    execute_query(client, graph_id,
        "MATCH (s:Statute) WHERE s.stub = true AND NOT (s)-[:BELONGS_TO]->() "
        "MATCH (f:Framework {id: 'FW-STATUTES'}) "
        "MERGE (s)-[:BELONGS_TO]->(f)"
    )
    execute_query(client, graph_id,
        "MATCH (r:AdminRule) WHERE r.stub = true AND NOT (r)-[:BELONGS_TO]->() "
        "MATCH (f:Framework {id: 'FW-ADMIN-RULES'}) "
        "MERGE (r)-[:BELONGS_TO]->(f)"
    )

    logger.info("  Hierarchy links complete")


def phase_8_chunks(client, graph_id: str, documents: list[dict]):
    logger.info("Phase 8: Creating chunk nodes with headings + chunk-level CITES edges...")

    total_chunks = 0
    cite_edges = 0
    for doc in documents:
        doc_id = doc["doc_id"]
        s3_key = doc.get("s3_key", "")
        for i, chunk in enumerate(doc.get("chunks", [])):
            chunk_id = f"{doc_id}_chunk_{i:04d}"
            meta = chunk.get("metadata", {})
            start_page = meta.get("start_page")
            end_page = meta.get("end_page")

            execute_query(client, graph_id,
                "MERGE (c:Chunk {id: $id}) "
                "SET c.text = $text, c.doc_id = $doc_id, "
                "c.source_url = $source_url, c.chunk_index = $idx, "
                "c.s3_key = $s3_key, c.start_page = $start_page, "
                "c.end_page = $end_page, "
                "c.heading = $heading, c.subheading = $subheading",
                {
                    "id": chunk_id,
                    "text": chunk["text"],
                    "doc_id": doc_id,
                    "source_url": meta.get("source_url", ""),
                    "idx": i,
                    "s3_key": meta.get("source", s3_key),
                    "start_page": start_page,
                    "end_page": end_page,
                    "heading": meta.get("heading", ""),
                    "subheading": meta.get("subheading", ""),
                },
            )

            execute_query(client, graph_id,
                "MATCH (c:Chunk {id: $chunk_id}), (d {id: $doc_id}) "
                "MERGE (c)-[:EXTRACTED_FROM]->(d)",
                {"chunk_id": chunk_id, "doc_id": doc_id},
            )

            for ref in meta.get("statute_refs", []):
                stub_id = f"WIS-STAT-{ref}"
                execute_query(client, graph_id,
                    "MERGE (s:Statute {id: $id}) ON CREATE SET s.title = $title, s.stub = true",
                    {"id": stub_id, "title": f"Wis. Stat. {ref}"},
                )
                execute_query(client, graph_id,
                    "MATCH (c:Chunk {id: $chunk_id}), (s:Statute {id: $stub_id}) "
                    "MERGE (c)-[:CITES]->(s)",
                    {"chunk_id": chunk_id, "stub_id": stub_id},
                )
                cite_edges += 1

            for ref in meta.get("admin_rule_refs", []):
                stub_id = f"ADMIN-{ref.replace(' ', '-')}"
                execute_query(client, graph_id,
                    "MERGE (r:AdminRule {id: $id}) ON CREATE SET r.title = $title, r.stub = true",
                    {"id": stub_id, "title": ref},
                )
                execute_query(client, graph_id,
                    "MATCH (c:Chunk {id: $chunk_id}), (r:AdminRule {id: $stub_id}) "
                    "MERGE (c)-[:CITES]->(r)",
                    {"chunk_id": chunk_id, "stub_id": stub_id},
                )
                cite_edges += 1

            total_chunks += 1

    logger.info(f"  Created {total_chunks} chunk nodes, {cite_edges} chunk-level CITES edges")


def phase_9_stub_resolution(client, graph_id: str):
    logger.info("Phase 9: Resolving stub nodes...")

    result = execute_query(client, graph_id,
        "MATCH (s) WHERE s.stub = true RETURN s.id AS id, labels(s) AS labels"
    )

    stubs = result.get("results", [])
    resolved = 0
    for stub in stubs:
        stub_id = stub["id"]
        match = re.match(r"WIS-STAT-(\d+)", stub_id)
        if match:
            chapter = match.group(1)
            real_id = f"statutes-wi-statute-ch{chapter}"
            result = execute_query(client, graph_id,
                "MATCH (real {id: $real_id}) WHERE real.stub IS NULL RETURN real.id AS id",
                {"real_id": real_id},
            )
            if result.get("results"):
                execute_query(client, graph_id,
                    "MATCH (s {id: $stub_id})<-[r]-(citing) "
                    "MATCH (real {id: $real_id}) "
                    "FOREACH (x IN CASE WHEN real IS NOT NULL THEN [1] ELSE [] END | "
                    "  MERGE (citing)-[:CITES]->(real))",
                    {"stub_id": stub_id, "real_id": real_id},
                )
                resolved += 1

    logger.info(f"  Resolved {resolved}/{len(stubs)} stubs")


def phase_10_vectors(client, graph_id: str, documents: list[dict]):
    logger.info("Phase 10: Upserting chunk vectors...")

    total = 0
    for doc in documents:
        doc_id = doc["doc_id"]
        for i, chunk in enumerate(doc.get("chunks", [])):
            embedding = chunk.get("embedding")
            if not embedding:
                continue

            chunk_id = f"{doc_id}_chunk_{i:04d}"

            execute_query(client, graph_id,
                "MATCH (c:Chunk {id: $id}) "
                "CALL neptune.algo.vectors.upsert(c, $embedding)",
                {"id": chunk_id, "embedding": embedding},
            )
            total += 1

            if total % 500 == 0:
                logger.info(f"  Upserted {total} vectors...")

    logger.info(f"  Upserted {total} chunk vectors")


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def phase_11_semantic_edges(client, graph_id: str, documents: list[dict], config: dict):
    logger.info("Phase 11: Discovering semantic edges...")

    threshold = config.get("semantic_similarity_threshold", 0.55)
    batch_size = config.get("semantic_batch_size", 15)
    llm_model = config.get("bedrock_llm_model", "us.anthropic.claude-sonnet-4-20250514")

    docs_with_embeddings = [d for d in documents if d.get("doc_embedding")]
    logger.info(f"  {len(docs_with_embeddings)} documents with embeddings")

    candidates = []
    for i, doc_a in enumerate(docs_with_embeddings):
        for j, doc_b in enumerate(docs_with_embeddings):
            if j <= i:
                continue
            sim = cosine_similarity(doc_a["doc_embedding"], doc_b["doc_embedding"])
            if sim >= threshold:
                candidates.append((doc_a, doc_b, sim))

    logger.info(f"  {len(candidates)} candidate pairs above {threshold} threshold")

    edges_created = 0
    for batch_start in range(0, len(candidates), batch_size):
        batch = candidates[batch_start : batch_start + batch_size]

        pairs_text = "\n".join([
            f"Pair {k+1}: Doc A = '{a['title']}' (type={a['doc_type']}), "
            f"Doc B = '{b['title']}' (type={b['doc_type']}), similarity={sim:.3f}"
            for k, (a, b, sim) in enumerate(batch)
        ])

        prompt = (
            "For each pair of Wisconsin DOR documents below, determine if they are meaningfully related. "
            "For each pair, return: {\"pair\": N, \"related\": true/false, "
            "\"type\": \"RELATED_TO\"|\"SUPPLEMENTS\"|\"SUPERSEDES\"|\"CONFLICTS_WITH\", "
            "\"reason\": \"brief explanation\"}\n\n"
            "Return a JSON array. Consider the Wisconsin legal hierarchy: "
            "Constitution > Statutes > Admin Rules > WPAM > FAQs > Guides.\n\n"
            f"Pairs:\n{pairs_text}"
        )

        try:
            response = bedrock.converse(
                modelId=llm_model,
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                inferenceConfig={"maxTokens": 4096, "temperature": 0.0},
            )

            result_text = response["output"]["message"]["content"][0]["text"]
            result_text = re.sub(r"^```(?:json)?\n?", "", result_text.strip())
            result_text = re.sub(r"\n?```$", "", result_text.strip())
            results = json.loads(result_text)

            if not isinstance(results, list):
                results = [results]

            for item in results:
                pair_idx = item.get("pair", 0) - 1
                if 0 <= pair_idx < len(batch) and item.get("related"):
                    doc_a, doc_b, sim = batch[pair_idx]
                    edge_type = item.get("type", "RELATED_TO")
                    reason = item.get("reason", "")

                    execute_query(client, graph_id,
                        f"MATCH (a {{id: $a_id}}), (b {{id: $b_id}}) "
                        f"MERGE (a)-[r:{edge_type}]->(b) "
                        f"SET r.similarity = $sim, r.reason = $reason",
                        {"a_id": doc_a["doc_id"], "b_id": doc_b["doc_id"],
                         "sim": sim, "reason": reason},
                    )
                    edges_created += 1

        except Exception as e:
            logger.warning(f"  Semantic edge batch failed: {e}")

    logger.info(f"  Created {edges_created} semantic edges")


def main():
    parser = argparse.ArgumentParser(description="Load documents into Neptune Analytics graph")
    parser.add_argument("--work-bucket", required=True)
    parser.add_argument("--graph-id", required=True, help="Neptune Analytics graph identifier")
    parser.add_argument("--config", default="scripts/graphrag/ingest_config.yaml")
    parser.add_argument("--start-phase", type=int, default=1, help="Resume from specific phase")
    args = parser.parse_args()

    config = load_config(args.config)
    client, graph_id = get_neptune_client(args.graph_id)
    documents = load_embedded_docs(args.work_bucket)
    logger.info(f"Loaded {len(documents)} documents for graph loading")

    phases = [
        (1, "Scaffold", lambda: phase_1_scaffold(client, graph_id, config)),
        (2, "Document Nodes", lambda: phase_2_document_nodes(client, graph_id, documents, config)),
        (3, "Cross-References", lambda: phase_3_cross_references(client, graph_id, documents)),
        (4, "Statute Hierarchy", lambda: phase_4_statute_hierarchy(client, graph_id)),
        (5, "Topic Merging", lambda: phase_5_topic_merging(client, graph_id, documents, config)),
        (6, "Hierarchy Links", lambda: phase_6_7_hierarchy(client, graph_id, documents)),
        (7, "Chunk Nodes", lambda: phase_8_chunks(client, graph_id, documents)),
        (8, "Stub Resolution", lambda: phase_9_stub_resolution(client, graph_id)),
        (9, "Vector Upserts", lambda: phase_10_vectors(client, graph_id, documents)),
        (10, "Semantic Edges", lambda: phase_11_semantic_edges(client, graph_id, documents, config)),
    ]

    for phase_num, name, fn in phases:
        if phase_num < args.start_phase:
            logger.info(f"Skipping Phase {phase_num}: {name}")
            continue
        logger.info(f"\n{'='*60}\nPhase {phase_num}: {name}\n{'='*60}")
        fn()

    logger.info("\nGraph loading complete!")


if __name__ == "__main__":
    main()

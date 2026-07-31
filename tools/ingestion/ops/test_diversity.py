"""
Test harness: compare vector_search results with and without diversity caps.

Runs real queries against the live Neptune graph and Bedrock embeddings.
No deployment needed — uses your local AWS credentials.

Usage:
    export AWS_REGION=us-east-1 AWS_PROFILE=<your-profile>
    # If on macOS with Python 3.13+:
    export AWS_CA_BUNDLE=$(python3 -c "import certifi; print(certifi.where())")

    # Run from the repo root:
    python tools/ingestion/ops/test_diversity.py

    # Run a single query:
    python tools/ingestion/ops/test_diversity.py --query "how is agricultural land assessed?"

    # Adjust caps:
    python tools/ingestion/ops/test_diversity.py --max-per-doc 4 --top-k 12
"""

import argparse
import json
import os
import sys
from collections import Counter
from dataclasses import dataclass, field

import boto3

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "../../../backend/lambdas/agentic_retrieval")
)
from graph.neptune_client import NeptuneClient
from wpam_dedup import dedupe_wpam_chunks

GRAPH_ID = "g-ndvl4j73v4"
REGION = os.environ.get("AWS_REGION", "us-east-1")

# Queries from the client testing performance matrix that got thumbs-down
# related to diversity problems (one source crowding out others)
TEST_QUERIES = [
    # Broad queries where diversity matters
    "how is agricultural land assessed?",
    "Is zoning considered for agricultural classification?",
    "how do i appeal the classification of my land?",
    "how do i become certified as an assessor?",
    "what assessor exam study material is available?",
    # Statute-heavy queries where depth matters
    "If a subject property recently sold along with other neighboring properties, what sale must an assessor first consider when determining the assessed value of the subject property?",
    "what is a levy limit?",
    "Does my 501c3 property qualify for exemption?",
    "what market information is applicable when determining the assessed value of large, big box, commercial property?",
    # Queries that worked well (control group — make sure we don't regress)
    "are pipelines state or locally assessed?",
    "what is required in a maintenance assessment vs. a revaluation?",
    "How can the clerk correct an assessment roll error after Board of Review has adjourned for the year?",
]


@dataclass
class SearchResult:
    query: str
    chunks: list[dict]
    doc_distribution: Counter = field(default_factory=Counter)
    framework_distribution: Counter = field(default_factory=Counter)
    unique_docs: int = 0
    unique_frameworks: int = 0


def embed_query(query: str) -> list[float]:
    bedrock = boto3.client("bedrock-runtime", region_name=REGION)
    body = json.dumps(
        {
            "inputText": query[:8000],
            "dimensions": 1024,
            "normalize": True,
        }
    )
    response = bedrock.invoke_model(
        modelId="amazon.titan-embed-text-v2:0",
        body=body,
        contentType="application/json",
        accept="application/json",
    )
    return json.loads(response["body"].read())["embedding"]


def apply_diversity_cap(chunks: list[dict], max_per_doc: int, top_k: int) -> list[dict]:
    """Apply per-document chunk cap, preserving score order."""
    doc_counts: Counter = Counter()
    result = []
    for chunk in chunks:
        doc_id = chunk.get("doc_id", "unknown")
        if doc_counts[doc_id] >= max_per_doc:
            continue
        doc_counts[doc_id] += 1
        result.append(chunk)
        if len(result) >= top_k:
            break
    return result


def analyze_chunks(query: str, chunks: list[dict]) -> SearchResult:
    result = SearchResult(query=query, chunks=chunks)
    for chunk in chunks:
        doc_id = chunk.get("doc_id", "unknown")
        framework = chunk.get("framework_id", "unknown")
        result.doc_distribution[doc_id] += 1
        result.framework_distribution[framework] += 1
    result.unique_docs = len(result.doc_distribution)
    result.unique_frameworks = len(result.framework_distribution)
    return result


def print_comparison(query: str, baseline: SearchResult, capped: SearchResult, max_per_doc: int):
    print(f"\n{'=' * 80}")
    print(f"QUERY: {query}")
    print(f"{'=' * 80}")

    print(f"\n{'BASELINE (no cap)':<40} | {f'CAPPED (max {max_per_doc}/doc)':<40}")
    print(f"{'-' * 40}-+-{'-' * 40}")
    print(f"{'Chunks returned:':<40} | {'Chunks returned:':<40}")
    print(f"  {len(baseline.chunks):<38} |   {len(capped.chunks):<38}")
    print(f"{'Unique documents:':<40} | {'Unique documents:':<40}")
    print(f"  {baseline.unique_docs:<38} |   {capped.unique_docs:<38}")
    print(f"{'Unique frameworks:':<40} | {'Unique frameworks:':<40}")
    print(f"  {baseline.unique_frameworks:<38} |   {capped.unique_frameworks:<38}")

    print("\n--- Document distribution (baseline) ---")
    for doc_id, count in baseline.doc_distribution.most_common():
        marker = " <<<" if count > max_per_doc else ""
        print(f"  {count:>2}x  {doc_id[:60]}{marker}")

    print("\n--- Document distribution (capped) ---")
    for doc_id, count in capped.doc_distribution.most_common():
        print(f"  {count:>2}x  {doc_id[:60]}")

    print("\n--- Framework distribution ---")
    print(f"  {'Framework':<20} {'Baseline':>8} {'Capped':>8} {'Delta':>8}")
    all_frameworks = sorted(
        set(
            list(baseline.framework_distribution.keys())
            + list(capped.framework_distribution.keys())
        )
    )
    for fw in all_frameworks:
        b = baseline.framework_distribution.get(fw, 0)
        c = capped.framework_distribution.get(fw, 0)
        delta = c - b
        delta_str = f"+{delta}" if delta > 0 else str(delta)
        print(f"  {fw:<20} {b:>8} {c:>8} {delta_str:>8}")

    # Show what was GAINED (docs in capped but not in baseline)
    gained_docs = set(capped.doc_distribution.keys()) - set(baseline.doc_distribution.keys())
    if gained_docs:
        print("\n--- NEW docs surfaced by diversity cap ---")
        for doc_id in sorted(gained_docs):
            fw = next(
                (c.get("framework_id", "?") for c in capped.chunks if c.get("doc_id") == doc_id),
                "?",
            )
            print(f"  + {doc_id[:60]} ({fw})")

    # Show what was LOST (docs in baseline whose chunks were ALL removed)
    lost_docs = set(baseline.doc_distribution.keys()) - set(capped.doc_distribution.keys())
    if lost_docs:
        print("\n--- Docs LOST (all chunks removed) ---")
        for doc_id in sorted(lost_docs):
            count = baseline.doc_distribution[doc_id]
            print(f"  - {doc_id[:60]} (had {count} chunks)")

    # Auto-enrichment impact: which 3 docs would be enriched?
    def top_3_parents(chunks):
        seen = []
        for c in chunks:
            doc_id = c.get("doc_id", "")
            if doc_id and doc_id not in seen:
                seen.append(doc_id)
                if len(seen) >= 3:
                    break
        return seen

    baseline_parents = top_3_parents(baseline.chunks)
    capped_parents = top_3_parents(capped.chunks)
    print("\n--- Auto-enrichment targets (top 3 parent docs) ---")
    print(f"  Baseline: {baseline_parents}")
    print(f"  Capped:   {capped_parents}")
    if len(set(baseline_parents)) < 3 and len(set(capped_parents)) >= 3:
        print(
            f"  >>> Capped version enriches {len(set(capped_parents))} distinct docs vs {len(set(baseline_parents))} <<<"
        )


def run_query(neptune: NeptuneClient, query: str, top_k: int, max_per_doc: int):
    """Run a single query and compare baseline vs diversity-capped results."""
    print(f"\nEmbedding query: '{query[:60]}...'")
    embedding = embed_query(query)

    # Fetch more than needed (same as current code: top_k * 3)
    fetch_k = top_k * 3
    raw_chunks = neptune.vector_search(embedding, top_k=fetch_k)
    print(f"  Raw results from Neptune: {len(raw_chunks)} chunks")

    # Apply WPAM dedup (same as current code)
    current_year = neptune.current_wpam_year
    deduped = dedupe_wpam_chunks(raw_chunks, target_year=None, current_wpam_year=current_year)
    print(f"  After WPAM dedup: {len(deduped)} chunks (current_wpam_year={current_year})")

    # Baseline: truncate to top_k (current behavior)
    baseline_chunks = deduped[:top_k]

    # Capped: apply diversity then truncate
    capped_chunks = apply_diversity_cap(deduped, max_per_doc=max_per_doc, top_k=top_k)

    baseline = analyze_chunks(query, baseline_chunks)
    capped = analyze_chunks(query, capped_chunks)

    print_comparison(query, baseline, capped, max_per_doc)

    return baseline, capped


def print_summary(
    all_baselines: list[SearchResult], all_capped: list[SearchResult], max_per_doc: int
):
    print(f"\n\n{'#' * 80}")
    print(f"AGGREGATE SUMMARY ({len(all_baselines)} queries)")
    print(f"{'#' * 80}")

    avg_baseline_docs = sum(r.unique_docs for r in all_baselines) / len(all_baselines)
    avg_capped_docs = sum(r.unique_docs for r in all_capped) / len(all_capped)
    avg_baseline_fw = sum(r.unique_frameworks for r in all_baselines) / len(all_baselines)
    avg_capped_fw = sum(r.unique_frameworks for r in all_capped) / len(all_capped)

    print(f"\n  {'Metric':<35} {'Baseline':>10} {'Capped':>10} {'Delta':>10}")
    print(f"  {'-' * 35} {'-' * 10} {'-' * 10} {'-' * 10}")
    print(
        f"  {'Avg unique docs per query':<35} {avg_baseline_docs:>10.1f} {avg_capped_docs:>10.1f} {avg_capped_docs - avg_baseline_docs:>+10.1f}"
    )
    print(
        f"  {'Avg unique frameworks per query':<35} {avg_baseline_fw:>10.1f} {avg_capped_fw:>10.1f} {avg_capped_fw - avg_baseline_fw:>+10.1f}"
    )

    # Max concentration: worst-case chunks from a single doc
    baseline_max_conc = [max(r.doc_distribution.values()) for r in all_baselines]
    capped_max_conc = [max(r.doc_distribution.values()) for r in all_capped]
    avg_baseline_conc = sum(baseline_max_conc) / len(baseline_max_conc)
    avg_capped_conc = sum(capped_max_conc) / len(capped_max_conc)
    print(
        f"  {'Avg max chunks from one doc':<35} {avg_baseline_conc:>10.1f} {avg_capped_conc:>10.1f} {avg_capped_conc - avg_baseline_conc:>+10.1f}"
    )

    # Queries where the cap actually made a difference
    improved = sum(
        1 for b, c in zip(all_baselines, all_capped, strict=False) if c.unique_docs > b.unique_docs
    )
    same = sum(
        1 for b, c in zip(all_baselines, all_capped, strict=False) if c.unique_docs == b.unique_docs
    )
    worse = sum(
        1 for b, c in zip(all_baselines, all_capped, strict=False) if c.unique_docs < b.unique_docs
    )
    print(f"\n  Queries with MORE docs surfaced:  {improved}")
    print(f"  Queries with SAME doc count:      {same}")
    print(f"  Queries with FEWER docs surfaced: {worse}")

    print(f"\n  Config: max_per_doc={max_per_doc}")


def main():
    parser = argparse.ArgumentParser(
        description="Test vector search diversity caps against live Neptune graph"
    )
    parser.add_argument(
        "--query", type=str, help="Run a single query instead of the full test suite"
    )
    parser.add_argument(
        "--max-per-doc", type=int, default=3, help="Max chunks per document (default: 3)"
    )
    parser.add_argument(
        "--top-k", type=int, default=10, help="Final number of chunks to return (default: 10)"
    )
    parser.add_argument("--graph-id", type=str, default=GRAPH_ID, help="Neptune graph ID")
    args = parser.parse_args()

    neptune = NeptuneClient(graph_id=args.graph_id, region=REGION)
    print(f"Connected to Neptune graph: {args.graph_id} in {REGION}")
    print(f"Config: max_per_doc={args.max_per_doc}, top_k={args.top_k}")
    print(f"WPAM current year: {neptune.current_wpam_year}")

    queries = [args.query] if args.query else TEST_QUERIES

    all_baselines = []
    all_capped = []

    for query in queries:
        baseline, capped = run_query(neptune, query, top_k=args.top_k, max_per_doc=args.max_per_doc)
        all_baselines.append(baseline)
        all_capped.append(capped)

    if len(queries) > 1:
        print_summary(all_baselines, all_capped, args.max_per_doc)


if __name__ == "__main__":
    main()

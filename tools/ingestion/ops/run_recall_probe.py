"""Raw vector-recall probe — where does the expected chunk/doc rank in topK?

Consumes tools/ingestion/tests/recall_eval_queries.yaml (built by
build_recall_eval.py) and, for every case, embeds the query with Titan Embed
Text v2 (1024-d, normalized) and runs neptune.algo.vectors.topKByEmbedding
against a Neptune Analytics graph. No agent loop, no FAQ KB, no re-ranking —
this isolates the effect of a change to *chunk embeddings* (e.g. document
expansion: prepending generated questions / synonyms before embedding).

Per case it records:
  doc_rank        1-based rank of the first hit whose doc_id is expected (None if
                  not in top-k)
  chunk_rank      same, for expected_chunk_ids (when the case has them)
  subsection_rank first rank of an expected-doc hit whose heading / subheading /
                  text mentions expected_subsection (when the case has one)

Summary: doc-level recall@10 / recall@30 / recall@k, MRR, and the number of
cases whose expected doc is absent from the top-k; chunk-level and subsection
variants over the subset of cases that carry those labels.

Two-run workflow (mirrors run_graph_regression.py):

    AWS_PROFILE=<your-profile> AWS_REGION=us-east-1 \\
      uv run python tools/ingestion/ops/run_recall_probe.py --mode baseline \\
        --graph-id g-xxxxxxxx --label "prod pre-expansion"

    # ... re-embed / load the graph with expanded chunks ...
    ... run_recall_probe.py --mode after --graph-id g-yyyyyyyy --label "expansion v1"

    ... run_recall_probe.py --compare-only

Results are written next to this script as recall_probe_<mode>.json
(gitignored, like graph_regression_*.json). Query embeddings are cached inside
the JSON so an `after` run against another graph never re-embeds.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3
import yaml

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
EMBED_MODEL_ID = "amazon.titan-embed-text-v2:0"
EMBED_DIMS = 1024

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
QUERIES_YAML = os.path.join(_REPO_ROOT, "tools", "ingestion", "tests", "recall_eval_queries.yaml")
RESULTS_DIR = os.path.dirname(os.path.abspath(__file__))
BASELINE_PATH = os.path.join(RESULTS_DIR, "recall_probe_baseline.json")
AFTER_PATH = os.path.join(RESULTS_DIR, "recall_probe_after.json")
MODE_PATHS = {"baseline": BASELINE_PATH, "after": AFTER_PATH}


# --------------------------------------------------------------------------- #
# Inputs
# --------------------------------------------------------------------------- #


def load_cases(path: str, only: set[str] | None, include_unlabeled: bool) -> list[dict]:
    with open(path) as f:
        cases = yaml.safe_load(f)["cases"]
    if only:
        cases = [c for c in cases if c["id"] in only]
    if not include_unlabeled:
        cases = [c for c in cases if c.get("expected_doc_ids")]
    return cases


def query_key(query: str) -> str:
    return hashlib.sha1(query.strip().encode()).hexdigest()


def load_embedding_cache() -> dict[str, list[float]]:
    cache: dict[str, list[float]] = {}
    for path in (BASELINE_PATH, AFTER_PATH):
        if not os.path.exists(path):
            continue
        try:
            with open(path) as f:
                cache.update(json.load(f).get("embeddings") or {})
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("could not read embedding cache from %s: %s", path, exc)
    return cache


# --------------------------------------------------------------------------- #
# Bedrock + Neptune
# --------------------------------------------------------------------------- #


class Prober:
    def __init__(self, graph_id: str, top_k: int, cache: dict[str, list[float]]):
        self.graph_id = graph_id
        self.top_k = top_k
        self.cache = cache
        self.bedrock = boto3.client("bedrock-runtime", region_name=AWS_REGION)
        self.neptune = boto3.client("neptune-graph", region_name=AWS_REGION)

    def embed(self, query: str) -> list[float]:
        key = query_key(query)
        if key in self.cache:
            return self.cache[key]
        body = json.dumps({"inputText": query, "dimensions": EMBED_DIMS, "normalize": True})
        resp = self.bedrock.invoke_model(modelId=EMBED_MODEL_ID, body=body)
        vec = json.loads(resp["body"].read())["embedding"]
        self.cache[key] = vec
        return vec

    def _cypher(self, query: str) -> list[dict]:
        resp = self.neptune.execute_query(
            graphIdentifier=self.graph_id, queryString=query, language="OPEN_CYPHER"
        )
        return json.loads(resp["payload"].read())["results"]

    def top_k_hits(self, vec: list[float], with_text: bool) -> list[dict]:
        lit = "[" + ",".join(repr(v) for v in vec) + "]"
        text_col = ", node.text AS text" if with_text else ""
        cy = (
            f"CALL neptune.algo.vectors.topKByEmbedding({lit}, {{topK: {self.top_k}}}) "
            "YIELD node, score "
            "RETURN node.id AS chunk_id, node.doc_id AS doc_id, node.heading AS heading, "
            f"node.subheading AS subheading, score AS score{text_col}"
        )
        return self._cypher(cy)


def subsection_regex(subsection: str) -> re.Pattern:
    """'70.11(49)' -> matches '70.11 (49)', '70.11(49)'; '70.10' -> '70.10' not '70.100'."""
    parts = re.split(r"(\(|\))", subsection)
    pat = r"\s*".join(re.escape(p) for p in parts if p)
    return re.compile(pat + r"(?!\d)")


def score_case(case: dict, hits: list[dict]) -> dict:
    expected_docs = set(case.get("expected_doc_ids") or [])
    expected_chunks = set(case.get("expected_chunk_ids") or [])
    subsection = case.get("expected_subsection")
    sub_re = subsection_regex(subsection) if subsection else None

    doc_rank = chunk_rank = subsection_rank = None
    doc_hit = None
    compact_hits = []
    for rank, h in enumerate(hits, 1):
        did = h.get("doc_id") or ""
        cid = h.get("chunk_id") or ""
        compact_hits.append(
            {
                "rank": rank,
                "chunk_id": cid,
                "doc_id": did,
                "heading": (h.get("heading") or "")[:80],
                # topKByEmbedding score is a DISTANCE (lower = closer); rank order is
                # what we measure, the raw value is kept only for eyeballing.
                "score": round(float(h.get("score") or 0.0), 4),
            }
        )
        if did in expected_docs:
            if doc_rank is None:
                doc_rank = rank
                doc_hit = compact_hits[-1]
            if sub_re is not None and subsection_rank is None:
                hay = " ".join(str(h.get(k) or "") for k in ("heading", "subheading", "text"))
                if sub_re.search(hay):
                    subsection_rank = rank
        if cid in expected_chunks and chunk_rank is None:
            chunk_rank = rank

    return {
        "id": case["id"],
        "tier": case.get("tier"),
        "query": case["query"],
        "expected_doc_ids": sorted(expected_docs),
        "expected_chunk_ids": sorted(expected_chunks),
        "expected_subsection": subsection,
        "doc_rank": doc_rank,
        "doc_hit": doc_hit,
        "chunk_rank": chunk_rank,
        "subsection_rank": subsection_rank,
        "subsection_hit_at_doc_hit": (
            None
            if sub_re is None or doc_rank is None
            else subsection_rank is not None and subsection_rank == doc_rank
        ),
        "top_doc_ids": [h["doc_id"] for h in compact_hits[:10]],
        "hits": compact_hits,
    }


def run_case(prober: Prober, case: dict) -> dict:
    t0 = time.time()
    vec = prober.embed(case["query"])
    hits = prober.top_k_hits(vec, with_text=bool(case.get("expected_subsection")))
    result = score_case(case, hits)
    result["latency_ms"] = int((time.time() - t0) * 1000)
    return result


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #


def _recall_at(ranks: list[int | None], k: int) -> float:
    if not ranks:
        return 0.0
    return sum(1 for r in ranks if r is not None and r <= k) / len(ranks)


def _mrr(ranks: list[int | None]) -> float:
    if not ranks:
        return 0.0
    return sum(1.0 / r for r in ranks if r is not None) / len(ranks)


def summarize(results: list[dict], top_k: int) -> dict:
    def block(rs: list[dict], field: str) -> dict:
        ranks = [r[field] for r in rs]
        return {
            "n": len(ranks),
            "recall@10": round(_recall_at(ranks, 10), 4),
            "recall@30": round(_recall_at(ranks, 30), 4),
            f"recall@{top_k}": round(_recall_at(ranks, top_k), 4),
            "mrr": round(_mrr(ranks), 4),
            f"not_in_top_{top_k}": sum(1 for r in ranks if r is None),
        }

    labeled = [r for r in results if r["expected_doc_ids"]]
    summary = {
        "doc": block(labeled, "doc_rank"),
        "doc_by_tier": {
            tier: block([r for r in labeled if r["tier"] == tier], "doc_rank")
            for tier in sorted({r["tier"] for r in labeled})
        },
        "chunk": block([r for r in labeled if r["expected_chunk_ids"]], "chunk_rank"),
        "subsection": block([r for r in labeled if r["expected_subsection"]], "subsection_rank"),
    }
    return summary


def print_summary(summary: dict, title: str) -> None:
    print(f"\n== {title} ==")
    for name, blk in (
        ("doc", summary["doc"]),
        ("chunk", summary["chunk"]),
        ("subsection", summary["subsection"]),
    ):
        parts = ", ".join(f"{k}={v}" for k, v in blk.items())
        print(f"  {name:<10} {parts}")
    for tier, blk in summary["doc_by_tier"].items():
        parts = ", ".join(f"{k}={v}" for k, v in blk.items())
        print(f"  doc/{tier:<13} {parts}")


def _rank_str(r: int | None) -> str:
    return "-" if r is None else str(r)


def print_worst(results: list[dict], n: int = 10) -> None:
    labeled = [r for r in results if r["expected_doc_ids"]]
    worst = sorted(
        labeled, key=lambda r: (r["doc_rank"] is not None, r["doc_rank"] or 0), reverse=False
    )
    # None first (worst), then highest rank first
    worst = [r for r in worst if r["doc_rank"] is None] + sorted(
        [r for r in worst if r["doc_rank"] is not None], key=lambda r: -r["doc_rank"]
    )
    print(f"\n== {n} worst cases (doc-level) ==")
    print(f"  {'id':<8} {'tier':<13} {'doc':>4} {'chunk':>5} {'sub':>4}  query / top hit")
    for r in worst[:n]:
        top = r["hits"][0] if r["hits"] else {}
        print(
            f"  {r['id']:<8} {r['tier']:<13} {_rank_str(r['doc_rank']):>4} "
            f"{_rank_str(r['chunk_rank']):>5} {_rank_str(r['subsection_rank']):>4}  "
            f"{r['query'][:70]}"
        )
        print(
            f"  {'':<8} {'':<13} {'':>4} {'':>5} {'':>4}  expected={r['expected_doc_ids'][:3]} "
            f"top1={top.get('doc_id', '')} ({top.get('score', '')})"
        )


# --------------------------------------------------------------------------- #
# Compare
# --------------------------------------------------------------------------- #


def _load_run(path: str) -> dict:
    if not os.path.exists(path):
        sys.exit(f"missing {path} — run with --mode first")
    with open(path) as f:
        return json.load(f)


def compare(baseline: dict, after: dict) -> None:
    b_res = {r["id"]: r for r in baseline["results"]}
    a_res = {r["id"]: r for r in after["results"]}
    ids = [i for i in b_res if i in a_res and b_res[i]["expected_doc_ids"]]
    print(
        f"baseline: {baseline.get('label') or '-'} (graph {baseline.get('graph_id')}, "
        f"{baseline.get('timestamp')})"
    )
    print(
        f"after:    {after.get('label') or '-'} (graph {after.get('graph_id')}, "
        f"{after.get('timestamp')})"
    )
    print_summary(baseline["summary"], "baseline")
    print_summary(after["summary"], "after")

    rows = []
    for i in ids:
        b, a = b_res[i], a_res[i]
        rows.append(
            {
                "id": i,
                "tier": b["tier"],
                "b_doc": b["doc_rank"],
                "a_doc": a["doc_rank"],
                "b_chunk": b["chunk_rank"],
                "a_chunk": a["chunk_rank"],
                "b_sub": b["subsection_rank"],
                "a_sub": a["subsection_rank"],
                "query": b["query"],
            }
        )

    def in_top(r: int | None, k: int) -> bool:
        return r is not None and r <= k

    top_k = after.get("top_k", 60)
    newly_top10 = [r for r in rows if in_top(r["a_doc"], 10) and not in_top(r["b_doc"], 10)]
    dropped_top10 = [r for r in rows if in_top(r["b_doc"], 10) and not in_top(r["a_doc"], 10)]
    newly_found = [r for r in rows if r["a_doc"] is not None and r["b_doc"] is None]
    newly_lost = [r for r in rows if r["b_doc"] is not None and r["a_doc"] is None]

    def delta(r: dict) -> int:
        # positive = improved; missing counts as rank top_k + 1
        b = r["b_doc"] if r["b_doc"] is not None else top_k + 1
        a = r["a_doc"] if r["a_doc"] is not None else top_k + 1
        return b - a

    print(f"\n== per-case doc-rank deltas ({len(rows)} cases; +N = improved by N ranks) ==")
    print(
        f"  {'id':<8} {'tier':<13} {'base':>4} {'after':>5} {'delta':>6} "
        f"{'chunk':>9} {'sub':>9}  query"
    )
    for r in sorted(rows, key=delta, reverse=True):
        d = delta(r)
        flag = ""
        if r in newly_top10:
            flag = " NEW-TOP10"
        elif r in dropped_top10:
            flag = " DROPPED-TOP10"
        if r in newly_lost:
            flag += " LOST"
        elif r in newly_found:
            flag += " FOUND"
        print(
            f"  {r['id']:<8} {r['tier']:<13} {_rank_str(r['b_doc']):>4} {_rank_str(r['a_doc']):>5} "
            f"{d:>+6} {_rank_str(r['b_chunk']) + '->' + _rank_str(r['a_chunk']):>9} "
            f"{_rank_str(r['b_sub']) + '->' + _rank_str(r['a_sub']):>9}  {r['query'][:60]}{flag}"
        )

    improved = sum(1 for r in rows if delta(r) > 0)
    regressed = sum(1 for r in rows if delta(r) < 0)
    print(
        f"\nimproved={improved} regressed={regressed} "
        f"unchanged={len(rows) - improved - regressed} | "
        f"newly-in-top-10={len(newly_top10)} dropped-from-top-10={len(dropped_top10)} | "
        f"newly-found={len(newly_found)} newly-lost={len(newly_lost)}"
    )


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #


def main() -> None:
    parser = argparse.ArgumentParser(description="Raw vector-recall probe")
    parser.add_argument("--mode", choices=("baseline", "after"))
    parser.add_argument("--compare-only", action="store_true")
    parser.add_argument("--graph-id", default=os.environ.get("NEPTUNE_GRAPH_ID"))
    parser.add_argument("--label", default="")
    parser.add_argument("--only", default="", help="comma-separated case ids")
    parser.add_argument("--queries", default=QUERIES_YAML)
    parser.add_argument("--top-k", type=int, default=60)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument(
        "--include-unlabeled",
        action="store_true",
        help="also run cases with empty expected_doc_ids (excluded from metrics).",
    )
    parser.add_argument("--worst", type=int, default=10)
    args = parser.parse_args()

    if args.compare_only:
        compare(_load_run(BASELINE_PATH), _load_run(AFTER_PATH))
        return
    if not args.mode:
        parser.error("--mode baseline|after (or --compare-only) is required")
    if not args.graph_id:
        parser.error("--graph-id (or NEPTUNE_GRAPH_ID) is required")

    only = {s.strip() for s in args.only.split(",") if s.strip()} or None
    cases = load_cases(args.queries, only, args.include_unlabeled)
    logger.info("probing %d cases against %s (top_k=%d)", len(cases), args.graph_id, args.top_k)

    prober = Prober(args.graph_id, args.top_k, load_embedding_cache())
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(run_case, prober, c): c["id"] for c in cases}
        for fut in as_completed(futures):
            cid = futures[fut]
            try:
                r = fut.result()
            except Exception as exc:  # noqa: BLE001 - report and keep going
                logger.error("%s failed: %s", cid, exc)
                continue
            results.append(r)
            logger.info(
                "%s doc=%s chunk=%s sub=%s (%dms)",
                cid,
                _rank_str(r["doc_rank"]),
                _rank_str(r["chunk_rank"]),
                _rank_str(r["subsection_rank"]),
                r["latency_ms"],
            )
    results.sort(key=lambda r: r["id"])
    summary = summarize(results, args.top_k)

    out_path = MODE_PATHS[args.mode]
    previous = {}
    if only and os.path.exists(out_path):
        # Partial re-run: merge into the existing file so --compare-only stays whole.
        previous = {r["id"]: r for r in _load_run(out_path).get("results", [])}
        previous.update({r["id"]: r for r in results})
        results = sorted(previous.values(), key=lambda r: r["id"])
        summary = summarize(results, args.top_k)
    payload = {
        "mode": args.mode,
        "label": args.label,
        "graph_id": args.graph_id,
        "top_k": args.top_k,
        "embed_model": EMBED_MODEL_ID,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "summary": summary,
        "results": results,
        "embeddings": prober.cache,
    }
    with open(out_path, "w") as f:
        json.dump(payload, f, indent=1)
    logger.info("wrote %s", out_path)

    print_summary(summary, f"{args.mode} {args.label}".strip())
    print_worst(results, args.worst)


if __name__ == "__main__":
    main()

"""Measure case-law citation stability across repeated runs (§7 variance floor).

Runs each Stratum-B (case-law two-hop) golden query N times and records, per run:
  - case_ids_cited: the `case-law-*` ids in cited_doc_ids
  - named_not_cited: citations that appear in the ANSWER PROSE but whose
    resolved CaseLaw node is NOT in cited_doc_ids (the broken-card failure class)

Emits, per query:
  - the set-union and per-run breakdown of cited case ids (shows variance)
  - how many of N runs had a named-but-not-cited case (the deterministic-fix signal)

This distinguishes two different problems the Phase-9-removal regression run
surfaced: (a) cases reaching context but inconsistently CITED (selection/prose
adherence — fixable deterministically), vs (b) cases not reaching context at
all (discovery recall — a cap-tuning question). Run BEFORE deciding which.

Usage:
    AWS_PROFILE=<your-profile> AWS_REGION=us-east-1 AWS_DEFAULT_REGION=us-east-1 \\
      NEPTUNE_GRAPH_ID=g-ndvl4j73v4 FAQ_KNOWLEDGE_BASE_ID=Y7SQRR3LHO \\
      RAW_BUCKET=wis-raw-bucket-c8e69250 AGENTIC_MODEL_ID=us.anthropic.claude-sonnet-4-6 \\
      MODEL_CONFIG_TABLE_NAME=... FAQ_URL_TABLE_NAME=... LOG_LEVEL=WARNING \\
      uv run python tools/ingestion/ops/measure_case_law_stability.py --runs 3
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# Reuse the harness's import wiring + query runner.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from run_graph_regression import (  # noqa: E402
    _REPO_ROOT,
    _cited_case_ids,
    load_queries,
    run_one_query,
)

OUT_PATH = os.path.join(
    _REPO_ROOT, "tools", "ingestion", "ops", "case_law_stability.json"
)


def _named_not_cited(answer: str, cited_doc_ids: list[str]) -> list[dict]:
    """Citations named in prose whose resolved CaseLaw node is NOT cited.

    Returns [{citation, resolved_id}] for each such gap. Resolves via the
    same Neptune path the retrieval layer uses.
    """
    from agent_tools.executor import extract_citations

    from config import neptune

    citations = extract_citations(answer or "")
    if not citations:
        return []
    resolved = neptune.resolve_case_citations(citations)
    cited = set(cited_doc_ids)
    gaps = []
    for case in resolved:
        cid = case.get("id")
        if cid and cid not in cited:
            gaps.append({"citation": case.get("citation", ""), "resolved_id": cid})
    return gaps


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure Stratum-B case-law citation stability")
    parser.add_argument("--runs", type=int, default=3, help="Runs per query (default 3)")
    parser.add_argument(
        "--stratum", default="B", help="Which stratum to measure (default B — case law)"
    )
    args = parser.parse_args()

    entries = [q for q in load_queries() if q.get("stratum") == args.stratum]
    logger.info(f"Measuring {len(entries)} Stratum-{args.stratum} queries × {args.runs} runs")

    report: dict[str, dict] = {}
    for entry in entries:
        qid = entry["queryId"]
        logger.info(f"\n=== {qid} — {entry['query']} ===")
        runs_data = []
        for i in range(1, args.runs + 1):
            run = run_one_query(entry)
            case_ids = _cited_case_ids(run["cited_doc_ids"])
            gaps = _named_not_cited(run["answer"], run["cited_doc_ids"])
            runs_data.append(
                {"run": i, "case_ids_cited": case_ids, "named_not_cited": gaps}
            )
            logger.info(
                f"  run {i}: cited_cases={case_ids} "
                f"named_not_cited={[g['resolved_id'] for g in gaps]} "
                f"({run['latency_ms']}ms)"
            )

        # Aggregate variance signals.
        all_sets = [frozenset(r["case_ids_cited"]) for r in runs_data]
        union = sorted(set().union(*all_sets)) if all_sets else []
        intersection = sorted(set.intersection(*[set(s) for s in all_sets])) if all_sets else []
        unstable = sorted(set(union) - set(intersection))  # cited in some runs, not all
        runs_with_gap = sum(1 for r in runs_data if r["named_not_cited"])

        report[qid] = {
            "query": entry["query"],
            "runs": runs_data,
            "union_case_ids": union,
            "always_cited": intersection,
            "unstable_case_ids": unstable,
            "runs_with_named_not_cited": runs_with_gap,
            "total_runs": args.runs,
        }
        logger.info(
            f"  SUMMARY: always_cited={intersection} unstable={unstable} "
            f"named_not_cited in {runs_with_gap}/{args.runs} runs"
        )

    with open(OUT_PATH, "w") as f:
        json.dump(report, f, indent=2)
    logger.info(f"\nWrote {OUT_PATH}")

    # Verdict banner.
    total_unstable = sum(len(r["unstable_case_ids"]) for r in report.values())
    total_gaps = sum(r["runs_with_named_not_cited"] for r in report.values())
    logger.info("\n=== VERDICT ===")
    logger.info(f"  unstable case citations (cited in some runs, not all): {total_unstable}")
    logger.info(f"  runs exhibiting named-in-prose-but-not-cited: {total_gaps}")
    if total_gaps:
        logger.info(
            "  → selection/prose-adherence gap present: a deterministic prose-"
            "backstop (auto-union prose citations into cited_doc_ids) would fix it."
        )
    if total_unstable and not total_gaps:
        logger.info(
            "  → variance is in WHICH cases get cited, but all named cases ARE "
            "carded — likely benign substitution, not a broken-card bug."
        )


if __name__ == "__main__":
    main()

"""Graph-regression harness — question-accuracy guardrail.

Runs the golden set in tools/ingestion/tests/graph_regression_queries.yaml
through the real agentic retrieval path (Phase A research loop + a
non-streaming Phase B answer generation), then grades each answer on:

  1. Cited-doc overlap — must_cite doc_ids present in cited_doc_ids (primary gate).
  2. Key-fact presence — must_contain regexes matched in the answer text.
  3. No hallucinated case citations — every cited `case-law-*` id exists in the graph.

Originally built to guard the removal of the LLM semantic-edge load phase, but
reusable as a general graph-regression gate.

Two-run workflow:

    # 1. BEFORE any graph mutation — capture the baseline against production:
    AWS_PROFILE=<your-profile> AWS_REGION=us-east-1 \\
      python tools/ingestion/ops/run_graph_regression.py --mode baseline

    # 2. AFTER deleting semantic edges + deploying:
    AWS_PROFILE=<your-profile> AWS_REGION=us-east-1 \\
      python tools/ingestion/ops/run_graph_regression.py --mode after

    # 3. Compare the two runs:
    python tools/ingestion/ops/run_graph_regression.py --compare-only

Requires the agentic_retrieval Lambda env (Neptune graph id, FAQ KB id) — run
it with the same AWS profile/region and env vars the Lambda uses. The relevant
env vars are FAQ_KNOWLEDGE_BASE_ID, RAW_BUCKET, AGENTIC_MODEL_ID, and the
Neptune graph id (NeptuneClient reads NEPTUNE_GRAPH_ID / GRAPH_ID).

The LLM-judge equivalence pass (§7 grading step 4) is intentionally NOT run
here — it needs both baseline+after answers side by side. Use --compare-only
after both runs; it reports cited-doc drift and fact-presence deltas per query,
which is what the pass criterion keys on. Add a judge pass on top of the
comparison report if a Stratum-C delta needs adjudication.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time

import yaml

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# --- Make the agentic_retrieval Lambda package importable, exactly as its
#     own tests/conftest.py does (Lambda root + backend/layers on sys.path). ---
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
_LAMBDA_ROOT = os.path.join(_REPO_ROOT, "backend", "lambdas", "agentic_retrieval")
_LAYERS_ROOT = os.path.join(_REPO_ROOT, "backend", "layers")
for _p in (_LAMBDA_ROOT, _LAYERS_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

QUERIES_YAML = os.path.join(
    _REPO_ROOT, "tools", "ingestion", "tests", "graph_regression_queries.yaml"
)
RESULTS_DIR = os.path.dirname(os.path.abspath(__file__))
BASELINE_PATH = os.path.join(RESULTS_DIR, "graph_regression_baseline.json")
AFTER_PATH = os.path.join(RESULTS_DIR, "graph_regression_after.json")


def load_queries() -> list[dict]:
    with open(QUERIES_YAML) as f:
        data = yaml.safe_load(f)
    return data.get("queries", [])


def run_one_query(entry: dict) -> dict:
    """Run a single query through Phase A + a non-streaming Phase B answer gen."""
    # Imported lazily so --compare-only works without AWS/Neptune configured.
    from loop.phase_a import run_agentic_loop
    from loop.phase_b import apply_persona, build_answer_context
    from prompt import ANSWER_STREAM_SYSTEM_PROMPT

    from config import AGENTIC_MODEL_ID, bedrock, neptune

    query = entry["query"]
    started = time.perf_counter()

    # Phase A: research loop. ws_server=None → no WebSocket emission.
    result = run_agentic_loop(query, chat_history=[], query_id=entry.get("queryId", ""))

    cited_doc_ids = list(result.cited_doc_ids)

    # Phase B: build context and generate the answer text non-streaming.
    answer_text = result.fallback_answer or ""
    if not answer_text:
        answer_context = build_answer_context(
            query=query,
            cited_chunks=result.all_chunks,
            cited_doc_ids=set(cited_doc_ids),
            discovery=result.discovery,
            fetched_opinions=result.fetched_opinions,
            answer_plan=result.answer_plan,
            chat_history=[],
            neptune_client=neptune,
        )
        try:
            resp = bedrock.converse(
                modelId=AGENTIC_MODEL_ID,
                messages=[{"role": "user", "content": [{"text": answer_context}]}],
                system=[{"text": apply_persona(ANSWER_STREAM_SYSTEM_PROMPT, None)}],
                inferenceConfig={"maxTokens": 4096, "temperature": 0.0},
            )
            answer_text = resp["output"]["message"]["content"][0].get("text", "")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"  answer generation failed for {entry.get('queryId')}: {exc}")
            answer_text = ""

    # Per-cited-doc discovery attribution: which retrieval path surfaced each
    # cited doc. This is what makes the baseline↔after comparison exact —
    # "auto-enrichment" is the path Direction 1 Option A removes, so a cited
    # doc tagged that way at baseline is a citation *at risk*; "graph-neighbor"
    # (explicit get_neighbors) is preserved. Also record the full discovery
    # count breakdown (all discovered docs, not just cited) so the ~96→~15-25
    # discovery-volume drop is captured directly in the snapshot.
    cited_discovery = {doc_id: result.discovery.get(doc_id, "unknown") for doc_id in cited_doc_ids}
    discovery_counts: dict[str, int] = {}
    for tag in result.discovery.values():
        discovery_counts[tag] = discovery_counts.get(tag, 0) + 1

    return {
        "queryId": entry.get("queryId", ""),
        "stratum": entry.get("stratum", ""),
        "query": query,
        "cited_doc_ids": cited_doc_ids,
        "cited_discovery": cited_discovery,
        "discovery_counts": discovery_counts,
        "discovered_doc_count": len(result.discovery),
        "answer": answer_text,
        "turns": len(result.trace_log),
        "latency_ms": round((time.perf_counter() - started) * 1000),
    }


def _cited_case_ids(cited_doc_ids: list[str]) -> list[str]:
    return [d for d in cited_doc_ids if d.startswith("case-law-")]


def verify_case_ids_exist(case_ids: list[str]) -> dict[str, bool]:
    """Return {case_id: exists_in_graph} for hallucination detection."""
    if not case_ids:
        return {}
    from config import neptune

    existence: dict[str, bool] = {}
    for cid in case_ids:
        try:
            doc = neptune.get_document(cid)
            existence[cid] = bool(doc)
        except Exception:  # noqa: BLE001
            existence[cid] = False
    return existence


def grade(entry: dict, run: dict, case_existence: dict[str, bool]) -> dict:
    """Grade one run against its golden-set expectations."""
    cited = set(run["cited_doc_ids"])
    answer = run["answer"] or ""

    must_cite = entry.get("must_cite", []) or []
    cite_hits = {doc_id: (doc_id in cited) for doc_id in must_cite}
    cite_pass = all(cite_hits.values())

    must_contain = entry.get("must_contain", []) or []
    fact_hits = {
        pat: bool(re.search(pat, answer, re.IGNORECASE)) for pat in must_contain
    }
    fact_pass = all(fact_hits.values())

    cited_cases = _cited_case_ids(run["cited_doc_ids"])
    hallucinated = [c for c in cited_cases if not case_existence.get(c, True)]

    return {
        "queryId": run["queryId"],
        "stratum": run["stratum"],
        "cite_hits": cite_hits,
        "cite_pass": cite_pass,
        "fact_hits": fact_hits,
        "fact_pass": fact_pass,
        "cited_case_ids": cited_cases,
        "hallucinated_case_ids": hallucinated,
        "no_hallucination": not hallucinated,
    }


def _checkpoint(out_path: str, mode: str, runs: list[dict], grades: list[dict]) -> None:
    """Atomically persist progress so a killed run is resumable/not lost.

    Writes to a temp file then renames — a crash mid-write can't corrupt the
    checkpoint. Called after every query, not just at the end.
    """
    tmp = out_path + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"mode": mode, "runs": runs, "grades": grades}, f, indent=2)
    os.replace(tmp, out_path)


def run_mode(mode: str, resume: bool = True) -> None:
    entries = load_queries()
    out_path = BASELINE_PATH if mode == "baseline" else AFTER_PATH

    # Resume: reload any queries already completed in a prior (partial) run so a
    # killed run picks up where it left off instead of re-billing every query.
    runs: list[dict] = []
    grades: list[dict] = []
    done_ids: set[str] = set()
    if resume and os.path.exists(out_path):
        try:
            with open(out_path) as f:
                prior = json.load(f)
            runs = prior.get("runs", [])
            grades = prior.get("grades", [])
            done_ids = {r["queryId"] for r in runs}
            if done_ids:
                logger.info(
                    f"Resuming {out_path}: {len(done_ids)} queries already done — "
                    f"{sorted(done_ids)}"
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Could not read prior {out_path} for resume: {exc}")

    todo = [e for e in entries if e.get("queryId") not in done_ids]
    logger.info(
        f"Running {len(todo)}/{len(entries)} golden-set queries (mode={mode}, "
        f"{len(done_ids)} skipped as done)..."
    )

    for i, entry in enumerate(todo, start=1):
        logger.info(f"  [{i}/{len(todo)}] {entry.get('queryId')} — {entry['query'][:70]}")
        run = run_one_query(entry)
        case_existence = verify_case_ids_exist(_cited_case_ids(run["cited_doc_ids"]))
        run["case_existence"] = case_existence
        g = grade(entry, run, case_existence)
        runs.append(run)
        grades.append(g)
        logger.info(
            f"      cited={len(run['cited_doc_ids'])} "
            f"cite_pass={g['cite_pass']} fact_pass={g['fact_pass']} "
            f"halluc={len(g['hallucinated_case_ids'])} ({run['latency_ms']}ms)"
        )
        # Checkpoint after every query — mid-run kill/crash loses at most one query.
        _checkpoint(out_path, mode, runs, grades)

    logger.info(f"\nWrote {out_path} ({len(runs)} queries total)")
    _print_grade_summary(mode, entries, grades)


def _print_grade_summary(mode: str, entries: list[dict], grades: list[dict]) -> None:
    logger.info(f"\n=== GRADE SUMMARY ({mode}) ===")
    hard_fail = 0
    for g in grades:
        stratum = g["stratum"]
        # Strata B/D/E must retain must_cite + must_contain; A must retain facts;
        # C is judged by comparison. Hallucinations fail any stratum.
        problems = []
        if stratum in ("B", "D", "E") and not g["cite_pass"]:
            missing = [d for d, ok in g["cite_hits"].items() if not ok]
            problems.append(f"missing must_cite {missing}")
        if stratum in ("A", "B", "D", "E") and not g["fact_pass"]:
            missing = [p for p, ok in g["fact_hits"].items() if not ok]
            problems.append(f"missing facts {missing}")
        if not g["no_hallucination"]:
            problems.append(f"HALLUCINATED cases {g['hallucinated_case_ids']}")
        status = "OK" if not problems else "FAIL"
        if problems:
            hard_fail += 1
        logger.info(f"  [{stratum}] {g['queryId']}: {status} {'; '.join(problems)}")
    logger.info(
        f"\n{len(grades) - hard_fail}/{len(grades)} passed intra-run gates "
        f"({hard_fail} flagged). Run --compare-only after both baseline+after "
        f"for cited-doc drift."
    )


def compare() -> None:
    if not (os.path.exists(BASELINE_PATH) and os.path.exists(AFTER_PATH)):
        logger.error(
            f"Need both {BASELINE_PATH} and {AFTER_PATH}. "
            "Run --mode baseline then --mode after first."
        )
        sys.exit(1)

    with open(BASELINE_PATH) as f:
        baseline = json.load(f)
    with open(AFTER_PATH) as f:
        after = json.load(f)

    base_runs = {r["queryId"]: r for r in baseline["runs"]}
    after_runs = {r["queryId"]: r for r in after["runs"]}
    base_grades = {g["queryId"]: g for g in baseline["grades"]}
    after_grades = {g["queryId"]: g for g in after["grades"]}

    logger.info("\n=== BASELINE vs AFTER COMPARISON ===")
    regressions = 0
    turn_increases = 0
    base_turns_total = 0
    after_turns_total = 0
    for qid in base_runs:
        b, a = base_runs[qid], after_runs.get(qid)
        if a is None:
            logger.warning(f"  {qid}: present in baseline, missing in after")
            continue
        bg, ag = base_grades[qid], after_grades[qid]
        stratum = b.get("stratum", "")

        base_cited = set(b["cited_doc_ids"])
        after_cited = set(a["cited_doc_ids"])
        dropped = sorted(base_cited - after_cited)
        added = sorted(after_cited - base_cited)

        # A regression = a fact that was present at baseline but lost after,
        # OR a must_cite doc dropped for a control stratum (B/D/E),
        # OR a newly hallucinated case.
        lost_facts = [
            p for p, ok in ag["fact_hits"].items() if bg["fact_hits"].get(p) and not ok
        ]
        lost_cites = []
        if stratum in ("B", "D", "E"):
            lost_cites = [
                d for d, ok in ag["cite_hits"].items() if bg["cite_hits"].get(d) and not ok
            ]
        new_halluc = ag["hallucinated_case_ids"]

        is_regression = bool(lost_facts or lost_cites or new_halluc)
        if is_regression:
            regressions += 1

        marker = "  ⚠️ REGRESSION" if is_regression else ""
        logger.info(f"\n  [{stratum}] {qid}{marker}")
        logger.info(f"      cited: baseline={len(base_cited)} after={len(after_cited)}")
        # Discovery-volume drop (Option A's headline effect): total docs
        # discovered should fall sharply as enrichment fan-out stops.
        base_disc = b.get("discovered_doc_count")
        after_disc = a.get("discovered_doc_count")
        if base_disc is not None and after_disc is not None:
            logger.info(f"      discovered: baseline={base_disc} after={after_disc}")
        # Loop-effort guardrail: extra tool-loop turns/discovery is not a
        # correctness regression, but a systematic increase means the change
        # made the agent search harder. Flag as a soft warning, not a fail.
        base_turns = b.get("turns")
        after_turns = a.get("turns")
        if base_turns is not None and after_turns is not None:
            base_turns_total += base_turns
            after_turns_total += after_turns
            turn_note = ""
            if after_turns > base_turns:
                turn_increases += 1
                turn_note = f"  ⚠️ +{after_turns - base_turns} turn(s)"
            logger.info(f"      turns: baseline={base_turns} after={after_turns}{turn_note}")
        if dropped:
            # Annotate each dropped citation with the baseline path that found
            # it. A drop tagged "auto-enrichment" is expected under Option A; a
            # drop tagged "vector-search"/"graph-neighbor"/etc. is a real loss.
            base_cd = b.get("cited_discovery", {})
            annotated = [f"{d} (was: {base_cd.get(d, 'unknown')})" for d in dropped]
            logger.info(f"      dropped: {annotated}")
        if added:
            logger.info(f"      added:   {added}")
        if lost_facts:
            logger.info(f"      LOST FACTS: {lost_facts}")
        if lost_cites:
            logger.info(f"      LOST must_cite: {lost_cites}")
        if new_halluc:
            logger.info(f"      NEW HALLUCINATED cases: {new_halluc}")

    # Loop-effort summary (soft signal, does not gate the exit code).
    if base_turns_total or after_turns_total:
        delta = after_turns_total - base_turns_total
        sign = f"+{delta}" if delta > 0 else str(delta)
        logger.info(
            f"\n=== turns: baseline={base_turns_total} after={after_turns_total} "
            f"(Δ {sign}); {turn_increases} quer(y/ies) took more turns ==="
        )
        if turn_increases:
            logger.info(
                "  ⚠️ Loop-effort increased on the queries marked above. Not a "
                "correctness regression, but review whether the change made the "
                "agent search harder than baseline."
            )

    logger.info(f"\n=== {regressions} regression(s) detected ===")
    if regressions:
        logger.info(
            "Per the pass criterion: dropped supplementary news-page docs on "
            "Stratum A are acceptable; lost facts / lost control-stratum cites / "
            "new hallucinations are NOT. Investigate each ⚠️ above."
        )
        sys.exit(1)
    logger.info("No regressions — safe per §7 grading (add LLM-judge for Stratum C if needed).")


def _run_single(mode: str, query_id: str) -> None:
    """Run exactly one query by id, print its result, and do NOT write output.

    For debugging a slow/failing query with full tracing on — leaves the
    checkpoint file untouched.
    """
    entries = load_queries()
    match = next((e for e in entries if e.get("queryId") == query_id), None)
    if match is None:
        logger.error(f"queryId {query_id!r} not found in golden set")
        sys.exit(1)
    logger.info(f"Running single query {query_id} — {match['query']}")
    run = run_one_query(match)
    case_existence = verify_case_ids_exist(_cited_case_ids(run["cited_doc_ids"]))
    g = grade(match, run, case_existence)
    logger.info(
        f"  cited={run['cited_doc_ids']}\n"
        f"  cite_pass={g['cite_pass']} fact_pass={g['fact_pass']} "
        f"halluc={g['hallucinated_case_ids']} turns={run['turns']} "
        f"({run['latency_ms']}ms)"
    )


def regrade(mode: str) -> None:
    """Re-grade a saved run against the current golden-set YAML — no re-run.

    Use after fixing a must_cite/must_contain expectation in the YAML so the
    stored grades match the (unchanged) captured answers without re-billing
    the retrieval path. Case-hallucination existence flags are preserved from
    the original run (already recorded per query).
    """
    out_path = BASELINE_PATH if mode == "baseline" else AFTER_PATH
    if not os.path.exists(out_path):
        logger.error(f"No saved run at {out_path} to re-grade.")
        sys.exit(1)
    with open(out_path) as f:
        data = json.load(f)
    entries = {e["queryId"]: e for e in load_queries()}

    new_grades = []
    for run in data["runs"]:
        entry = entries.get(run["queryId"])
        if entry is None:
            logger.warning(f"  {run['queryId']} no longer in golden set — keeping as-is")
            continue
        case_existence = run.get("case_existence", {})
        g = grade(entry, run, case_existence)
        new_grades.append(g)
        logger.info(
            f"  {run['queryId']} [{g['stratum']}]: "
            f"cite_pass={g['cite_pass']} fact_pass={g['fact_pass']} "
            f"halluc={len(g['hallucinated_case_ids'])}"
        )
    data["grades"] = new_grades
    _checkpoint(out_path, data["mode"], data["runs"], new_grades)
    logger.info(f"\nRe-graded {len(new_grades)} queries; rewrote {out_path}")
    _print_grade_summary(mode, list(entries.values()), new_grades)


def main() -> None:
    parser = argparse.ArgumentParser(description="Graph-regression accuracy harness")
    parser.add_argument(
        "--mode",
        choices=["baseline", "after"],
        help="Run the golden set and write baseline.json or after.json",
    )
    parser.add_argument(
        "--compare-only",
        action="store_true",
        help="Skip running; just compare existing baseline.json vs after.json",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Ignore any existing partial output and re-run every query from scratch",
    )
    parser.add_argument(
        "--only",
        default="",
        help="Run just this queryId (for debugging a single query)",
    )
    parser.add_argument(
        "--regrade",
        action="store_true",
        help="Re-grade the saved run for --mode against the current YAML (no re-run)",
    )
    args = parser.parse_args()

    if args.compare_only:
        compare()
        return
    if args.regrade:
        if not args.mode:
            parser.error("--regrade requires --mode {baseline,after}")
        regrade(args.mode)
        return
    if not args.mode:
        parser.error("provide --mode {baseline,after} or --compare-only")
    if args.only:
        _run_single(args.mode, args.only)
        return
    run_mode(args.mode, resume=not args.no_resume)


if __name__ == "__main__":
    main()

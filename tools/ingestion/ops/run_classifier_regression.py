"""Classifier-regression harness — pre-loop query-classifier guardrail.

SIBLING to run_graph_regression.py. That harness calls run_agentic_loop()
DIRECTLY and therefore never invokes the pre-loop classifier — so it cannot
observe OUT_OF_SCOPE / DISAMBIGUATE / TOPIC_SHIFT verdicts. This harness gates
the classifier decision itself, which is where the EOW P0 fixes (out-of-scope
message rewrite + targeted in-scope topic additions) live.

It reuses classify_query() from the agentic_retrieval Lambda package unchanged,
so it exercises the EXACT production code path:

  * Model:        the model id in disambiguation.CLASSIFIER_MODEL_ID. That is
                  os.environ["DISAMBIGUATION_MODEL_ID"] if set, else the bundled
                  default "us.anthropic.claude-haiku-4-5-20251001-v1:0". The
                  deployed Lambda does NOT set DISAMBIGUATION_MODEL_ID (verified
                  against the AgenticRetrieval function config), so it uses the
                  same default. This harness likewise does NOT set it → parity.
  * Inference:    temperature=0.0, maxTokens=16, hardcoded inside classify_query
                  → identical to prod (not overridable).
  * Prompt:       the LIVE disambiguationClassifier prompt loaded by prompt.py
                  from the ModelConfig DynamoDB table (id "disambiguationClassifier"),
                  NOT the _prompt_fallback bundled copy — provided
                  MODEL_CONFIG_TABLE_NAME is set (this module sets it to the
                  known production table below unless already exported). If the
                  DynamoDB read fails, prompt.py silently falls back to the
                  bundled copy; watch stderr for the "Using bundled fallback"
                  warning — a baseline captured on the fallback is NOT prod-faithful.

ENV / PLUMBING (documented per the task):
  * AWS creds     — needs an AWS profile/region with dynamodb:GetItem on the
                    ModelConfig table + bedrock:InvokeModel for Haiku. Run with
                    the same profile you deploy with, e.g.:
                        AWS_PROFILE=widor AWS_REGION=us-east-1 \
                          python tools/ingestion/ops/run_classifier_regression.py --mode baseline
  * MODEL_CONFIG_TABLE_NAME — set automatically to the prod table if unset; export
                    your own to point at a different stack.
  * DISAMBIGUATION_MODEL_ID — deliberately NOT set, to match prod. Do not export it.
  * ENABLE_* flags — irrelevant here: classify_query takes allow_topic_shift as an
                    argument (per-case in the YAML), it does not read the env flag.

NON-DETERMINISM CAVEAT: temp=0.0 is near-deterministic but not guaranteed bit-
identical run to run. For a guard sitting near a decision boundary (topic-shift
especially, and any newly-flipped verdict), re-run 3-5 times before trusting a
baseline↔after flip. Cases tagged "advisory" do NOT gate the exit code.

Two-run workflow:
    # 1. BEFORE pushing the revised prompt — capture the pre-fix baseline:
    AWS_PROFILE=<profile> AWS_REGION=us-east-1 \
      python tools/ingestion/ops/run_classifier_regression.py --mode baseline
    # 2. AFTER `upload_model_configs.py --only disambiguationClassifier`:
    AWS_PROFILE=<profile> AWS_REGION=us-east-1 \
      python tools/ingestion/ops/run_classifier_regression.py --mode after
    # 3. Diff verdicts per case:
    python tools/ingestion/ops/run_classifier_regression.py --compare-only

PRE-DEPLOY CANDIDATE TESTING (--candidate-prompt / --local-prompt):
    To prove a prompt edit BEFORE it is uploaded to DynamoDB or deployed, pass
    --candidate-prompt. It loads the disambiguationClassifier prompt from the local
    config/model_configs.toml working copy and monkeypatches the module-level
    constant classify_query() reads (disambiguation._CLASSIFIER_PROMPT). The model
    id, temperature (0), and maxTokens (16) are untouched — only the system prompt
    is swapped for the local candidate. The rewritten OUT_OF_SCOPE_MESSAGE is a code
    constant in disambiguation.py, so it is already picked up on import — no override
    needed. Export BOTH AWS_REGION and AWS_DEFAULT_REGION (see below).
        AWS_PROFILE=<profile> AWS_REGION=us-east-1 AWS_DEFAULT_REGION=us-east-1 \
          python tools/ingestion/ops/run_classifier_regression.py \
            --mode after --candidate-prompt

EXIT CODE:
  * --mode baseline : nonzero if any NON-advisory `passing` case does NOT match
                      its expected_verdict (a regression / broken anchor). Cases
                      marked expected_baseline: failing are EXPECTED to mismatch
                      at baseline (reported as RED-expected, not a failure).
  * --mode after    : nonzero if ANY non-advisory case does not match its
                      expected_verdict (post-fix, everything should be green).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time

import yaml

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# --- Make the agentic_retrieval Lambda package importable, exactly as
#     run_graph_regression.py does (Lambda root + backend/layers on sys.path). ---
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
_LAMBDA_ROOT = os.path.join(_REPO_ROOT, "backend", "lambdas", "agentic_retrieval")
_LAYERS_ROOT = os.path.join(_REPO_ROOT, "backend", "layers")
for _p in (_LAMBDA_ROOT, _LAYERS_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Ensure the LIVE prompt is sourced from the ModelConfig DynamoDB table (same as
# the Lambda). Set BEFORE importing disambiguation/prompt (prompt.py reads this at
# import time). Export your own to target a different stack. This is the prod
# GraphRAG ModelConfig table (us-east-1).
_PROD_MODEL_CONFIG_TABLE = (
    "WisconsinBotGraphRAG-WisconsinGraphRAGStackNestedStackWisconsinGraphRAGStackNestedStac"
    "-1O31X0VAIZPQR-ModelConfigTable2CFCAEFE-MWBL475NBYTE"
)
os.environ.setdefault("MODEL_CONFIG_TABLE_NAME", _PROD_MODEL_CONFIG_TABLE)
# prompt.py creates boto3.client("dynamodb") with NO explicit region, relying on
# the environment. botocore resolves the default client region from
# AWS_DEFAULT_REGION (AWS_REGION alone is NOT honored for this on the installed
# botocore), so set BOTH — otherwise the DynamoDB read raises "You must specify a
# region", prompt.py silently falls back to the BUNDLED copy, and the baseline is
# NOT prod-faithful. Prefer whatever the caller already exported.
_region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
os.environ["AWS_REGION"] = _region
os.environ["AWS_DEFAULT_REGION"] = _region
# Deliberately DO NOT set DISAMBIGUATION_MODEL_ID — prod leaves it unset so the
# bundled default is used; setting it here would break model parity.

QUERIES_YAML = os.path.join(
    _REPO_ROOT, "tools", "ingestion", "tests", "classifier_regression_queries.yaml"
)
# Local working-copy source of truth for prompts (pre-deploy). --candidate-prompt
# loads the disambiguationClassifier entry from here and injects it into
# classify_query, bypassing the DynamoDB copy.
MODEL_CONFIGS_TOML = os.path.join(_REPO_ROOT, "config", "model_configs.toml")
RESULTS_DIR = os.path.dirname(os.path.abspath(__file__))
BASELINE_PATH = os.path.join(RESULTS_DIR, "classifier_regression_baseline.json")
AFTER_PATH = os.path.join(RESULTS_DIR, "classifier_regression_after.json")

_VALID_VERDICTS = {"PROCEED", "DISAMBIGUATE", "OUT_OF_SCOPE", "TOPIC_SHIFT"}


def load_cases() -> list[dict]:
    with open(QUERIES_YAML) as f:
        data = yaml.safe_load(f)
    cases = data.get("cases", [])
    ids = [c.get("id") for c in cases]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ValueError(f"Duplicate case ids in {QUERIES_YAML}: {sorted(dupes)}")
    for c in cases:
        ev = c.get("expected_verdict")
        if ev not in _VALID_VERDICTS:
            raise ValueError(f"Case {c.get('id')}: invalid expected_verdict {ev!r}")
    return cases


def _verdict_message(verdict: str) -> str | None:
    """Map a verdict to the canned user-facing message, exactly as handler.py
    does. Imported from disambiguation.py so the message assertion tracks the
    live code constant (updated when the OOS-message rewrite ships)."""
    from disambiguation import (
        CLARIFICATION_QUESTION,
        OUT_OF_SCOPE_MESSAGE,
        TOPIC_SHIFT_SUGGESTION,
        VERDICT_DISAMBIGUATE,
        VERDICT_OUT_OF_SCOPE,
        VERDICT_TOPIC_SHIFT,
    )

    return {
        VERDICT_DISAMBIGUATE: CLARIFICATION_QUESTION,
        VERDICT_OUT_OF_SCOPE: OUT_OF_SCOPE_MESSAGE,
        VERDICT_TOPIC_SHIFT: TOPIC_SHIFT_SUGGESTION,
    }.get(verdict)


def apply_candidate_prompt() -> None:
    """Override the disambiguationClassifier prompt with the LOCAL working-copy
    version from config/model_configs.toml, bypassing DynamoDB.

    Purpose: test an UN-DEPLOYED prompt edit. Without this, classify_query() pulls
    the LIVE prompt from the ModelConfig DynamoDB table, so a local TOML change is
    invisible until `upload_model_configs.py` + deploy. This reads the local TOML
    and monkeypatches the module-level constant classify_query() reads
    (disambiguation._CLASSIFIER_PROMPT). Everything else — model id, temperature 0,
    maxTokens 16, the pre-LLM keyword short-circuits — stays byte-identical to prod.
    Monkeypatching module state is intentional and acceptable in this test harness.

    The rewritten OUT_OF_SCOPE_MESSAGE is a plain code constant in disambiguation.py,
    so it is already picked up on import — it needs no override here.
    """
    import tomllib

    with open(MODEL_CONFIGS_TOML, "rb") as f:
        cfg = tomllib.load(f)
    try:
        candidate = cfg["disambiguationClassifier"]["prompt"]
    except KeyError as e:  # pragma: no cover - config shape guard
        raise SystemExit(
            f"disambiguationClassifier.prompt not found in {MODEL_CONFIGS_TOML}: {e}"
        )

    import disambiguation

    disambiguation._CLASSIFIER_PROMPT = candidate
    logger.info(
        "CANDIDATE PROMPT ACTIVE — classify_query is using the LOCAL "
        f"{os.path.relpath(MODEL_CONFIGS_TOML, _REPO_ROOT)} disambiguationClassifier "
        f"prompt ({len(candidate)} chars), NOT the deployed DynamoDB copy."
    )


def run_one_case(case: dict) -> dict:
    """Classify one case via the live classify_query() and grade it."""
    from disambiguation import classify_query

    query = case["query"]
    chat_history = case.get("chat_history") or []
    allow_topic_shift = bool(case.get("allow_topic_shift", False))
    expected = case["expected_verdict"]

    started = time.perf_counter()
    actual = classify_query(query, chat_history, allow_topic_shift=allow_topic_shift)
    latency_ms = round((time.perf_counter() - started) * 1000)

    verdict_match = actual == expected

    # Optional canned-message substring assertion (wired but empty today).
    msg_substr = (case.get("expected_message_substring") or "").strip()
    message = _verdict_message(actual)
    if msg_substr:
        message_ok = bool(message and msg_substr in message)
    else:
        message_ok = True  # nothing to assert

    return {
        "id": case["id"],
        "queryId_ref": case.get("queryId_ref", ""),
        "tags": case.get("tags", []),
        "query": query,
        "history_turns": len(chat_history),
        "allow_topic_shift": allow_topic_shift,
        "expected_verdict": expected,
        "actual_verdict": actual,
        "verdict_match": verdict_match,
        "expected_baseline": case.get("expected_baseline", ""),
        "expected_message_substring": msg_substr,
        "message_ok": message_ok,
        "advisory": "advisory" in (case.get("tags") or []),
        "latency_ms": latency_ms,
    }


def _classify_case(r: dict, mode: str) -> str:
    """Return one of: OK, RED_EXPECTED, BAD_SURPRISE, GOOD_SURPRISE, ADVISORY_MISS.

    OK            — matched (and, if it was a `failing` baseline case in --after,
                    it is now green as intended).
    RED_EXPECTED  — mismatch on a case marked expected_baseline: failing, at
                    baseline. Expected pre-fix RED; not a harness failure.
    BAD_SURPRISE  — mismatch on a `passing` case (regression / broken anchor), or
                    ANY non-advisory mismatch in --after. Gates exit code.
    GOOD_SURPRISE — a `failing` case already matches at baseline (fix landed early
                    or PR held). Reported, does not gate.
    ADVISORY_MISS — mismatch on an advisory case. Reported only.
    """
    matched = r["verdict_match"] and r["message_ok"]
    expected_pass = r["expected_baseline"] == "passing"

    if r["advisory"]:
        return "OK" if matched else "ADVISORY_MISS"

    if matched:
        if mode == "baseline" and not expected_pass:
            return "GOOD_SURPRISE"
        return "OK"

    # not matched
    if mode == "after":
        return "BAD_SURPRISE"
    # baseline mode
    return "RED_EXPECTED" if not expected_pass else "BAD_SURPRISE"


def _print_summary(mode: str, results: list[dict]) -> int:
    logger.info(f"\n=== CLASSIFIER REGRESSION SUMMARY ({mode}) ===")
    dist: dict[str, int] = {}
    bad = 0
    statuses: list[tuple[dict, str]] = []
    for r in results:
        dist[r["actual_verdict"]] = dist.get(r["actual_verdict"], 0) + 1
        status = _classify_case(r, mode)
        statuses.append((r, status))
        if status == "BAD_SURPRISE":
            bad += 1

    # Per-case table.
    logger.info(f"\n{'CASE':<44} {'EXPECTED':<13} {'ACTUAL':<13} STATUS")
    logger.info("-" * 92)
    for r, status in statuses:
        badge = {
            "OK": "OK",
            "RED_EXPECTED": "RED (expected pre-fix)",
            "BAD_SURPRISE": "*** BAD SURPRISE ***",
            "GOOD_SURPRISE": "GOOD SURPRISE (early green)",
            "ADVISORY_MISS": "advisory miss (soft)",
        }[status]
        msg_note = ""
        if r["expected_message_substring"] and not r["message_ok"]:
            msg_note = f"  [msg missing: {r['expected_message_substring']!r}]"
        logger.info(
            f"{r['id']:<44} {r['expected_verdict']:<13} {r['actual_verdict']:<13} "
            f"{badge}{msg_note}"
        )

    logger.info("\nVerdict distribution (actual): " + ", ".join(
        f"{k}={dist[k]}" for k in sorted(dist)
    ))
    good = sum(1 for _, s in statuses if s in ("OK", "GOOD_SURPRISE"))
    red_exp = sum(1 for _, s in statuses if s == "RED_EXPECTED")
    adv = sum(1 for _, s in statuses if s == "ADVISORY_MISS")
    logger.info(
        f"{good}/{len(results)} matched target verdict; {red_exp} RED-as-expected "
        f"(pre-fix); {adv} advisory miss(es); {bad} BAD SURPRISE(S)."
    )
    if bad:
        logger.info(
            "  *** BAD SURPRISE(S) above are regressions / broken anchors — investigate. ***"
        )
    return bad


def run_mode(mode: str) -> None:
    cases = load_cases()
    out_path = BASELINE_PATH if mode == "baseline" else AFTER_PATH
    logger.info(f"Classifying {len(cases)} cases (mode={mode})...")
    results: list[dict] = []
    for i, case in enumerate(cases, start=1):
        logger.info(f"  [{i}/{len(cases)}] {case['id']} — {case['query'][:60]}")
        r = run_one_case(case)
        results.append(r)
        logger.info(
            f"      expected={r['expected_verdict']} actual={r['actual_verdict']} "
            f"match={r['verdict_match']} ({r['latency_ms']}ms)"
        )
    with open(out_path, "w") as f:
        json.dump({"mode": mode, "results": results}, f, indent=2)
    logger.info(f"\nWrote {out_path}")
    bad = _print_summary(mode, results)
    sys.exit(1 if bad else 0)


def compare() -> None:
    if not (os.path.exists(BASELINE_PATH) and os.path.exists(AFTER_PATH)):
        logger.error(
            f"Need both {BASELINE_PATH} and {AFTER_PATH}. "
            "Run --mode baseline then --mode after first."
        )
        sys.exit(1)
    with open(BASELINE_PATH) as f:
        base = {r["id"]: r for r in json.load(f)["results"]}
    with open(AFTER_PATH) as f:
        after = {r["id"]: r for r in json.load(f)["results"]}

    logger.info("\n=== BASELINE vs AFTER (verdict changes) ===")
    flips = 0
    fixed = 0
    broke = 0
    for cid, b in base.items():
        a = after.get(cid)
        if a is None:
            logger.warning(f"  {cid}: in baseline, missing in after")
            continue
        if a["actual_verdict"] != b["actual_verdict"]:
            flips += 1
            exp = a["expected_verdict"]
            arrow = f"{b['actual_verdict']} → {a['actual_verdict']}"
            if a["actual_verdict"] == exp and b["actual_verdict"] != exp:
                fixed += 1
                logger.info(f"  {cid}: {arrow}  ✓ now matches target ({exp})")
            elif b["actual_verdict"] == exp and a["actual_verdict"] != exp:
                broke += 1
                logger.info(f"  {cid}: {arrow}  *** REGRESSED away from target ({exp}) ***")
            else:
                logger.info(f"  {cid}: {arrow}  (target {exp})")
    logger.info(
        f"\n{flips} verdict change(s): {fixed} fixed toward target, {broke} regressed."
    )
    sys.exit(1 if broke else 0)


def main() -> None:
    parser = argparse.ArgumentParser(description="Classifier-regression harness")
    parser.add_argument("--mode", choices=["baseline", "after"])
    parser.add_argument("--compare-only", action="store_true")
    parser.add_argument(
        "--candidate-prompt",
        "--local-prompt",
        dest="candidate_prompt",
        action="store_true",
        help=(
            "Test an UN-DEPLOYED prompt edit: load the disambiguationClassifier "
            "prompt from the local config/model_configs.toml and inject it into "
            "classify_query, bypassing the deployed DynamoDB copy. Model id, "
            "temperature (0), and maxTokens (16) stay identical to prod. Use with "
            "--mode after to prove a candidate before pushing to DynamoDB."
        ),
    )
    args = parser.parse_args()

    if args.candidate_prompt:
        apply_candidate_prompt()

    if args.compare_only:
        compare()
        return
    if not args.mode:
        parser.error("provide --mode {baseline,after} or --compare-only")
    run_mode(args.mode)


if __name__ == "__main__":
    main()

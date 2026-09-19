"""Graph-regression harness — question-accuracy guardrail.

Runs the golden set in tools/ingestion/tests/graph_regression_queries.yaml
through the real agentic retrieval path (Phase A research loop + a
non-streaming Phase B answer generation), then grades each answer on:

  1. Cited-doc overlap — must_cite doc_ids present in cited_doc_ids (primary gate).
  2. Rubric judge — an LLM judge (Sonnet, temp 0) grades the full answer text
     against the case's SME-authored `rubric`, a concrete PASS/FAIL criterion.
     This replaces the old fragile must_contain / must_not_contain prose regexes
     (which are still computed for provenance but no longer gate).
  3. No hallucinated case citations — every cited `case-law-*` id exists in the graph.

The judge is grounded: it grades ONLY against the supplied rubric and must not
substitute its own knowledge of Wisconsin law — so verdicts track the SME
feedback, not the model's guesses.

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

Scope / adequacy cases
----------------------
With ADEQUACY_JUDGE_ENABLED=true the harness also runs the post-retrieval
adequacy judge (backend/lambdas/agentic_retrieval/adequacy_judge.py) after
Phase A and injects its Finding into the Phase-B context, exactly as the Lambda
does. The finding is stored on each run record under "finding", and two extra
per-case expectations gate on it:

    expected_verdict:            ANSWER | CLARIFY | DECLINE
    expected_clarification_axis: substring of finding.clarification.axis

Run only the scope slice (cheap — ~16 cases):

    ADEQUACY_JUDGE_ENABLED=true AWS_PROFILE=<your-profile> AWS_REGION=us-east-1 \\
      python tools/ingestion/ops/run_graph_regression.py --mode after --tags scope

If adequacy_judge is not importable (not merged yet) the harness logs once and
runs without it; verdict expectations are then reported as UNCHECKED, never as
silent passes.

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

VALID_VERDICTS = ("ANSWER", "CLARIFY", "DECLINE")


def load_queries() -> list[dict]:
    with open(QUERIES_YAML) as f:
        data = yaml.safe_load(f)
    return data.get("queries", [])


def filter_entries(
    entries: list[dict], ids: list[str] | None = None, tags: list[str] | None = None
) -> list[dict]:
    """Restrict the golden set by queryId and/or tag.

    `--ids` is an exact queryId match; `--tags` keeps any case whose `tags:` list
    intersects the requested tags (case-insensitive). Both may be combined — the
    filters are ANDed — so `--tags scope --ids gq-x` runs the scope cases that are
    also gq-x. A tag filter is how the orchestrator runs one cheap slice of the
    golden set (e.g. `--tags scope`) instead of the whole ~60-query corpus.
    """
    if ids:
        wanted = set(ids)
        entries = [e for e in entries if e.get("queryId") in wanted]
        missing = wanted - {e.get("queryId") for e in entries}
        if missing:
            logger.warning(f"--ids not found in golden set: {sorted(missing)}")
    if tags:
        wanted_tags = {t.strip().lower() for t in tags if t.strip()}
        entries = [
            e for e in entries if wanted_tags & {str(t).lower() for t in (e.get("tags") or [])}
        ]
        logger.info(f"--tags {sorted(wanted_tags)} → {len(entries)} case(s)")
    return entries


# ── Adequacy judge (post-retrieval ANSWER / CLARIFY / DECLINE) ────────────────
# The judge lives in the agentic_retrieval Lambda source dir (already on sys.path
# above). It may not exist yet on this branch — the harness must keep working, so
# the import is lazy, failure-tolerant, and logged exactly once.
ADEQUACY_ENV_FLAG = "ADEQUACY_JUDGE_ENABLED"
_adequacy_import_logged = False


def adequacy_enabled() -> bool:
    return os.environ.get(ADEQUACY_ENV_FLAG, "").strip().lower() == "true"


def _load_adequacy_judge():
    """Import the adequacy_judge module, or return None (logged once)."""
    global _adequacy_import_logged
    try:
        import adequacy_judge  # type: ignore[import-not-found]

        return adequacy_judge
    except Exception as exc:  # noqa: BLE001 — branch may not be merged yet
        if not _adequacy_import_logged:
            _adequacy_import_logged = True
            logger.warning(
                f"{ADEQUACY_ENV_FLAG}=true but adequacy_judge is unavailable ({exc}); "
                "running without it. Verdict expectations will be reported as UNCHECKED."
            )
        return None


def finding_to_dict(finding) -> dict | None:
    """Normalize a Finding dataclass (or dict) to a JSON-serializable dict."""
    if finding is None:
        return None
    if isinstance(finding, dict):
        raw = dict(finding)
    else:
        import dataclasses

        if dataclasses.is_dataclass(finding):
            raw = dataclasses.asdict(finding)
        else:  # defensive — an object with the documented attributes
            raw = {
                k: getattr(finding, k, None)
                for k in (
                    "verdict",
                    "supported",
                    "unsupported",
                    "clarification",
                    "rationale",
                    "model_id",
                    "latency_ms",
                )
            }
    clar = raw.get("clarification")
    if clar is not None and not isinstance(clar, dict):
        clar = {
            "axis": getattr(clar, "axis", ""),
            "question": getattr(clar, "question", ""),
            "options": list(getattr(clar, "options", []) or []),
        }
        raw["clarification"] = clar
    return raw


def history_from_entry(entry: dict) -> list[dict]:
    """Build the chat_history list for a multi-turn case.

    A case may carry:

        history:
          - query: "How do I appeal the classification of my land?"
            answer: "<the prior assistant turn>"
            clarification: "Which classification …? (agricultural / manufacturing)"

    `clarification` is optional sugar for a CLARIFY prior turn: it is appended to
    that turn's answer text so the follow-up ("Agricultural") is interpretable as
    an answer to the question the assistant just asked. The shape handed to
    run_agentic_loop / build_answer_context is the production one — a list of
    {query, answer} dicts, oldest first (see chat_history.get_chat_history).
    """
    history: list[dict] = []
    for turn in entry.get("history") or []:
        answer = str(turn.get("answer", "") or "")
        clarification = turn.get("clarification")
        if clarification:
            answer = f"{answer}\n\n{clarification}".strip()
        history.append({"query": str(turn.get("query", "") or ""), "answer": answer})
    return history


def _load_answerstream_from_toml(path: str) -> str:
    """Load the answerStream prompt from a local model_configs.toml.

    Used by the --candidate-answerstream override so Phase B can be re-run with
    the LOCAL (edited, not-yet-deployed) prompt while everything else — model,
    temperature, retrieved context — stays identical to production.
    """
    import tomllib

    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    return cfg["answerStream"]["prompt"]


def _load_agenticretrieval_from_toml(path: str) -> str:
    """Load the agenticRetrieval prompt from a local model_configs.toml.

    Used by the --candidate-agenticretrieval override so the Phase-A research
    loop can be re-run with the LOCAL (edited, not-yet-deployed) agentic system
    prompt while everything else — model, temperature, tools — stays identical
    to production. Mirrors _load_answerstream_from_toml.
    """
    import tomllib

    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    return cfg["agenticRetrieval"]["prompt"]


def _phase_b_generate(
    query: str,
    answer_context: str,
    fallback_answer: str,
    answerstream_prompt: str,
    retrieved_doc_ids: set[str] | None = None,
    chunks: list[dict] | None = None,
) -> str:
    """Non-streaming Phase-B answer generation from a prebuilt context.

    Isolated so it can be re-run alone (--phase-b-only) on a saved answer_context
    without re-billing Phase A. The answerStream system prompt is a parameter so a
    candidate prompt can be swapped in with model/temp held identical.
    """
    from loop.phase_b import apply_persona

    from config import AGENTIC_MODEL_ID, bedrock

    if fallback_answer:
        return fallback_answer
    if not answer_context:
        return ""
    try:
        from streaming.bedrock import converse_inference_kwargs

        resp = bedrock.converse(
            modelId=AGENTIC_MODEL_ID,
            messages=[{"role": "user", "content": [{"text": answer_context}]}],
            system=[{"text": apply_persona(answerstream_prompt, None)}],
            **converse_inference_kwargs(AGENTIC_MODEL_ID, 4096),
        )
        text = resp["output"]["message"]["content"][0].get("text", "")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"  answer generation failed: {exc}")
        return ""
    # Mirror the Lambda's pre-persist link repair (loop/phase_b.finalize_answer_links)
    # so the harness grades what production actually emits.
    if retrieved_doc_ids is not None:
        from loop.link_repair import repair_citation_links

        by_doc: dict[str, list[dict]] = {}
        for ch in chunks or []:
            by_doc.setdefault(ch.get("doc_id", ""), []).append(ch)
        text, stats = repair_citation_links(text, set(retrieved_doc_ids), by_doc)
        if stats.get("repointed") or stats.get("stripped"):
            logger.info(
                f"  link repair: {stats['repointed']} repointed, {stats['stripped']} stripped"
            )
    return text


def run_one_query(
    entry: dict,
    answerstream_prompt: str | None = None,
    agenticretrieval_prompt: str | None = None,
) -> dict:
    """Run a single query through Phase A + a non-streaming Phase B answer gen.

    If answerstream_prompt is given (candidate override), Phase B uses it instead
    of the live/DynamoDB answerStream prompt. If agenticretrieval_prompt is given,
    the Phase-A research loop uses that candidate agentic system prompt instead of
    the live/DynamoDB one (model/temp/tools held identical). The prebuilt
    answer_context is stored on the run so a later --phase-b-only pass can
    re-generate the answer cheaply.
    """
    # Imported lazily so --compare-only works without AWS/Neptune configured.
    import loop.phase_a as _phase_a
    from loop.phase_a import run_agentic_loop
    from loop.phase_b import build_answer_context
    from prompt import ANSWER_STREAM_SYSTEM_PROMPT

    from config import neptune

    # Candidate agenticRetrieval override: run_agentic_loop reads the module-level
    # SYSTEM_PROMPT global at call time, so patching it on the phase_a module swaps
    # in the local (not-yet-deployed) prompt without touching anything else.
    if agenticretrieval_prompt is not None:
        _phase_a.SYSTEM_PROMPT = agenticretrieval_prompt

    if answerstream_prompt is None:
        answerstream_prompt = ANSWER_STREAM_SYSTEM_PROMPT

    query = entry["query"]
    chat_history = history_from_entry(entry)
    started = time.perf_counter()

    # Phase A: research loop. ws_server=None → no WebSocket emission.
    result = run_agentic_loop(query, chat_history=chat_history, query_id=entry.get("queryId", ""))

    cited_doc_ids = list(result.cited_doc_ids)

    # Post-retrieval adequacy judge: ANSWER / CLARIFY / DECLINE on the Phase-A
    # plan + the chunks actually cited. Phase B always streams; the finding is
    # injected into its context so the answer can hedge, clarify, or decline.
    finding_dict: dict | None = None
    finding = None
    if adequacy_enabled():
        judge_mod = _load_adequacy_judge()
        if judge_mod is not None:
            cited_set = set(cited_doc_ids)
            # Everything retrieved (mirrors handler.py); the judge labels cited vs not.
            cited_chunks = list(result.all_chunks)
            try:
                finding = judge_mod.judge_answer_plan(
                    query,
                    chat_history,
                    result.answer_plan,
                    cited_chunks,
                    result.discovery,
                    cited_doc_ids=cited_set,
                )
                finding_dict = finding_to_dict(finding)
            except Exception as exc:  # noqa: BLE001 — a flaky judge must not kill the run
                logger.warning(f"  adequacy judge failed: {exc}")
                finding = None

    # Phase B: build context (independent of answerStream) then generate the
    # answer text non-streaming with the (possibly candidate) answerStream prompt.
    answer_context = ""
    if finding is not None and result.fallback_answer:
        # Mirrors handler.py: under a finding the fallback text becomes the plan
        # and Phase B writes the answer (one prose path).
        result.answer_plan = result.fallback_answer
        result.fallback_answer = None
    if not result.fallback_answer:
        ctx_kwargs: dict = {}
        if finding is not None:
            ctx_kwargs["finding"] = finding
        answer_context = build_answer_context(
            query=query,
            cited_chunks=result.all_chunks,
            cited_doc_ids=set(cited_doc_ids),
            discovery=result.discovery,
            fetched_opinions=result.fetched_opinions,
            answer_plan=result.answer_plan,
            chat_history=chat_history,
            neptune_client=neptune,
            **ctx_kwargs,
        )
    answer_text = _phase_b_generate(
        query,
        answer_context,
        result.fallback_answer,
        answerstream_prompt,
        retrieved_doc_ids=(
            set(result.all_doc_ids) | set(cited_doc_ids) | set(result.discovery or {})
        ),
        chunks=result.all_chunks,
    )

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
        "history_turns": len(chat_history),
        "finding": finding_dict,
        "cited_doc_ids": cited_doc_ids,
        "cited_discovery": cited_discovery,
        "discovery_counts": discovery_counts,
        "discovered_doc_count": len(result.discovery),
        "answer": answer_text,
        # Stored so --phase-b-only can re-generate the answer with a candidate
        # answerStream prompt without re-running Phase A (context is unchanged by
        # answerStream). fallback_answer, when set, means Phase B was bypassed.
        "answer_context": answer_context,
        "fallback_answer": result.fallback_answer or "",
        "turns": len(result.trace_log),
        "latency_ms": round((time.perf_counter() - started) * 1000),
    }


# ── LLM judge ────────────────────────────────────────────────────────────────
# Replaces the fragile must_contain / must_not_contain prose regexes with a
# Sonnet judge that grades each answer against the case's `rubric` — a concrete
# PASS/FAIL criterion authored from the tester's verbatim complaint (or DOR's
# confirmed-correct verdict for known-good anchors). The deterministic checks
# (must_cite doc-id overlap, no-hallucinated-case-law) stay as-is; only the
# prose checks move to the judge.
JUDGE_MODEL_ID = "us.anthropic.claude-sonnet-4-6"

# CRITICAL grounding rule: the judge grades ONLY against the supplied rubric and
# must NOT substitute its own knowledge of Wisconsin law. This keeps every
# verdict anchored to the SME feedback, not the model's guesses.
JUDGE_SYSTEM_PROMPT = """You are a strict grader for a Wisconsin Department of Revenue property-tax Q&A assistant.

You are given ONE question, ONE answer produced by the assistant, and ONE rubric
written by a subject-matter expert (SME). The rubric encodes either the SME's
own complaint about a past answer or the confirmed-correct behavior for a
known-good question.

GROUNDING RULE (critical):
- Grade the answer ONLY against the provided rubric.
- Do NOT use your own knowledge of Wisconsin statutes, forms, or property-tax
  procedure to decide whether the answer is "really" correct. The rubric is the
  sole authority. If you believe the rubric is wrong, grade against it anyway.
- If the rubric's stated PASS condition is satisfied by the answer, return PASS.
- If the rubric's stated FAIL condition is met (or the PASS condition is not
  satisfied), return FAIL.
- The list of cited document IDs is provided as supporting context for
  conditions that mention citations; the answer prose is the primary evidence.

Record your decision by calling the `record_verdict` tool exactly once."""

JUDGE_TOOL = {
    "toolSpec": {
        "name": "record_verdict",
        "description": "Record the PASS/FAIL verdict of the answer against the rubric.",
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "verdict": {
                        "type": "string",
                        "enum": ["PASS", "FAIL"],
                        "description": "PASS if the rubric's PASS condition is met; FAIL otherwise.",
                    },
                    "reason": {
                        "type": "string",
                        "description": "One sentence naming the specific rubric condition met or violated.",
                    },
                },
                "required": ["verdict", "reason"],
            }
        },
    }
}


def judge_answer(query: str, answer: str, rubric: str, cited_doc_ids: list[str]) -> dict:
    """Grade one answer against its rubric with the Sonnet judge (temp 0).

    Returns {"verdict": "PASS"|"FAIL", "reason": str}. On any Bedrock/parse
    failure returns verdict "ERROR" with the exception text so a flaky judge
    call never masquerades as a PASS.
    """
    from config import bedrock  # lazy — keeps --compare-only import-free

    user_msg = (
        f"QUESTION:\n{query}\n\n"
        f"RUBRIC (grade ONLY against this):\n{rubric}\n\n"
        f"CITED DOCUMENT IDS:\n{cited_doc_ids or '(none)'}\n\n"
        f"ANSWER TO GRADE:\n{answer or '(empty answer)'}"
    )
    try:
        resp = bedrock.converse(
            modelId=JUDGE_MODEL_ID,
            messages=[{"role": "user", "content": [{"text": user_msg}]}],
            system=[{"text": JUDGE_SYSTEM_PROMPT}],
            inferenceConfig={"maxTokens": 512, "temperature": 0.0},
            toolConfig={
                "tools": [JUDGE_TOOL],
                "toolChoice": {"tool": {"name": "record_verdict"}},
            },
        )
        for block in resp["output"]["message"]["content"]:
            if "toolUse" in block:
                inp = block["toolUse"]["input"]
                verdict = str(inp.get("verdict", "")).upper()
                if verdict not in ("PASS", "FAIL"):
                    verdict = "ERROR"
                return {"verdict": verdict, "reason": inp.get("reason", "")}
        return {"verdict": "ERROR", "reason": "no tool_use block in judge response"}
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"  judge call failed: {exc}")
        return {"verdict": "ERROR", "reason": f"judge exception: {exc}"}


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


def grade_verdict(entry: dict, run: dict) -> dict:
    """Check the adequacy-judge finding against a case's verdict expectations.

    Case fields (both optional):
      expected_verdict            — ANSWER | CLARIFY | DECLINE
      expected_clarification_axis — case-insensitive SUBSTRING of finding.clarification.axis

    An expected_verdict of CLARIFY additionally requires the finding to carry a
    usable clarification: at least two options for the user to pick from (a
    one-option "clarification" is a statement, not a choice).

    Returns {verdict_checked, verdict_pass, actual_verdict, verdict_reason}.
    When the run carries no finding — the adequacy judge is disabled or its
    module is not importable — the check is SKIPPED (verdict_checked False,
    verdict_pass True) so this harness still runs green on a branch without the
    judge. The summary marks such cases UNCHECKED so a skip is never mistaken
    for a pass.
    """
    expected = (entry.get("expected_verdict") or "").strip().upper()
    expected_axis = (entry.get("expected_clarification_axis") or "").strip()
    if not expected and not expected_axis:
        return {
            "verdict_checked": False,
            "verdict_pass": True,
            "actual_verdict": (run.get("finding") or {}).get("verdict"),
            "verdict_reason": "",
        }
    if expected and expected not in VALID_VERDICTS:
        return {
            "verdict_checked": True,
            "verdict_pass": False,
            "actual_verdict": None,
            "verdict_reason": (
                f"case declares expected_verdict={expected!r}, not one of {VALID_VERDICTS}"
            ),
        }

    finding = run.get("finding")
    if not finding:
        return {
            "verdict_checked": False,
            "verdict_pass": True,
            "actual_verdict": None,
            "verdict_reason": "no finding on run (adequacy judge disabled/unavailable)",
        }

    actual = str(finding.get("verdict") or "").strip().upper()
    problems: list[str] = []
    if expected and actual != expected:
        problems.append(f"expected verdict {expected}, got {actual or '(none)'}")

    clar = finding.get("clarification") or {}
    if expected == "CLARIFY":
        options = list(clar.get("options") or [])
        if len(options) < 2:
            problems.append(f"CLARIFY needs >=2 clarification options, got {len(options)}")
    if expected_axis:
        axis = str(clar.get("axis") or "")
        if expected_axis.lower() not in axis.lower():
            problems.append(
                f"expected clarification axis containing {expected_axis!r}, got {axis!r}"
            )

    return {
        "verdict_checked": True,
        "verdict_pass": not problems,
        "actual_verdict": actual or None,
        "verdict_reason": "; ".join(problems),
    }


def grade(entry: dict, run: dict, case_existence: dict[str, bool], run_judge: bool = True) -> dict:
    """Grade one run against its golden-set expectations.

    Gating checks (determine overall pass):
      - must_cite  — doc_ids present in cited_doc_ids (deterministic, free).
      - rubric     — LLM judge PASS against the SME-authored rubric (if present).
      - no-hallucination — every cited case-law node exists in the graph.

    The legacy must_contain / must_not_contain regexes are STILL computed and
    stored (fact_hits / notcontain_hits) for provenance and for the historical
    baseline↔after comparison, but they NO LONGER GATE — the rubric+judge
    replaces them. Overall pass = cite_pass AND judge_pass AND no_hallucination.
    """
    cited = set(run["cited_doc_ids"])
    answer = run["answer"] or ""

    must_cite = entry.get("must_cite", []) or []
    cite_hits = {doc_id: (doc_id in cited) for doc_id in must_cite}
    cite_pass = all(cite_hits.values())

    # --- Legacy prose regexes: retained as advisory/provenance only (non-gating).
    must_contain = entry.get("must_contain", []) or []
    fact_hits = {pat: bool(re.search(pat, answer, re.IGNORECASE)) for pat in must_contain}
    fact_pass = all(fact_hits.values())

    must_not_contain = entry.get("must_not_contain", []) or []
    notcontain_hits = {pat: bool(re.search(pat, answer, re.IGNORECASE)) for pat in must_not_contain}
    notcontain_pass = not any(notcontain_hits.values())

    cited_cases = _cited_case_ids(run["cited_doc_ids"])
    hallucinated = [c for c in cited_cases if not case_existence.get(c, True)]
    no_hallucination = not hallucinated

    # --- LLM judge against the rubric (the gating semantic check).
    rubric = (entry.get("rubric") or "").strip()
    judge_verdict = None
    judge_reason = ""
    if rubric and run_judge:
        j = judge_answer(entry["query"], answer, rubric, run["cited_doc_ids"])
        judge_verdict = j["verdict"]
        judge_reason = j["reason"]
    # A case with a rubric must earn a PASS; a case without a rubric is not
    # judge-gated. An ERROR verdict is NOT a pass (fail-closed).
    judge_pass = True if not rubric else (judge_verdict == "PASS")

    # --- Adequacy-judge verdict expectations (ANSWER / CLARIFY / DECLINE).
    v = grade_verdict(entry, run)

    overall_pass = cite_pass and judge_pass and no_hallucination and v["verdict_pass"]

    return {
        "queryId": run["queryId"],
        "stratum": run["stratum"],
        "tags": entry.get("tags", []) or [],
        "expected_verdict": (entry.get("expected_verdict") or "").strip().upper() or None,
        "verdict_checked": v["verdict_checked"],
        "verdict_pass": v["verdict_pass"],
        "actual_verdict": v["actual_verdict"],
        "verdict_reason": v["verdict_reason"],
        "cite_hits": cite_hits,
        "cite_pass": cite_pass,
        "fact_hits": fact_hits,
        "fact_pass": fact_pass,
        "notcontain_hits": notcontain_hits,
        "notcontain_pass": notcontain_pass,
        "cited_case_ids": cited_cases,
        "hallucinated_case_ids": hallucinated,
        "no_hallucination": no_hallucination,
        "has_rubric": bool(rubric),
        "judge_verdict": judge_verdict,
        "judge_reason": judge_reason,
        "judge_pass": judge_pass,
        "overall_pass": overall_pass,
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


def run_mode(
    mode: str,
    resume: bool = True,
    ids: list[str] | None = None,
    out_path: str | None = None,
    answerstream_prompt: str | None = None,
    agenticretrieval_prompt: str | None = None,
    workers: int = 1,
    tags: list[str] | None = None,
) -> None:
    entries = filter_entries(load_queries(), ids=ids, tags=tags)
    if out_path is None:
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

    def _one(entry: dict) -> tuple[dict, dict]:
        run = run_one_query(
            entry,
            answerstream_prompt=answerstream_prompt,
            agenticretrieval_prompt=agenticretrieval_prompt,
        )
        case_existence = verify_case_ids_exist(_cited_case_ids(run["cited_doc_ids"]))
        run["case_existence"] = case_existence
        return run, grade(entry, run, case_existence)

    # Queries are independent (run_one_query is self-contained; the Lambda
    # modules' caches — flowchart vectors, case-law index — are read-mostly and
    # safe to populate from several threads). Each query is ~45 s of waiting on
    # Bedrock/Neptune, so a small pool cuts wall clock almost linearly; keep it
    # modest (4–6) to stay under Bedrock's per-model request rate.
    if workers > 1:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        logger.info(f"  parallel: {workers} workers")
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_one, e): e for e in todo}
            for i, fut in enumerate(as_completed(futures), start=1):
                entry = futures[fut]
                try:
                    run, g = fut.result()
                except Exception as exc:  # noqa: BLE001 — one bad query must not kill the run
                    logger.error(f"  [{i}/{len(todo)}] {entry.get('queryId')} FAILED: {exc}")
                    continue
                runs.append(run)
                grades.append(g)
                logger.info(
                    f"  [{i}/{len(todo)}] {entry.get('queryId')} cited={len(run['cited_doc_ids'])} "
                    f"cite_pass={g['cite_pass']} judge={g.get('judge_verdict')} "
                    f"({run['latency_ms']}ms)"
                )
                _checkpoint(out_path, mode, runs, grades)  # main thread only — no lock needed
    else:
        for i, entry in enumerate(todo, start=1):
            logger.info(f"  [{i}/{len(todo)}] {entry.get('queryId')} — {entry['query'][:70]}")
            try:
                run, g = _one(entry)
            except Exception as exc:  # noqa: BLE001 — one bad query must not kill the run
                logger.error(f"  [{i}/{len(todo)}] {entry.get('queryId')} FAILED: {exc}")
                continue
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


def phase_b_only(
    mode: str,
    answerstream_prompt: str,
    ids: list[str] | None = None,
    out_path: str | None = None,
) -> None:
    """Re-run ONLY Phase B (+ judge) on a saved run's stored answer_context.

    Cheap iteration path: Phase A retrieval is untouched (answerStream does not
    affect it), so we reuse each run's saved answer_context and only re-bill one
    converse per case for the answer + one judge call. Requires a prior full run
    (with answer_context stored) at out_path.
    """
    entries = {e["queryId"]: e for e in load_queries()}
    if out_path is None:
        out_path = BASELINE_PATH if mode == "baseline" else AFTER_PATH
    if not os.path.exists(out_path):
        logger.error(f"No saved run at {out_path} — run a full pass first to capture context.")
        sys.exit(1)
    with open(out_path) as f:
        data = json.load(f)

    runs = data["runs"]
    wanted = set(ids) if ids else None
    new_runs: list[dict] = []
    new_grades: list[dict] = []
    for run in runs:
        qid = run["queryId"]
        if wanted is not None and qid not in wanted:
            new_runs.append(run)
            # keep prior grade for untouched cases
            prior = next((g for g in data.get("grades", []) if g["queryId"] == qid), None)
            if prior:
                new_grades.append(prior)
            continue
        if "answer_context" not in run:
            logger.warning(f"  {qid}: no stored answer_context — skipping (need a fresh full run)")
            new_runs.append(run)
            continue
        logger.info(f"  [phase-B] {qid} — {run['query'][:70]}")
        run = dict(run)
        run["answer"] = _phase_b_generate(
            run["query"],
            run.get("answer_context", ""),
            run.get("fallback_answer", ""),
            answerstream_prompt,
        )
        entry = entries.get(qid)
        case_existence = run.get("case_existence", {})
        g = grade(entry, run, case_existence) if entry else None
        new_runs.append(run)
        if g:
            new_grades.append(g)
            logger.info(
                f"      judge={g.get('judge_verdict') or '-'} ({g.get('judge_reason', '')[:100]})"
            )
    _checkpoint(out_path, data.get("mode", mode), new_runs, new_grades)
    logger.info(f"\nRe-ran Phase B for {out_path}")
    _print_grade_summary(mode, list(entries.values()), new_grades)


def _print_grade_summary(mode: str, entries: list[dict], grades: list[dict]) -> None:
    logger.info(f"\n=== GRADE SUMMARY ({mode}) ===")
    hard_fail = 0
    gated = 0  # cases that count toward the engineering pass/fail gate
    blocked_lines: list[str] = []
    for g in grades:
        stratum = g["stratum"]
        # Gates: must_cite (deterministic) + rubric judge (semantic) + no
        # hallucinated case law. The legacy must_contain / must_not_contain
        # regexes no longer gate — the judge replaces them.
        problems = []
        if not g["cite_pass"]:
            missing = [d for d, ok in g["cite_hits"].items() if not ok]
            problems.append(f"missing must_cite {missing}")
        if g.get("has_rubric") and not g.get("judge_pass"):
            verdict = g.get("judge_verdict") or "?"
            problems.append(f"JUDGE {verdict}: {g.get('judge_reason', '')}")
        if not g["no_hallucination"]:
            problems.append(f"HALLUCINATED cases {g['hallucinated_case_ids']}")
        if not g.get("verdict_pass", True):
            problems.append(f"VERDICT: {g.get('verdict_reason', '')}")
        status = "OK" if not problems else "FAIL"
        # A declared expected_verdict that could not be evaluated (no finding on
        # the run) is neither a pass nor a fail — say so rather than let a skip
        # read as a green check.
        if g.get("expected_verdict") and not g.get("verdict_checked"):
            status = f"{status} [verdict UNCHECKED: {g.get('verdict_reason', '')}]"
        # Surface the judge reason even on PASS for auditability.
        judge_note = ""
        if g.get("has_rubric") and g.get("judge_pass") and not problems:
            judge_note = f"  [judge PASS: {g.get('judge_reason', '')}]"
        line = f"  [{stratum}] {g['queryId']}: {status} {'; '.join(problems)}{judge_note}"
        # Cases tagged blocked-on-dor-content are external/blocked (e.g. a stale
        # DOR guide we cannot fix in code). Show them, but do NOT count them in
        # the engineering pass/fail gate.
        if "blocked-on-dor-content" in (g.get("tags") or []):
            blocked_lines.append(line + "  [EXTERNAL/BLOCKED — not gated]")
            continue
        gated += 1
        if problems:
            hard_fail += 1
        logger.info(line)
    if blocked_lines:
        logger.info("\n--- external/blocked (not counted in gate) ---")
        for bl in blocked_lines:
            logger.info(bl)
    verdict_counts: dict[str, int] = {}
    for g in grades:
        av = g.get("actual_verdict")
        if av:
            verdict_counts[av] = verdict_counts.get(av, 0) + 1
    if verdict_counts:
        dist = ", ".join(f"{v}={verdict_counts[v]}" for v in sorted(verdict_counts))
        logger.info(f"\n--- adequacy verdicts: {dist} ---")
    logger.info(
        f"\n{gated - hard_fail}/{gated} passed intra-run gates "
        f"({hard_fail} flagged; {len(blocked_lines)} external/blocked excluded). "
        f"Gate = must_cite AND judge(rubric) AND no-hallucination AND verdict. "
        f"Run --compare-only after both baseline+after for drift."
    )


def _run_verdict(run: dict | None) -> str:
    """The adequacy verdict recorded on a run, or '-' when none was captured."""
    if not run:
        return "-"
    finding = run.get("finding") or {}
    return str(finding.get("verdict") or "-").upper()


def _print_verdict_flips(base_runs: dict[str, dict], after_runs: dict[str, dict]) -> None:
    """Report adequacy-verdict distribution per run and every per-case flip.

    A flip is the headline signal when tuning the adequacy judge: a case that
    used to ANSWER now DECLINEs (over-refusal) or vice versa (over-reach). '-'
    means that run captured no finding (judge disabled or unavailable).
    """
    base_counts: dict[str, int] = {}
    after_counts: dict[str, int] = {}
    flips: list[tuple[str, str, str]] = []
    for qid, b in base_runs.items():
        a = after_runs.get(qid)
        bv, av = _run_verdict(b), _run_verdict(a)
        base_counts[bv] = base_counts.get(bv, 0) + 1
        if a is not None:
            after_counts[av] = after_counts.get(av, 0) + 1
        if a is not None and bv != av:
            flips.append((qid, bv, av))

    if not any(v in VALID_VERDICTS for v in (*base_counts, *after_counts)):
        return  # neither run carried adequacy findings — nothing to report

    def _fmt(counts: dict[str, int]) -> str:
        return ", ".join(f"{k}={counts[k]}" for k in sorted(counts)) or "(none)"

    logger.info("\n=== ADEQUACY VERDICTS ===")
    logger.info(f"  baseline: {_fmt(base_counts)}")
    logger.info(f"  after:    {_fmt(after_counts)}")
    if flips:
        logger.info(f"\n  verdict flips ({len(flips)}):")
        for qid, bv, av in sorted(flips):
            logger.info(f"    {qid}: {bv} → {av}")
    else:
        logger.info("\n  verdict flips: none")


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
        lost_facts = [p for p, ok in ag["fact_hits"].items() if bg["fact_hits"].get(p) and not ok]
        lost_cites = []
        if stratum in ("B", "D", "E"):
            lost_cites = [
                d for d, ok in ag["cite_hits"].items() if bg["cite_hits"].get(d) and not ok
            ]
        new_halluc = ag["hallucinated_case_ids"]
        # A forbidden phrase that was absent at baseline but appears after is a
        # regression (e.g. a fabricated URL or chapter-conflated link reappears).
        new_forbidden = [
            p
            for p, hit in ag.get("notcontain_hits", {}).items()
            if hit and not bg.get("notcontain_hits", {}).get(p, False)
        ]

        is_regression = bool(lost_facts or lost_cites or new_halluc or new_forbidden)
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
        if new_forbidden:
            logger.info(f"      NEW FORBIDDEN phrases: {new_forbidden}")

    _print_verdict_flips(base_runs, after_runs)

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
        f"  cite_pass={g['cite_pass']} judge={g.get('judge_verdict') or '-'} "
        f"({g.get('judge_reason', '')}) "
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
            f"cite_pass={g['cite_pass']} "
            f"judge={g.get('judge_verdict') or '-'} "
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
        "--workers",
        type=int,
        default=1,
        help="Run this many golden-set queries concurrently (default 1). 4–6 is a safe "
        "range for Bedrock rate limits; results are checkpointed as they complete.",
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
    parser.add_argument(
        "--ids",
        default="",
        help="Comma-separated queryIds to restrict the run to (subset testing)",
    )
    parser.add_argument(
        "--tags",
        default="",
        help="Comma-separated tags; run only cases whose `tags:` list contains one of "
        "them (case-insensitive). e.g. --tags scope. Combines with --ids (ANDed).",
    )
    parser.add_argument(
        "--out",
        default="",
        help="Override output JSON path (default baseline.json/after.json by --mode)",
    )
    _default_toml = os.path.join(_REPO_ROOT, "config", "model_configs.toml")
    parser.add_argument(
        "--candidate-answerstream",
        nargs="?",
        const=_default_toml,
        default=None,
        help="Inject the LOCAL config/model_configs.toml answerStream into Phase B "
        "(pre-deploy test). Optionally pass a path to a different TOML.",
    )
    parser.add_argument(
        "--candidate-agenticretrieval",
        nargs="?",
        const=_default_toml,
        default=None,
        help="Inject the LOCAL config/model_configs.toml agenticRetrieval into the "
        "Phase-A research loop (pre-deploy test). Optionally pass a path to a "
        "different TOML. Only affects full runs (--mode), not --phase-b-only.",
    )
    parser.add_argument(
        "--phase-b-only",
        action="store_true",
        help="Re-run ONLY Phase B (+ judge) on a saved run's stored answer_context "
        "(cheap iteration; requires a prior full run at --out/--mode file).",
    )
    args = parser.parse_args()

    ids = [s.strip() for s in args.ids.split(",") if s.strip()] or None
    tags = [s.strip() for s in args.tags.split(",") if s.strip()] or None
    out_path = args.out or None
    if adequacy_enabled():
        logger.info(f"{ADEQUACY_ENV_FLAG}=true — running the post-retrieval adequacy judge.")
    answerstream_prompt = None
    if args.candidate_answerstream:
        answerstream_prompt = _load_answerstream_from_toml(args.candidate_answerstream)
        logger.info(
            f"Using CANDIDATE answerStream from {args.candidate_answerstream} "
            f"({len(answerstream_prompt)} chars) for Phase B."
        )
    agenticretrieval_prompt = None
    if args.candidate_agenticretrieval:
        agenticretrieval_prompt = _load_agenticretrieval_from_toml(args.candidate_agenticretrieval)
        logger.info(
            f"Using CANDIDATE agenticRetrieval from {args.candidate_agenticretrieval} "
            f"({len(agenticretrieval_prompt)} chars) for the Phase-A loop."
        )

    if args.compare_only:
        compare()
        return
    if args.regrade:
        if not args.mode:
            parser.error("--regrade requires --mode {baseline,after}")
        regrade(args.mode)
        return
    if args.phase_b_only:
        if not args.mode:
            parser.error("--phase-b-only requires --mode {baseline,after}")
        if answerstream_prompt is None:
            parser.error("--phase-b-only requires --candidate-answerstream")
        phase_b_only(args.mode, answerstream_prompt, ids=ids, out_path=out_path)
        return
    if not args.mode:
        parser.error("provide --mode {baseline,after} or --compare-only")
    if args.only:
        _run_single(args.mode, args.only)
        return
    run_mode(
        args.mode,
        resume=not args.no_resume,
        ids=ids,
        out_path=out_path,
        answerstream_prompt=answerstream_prompt,
        agenticretrieval_prompt=agenticretrieval_prompt,
        workers=args.workers,
        tags=tags,
    )


if __name__ == "__main__":
    main()

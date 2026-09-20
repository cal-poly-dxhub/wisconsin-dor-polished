# Testing Guide

Single source of truth for where tests live, how to run them, and — most
importantly — **which tests to update when you change something**. If you
change behavior anywhere in this repo, find your change in the "When you
change X" table below before you consider the work done.

## How to run everything

```bash
uv run pytest        # all Python tests (backend lambdas, layers, ingestion)
bun run test         # all JS/TS tests (frontend bun tests + infra Jest)
uvx ruff check backend tools   # Python lint
cd frontend && bunx eslint .   # TS/JS lint — run from frontend/, see note below
```

Two commands need extra flags:

```bash
# chat_api tests need the chat-api dependency group (Powertools) plus pyyaml:
uv run --group chat-api --with pyyaml pytest backend/lambdas/chat_api -q

# frontend only:
cd frontend && bun test src     # all green as of 2026-09-19
```

`bunx eslint .` from the repo root fails to load: the root `eslint.config.ts` does not
parse under the pinned ESLint 8. Lint from `frontend/`.

Both suites must be fully green before merging. **The unit suites have no known-flaky
tests** — a red unit test means the code or the test is wrong. The *live-AWS accuracy
harness* is a different matter; see below.

## The graph-regression harness (not a unit test)

`tools/ingestion/ops/run_graph_regression.py` runs the **63-case** golden set in
`tools/ingestion/tests/graph_regression_queries.yaml` end to end against the live Neptune
graph and Bedrock, grading each answer with an LLM judge. It costs real money and takes
tens of minutes, so it is a gate you run deliberately, not part of `uv run pytest`.

```bash
export ADEQUACY_JUDGE_ENABLED=true        # production parity — the judge is ON in prod
uv run python tools/ingestion/ops/run_graph_regression.py --mode baseline
uv run python tools/ingestion/ops/run_graph_regression.py --mode after
uv run python tools/ingestion/ops/run_graph_regression.py --compare-only
```

Useful flags: `--tags scope` (run one cheap slice), `--ids a,b`, `--only <queryId>`,
`--workers 4` (4–6 is safe for Bedrock rate limits; results checkpoint as they complete),
`--no-resume`, `--out <path>`, `--regrade` (re-grade a saved run against the current YAML
with no re-run; honors `--out`), `--phase-b-only` (re-run only Phase B + the judge against
a saved run's stored answer context), and `--candidate-answerstream` /
`--candidate-agenticretrieval` (inject the LOCAL `config/model_configs.toml` prompt so you
can test a prompt edit before pushing it to DynamoDB). The grader appends the clarification
block to the answer text before grading, so a CLARIFY case is graded the way the UI shows it.

**Known-flaky and known-red cases** — do not treat these as a clean signal:

| Case | Status |
| --- | --- |
| `gq-full-value-annual` | **Flaky.** The maintenance-vs-revaluation material is in the graph (WPAM Ch. 4, pp. 59–61) but vector search often prefers the § 70.05(5) compliance chunks in Ch. 6. A ranking miss on a manual chunk — manuals are not alias-enriched by design, so the gold-query lever does not apply. This is the one stable delta between recent runs. |
| `gq-prior-year-roll` | **Flaky.** Flips between runs on judge/ranking noise. |
| `gq-bor-interpreter-waiver` | **Never passes, by design.** It is a DOR *content* question, not a retrieval bug: § 70.47(8m) is a hearing waiver, not an interpreter provision, and § 70.47 has no interpreter requirement. It stays red until DOR confirms the intended reference. |

Related harnesses in the same directory: `run_recall_probe.py` (raw vector recall@10/30 and
MRR against any graph id, baseline vs after) and `run_classifier_regression.py` (legacy —
the pre-loop classifier it grades is off in production).

## Test map

| Area | Test location | Runner | Notes |
|------|--------------|--------|-------|
| Agentic retrieval Lambda | `backend/lambdas/agentic_retrieval/tests/` | pytest | **28 `test_*.py` files** plus `conftest.py` — roughly one per source module, incl. `test_adequacy_judge.py`, `test_flowchart_router.py`, `test_flowchart_loop.py`, `test_flowcharts.py`, `test_link_repair.py`, `test_statute_section_pages.py`, `test_worksheets.py`, `test_pipeline.py`, `test_statute_backfill.py`, `test_caselaw_backfill.py`, `test_wpam_dedup.py`, `test_config_validator.py`, `test_retrieval_toml_schema.py`. `conftest.py` provides the `fresh_modules` fixture for re-importing modules with AWS clients mocked. |
| Chat API Lambda | `backend/lambdas/chat_api/test_chat_api.py` | pytest | Needs the `chat-api` dependency group: `uv run --group chat-api --with pyyaml pytest backend/lambdas/chat_api -q` |
| WebSocket handlers | `backend/lambdas/websocket/test_websocket.py` | pytest | connect/disconnect/default routes |
| Shared layers | `backend/layers/test_step_function_types.py`, `backend/layers/test_websocket_utils.py` | pytest | Pydantic model contracts + WS send_json router |
| Ingestion pipeline | `tools/ingestion/tests/` | pytest | **27 test files**: chunkers, extractors, boilerplate/TOC (incl. `test_wpam_toc_heuristic.py`, the Task 72 guard), aliases + enrich policy, metadata integrity, `load.py` graph-id default, Textract staging bucket, scraper authority, case law, FAQ URL map, and `test_graph_regression_grade.py` (grades the harness grader itself). This dir **is** a package (`__init__.py`) — unlike the lambda test dirs. Also holds the harness YAMLs. |
| Chunking fixtures | `tools/ingestion/chunking_test/samples/` | fixture data | Real case-law opinion `.txt` files used as chunker inputs. It is **not** a test directory — there are no `test_*.py` files in it and pytest collects nothing; it is the sample corpus the ingestion tests read from. |
| Ingestion ops scripts | `tools/ingestion/ops/` (e.g. `test_diversity.py`) | manual | Live-AWS harnesses. `test_diversity.py` matches `test_*.py` and sits under `testpaths`, but defines **no `test_*` functions** (logic is under `if __name__ == "__main__"`), so pytest collects nothing from it. Not excluded by config — just not written as collected tests. |
| Frontend | `frontend/src/**/test/*.test.ts(x)` | `bun test src` (via `bun run test`) | 19 co-located test files next to the code under test. All green as of 2026-09-19. |
| Infra (CDK) | `infra/test/infra.test.ts` | Jest (via `bun run test`) | Synth-level assertions on stack resources |

Pytest configuration lives in the root `pyproject.toml` — just `testpaths =
["backend", "tools"]` and `pythonpath = ["backend/layers",
"backend/lambdas/websocket", "tools"]` (no `addopts`). The **lambda** test dirs
(`agentic_retrieval/tests/`, `chat_api/`, `websocket/`) are NOT packages — pytest
adds them to `sys.path` per-directory via conftest files. `tools/ingestion/tests/`
is the exception: it *is* a package (`__init__.py`).

## When you change X, update Y

| Change | Tests to update / add |
|--------|----------------------|
| Agentic loop behavior (`loop/phase_a.py`, `loop/phase_b.py`, `handler.py`) | `agentic_retrieval/tests/test_handler.py` |
| Agent tools (`agent_tools/definitions.py`, `agent_tools/executor.py`) | `tests/test_tools.py`; `tests/test_pipeline.py` for stage order |
| Adequacy judge (`adequacy_judge.py`) — verdicts, guards, `resolve_pending_clarification` | `tests/test_adequacy_judge.py`; the judge's effect on answers is gated by `run_graph_regression.py --tags scope` |
| Flowchart router / sidecars (`flowchart_router.py`, `flowcharts.py`, `tools/ingestion/flowcharts/data/*.json`) | `tests/test_flowchart_router.py`, `test_flowchart_loop.py`, `test_flowcharts.py`. These guard **structure only** — they cannot catch a stale WPAM page anchor or an outdated branch, which is why the annual refresh has a manual re-verify step |
| Statute link pages (`loop/link_repair.py`, `phase_b.statute_section_pages`) | `tests/test_link_repair.py`, `tests/test_statute_section_pages.py` |
| Worksheets (`worksheets.py`) | `tests/test_worksheets.py` |
| Alias enrichment / `EnrichPolicy` (`tools/ingestion/lib/aliases.py`, `extract.py`, `embed.py`) | `tools/ingestion/tests/test_aliases.py`, `test_embed_enriched.py`, `test_extract_aliases_backfill.py` |
| Loader phases / metadata overlay (`tools/ingestion/load.py`) | `tools/ingestion/tests/test_metadata_integrity.py`, `test_load_authority_default.py`, `test_load_chunk_citations.py`, `test_load_graph_id_default.py` |
| Neptune queries (`graph/neptune_client.py`) | `tests/test_neptune_client.py` |
| Citation cards / doc building (`rag_documents.py`, `case_law.py`) | `tests/test_rag_documents.py`, `tests/test_case_law.py`, `tests/test_case_opinion.py` |
| FAQ handling (`faq.py`) | `tests/test_faq.py` |
| System prompt (`config/model_configs.toml` → `agenticRetrieval`) | `tests/test_prompt.py` pins load-bearing phrases — update the pins deliberately, don't delete them |
| Tracing / trace summaries | `tests/test_tracing.py`, `tests/test_trace_summaries.py` |
| **WebSocket message shapes** | Three places, always together: `backend/layers/websocket_utils/models.py` (+ `backend/layers/test_websocket_utils.py`), Zod schemas in `frontend/types/message-types.ts`, and the handler switch in `frontend/src/hooks/use-websocket-chat.ts` (+ `frontend/src/hooks/test/use-websocket-chat.test.tsx`). The frontend rejects any message not in the Zod union. |
| Shared Pydantic models (`backend/layers/step_function_types/`) | `backend/layers/test_step_function_types.py`; check consumers: agentic_retrieval AND chat_api |
| Chunking / extraction (`tools/ingestion/chunking/`, `extract.py`) | `tools/ingestion/tests/` (chunker, page-tracking, TOC, authority tests) |
| Scraper / manifest (`scrape_documents.py`, `config/document_manifest.yaml`) | `tools/ingestion/tests/test_scrape_authority_levels.py`, `test_case_slug.py` |
| CDK stacks | `infra/test/infra.test.ts`; run `cdk diff` before merging |
| Frontend components/hooks/stores | Co-located `test/` dir next to the changed file — create one if it doesn't exist |

## Conventions and pitfalls

- **Never mutate `sys.modules` entries shared with other test files** (e.g.
  assigning fakes onto `step_function_types.models`). That poisons every test
  that runs later in the same process. Use the real layer models (they're on
  `sys.path`), inject fakes into the *consuming module's* namespace via
  `monkeypatch.setattr(module, "Name", Fake)`, or use the `fresh_modules`
  fixture in `agentic_retrieval/tests/conftest.py`.
- **No network in unit tests.** Module-level boto3 clients are mocked by
  patching `boto3.client`/`boto3.resource` during import (`fresh_modules`
  does this for the agentic retrieval package).
- **Verify order-independence** if you touch import-time state: run the file
  alone (`uv run pytest path/to/test_file.py`) and as part of the full suite.
- Lambda bundles exclude `tests/`, `conftest.py`, and `test_*.py`
  (see `tools/bundle.py` IGNORE_PATTERNS) — tests never ship to Lambda.
- **Lint from `frontend/`.** The repo-root `eslint.config.ts` does not load under the
  pinned ESLint 8, so `bunx eslint .` at the root errors out.
- If you find a stale test after a behavior change, fix it in the same PR as the change,
  not later. The unit suites are fully green; keep them that way.

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
bunx eslint .                  # TS/JS lint
```

Both suites must be fully green before merging. There are no known-flaky or
expected-failure tests; a red test means the code or the test is wrong.

## Test map

| Area | Test location | Runner | Notes |
|------|--------------|--------|-------|
| Agentic retrieval Lambda | `backend/lambdas/agentic_retrieval/tests/` | pytest | One `test_<module>.py` per source module. `conftest.py` provides the `fresh_modules` fixture for re-importing modules with AWS clients mocked. |
| Chat API Lambda | `backend/lambdas/chat_api/test_chat_api.py` | pytest | |
| WebSocket handlers | `backend/lambdas/websocket/test_websocket.py` | pytest | connect/disconnect/default routes |
| Shared layers | `backend/layers/test_step_function_types.py`, `backend/layers/test_websocket_utils.py` | pytest | Pydantic model contracts + WS send_json router |
| Ingestion pipeline | `tools/ingestion/tests/` | pytest | chunkers, extractors, scraper, case-law, FAQ URL map |
| Ingestion ops scripts | `tools/ingestion/ops/` (e.g. `test_diversity.py`) | manual | Live-AWS harnesses, not collected assertions — run by hand |
| Frontend | `frontend/src/**/test/*.test.ts(x)` | `bun test` (via `bun run test`) | Co-located `test/` dirs next to the code under test |
| Infra (CDK) | `infra/test/infra.test.ts` | Jest (via `bun run test`) | Synth-level assertions on stack resources |

Pytest configuration lives in the root `pyproject.toml` (`testpaths`,
`pythonpath`, and `per-file-ignores` for ruff). The lambda test dirs are NOT
packages — pytest adds them to `sys.path` per-directory via conftest files.

## When you change X, update Y

| Change | Tests to update / add |
|--------|----------------------|
| Agentic loop behavior (`loop/phase_a.py`, `loop/phase_b.py`, `handler.py`) | `agentic_retrieval/tests/test_handler.py` |
| Agent tools (`agent_tools/definitions.py`, `agent_tools/executor.py`) | `tests/test_tools.py`, `tests/test_auto_enrichment.py` |
| Neptune queries (`graph/neptune_client.py`) | `tests/test_neptune_client.py` |
| Citation cards / doc building (`rag_documents.py`, `case_law.py`) | `tests/test_rag_documents.py`, `tests/test_case_law.py`, `tests/test_case_opinion.py` |
| FAQ handling (`faq.py`) | `tests/test_faq.py` |
| System prompt (`config/model_configs.toml` → `agenticRetrieval`) | `tests/test_prompt.py` pins load-bearing phrases — update the pins deliberately, don't delete them |
| Tracing / trace summaries | `tests/test_tracing.py`, `tests/test_trace_summaries.py` |
| **WebSocket message shapes** | Three places, always together: `backend/layers/websocket_utils/models.py` (+ `backend/layers/test_websocket_utils.py`), Zod schemas in `frontend/types/message-types.ts`, and the handler switch in `frontend/src/hooks/use-websocket-chat.ts` (+ `frontend/src/hooks/test/use-websocket-chat.test.tsx`). The frontend rejects any message not in the Zod union. |
| Shared Pydantic models (`backend/layers/step_function_types/`) | `backend/layers/test_step_function_types.py`; check consumers: agentic_retrieval AND chat_api |
| Chunking / extraction (`tools/ingestion/chunking/`, `extract.py`) | `tools/ingestion/tests/` (chunker, page-tracking, TOC, authority tests) |
| Scraper / manifest (`scrape_documents.py`, `document_manifest.yaml`) | `tools/ingestion/tests/test_scrape_authority_levels.py`, `test_case_slug.py` |
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
- Commit eb30ba8 established the fully-green baseline; keep it that way.
  If you find a stale test after a behavior change, fix it in the same PR
  as the change, not later.

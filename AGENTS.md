# AGENTS.md — instructions for the local implementer agent (opencode)

You are the **implementer**. A planning model (Claude Code / Opus 4.8) writes specs; you execute them
precisely. When given `handoff/SPEC.md` (or a spec inline), follow it exactly and do not expand scope.

## Golden rules
- Do exactly what the spec says. If the spec is ambiguous or contradicts the code, STOP and report the
  ambiguity instead of guessing.
- Touch only the files the spec names. Never edit files listed under "Do NOT touch".
- **Never** run `cdk deploy`, `bun run deploy`, `git push`, or anything that mutates AWS. Local edits only.
- Do not stage or commit unless the spec explicitly tells you to. Leave changes unstaged; when done,
  show `git diff` and stop for review.

## Repo conventions
- **Python:** Pydantic v2 (`model_validate` / `model_dump`). Serialized models extend `CamelCaseModel`
  (snake_case → camelCase JSON). Deps via `uv` (root `pyproject.toml`).
- **TypeScript:** Next.js app in `frontend/`. CDK infra in `infra/`. Tests via Jest (infra only).
- **WebSocket contract:** any change to a message shape must update BOTH sides —
  `frontend/types/message-types.ts` (Zod) AND `backend/layers/websocket_utils/models.py` (Pydantic).
- Match the style of surrounding code: naming, comment density, import order.

## Commands
Run these exactly — the forms below are the ones that actually work in this repo.
Test files live NEXT TO the code they cover (e.g. `backend/lambdas/chat_api/test_chat_api.py`,
`tools/ingestion/tests/test_*.py`). There is **no** repo-root `tests/` directory; `pyproject.toml`
already scopes collection to `backend` and `tools`.
- Python tests (all):    `uv run pytest -q`
- Python tests (one file): `uv run pytest backend/lambdas/chat_api/test_chat_api.py -q`
- Python tests (one test): `uv run pytest <path/to/test_file.py> -k "<test_name>"`
- TS tests:              `bun run test`   (runs the infra Jest suite from repo root)
- Python lint/format:    `uvx ruff check <files you changed>`   /   `uvx ruff format <files you changed>`
  - Use `uvx ruff`, NOT `uv run ruff` — ruff is not a project dependency, so `uv run ruff` fails.
  - The repo has many pre-existing ruff warnings. Do NOT try to make the whole repo clean.
    Only lint the files your spec had you change, and only fix issues in those files.
- Frontend lint:         `cd frontend && bun run lint`   (Next.js ESLint)
  - Do NOT run `bunx eslint .` from the repo root — it can't find a config and errors out.

### Verifying tests properly
- After any change, run the affected test file in isolation first, then the FULL suite
  (`uv run pytest -q`). A test that passes alone but fails in the full run usually means
  cross-file state leaked (e.g. a shared module or class attribute was mutated) — that is a
  real bug to fix, not something to ignore. The baseline is GREEN: `uv run pytest -q` must end
  with `0 failed`. If it doesn't after your change, you broke something.

## Layout
- `backend/lambdas/` — Lambda source   ·   `backend/layers/` — shared layers
- `infra/` — CDK stacks   ·   `frontend/` — Next.js   ·   `tools/ingestion/` — ingestion pipeline
- `config/` — shared config (prompts in `config/model_configs.toml`)

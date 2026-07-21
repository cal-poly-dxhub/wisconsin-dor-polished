# Spec: Retrieval Pipeline Refactor

## Goal

Refactor the agentic retrieval Lambda's tool execution layer from a monolithic 1000+ line function into composable pipeline stages, driven by a single structured config file that serves as the source of truth for all environment variables across the stack.

## Problem

1. **`executor.py`** is a monolith. `vector_search` alone is ~300 lines interleaving: auto-refine, Neptune search, WPAM dedup, diversity cap, authority tiebreak, statute backfill, case-law backfill, broad discovery, citation extraction, and logging. Each "stage" has its own hardcoded constants, env var lookups, try/except, and logging calls mixed in. Adding a new stage means inserting 50 lines into the middle.

2. **Configuration is scattered** across `os.environ.get()` calls inline in executor.py, `config.py`, hardcoded constants, DynamoDB (prompts), and tool definition schemas. There is no single document listing what env vars exist, what they default to, or how they interact with model-controlled params.

3. **Tracing is ad-hoc.** Each stage emits differently-shaped log events with inconsistent field names. Adding observability to a new stage requires knowing the patterns of 3 separate output channels.

## Design

### 1. Structured Config File (`config/retrieval.toml`)

A single TOML file that is:
- **Source of truth** for all Lambda environment variables across the stack
- **Read by CDK** (`infra/`) to set Lambda env vars at deploy time
- **Read by the Lambda** at cold-start to validate env vars + provide defaults
- **Human-readable documentation** of every parameter

#### Schema per entry:

```toml
[env.STATUTE_BACKFILL_SOURCE_GATE]
default = 3
type = "int"
description = "Number of top relevance chunks to use as backfill sources"
range = [0, 10]
stage = "statute_backfill"
model_override = false

[env.DIVERSITY_CAP_PER_DOC]
default = 5
type = "int"
description = "Max chunks per document in vector_search results"
range = [1, 25]
stage = "diversity_cap"
model_override = false

[env.BROAD_DISCOVERY_CAP]
default = 15
type = "int"
description = "Max chunks returned by the broad discovery arm (same pipeline as narrow, additive docs only)"
range = [0, 25]
stage = "broad_discovery"
model_override = false

[env.CASELAW_BACKFILL_CAP]
default = 3
type = "int"
description = "Max ranked case-law holdings surfaced via CITES traversal from statute stubs"
range = [0, 10]
stage = "caselaw_backfill"
model_override = false

[env.FAQ_SCORE_THRESHOLD]
default = 0.70
type = "float"
description = "Minimum FAQ score to trigger high-confidence FAQ steering"
range = [0.0, 1.0]
stage = "faq_seed"
model_override = false

[env.MAX_TURNS]
default = 8
type = "int"
description = "Maximum agentic loop turns before forced prepare_answer"
range = [3, 15]
stage = "loop"
model_override = false

[env.AGENTIC_MODEL_ID]
default = "us.anthropic.claude-sonnet-4-6"
type = "string"
description = "Bedrock model ID for the agentic loop (Phase A)"
stage = "loop"
model_override = false

[env.CASE_LAW_SUMMARY_MODEL]
default = "us.amazon.nova-2-lite-v1:0"
type = "string"
description = "Model used by extract.py to generate case-law holding summaries"
stage = "ingestion"
model_override = false

[env.LOG_TOOL_TRACE]
default = "true"
type = "bool"
description = "Emit granular CloudWatch log events for each tool stage"
stage = "observability"
model_override = false
```

Additionally, document model-controlled params (from tool input schemas):

```toml
[tool_params.vector_search.top_k]
default = 15
max = 25
description = "Number of chunks returned to the model. Model can pass lower values."

[tool_params.get_section.top_k]
default = 5
max = 10
description = "Max chunks returned when query is provided for relevance ranking."

[tool_params.get_neighbors.top_k]
default = 5
max = 10
description = "Max ranked neighbors when query param triggers semantic ranking."
```

#### CDK integration:

```typescript
// infra/lib/retrieval-config.ts
import { parse } from '@iarna/toml';
import { readFileSync } from 'fs';

const config = parse(readFileSync('../config/retrieval.toml', 'utf-8'));

// Extract env vars with defaults for a Lambda
export function getRetrievalEnv(): Record<string, string> {
  const env: Record<string, string> = {};
  for (const [key, entry] of Object.entries(config.env)) {
    env[key] = String(entry.default);
  }
  return env;
}
```

#### Lambda cold-start validation:

```python
# backend/lambdas/agentic_retrieval/config_validator.py
import os, tomllib
from pathlib import Path

def validate_env():
    """Validate env vars against retrieval.toml schema at cold-start."""
    config = tomllib.loads(Path("retrieval.toml").read_text())
    for key, spec in config.get("env", {}).items():
        value = os.environ.get(key)
        if value is None:
            continue  # will use default from spec
        expected_type = spec.get("type", "string")
        range_ = spec.get("range")
        # type check + range check
        ...
```

### 2. Pipeline Stages (`agent_tools/stages/`)

Refactor vector_search into a pipeline of composable stages. Each stage is a function with a standard signature:

```python
# agent_tools/stages/base.py
@dataclass
class StageContext:
    """Shared state flowing through the pipeline."""
    query: str                        # original user query
    refined_query: str                # after auto-refine
    embedding: list[float] | None     # query embedding (computed once, reused)
    chunks: list[dict]                # narrow arm results (accumulates)
    statute_backfill: list[dict]
    caselaw_backfill: list[dict]
    broad_discovery: list[dict]
    related_case_law: list[dict]
    config: dict                      # parsed retrieval.toml env section
    neptune: NeptuneClient
    chat_history: list[dict] | None
    original_user_query: str | None
    timings: dict[str, float]         # stage_name -> latency_ms


@dataclass
class StageResult:
    """What a stage returns."""
    trace: dict | None = None         # CloudWatch log event (standard shape)
```

Each stage:

```python
# agent_tools/stages/auto_refine.py
def run(ctx: StageContext) -> StageResult:
    """Refine the query for retrieval."""
    started = time.perf_counter()
    refined, target_year = _auto_refine(ctx.query, ctx.chat_history)
    ctx.refined_query = refined
    ctx.target_year = target_year
    ctx.timings["auto_refine"] = (time.perf_counter() - started) * 1000
    return StageResult(trace={...})
```

```python
# agent_tools/stages/neptune_search.py
def run(ctx: StageContext) -> StageResult:
    """Embed and search Neptune."""
    ctx.embedding = embed_query(ctx.refined_query)
    fetch_k = ctx.config["top_k"] * 6
    raw = ctx.neptune.vector_search(ctx.embedding, top_k=fetch_k)
    ctx.pre_dedup_count = len(raw)
    ctx.chunks = raw
    ...
```

The pipeline runner:

```python
# agent_tools/pipeline.py
VECTOR_SEARCH_STAGES = [
    stages.auto_refine,
    stages.neptune_search,
    stages.wpam_dedup,
    stages.diversity_cap,
    stages.authority_tiebreak,
    stages.statute_backfill,
    stages.caselaw_backfill,
    stages.broad_discovery,
    stages.citation_extraction,
]

def run_vector_search(query, neptune, chat_history, original_user_query, config):
    ctx = StageContext(query=query, neptune=neptune, ...)
    for stage in VECTOR_SEARCH_STAGES:
        if not _stage_enabled(stage, config):
            continue
        result = stage.run(ctx)
        if result.trace:
            _log_tool_event(result.trace)
    return _build_result(ctx)
```

### 3. Standard Trace Shape

Every stage emits the same CloudWatch event shape:

```json
{
  "component": "graphrag.agentic_retrieval.tools",
  "event": "stage_complete",
  "stage": "statute_backfill",
  "tool": "vector_search",
  "latency_ms": 45,
  "input_count": 3,
  "output_count": 3,
  "details": { ... stage-specific fields ... }
}
```

The frontend trace (WebSocket/DynamoDB) is built by the pipeline runner from the accumulated stage results — not by each stage individually.

### 4. File Structure

```
backend/lambdas/agentic_retrieval/
├── agent_tools/
│   ├── __init__.py
│   ├── executor.py          # thin dispatcher: routes tool_name to pipeline or handler
│   ├── pipeline.py          # pipeline runner (run_vector_search, run_get_neighbors, etc.)
│   ├── definitions.py       # tool schemas (unchanged)
│   └── stages/
│       ├── __init__.py
│       ├── base.py          # StageContext, StageResult dataclasses
│       ├── auto_refine.py
│       ├── neptune_search.py
│       ├── wpam_dedup.py
│       ├── diversity_cap.py
│       ├── authority_tiebreak.py
│       ├── statute_backfill.py
│       ├── caselaw_backfill.py
│       ├── broad_discovery.py
│       └── citation_extraction.py
├── config_validator.py      # cold-start env validation against retrieval.toml
config/
├── retrieval.toml           # THE source of truth
```

## Constraints

- **Don't change behavior.** This is a refactor, not a feature change. The pipeline should produce identical results to the current monolith for any given query. Validate by running the same test queries before/after and diffing results.
- **Keep env vars as the deploy-time knob.** The TOML provides defaults; env vars override. CDK reads the TOML for defaults but operators can still override via `-c` context or manual env var changes.
- **Backward compatible.** If a Lambda doesn't have the TOML bundled (e.g., during a partial deploy), it should fall back to hardcoded defaults gracefully.
- **Don't touch the prompt/DynamoDB config path.** Prompts stay in `model_configs.toml` → DynamoDB. The retrieval.toml is for infra/pipeline config only.
- **Tests must pass before and after.** The existing test suite (201 tests) validates behavior; the refactor should not break any of them. New tests for the pipeline runner and config validator are expected.

## Env Vars to Document (current, incomplete — implementer should audit executor.py for all)

| Env Var | Default | Type | Stage | Model Override |
|---------|---------|------|-------|----------------|
| STATUTE_BACKFILL_SOURCE_GATE | 3 | int | statute_backfill | no |
| STATUTE_BACKFILL_CAP | 3 | int | statute_backfill | no |
| CASELAW_BACKFILL_CAP | 3 | int | caselaw_backfill | no |
| BROAD_DISCOVERY_CAP | 15 | int | broad_discovery | no |
| DIVERSITY_CAP_PER_DOC | 5 | int | diversity_cap | no |
| ENRICH_CAP_PER_DOC | 5 | int | auto_enrichment | no |
| ENRICH_CAP_PER_TYPE | 4 | int | auto_enrichment | no |
| FAQ_KNOWLEDGE_BASE_ID | "" | string | faq_seed | no |
| FAQ_SCORE_THRESHOLD | 0.70 | float | faq_seed | no |
| MAX_TURNS | 8 | int | loop | no |
| AGENTIC_MODEL_ID | us.anthropic.claude-sonnet-4-6 | string | loop | no |
| REFINEMENT_MODEL_ID | (same as AGENTIC_MODEL_ID) | string | auto_refine | no |
| CASE_LAW_SUMMARY_MODEL | us.amazon.nova-2-lite-v1:0 | string | ingestion | no |
| RAW_BUCKET | "" | string | fetch_case_opinion | no |
| LOG_TOOL_TRACE | true | bool | observability | no |
| LOG_QUERY_TEXT | true | bool | observability | no |
| LOG_MAX_TEXT_CHARS | 500 | int | observability | no |
| WEBSOCKET_CALLBACK_URL | "" | string | streaming | no |
| SESSIONS_TABLE_NAME | "" | string | persistence | no |
| MODEL_CONFIG_TABLE_NAME | "" | string | prompt_loading | no |
| NEPTUNE_GRAPH_ID | "" | string | neptune | no |

**Tool-input params (model-controlled):**

| Tool | Param | Default | Max | Notes |
|------|-------|---------|-----|-------|
| vector_search | top_k | 15 | 25 | model can request fewer |
| search_document | top_k | 5 | 10 | |
| get_section | top_k | 5 | 10 | only applies when query is provided |
| get_neighbors | top_k | 5 | 10 | only applies when query triggers ranking |

## Validation

1. Run existing 201 tests before and after — all must pass.
2. Run 3 real queries (the CMA/SCMA, the BOR appeal, the agricultural classification) against both old and new code paths. Diff the `vector_search_complete` CloudWatch events — chunk_ids, backfill counts, and broad_discovery docs must be identical.
3. The CDK `cdk diff` after wiring the TOML should show only env var value changes (from hardcoded in CDK to read-from-TOML), no new resources.

## Out of Scope

- Prompt management (stays in model_configs.toml → DynamoDB)
- Frontend trace rendering changes (the payload shape stays the same; just produced more cleanly)
- Ingestion pipeline refactor (tools/ingestion/ is separate)
- Changing any retrieval behavior or defaults

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Wisconsin DOR Chatbot — a property tax Q&A assistant for the Wisconsin Department of Revenue. NextJS frontend + CDK-managed AWS backend with two retrieval paths: legacy OpenSearch RAG and new GraphRAG (Neptune Analytics).

## Commands

### Build & Deploy
```bash
bun install                    # install all workspace deps
bun run bundle                 # copy Python lambdas to infra/bundle/ (uses bundles.toml)
bun run deploy                 # bundle + cdk deploy (uses --profile and region from env)

# CDK must be run from infra/:
cd infra
AWS_PROFILE=widor AWS_REGION=us-east-1 cdk diff -c useGraphRAG=true -c stackName=WisconsinBotGraphRAG
AWS_PROFILE=widor AWS_REGION=us-east-1 cdk deploy -c useGraphRAG=true -c stackName=WisconsinBotGraphRAG --require-approval never
```

### Testing
```bash
bun run test                   # run all Jest tests (TypeScript)
bun run pytest                 # run all pytest tests (Python, via uv)
uv run pytest tests/ -v        # run Python tests directly
uv run pytest tests/path -k "test_name"  # single Python test
```

### Linting & Formatting
```bash
bunx eslint .                  # TypeScript/JS linting (eslint + prettier)
uv run ruff check .            # Python linting
uv run ruff format .           # Python formatting
```

### Frontend
```bash
cd frontend
bun dev                        # local dev server (Next.js + Turbopack)
```

### GraphRAG Ingestion Pipeline (Fargate — preferred)
```bash
# First-time: deploy infra + build/push Docker image
cd infra
AWS_PROFILE=widor AWS_REGION=us-east-1 cdk deploy -c useGraphRAG=true -c stackName=WisconsinBotGraphRAG --require-approval never
cd ../tools/graphrag
./build_and_push.sh              # builds container image and pushes to ECR

# Run full pipeline (extract → embed → load) on Fargate:
./tools/graphrag/run_full_ingest.sh

# Run a single phase:
./tools/graphrag/run_fargate.sh extract
./tools/graphrag/run_fargate.sh embed
./tools/graphrag/run_fargate.sh load

# Common options:
./tools/graphrag/run_fargate.sh extract --source-filter wpam- --force
./tools/graphrag/run_fargate.sh load --start-phase 5 --stop-after-phase 8

# Monitor logs:
aws logs tail /ecs/wis-dor-ingestion --follow --profile widor --region us-east-1
```

### GraphRAG Ingestion Pipeline (local — alternative)
```bash
# Requires venv with deps: uv venv .venv && uv pip install -r tools/graphrag/requirements.txt
# Always set SSL certs (Python 3.14 on macOS needs this):
export CERT=$(.venv/bin/python3 -c "import certifi; print(certifi.where())")

# Phase 1: Upload local docs to S3
AWS_REGION=us-east-1 AWS_PROFILE=widor .venv/bin/python3 tools/graphrag/upload_local_docs.py \
  --bucket wis-raw-bucket-c8e69250 --profile widor --region us-east-1

# Phase 2: Extract + classify (PyMuPDF first, Textract fallback, LLM classification)
AWS_CA_BUNDLE=$CERT AWS_REGION=us-east-1 AWS_PROFILE=widor .venv/bin/python3 tools/graphrag/extract.py \
  --raw-bucket wis-raw-bucket-c8e69250 --work-bucket wis-work-bucket-c8e69250 \
  --config tools/graphrag/ingest_config.yaml --max-workers 3

# Phase 3: Embed chunks with Titan Embed v2
AWS_CA_BUNDLE=$CERT AWS_REGION=us-east-1 AWS_PROFILE=widor .venv/bin/python3 tools/graphrag/embed.py \
  --work-bucket wis-work-bucket-c8e69250 --config tools/graphrag/ingest_config.yaml

# Phase 4: Load into Neptune graph (11 sub-phases)
AWS_CA_BUNDLE=$CERT AWS_REGION=us-east-1 AWS_PROFILE=widor .venv/bin/python3 tools/graphrag/load.py \
  --work-bucket wis-work-bucket-c8e69250 --graph-id g-ndvl4j73v4 \
  --config tools/graphrag/ingest_config.yaml

# FAQ sync
./tools/graphrag/sync_faq_bucket.sh  # sync FAQ files + trigger KB ingestion
```

## Architecture

### Monorepo Structure

Flat layout: `backend/` (lambdas + layers), `infra/` (CDK stacks), `frontend/` (Next.js app), `tools/` (ingestion scripts), `config/`. Python deps managed by uv (pyproject.toml at root). Lambda bundling is defined in `bundles.toml` — a Python script copies lambda source into `infra/bundle/` before CDK synth.

### Two Retrieval Paths (Mutually Exclusive)

Controlled by CDK context flag `useGraphRAG`. EventBridge rule `wisconsin-dor.chat-api:ChatMessageReceived` routes to one path:

**Legacy path** (`useGraphRAG=false`): EventBridge → `MessagesStack` Step Function → Classifier Lambda (queries Bedrock FAQ KB, classifies as faq/rag) → branch to either FAQ response or RAG retrieval (OpenSearch) → Parallel(ResourceStreaming, ResponseStreaming)

**GraphRAG path** (`useGraphRAG=true`): EventBridge → AgenticRetrieval Lambda directly (no Step Function). The Lambda runs the Claude tool loop (faq_search → Neptune vector_search/get_neighbors/get_authority_chain → answer), then streams documents, FAQs, and answer fragments over WebSocket itself. Single Lambda, single DynamoDB write.

The legacy path still uses ResponseStreaming and ResourceStreaming Lambdas from `MessagesStack`.

### Directory Responsibilities

- **infra/** — CDK stacks (root stack `WisconsinBotStack` in `stacks/stack.ts`). Entry point: `infra/bin/wisconsin-app.ts`
  - `stacks/sessions-stack.ts` — Cognito auth, HTTP API, WebSocket API, DynamoDB sessions/chat history
  - `stacks/messages-stack.ts` — Legacy retrieval: classifier, retrieval, streaming Lambdas + Step Function
  - `stacks/graphrag-stack.ts` — Neptune Analytics graph, Bedrock FAQ KB
  - `stacks/graphrag-messages-stack.ts` — Agentic retrieval Lambda + EventBridge trigger
  - `stacks/webapp-stack.ts` — Next.js frontend (deployed via CloudFront + `cdk-nextjs-standalone`)
  - `stacks/ingestion-stack.ts` — Fargate compute for ingestion (VPC, ECS cluster, task def, ECR)
  - `stacks/lambda-layers-stack.ts` — Shared Lambda layers
- **backend/lambdas/** — Lambda source code (agentic_retrieval, chat_api, streaming, etc.)
- **backend/layers/** — Lambda layers: `step_function_types` (Pydantic models) and `websocket_utils`
- **frontend/** — Next.js frontend app
- **tools/graphrag/** — GraphRAG ingestion pipeline scripts
- **tools/pdf_chunking/** — PDF extraction and chunking utilities
- **config/** — Shared configuration (model configs, etc.)

### Shared Types (Critical)

`backend/layers/step_function_types/models.py` defines all inter-Lambda contracts: `UserQuery`, `ClassifierResult`, `RetrieveResult`, `GenerateResponseJob`, `StreamResourcesJob`, `FAQ`, `RAGDocument`. All Lambdas import from this layer. Changes here affect the entire pipeline.

### WebSocket Streaming

Responses stream to the frontend via API Gateway WebSocket. The `websocket_utils` layer provides connection management. Lambdas need `WEBSOCKET_CALLBACK_URL` and `SESSIONS_TABLE_NAME` env vars.

### GraphRAG Data Model

Neptune Analytics graph (`g-ndvl4j73v4` in us-east-1) with 1024-dim vectors and IAM auth.

**Node types:** Framework → Document → Chunk (with vector embeddings), Topic nodes for semantic grouping.

**Authority hierarchy (9 levels, by legal precedence):**
Constitution (1) → Statutes (2) → Case Law (3) → Admin Rules (4) → WPAM (5) → FAQs (6) → Gov Pubs (7) → IAAO (8) → USPAP (9)

**Edge types:**
- Authority: `CITES` (Doc→Statute, Doc→AdminRule, Statute→CaseLaw mirror, Chunk→Statute, Chunk→AdminRule), `IMPLEMENTS` (Doc→Statute)
- Hierarchy: `PART_OF` (Section→Chapter, Subsection→Section), `BELONGS_TO` (Doc→Framework), `HAS_SUBSECTION` (Doc→Doc multi-part), `EXTRACTED_FROM` (Chunk→Doc), `DERIVED_FROM` (Framework→Framework, e.g., IAAO→WPAM)
- Topical: `COVERS_TOPIC` (Doc→Topic)
- Semantic (LLM-classified, phase 11): `RELATED_TO`, `SUPPLEMENTS`, `SUPERSEDES`, `CONFLICTS_WITH`

**S3 bucket structure:** `raw/{category}-{clean-name}/{category}-{clean-name}.pdf` + `.metadata.json`

**Ingestion config:** `tools/graphrag/ingest_config.yaml` — defines frameworks, doc types, chunking params, source-to-framework mappings.

### PDF Processing Pipeline (`tools/pdf_chunking/`)

PyMuPDF-first extraction with Textract fallback. `pdfChunker.py` routes by source type (`CHUNKER_BY_SOURCE` dict) to strategy-specific chunking (statute, wpam, general). Each chunk gets `start_page`/`end_page` metadata for citation linking. Quality gate in `pymupdf_extractor.py` (`extraction_looks_good()`) triggers Textract fallback.

### Citation Support

Chunks carry `s3_key`, `start_page`, `end_page` metadata through the full pipeline. At query time, `backend/lambdas/agentic_retrieval/main.py` generates presigned S3 URLs with `#page=N` fragments so users get direct links to specific PDF pages. Case law is metadata stubs with Google Scholar links (no full opinion text).

## Key Conventions

- **Python Lambdas use Pydantic v2** for input validation and serialization. Models use `BaseModel` with `model_validate()` / `model_dump()`.
- **CamelCase serialization** — `CamelCaseModel` base class in shared types converts snake_case Python to camelCase JSON via alias generator.
- **Lambda bundling** — Python deps are installed during CDK synth via Docker bundling (pip install in bundling image). Each Lambda in `backend/lambdas/` has its own `requirements.txt`.
- **CDK context flags** — `useGraphRAG`, `stackName`, `domainName`, `hostedZoneName`, `hostedZoneId` are passed via `-c` flag.
- **Embedding model** — Titan Embed Text V2 (1024 dimensions) used throughout for both Bedrock KBs and Neptune vector search.
- **Bedrock model IDs** — Inference profiles require the full format: `us.anthropic.claude-sonnet-4-6` (not bare model IDs or old `-v1:0` suffix forms). Check `aws bedrock list-inference-profiles` for valid IDs.
- **Region in scripts** — `tools/graphrag/*.py` use `os.environ.get("AWS_REGION", "us-east-1")` for boto3 clients. Always set `AWS_REGION` explicitly when running locally.
- **SSL certs on macOS** — Set `AWS_CA_BUNDLE` to the certifi cert path when running ingestion scripts. Without this, Python 3.13+/3.14 may fail with `SSLError: [Errno 2] No such file or directory` after ~200 S3 calls.

## WebSocket Contract

Any change to messages sent over WebSocket (adding fields, changing `responseType` values, adding new message types, modifying trace/logging payloads) **must** update both sides:

1. **Backend** — Python models in `backend/layers/websocket_utils/models.py` and the `send_json` router in `backend/layers/websocket_utils/utils.py`
2. **Frontend** — Zod schemas in `frontend/types/message-types.ts` (the `MessageUnionSchema` discriminated union and `WebSocketMessageSchema`)
3. **Handler** — The `messageHandler` switch in `frontend/src/hooks/use-websocket-chat.ts`

The frontend validates every WebSocket message via `WebSocketMessageSchema.parse()`. If the backend sends a `responseType` or shape that isn't in the Zod union, the message is rejected and an error is shown to the user. This applies to trace/logging messages too — they flow through the same validated WebSocket path.

## Chat History & Activity Data (DynamoDB)

The **ChatHistoryTable** stores every user query and bot response. When the user asks for recent queries, chat responses, or activity data, query DynamoDB directly rather than parsing CloudWatch logs — DynamoDB has the complete structured data.

**Table name:** `WisconsinBotGraphRAG-WisconsinSessionsStackNestedStackWisconsinSessionsStackNestedStac-1P3H46X50M51H-ChatHistoryTableA22BA13C-GTH2UH9SGD0W`

**Schema:**
- Partition key: `queryId` (String, UUID)
- GSI `sessionIdKey`: partition `sessionId`, sort `timestamp`
- Attributes: `queryId`, `sessionId`, `query`, `answer`, `timestamp` (ISO 8601), `resources`, `faqs`, `documents`
- Feedback attributes (set via `POST /session/{id}/feedback`): `thumbUp` (BOOL), `feedback` (String)

**CLI examples:**
```bash
# Get all items (full scan):
AWS_PROFILE=widor AWS_REGION=us-east-1 aws dynamodb scan \
  --table-name "WisconsinBotGraphRAG-WisconsinSessionsStackNestedStackWisconsinSessionsStackNestedStac-1P3H46X50M51H-ChatHistoryTableA22BA13C-GTH2UH9SGD0W" \
  --output json > /tmp/chat_history.json

# Filter to recent items (e.g. last week):
AWS_PROFILE=widor AWS_REGION=us-east-1 aws dynamodb scan \
  --table-name "WisconsinBotGraphRAG-WisconsinSessionsStackNestedStackWisconsinSessionsStackNestedStac-1P3H46X50M51H-ChatHistoryTableA22BA13C-GTH2UH9SGD0W" \
  --filter-expression "#ts >= :cutoff" \
  --expression-attribute-names '{"#ts": "timestamp"}' \
  --expression-attribute-values '{":cutoff": {"S": "2026-06-14"}}' \
  --output json

# Get items with thumbs-down feedback:
AWS_PROFILE=widor AWS_REGION=us-east-1 aws dynamodb scan \
  --table-name "WisconsinBotGraphRAG-WisconsinSessionsStackNestedStackWisconsinSessionsStackNestedStac-1P3H46X50M51H-ChatHistoryTableA22BA13C-GTH2UH9SGD0W" \
  --filter-expression "thumbUp = :val" \
  --expression-attribute-values '{":val": {"BOOL": false}}' \
  --output json
```

**Admin dashboard:** `/admin/activity` route in the frontend (dev-only, Cognito-gated). Fetches all items via `GET /admin/activity` API endpoint, caches locally for 1 hour with manual sync override. Supports filtering by time range, feedback status, and text search.

**When user asks about recent queries or chat activity:** Prefer DynamoDB scan over CloudWatch logs — it returns structured data with the full question, answer, feedback, and timestamps. Offer to use the admin API endpoint or direct CLI scan depending on context.

## Prompt Management

All LLM prompts are externalized to `config/model_configs.toml` and loaded from DynamoDB at Lambda cold-start. The TOML is the source of truth; DynamoDB is the runtime store.

**Entries:** `agenticRetrieval` (agentic system prompt), `ragResponse` (legacy RAG generation), `faqResponse` (FAQ synthesis).

**Iteration workflow:**
```bash
# Edit the prompt in the TOML, then push to DynamoDB without a full deploy:
AWS_PROFILE=widor AWS_REGION=us-east-1 python tools/upload_model_configs.py --only agenticRetrieval
```

**Convention:** When committing changes to `config/model_configs.toml`, always run `tools/upload_model_configs.py` afterward so DynamoDB matches git. `cdk deploy` does NOT write prompt content to DynamoDB — only the upload script does. `cdk deploy` is only needed when the infra itself changes (new env vars, table permissions, etc.).

## Deployment

- **us-east-1** — GraphRAG production stack (`WisconsinBotGraphRAG`). All GraphRAG development deploys here.
- Always use `--profile widor` for AWS commands.
- Run `cdk diff` before every deploy to verify only additive changes.

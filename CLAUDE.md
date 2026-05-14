# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Wisconsin DOR Chatbot — a property tax Q&A assistant for the Wisconsin Department of Revenue. NextJS frontend + CDK-managed AWS backend with two retrieval paths: legacy OpenSearch RAG and new GraphRAG (Neptune Analytics).

## Commands

### Build & Deploy
```bash
bun install                    # install all workspace deps
bun run bundle                 # copy Python lambdas to packages/infra/bundle/ (uses bundles.toml)
bun run deploy                 # bundle + cdk deploy (uses --profile and region from env)

# CDK must be run from packages/infra/:
cd packages/infra
AWS_PROFILE=wisco AWS_REGION=us-east-1 cdk diff -c useGraphRAG=true -c stackName=WisconsinBotGraphRAG
AWS_PROFILE=wisco AWS_REGION=us-east-1 cdk deploy -c useGraphRAG=true -c stackName=WisconsinBotGraphRAG --require-approval never
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
cd packages/webapp
bun dev                        # local dev server (Next.js + Turbopack)
```

### GraphRAG Ingestion Pipeline
```bash
# Requires venv with deps: uv venv .venv && uv pip install -r scripts/graphrag/requirements.txt
# Always set SSL certs (Python 3.14 on macOS needs this):
export CERT=$(.venv/bin/python3 -c "import certifi; print(certifi.where())")

# Phase 1: Upload local docs to S3
AWS_REGION=us-east-1 AWS_PROFILE=wisco .venv/bin/python3 scripts/graphrag/upload_local_docs.py \
  --bucket wis-raw-bucket-c8e69250 --profile wisco --region us-east-1

# Phase 2: Extract + classify (PyMuPDF first, Textract fallback, LLM classification)
AWS_CA_BUNDLE=$CERT AWS_REGION=us-east-1 AWS_PROFILE=wisco .venv/bin/python3 scripts/graphrag/extract.py \
  --raw-bucket wis-raw-bucket-c8e69250 --work-bucket wis-work-bucket-c8e69250 \
  --config scripts/graphrag/ingest_config.yaml --max-workers 3

# Phase 3: Embed chunks with Titan Embed v2
AWS_CA_BUNDLE=$CERT AWS_REGION=us-east-1 AWS_PROFILE=wisco .venv/bin/python3 scripts/graphrag/embed.py \
  --work-bucket wis-work-bucket-c8e69250 --config scripts/graphrag/ingest_config.yaml

# Phase 4: Load into Neptune graph (11 sub-phases)
AWS_CA_BUNDLE=$CERT AWS_REGION=us-east-1 AWS_PROFILE=wisco .venv/bin/python3 scripts/graphrag/load.py \
  --work-bucket wis-work-bucket-c8e69250 --graph-id g-ndvl4j73v4 \
  --config scripts/graphrag/ingest_config.yaml

# FAQ sync
./scripts/graphrag/sync_faq_bucket.sh  # sync FAQ files + trigger KB ingestion
```

## Architecture

### Monorepo Structure

Bun workspaces with 5 packages under `packages/`. Python deps managed by uv (pyproject.toml at root). Lambda bundling is defined in `bundles.toml` — a Python script copies lambda source into `packages/infra/bundle/` before CDK synth.

### Two Retrieval Paths (Mutually Exclusive)

Controlled by CDK context flag `useGraphRAG`. EventBridge rule `wisconsin-dor.chat-api:ChatMessageReceived` routes to one path:

**Legacy path** (`useGraphRAG=false`): EventBridge → `MessagesStack` Step Function → Classifier Lambda (queries Bedrock FAQ KB, classifies as faq/rag) → branch to either FAQ response or RAG retrieval (OpenSearch) → Parallel(ResourceStreaming, ResponseStreaming)

**GraphRAG path** (`useGraphRAG=true`): EventBridge → `GraphRAGMessagesStack` Step Function → AgenticRetrieval Lambda (Claude tool loop: faq_search → Neptune vector_search/get_neighbors/get_authority_chain → answer) → Parallel(ResourceStreaming, ResponseStreaming)

Both paths share the same ResponseStreaming and ResourceStreaming Lambdas from `MessagesStack`.

### Package Responsibilities

- **infra** — Root CDK stack (`WisconsinBotStack`), instantiates all nested stacks. Entry point: `bin/wisconsin-app.ts`
- **sessions** — Cognito auth, HTTP API, WebSocket API, DynamoDB sessions/chat history
- **messages** — Legacy retrieval: classifier, retrieval, streaming Lambdas + Step Function
- **knowledge-base** — Bedrock Knowledge Bases (FAQ + RAG) backed by OpenSearch Serverless
- **graphrag** — Neptune Analytics graph, Bedrock FAQ KB, agentic retrieval Lambda + Step Function
- **webapp** — Next.js frontend (deployed via CloudFront + `cdk-nextjs-standalone`)
- **shared** — Lambda layers: `step_function_types` (Pydantic models) and `websocket_utils`

### Shared Types (Critical)

`packages/shared/lambda_layers/step_function_types/models.py` defines all inter-Lambda contracts: `UserQuery`, `ClassifierResult`, `RetrieveResult`, `GenerateResponseJob`, `StreamResourcesJob`, `FAQ`, `RAGDocument`. All Lambdas import from this layer. Changes here affect the entire pipeline.

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

**Ingestion config:** `scripts/graphrag/ingest_config.yaml` — defines frameworks, doc types, chunking params, source-to-framework mappings.

### PDF Processing Pipeline (`pdf_chunking/`)

PyMuPDF-first extraction with Textract fallback. `pdfChunker.py` routes by source type (`CHUNKER_BY_SOURCE` dict) to strategy-specific chunking (statute, wpam, general). Each chunk gets `start_page`/`end_page` metadata for citation linking. Quality gate in `pymupdf_extractor.py` (`extraction_looks_good()`) triggers Textract fallback.

### Citation Support

Chunks carry `s3_key`, `start_page`, `end_page` metadata through the full pipeline. At query time, `agentic_retrieval/main.py` generates presigned S3 URLs with `#page=N` fragments so users get direct links to specific PDF pages. Case law is metadata stubs with Google Scholar links (no full opinion text).

## Key Conventions

- **Python Lambdas use Pydantic v2** for input validation and serialization. Models use `BaseModel` with `model_validate()` / `model_dump()`.
- **CamelCase serialization** — `CamelCaseModel` base class in shared types converts snake_case Python to camelCase JSON via alias generator.
- **Lambda bundling** — Python deps are installed during CDK synth via Docker bundling (pip install in bundling image). Each Lambda has its own `requirements.txt`.
- **CDK context flags** — `useGraphRAG`, `stackName`, `domainName`, `hostedZoneName`, `hostedZoneId` are passed via `-c` flag.
- **Embedding model** — Titan Embed Text V2 (1024 dimensions) used throughout for both Bedrock KBs and Neptune vector search.
- **Bedrock model IDs** — Inference profiles require the full format: `us.anthropic.claude-sonnet-4-6` (not bare model IDs or old `-v1:0` suffix forms). Check `aws bedrock list-inference-profiles` for valid IDs.
- **Region in scripts** — `scripts/graphrag/*.py` use `os.environ.get("AWS_REGION", "us-east-1")` for boto3 clients. Always set `AWS_REGION` explicitly when running locally.
- **SSL certs on macOS** — Set `AWS_CA_BUNDLE` to the certifi cert path when running ingestion scripts. Without this, Python 3.13+/3.14 may fail with `SSLError: [Errno 2] No such file or directory` after ~200 S3 calls.

## WebSocket Contract

Any change to messages sent over WebSocket (adding fields, changing `responseType` values, adding new message types, modifying trace/logging payloads) **must** update both sides:

1. **Backend** — Python models in `packages/shared/lambda_layers/websocket_utils/models.py` and the `send_json` router in `utils.py`
2. **Frontend** — Zod schemas in `packages/messages/types/message-types.ts` (the `MessageUnionSchema` discriminated union and `WebSocketMessageSchema`)
3. **Handler** — The `messageHandler` switch in `packages/webapp/src/hooks/use-websocket-chat.ts`

The frontend validates every WebSocket message via `WebSocketMessageSchema.parse()`. If the backend sends a `responseType` or shape that isn't in the Zod union, the message is rejected and an error is shown to the user. This applies to trace/logging messages too — they flow through the same validated WebSocket path.

## Deployment

- **us-west-2** — Production stack. Do not deploy from feature branches.
- **us-east-1** — GraphRAG test stack (`WisconsinBotGraphRAG`). All GraphRAG development deploys here.
- Always use `--profile wisco` for AWS commands.
- Run `cdk diff` before every deploy to verify only additive changes.

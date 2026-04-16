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

### GraphRAG Scripts
```bash
uv run scripts/graphrag/embed.py       # embed documents for Neptune
uv run scripts/graphrag/load.py        # load graph into Neptune
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

## Key Conventions

- **Python Lambdas use Pydantic v2** for input validation and serialization. Models use `BaseModel` with `model_validate()` / `model_dump()`.
- **CamelCase serialization** — `CamelCaseModel` base class in shared types converts snake_case Python to camelCase JSON via alias generator.
- **Lambda bundling** — Python deps are installed during CDK synth via Docker bundling (pip install in bundling image). Each Lambda has its own `requirements.txt`.
- **CDK context flags** — `useGraphRAG`, `stackName`, `domainName`, `hostedZoneName`, `hostedZoneId` are passed via `-c` flag.
- **Embedding model** — Titan Embed Text V2 (1024 dimensions) used throughout for both Bedrock KBs and Neptune vector search.

## Deployment

- **us-west-2** — Production stack. Do not deploy from feature branches.
- **us-east-1** — GraphRAG test stack (`WisconsinBotGraphRAG`). All GraphRAG development deploys here.
- Always use `--profile wisco` for AWS commands.
- Run `cdk diff` before every deploy to verify only additive changes.

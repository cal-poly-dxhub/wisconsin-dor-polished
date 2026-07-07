# Collaboration

Thanks for your interest in our solution. Having specific examples of replication and cloning allows us to continue to grow and scale our work. If you clone or download this repository, kindly shoot us a quick email to let us know you are interested in this work!

[wwps-cic@amazon.com]

---

# Disclaimers 

Customers are responsible for making their own independent assessment of the information in this document. 

This document: 

(a) is for informational purposes only, 

(b) references AWS product offerings and practices, which are subject to change without notice, 

(c) does not create any commitments or assurances from AWS and its affiliates, suppliers or licensors. AWS products or services are provided "as is" without warranties, representations, or conditions of any kind, whether express or implied. The responsibilities and liabilities of AWS to its customers are controlled by AWS agreements, and this document is not part of, nor does it modify, any agreement between AWS and its customers, and 

(d) is not to be considered a recommendation or viewpoint of AWS. 

Additionally, you are solely responsible for testing, security and optimizing all code and assets on GitHub repo, and all such code and assets should be considered: 

(a) as-is and without warranties or representations of any kind, 

(b) not suitable for production environments, or on production or other critical data, and 

(c) to include shortcuts in order to support rapid prototyping such as, but not limited to, relaxed authentication and authorization and a lack of strict adherence to security best practices. 

All work produced is open source. More information can be found in the GitHub repo.
# Wisconsin DOR Chatbot

A property tax Q&A assistant for the Wisconsin Department of Revenue (DOR). Users ask natural-language questions about property assessment, Wisconsin statutes, administrative rules, and DOR policy — the system retrieves authoritative sources from a knowledge graph and streams cited answers in real time.

## Architecture

```
┌──────────────┐  WSS  ┌────────────────────────────────────────────────────┐
│  Next.js     │──────▶│  AWS (CDK-managed)                                  │
│  Frontend    │       │  Cognito ─▶ API Gateway (HTTP + WebSocket)          │
│  (CloudFront)│       │       │                                             │
└──────────────┘       │       ▼                                             │
                       │  EventBridge (ChatMessageReceived)                  │
                       │       │                                             │
                       │       ▼                                             │
                       │  ┌──────────────────────┐    ┌───────────────────┐ │
                       │  │ Agentic Retrieval    │───▶│ Neptune Analytics │ │
                       │  │ Lambda (Claude loop) │    │ (Knowledge Graph) │ │
                       │  └──────────────────────┘    └───────────────────┘ │
                       │       │                                             │
                       │       ▼  streams answer + documents + FAQs          │
                       │  WebSocket ─▶ Client                                │
                       └────────────────────────────────────────────────────┘
```

### How a Query Flows

1. User sends a message via WebSocket (authenticated by Cognito)
2. A `ChatMessageReceived` event is published to EventBridge
3. EventBridge triggers the **Agentic Retrieval Lambda** directly (no Step Function)
4. The Lambda runs a Claude tool-use loop:
   - Searches a Bedrock FAQ Knowledge Base (`faq_search`) for quick-answer matches
   - Calls Neptune graph tools (`vector_search`, `get_neighbors`, `get_authority_chain`) to find
     relevant document chunks and their legal context
   - Claude decides when it has enough evidence and produces a cited answer
5. The same Lambda streams the answer, source documents, and FAQs back to the client over WebSocket
   (single Lambda, single DynamoDB write)

### Key Components

| Component | Description |
|-----------|-------------|
| **Frontend** (`frontend/`) | Next.js app served via CloudFront. Real-time streaming via WebSocket, Cognito auth, Zustand state management. |
| **Agentic Retrieval** (`backend/lambdas/agentic_retrieval/`) | Core retrieval engine — a Claude tool-use loop backed by Neptune Analytics vector search and graph traversal. Also streams the answer, documents, and FAQs to the frontend over WebSocket. |
| **Chat API** (`backend/lambdas/chat_api/`) | REST endpoint that initiates a chat and publishes the `ChatMessageReceived` event to EventBridge. |
| **WebSocket Handlers** (`backend/lambdas/websocket/`) | Connect / disconnect / default WebSocket route handlers. |
| **Neptune Analytics** | Knowledge graph storing documents, chunks (with 1024-dim vector embeddings), topics, and a 9-level authority hierarchy (Constitution → Statutes → Case Law → Admin Rules → WPAM → FAQs → Gov Pubs → IAAO → USPAP). |
| **Sessions** (`infra/stacks/sessions-stack.ts`) | Cognito user pool, HTTP API, WebSocket API, DynamoDB sessions + chat history. |
| **Infrastructure** (`infra/`) | CDK stacks. Entry point: `infra/bin/wisconsin-app.ts`. |

## Project Layout

```
├── backend/
│   ├── lambdas/          # Python Lambda functions
│   │   ├── agentic_retrieval/   # Claude tool-use loop + WebSocket streaming
│   │   ├── chat_api/            # REST chat initiation → EventBridge
│   │   └── websocket/           # Connect/disconnect/default handlers
│   └── layers/           # Shared Lambda layers
│       ├── step_function_types/ # Pydantic models for inter-Lambda contracts
│       └── websocket_utils/     # WebSocket connection management + message models
├── frontend/             # Next.js application (CloudFront-served)
├── infra/                # CDK stacks (TypeScript)
│   ├── bin/              # CDK app entry point (wisconsin-app.ts)
│   └── stacks/           # sessions, graphrag, graphrag-messages, webapp,
│                         #   ingestion, lambda-layers, cloudwatch-iam
├── tools/                # Ingestion pipeline + build tooling
│   ├── ingestion/        # scrape, extract, embed, load, case law, config, Docker
│   ├── bundle.py         # Lambda bundling script
│   └── upload_model_configs.py  # push prompts to DynamoDB
├── pdf_chunking/         # PDF extraction + chunking library (used by ingestion)
├── config/               # Shared config (model prompts, inference params)
├── archive/              # Retired scripts + seed data (pre-GraphRAG)
├── docs/                 # Engineering documentation
└── bundles.toml          # Lambda bundling manifest
```

## Development

### Prerequisites

- [Bun](https://bun.sh) >= 1.2.19
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- Docker >= 27.4.0
- AWS CDK CLI (`bun add -g aws-cdk`)
- AWS account with CDK bootstrapped

### Install

```bash
bun install        # TypeScript/JS deps
uv sync            # Python deps (from pyproject.toml)
```

### Run Locally

```bash
cd frontend
bun dev            # Next.js dev server with Turbopack
```

### Test

```bash
bun run test       # Jest (TypeScript)
bun run pytest     # pytest (Python, via uv)
```

### Lint

```bash
bunx eslint .      # TypeScript/JS
uv run ruff check . && uv run ruff format .  # Python
```

### Deploy

```bash
bun run deploy     # bundles lambdas + cdk deploy
```

For GraphRAG-specific deploys:
```bash
cd infra
AWS_PROFILE=<profile> AWS_REGION=us-east-1 cdk diff -c stackName=WisconsinBotGraphRAG
AWS_PROFILE=<profile> AWS_REGION=us-east-1 cdk deploy -c stackName=WisconsinBotGraphRAG --require-approval never
```

### First-Time Setup

After initial deploy, run:
```bash
bun run first-time   # uploads model configs + syncs FAQ data
```

#### Local Frontend Environment

Copy `frontend/.env.example` to `frontend/.env.local` and populate from CDK outputs:

```
WisconsinBotStack.ApiBaseUrl        -> NEXT_PUBLIC_API_BASE_URL
WisconsinBotStack.WebSocketUrl      -> NEXT_PUBLIC_WEBSOCKET_URL
WisconsinBotStack.CognitoUserPoolId -> NEXT_PUBLIC_USER_POOL_ID
WisconsinBotStack.CognitoUserPoolClientId -> NEXT_PUBLIC_USER_POOL_CLIENT_ID
```

## GraphRAG Knowledge Graph

The Neptune Analytics graph stores the full Wisconsin property tax knowledge base:

- **Node types:** Framework, Document, Chunk (with vector embeddings), Topic
- **Authority hierarchy:** 9 levels of legal precedence from Constitution down to USPAP standards
- **Edge types:** `CITES`, `IMPLEMENTS`, `PART_OF`, `BELONGS_TO`, `EXTRACTED_FROM`, `COVERS_TOPIC`, `RELATED_TO`, `SUPPLEMENTS`, `SUPERSEDES`, `CONFLICTS_WITH`
- **Embeddings:** Amazon Titan Embed Text V2 (1024 dimensions)

Documents are ingested via a multi-phase pipeline (see `CLAUDE.md` for full ingestion commands):
1. Upload PDFs to S3
2. Extract + classify (PyMuPDF with Textract fallback)
3. Embed chunks with Titan
4. Load into Neptune graph (11 sub-phases including semantic relationship classification)



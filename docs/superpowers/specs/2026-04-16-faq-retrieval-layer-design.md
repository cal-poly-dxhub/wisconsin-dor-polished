# FAQ-First Retrieval Layer for GraphRAG

## Problem

The GraphRAG agentic retrieval path always runs the full Claude tool loop against Neptune, even for questions that have pre-written FAQ answers. This wastes tokens and adds latency for common questions. We need a fast path that checks FAQ content first and only falls through to graph retrieval when FAQs don't adequately answer the question.

## Solution

Add a `faq_search` tool to the existing agentic retrieval Lambda. The Bedrock Agent (Claude) queries a FAQ Knowledge Base first, evaluates whether the results adequately answer the user's question, and either returns immediately or continues with Neptune graph tools.

## Infrastructure

### New Resources (in `GraphRAGStack`, us-east-1)

1. **S3 Bucket** (`wis-faq-bucket-graphrag-{uid}`)
   - Holds copies of the 647 FAQ text files from the source bucket (`wis-faq-bucket` in us-west-2)
   - `removalPolicy: RETAIN` to preserve FAQ data

2. **Bedrock Knowledge Base** (`wis-faq-graphrag`)
   - Embedding model: Titan Embed Text V2 (1024 dimensions)
   - Backed by auto-provisioned OpenSearch Serverless (via `@cdklabs/generative-ai-cdk-constructs`)
   - Instruction: "Use this knowledge base to answer frequently asked questions about Wisconsin DOR property assessment and taxation."

3. **S3 Data Source**
   - Points the KB at the new FAQ bucket
   - Chunking strategy: `NONE` (each file is a single Q&A pair)

### FAQ File Sync

A one-time shell script:
1. Copies files from `wis-faq-bucket` (us-west-2) to the new bucket in us-east-1 via `aws s3 sync`
2. Looks up the KB ID and data source ID from CloudFormation stack outputs
3. Triggers `bedrock-agent:StartIngestionJob` to build embeddings

### Props & Environment Variables

- `GraphRAGStack` exposes `faqKnowledgeBaseId` and `faqBucketName`
- `GraphRAGMessagesStack` receives these as props
- Agentic retrieval Lambda gets `FAQ_KNOWLEDGE_BASE_ID` env var

### IAM

The agentic retrieval Lambda gets `bedrock-agent-runtime:Retrieve` permission scoped to the FAQ KB ARN.

## Agent Tool: `faq_search`

### Tool Definition

```python
{
    "toolSpec": {
        "name": "faq_search",
        "description": "Search frequently asked questions about Wisconsin DOR property taxation. Returns Q&A pairs ranked by relevance. Always try this FIRST before other tools -- if a FAQ adequately answers the question, use it directly.",
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query"
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Number of results (default: 5, max: 10)",
                        "default": 5
                    }
                },
                "required": ["query"]
            }
        }
    }
}
```

### Tool Executor

Calls `bedrock-agent-runtime:Retrieve` with the FAQ Knowledge Base ID. Returns FAQ text content with relevance scores.

### System Prompt Update

The system prompt instructs Claude to:
1. Always try `faq_search` first before `vector_search`
2. If FAQ results adequately answer the question, call `answer` immediately
3. If FAQs are insufficient or irrelevant, proceed with Neptune graph tools
4. The agent may combine FAQ and graph results if FAQs partially answer the question

## End-to-End Flow

```
EventBridge (ChatMessageReceived)
  -> GraphRAG Step Function
    -> AgenticRetrievalFunction
      |
      |  Claude agentic loop:
      |  1. Receives user question
      |  2. Calls faq_search(query) -> Bedrock KB Retrieve
      |  3. Evaluates FAQ results:
      |     - FAQ adequate -> answer(response, cited_doc_ids) -> done
      |     - FAQ insufficient or irrelevant -> continue
      |  4. Calls vector_search, get_neighbors, etc. (Neptune graph tools)
      |  5. answer(response, cited_doc_ids) -> done
      |
      <- RetrieveResult { generate_response_job, stream_documents_job }
    -> Parallel
      +-- ResourceStreaming (sends citations via WebSocket)
      +-- ResponseStreaming (streams answer via WebSocket)
```

No new Step Function states are required. The FAQ check happens entirely within the existing agentic retrieval Lambda.

## Files Modified

| File | Change |
|------|--------|
| `packages/graphrag/infra/graphrag-stack.ts` | Add FAQ S3 bucket, Bedrock KB, S3 data source; expose `faqKnowledgeBaseId` and `faqBucketName` |
| `packages/graphrag/infra/graphrag-messages-stack.ts` | Accept FAQ KB ID prop, pass as env var, add IAM for `bedrock-agent-runtime:Retrieve` |
| `packages/graphrag/lambdas/agentic_retrieval/tools.py` | Add `faq_search` tool definition and executor |
| `packages/graphrag/lambdas/agentic_retrieval/main.py` | Update system prompt to prioritize FAQ search |

## Files Created

| File | Purpose |
|------|---------|
| `scripts/graphrag/sync_faq_bucket.sh` | One-time script: copies 647 FAQ files from us-west-2 source bucket to us-east-1 GraphRAG bucket, triggers KB sync |

## Not In Scope

- Changes to the old messages stack / classifier path
- Changes to the production us-west-2 stack
- S3 replication rules (one-time copy is sufficient)
- Changes to `step_function_types/models.py`
- Changes to ResourceStreaming or ResponseStreaming Lambdas

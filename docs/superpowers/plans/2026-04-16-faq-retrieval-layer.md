# FAQ-First Retrieval Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `faq_search` tool to the GraphRAG agentic retrieval Lambda so the agent checks FAQ answers before falling through to Neptune graph retrieval.

**Architecture:** A new Bedrock Knowledge Base (OpenSearch Serverless-backed) in the GraphRAG stack holds 647 FAQ Q&A pairs. The agentic retrieval Lambda gets a new `faq_search` tool that calls `bedrock-agent-runtime:Retrieve`. The system prompt instructs Claude to try FAQs first and only proceed to graph tools when FAQs are insufficient.

**Tech Stack:** AWS CDK (TypeScript), `@cdklabs/generative-ai-cdk-constructs` for Bedrock KB, Python 3.12 Lambda, boto3 `bedrock-agent-runtime` client.

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `packages/graphrag/infra/graphrag-stack.ts` | Modify | Add FAQ S3 bucket, Bedrock KB, S3 data source; expose new props |
| `packages/graphrag/infra/graphrag-messages-stack.ts` | Modify | Accept FAQ KB ID, pass as env var, add IAM permission |
| `packages/graphrag/package.json` | Modify | Add `@cdklabs/generative-ai-cdk-constructs` dependency |
| `packages/graphrag/lambdas/agentic_retrieval/tools.py` | Modify | Add `faq_search` tool definition and executor |
| `packages/graphrag/lambdas/agentic_retrieval/main.py` | Modify | Update system prompt to prioritize FAQ search |
| `packages/infra/lib/stack.ts` | Modify | Pass new FAQ KB props through to GraphRAGMessagesStack |
| `scripts/graphrag/sync_faq_bucket.sh` | Create | One-time FAQ copy + KB sync script |

---

### Task 1: Add FAQ Infrastructure to GraphRAGStack

**Files:**
- Modify: `packages/graphrag/package.json`
- Modify: `packages/graphrag/infra/graphrag-stack.ts`

- [ ] **Step 1: Add `@cdklabs/generative-ai-cdk-constructs` dependency**

In `packages/graphrag/package.json`, add to `dependencies`:

```json
"@cdklabs/generative-ai-cdk-constructs": "^0.1.312"
```

Then run:

```bash
cd /Users/jonahchan/dev/dxhub/wisco && bun install
```

Expected: lockfile updates, no errors.

- [ ] **Step 2: Add FAQ bucket, Bedrock KB, and S3 data source to GraphRAGStack**

In `packages/graphrag/infra/graphrag-stack.ts`, add the following imports at the top:

```typescript
import { bedrock } from '@cdklabs/generative-ai-cdk-constructs';
```

Add three new public readonly properties to the class:

```typescript
public readonly faqKnowledgeBaseId: string;
public readonly faqBucketName: string;
public readonly faqDataSourceId: string;
```

Add the following resources after the existing `workBucket` definition (before the Neptune graph):

```typescript
    const faqBucket = new s3.Bucket(this, 'WisDorFaqGraphRAG', {
      bucketName: cdk.Fn.join('-', ['wis-faq-bucket-graphrag', uid]),
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      autoDeleteObjects: false,
    });

    const faqKb = new bedrock.VectorKnowledgeBase(this, 'WisDorFaqKbGraphRAG', {
      name: 'wis-faq-graphrag',
      embeddingsModel: bedrock.BedrockFoundationModel.TITAN_EMBED_TEXT_V2_1024,
      instruction:
        'Use this knowledge base to answer frequently asked questions about Wisconsin DOR property assessment and taxation.',
    });

    const faqDataSource = new bedrock.S3DataSource(this, 'WisDorFaqDataSourceGraphRAG', {
      bucket: faqBucket,
      knowledgeBase: faqKb,
      dataSourceName: 'faq-docs-graphrag',
      chunkingStrategy: bedrock.ChunkingStrategy.NONE,
    });
```

Assign the new properties:

```typescript
    this.faqKnowledgeBaseId = faqKb.knowledgeBaseId;
    this.faqBucketName = faqBucket.bucketName;
    this.faqDataSourceId = faqDataSource.dataSourceId;
```

Add CfnOutputs after the existing outputs:

```typescript
    new cdk.CfnOutput(this, 'FaqKnowledgeBaseId', {
      value: faqKb.knowledgeBaseId,
      description: 'FAQ Bedrock Knowledge Base ID (GraphRAG)',
    });
    new cdk.CfnOutput(this, 'FaqBucketNameGraphRAG', {
      value: faqBucket.bucketName,
      description: 'S3 bucket for FAQ documents (GraphRAG)',
    });
    new cdk.CfnOutput(this, 'FaqDataSourceId', {
      value: faqDataSource.dataSourceId,
      description: 'FAQ Bedrock KB Data Source ID (GraphRAG)',
    });
```

- [ ] **Step 3: Verify CDK synth compiles**

```bash
cd /Users/jonahchan/dev/dxhub/wisco/packages/infra && npx cdk synth --no-staging -q 2>&1 | tail -5
```

Expected: synthesizes without errors.

- [ ] **Step 4: Commit**

```bash
git add packages/graphrag/package.json packages/graphrag/infra/graphrag-stack.ts bun.lock
git commit -m "feat: add FAQ S3 bucket and Bedrock Knowledge Base to GraphRAG stack"
```

---

### Task 2: Wire FAQ KB ID Through to Agentic Retrieval Lambda

**Files:**
- Modify: `packages/graphrag/infra/graphrag-messages-stack.ts`
- Modify: `packages/infra/lib/stack.ts`

- [ ] **Step 1: Add FAQ KB ID to GraphRAGMessagesStackProps**

In `packages/graphrag/infra/graphrag-messages-stack.ts`, add to the `GraphRAGMessagesStackProps` interface:

```typescript
  faqKnowledgeBaseId: string;
```

- [ ] **Step 2: Pass FAQ KB ID as env var and add IAM permission**

In the same file, add the env var to the `agenticRetrievalHandler` environment block:

```typescript
          FAQ_KNOWLEDGE_BASE_ID: props.faqKnowledgeBaseId,
```

Add a new IAM policy statement after the existing Bedrock permissions block (after the `bedrock:InvokeModel` policy):

```typescript
    // Bedrock KB Retrieve permissions for FAQ search
    agenticRetrievalHandler.addToRolePolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ['bedrock:Retrieve'],
        resources: [
          `arn:aws:bedrock:${cdk.Stack.of(this).region}:${cdk.Stack.of(this).account}:knowledge-base/*`,
        ],
      })
    );
```

- [ ] **Step 3: Pass faqKnowledgeBaseId from root stack**

In `packages/infra/lib/stack.ts`, update the `GraphRAGMessagesStack` constructor call to include the new prop. Find the existing props object and add:

```typescript
        faqKnowledgeBaseId: graphRAGStack.faqKnowledgeBaseId,
```

This goes alongside the other `graphRAGStack.*` props like `neptuneGraphId`, `neptuneGraphEndpoint`, `rawBucketName`.

- [ ] **Step 4: Verify CDK synth compiles**

```bash
cd /Users/jonahchan/dev/dxhub/wisco/packages/infra && npx cdk synth --no-staging -q 2>&1 | tail -5
```

Expected: synthesizes without errors.

- [ ] **Step 5: Commit**

```bash
git add packages/graphrag/infra/graphrag-messages-stack.ts packages/infra/lib/stack.ts
git commit -m "feat: wire FAQ KB ID to agentic retrieval Lambda with IAM permissions"
```

---

### Task 3: Add `faq_search` Tool to Agentic Retrieval

**Files:**
- Modify: `packages/graphrag/lambdas/agentic_retrieval/tools.py`

- [ ] **Step 1: Add `faq_search` tool definition**

In `packages/graphrag/lambdas/agentic_retrieval/tools.py`, add the following to the `TOOL_DEFINITIONS` list, as the **first** entry (before `vector_search`):

```python
    {
        "toolSpec": {
            "name": "faq_search",
            "description": (
                "Search frequently asked questions about Wisconsin DOR property "
                "assessment and taxation. Returns Q&A pairs ranked by relevance. "
                "Always try this FIRST before vector_search — if a FAQ adequately "
                "answers the user's question, use it directly via the answer tool."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The search query to find relevant FAQs",
                        },
                        "top_k": {
                            "type": "integer",
                            "description": "Number of FAQ results to return (default: 5, max: 10)",
                            "default": 5,
                        },
                    },
                    "required": ["query"],
                }
            },
        }
    },
```

- [ ] **Step 2: Add bedrock-agent-runtime client and FAQ KB ID**

At the top of `tools.py`, add after the existing `bedrock` client:

```python
bedrock_agent_runtime = boto3.client("bedrock-agent-runtime", region_name="us-east-1")

FAQ_KNOWLEDGE_BASE_ID = os.environ.get("FAQ_KNOWLEDGE_BASE_ID", "")
```

Also add `os` to the imports at the top of the file (it's not currently imported):

```python
import os
```

- [ ] **Step 3: Add `faq_search` executor**

In the `execute_tool` function, add a new branch **before** the `vector_search` branch:

```python
    if tool_name == "faq_search":
        if not FAQ_KNOWLEDGE_BASE_ID:
            return {"error": "FAQ knowledge base not configured"}
        top_k = min(tool_input.get("top_k", 5), 10)
        response = bedrock_agent_runtime.retrieve(
            knowledgeBaseId=FAQ_KNOWLEDGE_BASE_ID,
            retrievalQuery={"text": tool_input["query"]},
            retrievalConfiguration={
                "vectorSearchConfiguration": {
                    "numberOfResults": top_k,
                    "overrideSearchType": "SEMANTIC",
                }
            },
        )
        faqs = []
        for result in response.get("retrievalResults", []):
            text = result.get("content", {}).get("text", "")
            score = result.get("score", 0.0)
            faqs.append({"text": text, "score": score})
        return {"faqs": faqs, "count": len(faqs)}

    elif tool_name == "vector_search":
```

Note: the existing `if tool_name == "vector_search":` becomes `elif tool_name == "vector_search":`.

- [ ] **Step 4: Commit**

```bash
git add packages/graphrag/lambdas/agentic_retrieval/tools.py
git commit -m "feat: add faq_search tool definition and executor for Bedrock KB retrieval"
```

---

### Task 4: Update System Prompt to Prioritize FAQ Search

**Files:**
- Modify: `packages/graphrag/lambdas/agentic_retrieval/main.py`

- [ ] **Step 1: Update SYSTEM_PROMPT**

In `packages/graphrag/lambdas/agentic_retrieval/main.py`, replace the entire `SYSTEM_PROMPT` string with:

```python
SYSTEM_PROMPT = """You are a Wisconsin Department of Revenue property tax assistant. Answer questions about property assessment, taxation, statutes, administrative rules, and procedures using the provided tools.

WORKFLOW:
1. ALWAYS start by calling faq_search with the user's question
2. Evaluate the FAQ results:
   - If one or more FAQs directly and adequately answer the question, call the answer tool immediately with the FAQ content
   - If FAQs are partially relevant, note them and continue to step 3 for more detail
   - If FAQs are irrelevant or no results returned, proceed to step 3
3. Use vector_search to find relevant document chunks in the knowledge graph
4. Follow graph edges to find authoritative sources (get_neighbors with CITES, IMPLEMENTS edges)
5. Trace authority chains (get_authority_chain) to cite the correct level of authority
6. When you have enough information, call the answer tool

ALWAYS:
- Cite specific document IDs, section numbers, and statute references
- Distinguish between different authority levels: Constitution > Statutes > Admin Rules > WPAM > FAQs > Guides
- Note when guidance has been superseded (check SUPERSEDES edges)

NEVER:
- Make up statute references or section numbers
- Provide advice without citing sources
- Ignore SUPERSEDES relationships (always check for newer guidance)
- Skip faq_search — even if the question seems complex, FAQs may have a direct answer

When you have enough information, call the 'answer' tool with your complete response in Markdown format."""
```

- [ ] **Step 2: Commit**

```bash
git add packages/graphrag/lambdas/agentic_retrieval/main.py
git commit -m "feat: update system prompt to prioritize FAQ search before graph retrieval"
```

---

### Task 5: Create FAQ Sync Script

**Files:**
- Create: `scripts/graphrag/sync_faq_bucket.sh`

- [ ] **Step 1: Create the sync script**

Create `scripts/graphrag/sync_faq_bucket.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

# Syncs FAQ files from the source bucket (us-west-2) to the GraphRAG FAQ bucket
# (us-east-1) and triggers a Bedrock KB ingestion job.
#
# Usage: ./scripts/graphrag/sync_faq_bucket.sh [--profile PROFILE] [--stack-name STACK_NAME]

PROFILE="${AWS_PROFILE:-wisco}"
STACK_NAME="WisconsinBotStack"
SOURCE_BUCKET="wis-faq-bucket"
SOURCE_REGION="us-west-2"
TARGET_REGION="us-east-1"

while [[ $# -gt 0 ]]; do
  case $1 in
    --profile) PROFILE="$2"; shift 2 ;;
    --stack-name) STACK_NAME="$2"; shift 2 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

echo "==> Looking up GraphRAG FAQ bucket and KB IDs from CloudFormation stack outputs..."

get_output() {
  aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --profile "$PROFILE" \
    --region "$TARGET_REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" \
    --output text
}

# The nested stack outputs are flattened with prefixed keys in the root stack
TARGET_BUCKET=$(get_output "WisconsinGraphRAGStackFaqBucketNameGraphRAG")
FAQ_KB_ID=$(get_output "WisconsinGraphRAGStackFaqKnowledgeBaseId")
FAQ_DS_ID=$(get_output "WisconsinGraphRAGStackFaqDataSourceId")

if [[ -z "$TARGET_BUCKET" || -z "$FAQ_KB_ID" || -z "$FAQ_DS_ID" ]]; then
  echo "ERROR: Could not find required stack outputs. Ensure the stack is deployed with FAQ resources."
  echo "  TARGET_BUCKET=$TARGET_BUCKET"
  echo "  FAQ_KB_ID=$FAQ_KB_ID"
  echo "  FAQ_DS_ID=$FAQ_DS_ID"
  exit 1
fi

echo "  Source:      s3://$SOURCE_BUCKET (us-west-2)"
echo "  Target:      s3://$TARGET_BUCKET ($TARGET_REGION)"
echo "  KB ID:       $FAQ_KB_ID"
echo "  DataSource:  $FAQ_DS_ID"

echo ""
echo "==> Syncing FAQ files..."
aws s3 sync \
  "s3://$SOURCE_BUCKET" \
  "s3://$TARGET_BUCKET" \
  --source-region "$SOURCE_REGION" \
  --region "$TARGET_REGION" \
  --profile "$PROFILE"

COPIED_COUNT=$(aws s3 ls "s3://$TARGET_BUCKET/" --profile "$PROFILE" --region "$TARGET_REGION" | wc -l | tr -d ' ')
echo "  $COPIED_COUNT files in target bucket."

echo ""
echo "==> Starting Bedrock KB ingestion job..."
INGESTION_JOB=$(aws bedrock-agent start-ingestion-job \
  --knowledge-base-id "$FAQ_KB_ID" \
  --data-source-id "$FAQ_DS_ID" \
  --profile "$PROFILE" \
  --region "$TARGET_REGION" \
  --query "ingestionJob.ingestionJobId" \
  --output text)

echo "  Ingestion job started: $INGESTION_JOB"
echo ""
echo "==> Waiting for ingestion to complete..."

while true; do
  STATUS=$(aws bedrock-agent get-ingestion-job \
    --knowledge-base-id "$FAQ_KB_ID" \
    --data-source-id "$FAQ_DS_ID" \
    --ingestion-job-id "$INGESTION_JOB" \
    --profile "$PROFILE" \
    --region "$TARGET_REGION" \
    --query "ingestionJob.status" \
    --output text)

  echo "  Status: $STATUS"

  case "$STATUS" in
    COMPLETE) echo "==> Ingestion complete!"; break ;;
    FAILED)   echo "ERROR: Ingestion failed."; exit 1 ;;
    *)        sleep 10 ;;
  esac
done

echo ""
echo "==> Done. FAQ Knowledge Base is ready for queries."
```

- [ ] **Step 2: Make the script executable**

```bash
chmod +x scripts/graphrag/sync_faq_bucket.sh
```

- [ ] **Step 3: Commit**

```bash
git add scripts/graphrag/sync_faq_bucket.sh
git commit -m "feat: add FAQ bucket sync script for GraphRAG KB ingestion"
```

---

### Task 6: Add FAQ KB Outputs to Root Stack

**Files:**
- Modify: `packages/infra/lib/stack.ts`

- [ ] **Step 1: Add FAQ KB outputs to root stack**

In `packages/infra/lib/stack.ts`, add the following CfnOutputs after the existing GraphRAG outputs (after `GraphRAGStateMachineArn`):

```typescript
    new cdk.CfnOutput(this, 'GraphRAGFaqKnowledgeBaseId', {
      value: graphRAGStack.faqKnowledgeBaseId,
      description: 'FAQ Bedrock Knowledge Base ID (GraphRAG)',
      exportName: 'WisconsinBot-GraphRAGFaqKnowledgeBaseId',
    });

    new cdk.CfnOutput(this, 'GraphRAGFaqBucketName', {
      value: graphRAGStack.faqBucketName,
      description: 'S3 bucket for FAQ documents (GraphRAG)',
      exportName: 'WisconsinBot-GraphRAGFaqBucketName',
    });

    new cdk.CfnOutput(this, 'GraphRAGFaqDataSourceId', {
      value: graphRAGStack.faqDataSourceId,
      description: 'FAQ Bedrock KB Data Source ID (GraphRAG)',
      exportName: 'WisconsinBot-GraphRAGFaqDataSourceId',
    });
```

- [ ] **Step 2: Verify CDK synth compiles**

```bash
cd /Users/jonahchan/dev/dxhub/wisco/packages/infra && npx cdk synth --no-staging -q 2>&1 | tail -5
```

Expected: synthesizes without errors.

- [ ] **Step 3: Commit**

```bash
git add packages/infra/lib/stack.ts
git commit -m "feat: expose FAQ KB outputs from root stack for sync script"
```

---

### Task 7: Deploy and Sync

This task is manual — run after all code changes are committed.

- [ ] **Step 1: Deploy the stack**

```bash
cd /Users/jonahchan/dev/dxhub/wisco/packages/infra && npx cdk diff --profile wisco --region us-east-1 -c useGraphRAG=true
```

Review the diff to confirm only additive changes (new FAQ bucket, KB, data source, IAM policy, env var). Then:

```bash
npx cdk deploy --profile wisco --region us-east-1 -c useGraphRAG=true
```

- [ ] **Step 2: Sync FAQ files and trigger ingestion**

```bash
./scripts/graphrag/sync_faq_bucket.sh --profile wisco
```

Expected: 647 files synced, ingestion job completes successfully.

- [ ] **Step 3: Smoke test the FAQ search**

Invoke the agentic retrieval Lambda directly to verify `faq_search` works:

```bash
aws lambda invoke \
  --function-name $(aws cloudformation describe-stacks \
    --stack-name WisconsinBotStack \
    --profile wisco \
    --region us-east-1 \
    --query "Stacks[0].Outputs[?OutputKey=='WisconsinGraphRAGMessagesStackAgenticRetrievalFunctionArn'].OutputValue" \
    --output text) \
  --payload '{"query": "Is agricultural forest a separate class?", "query_id": "test-faq-1", "session_id": "test-session"}' \
  --profile wisco \
  --region us-east-1 \
  --cli-binary-format raw-in-base64-out \
  /dev/stdout 2>/dev/null | python3 -m json.tool
```

Expected: Response contains FAQ-sourced answer about agricultural forest being class 5m.

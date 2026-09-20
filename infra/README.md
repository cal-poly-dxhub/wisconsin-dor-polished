# infra — CDK app for the Wisconsin DOR chatbot

One CloudFormation stack (`WisconsinBotGraphRAG`) built from nested stacks in
`stacks/`. Entry point: `bin/wisconsin-app.ts`.

| Nested stack | File | What it owns |
| --- | --- | --- |
| `LambdaLayersStack` | `stacks/lambda-layers-stack.ts` | Shared Lambda layers (`websocket_utils`, step-function types) |
| `WisconsinGraphRAGStack` | `stacks/graphrag-stack.ts` | Raw/work/FAQ S3 buckets, FAQ Bedrock knowledge base + data source, the FAQ-URL and model-config DynamoDB tables |
| `WisconsinSessionsStack` | `stacks/sessions-stack.ts` | Cognito, HTTP API, WebSocket API, sessions + chat-history tables |
| `WisconsinGraphRAGMessagesStack` | `stacks/graphrag-messages-stack.ts` | Agentic retrieval Lambda + its EventBridge trigger |
| `WisconsinIngestionStack` | `stacks/ingestion-stack.ts` | Ingestion VPC, ECS cluster, ECR repo, Fargate task definition + task role |
| `WisconsinWebAppStack` | `stacks/webapp-stack.ts` | Next.js frontend on CloudFront (`cdk-nextjs-standalone`) |
| `WisconsinCloudWatchIam` | `stacks/cloudwatch-iam.ts` | API Gateway CloudWatch logging role |

## The Neptune graph is pinned, not managed

The Neptune Analytics graph is **not a CDK resource**. It is created and loaded
out of band by the ingestion pipeline, and the deployment selects one by id:

```jsonc
// infra/cdk.json
"neptuneGraphId": "g-svphgiu4k6"
```

`stacks/stack.ts` reads that context and hands it to exactly two consumers — the
retrieval Lambda (its `NEPTUNE_GRAPH_ID` env var and the `neptune-graph:*` IAM
statement) and the ingestion task definition (same env var name, plus the task
role's IAM). **There is no fallback: a missing `neptuneGraphId` fails synth.**

Why it is out of CloudFormation: a re-index produces a *new* graph loaded over
hours on Fargate and validated before anyone points production at it. As a CDK
resource that would be a stack replacement with the corpus inside it. As a
pinned id, promotion and rollback are both a one-line context change.

## Blue/green: moving to a new graph

1. Create the new graph (console or `aws neptune-graph create-graph`) with
   1024-dim vector search and `publicConnectivity: true`.
2. Grant the ingestion task role access to it. The role is scoped to the pinned
   graph only, so add the new graph's ARN to the `neptune-graph` statement in
   `stacks/ingestion-stack.ts`, deploy, and revert that line after promotion.
   (Alternatively run `load` locally with your own credentials.)
3. Load it: `./tools/ingestion/scripts/run_fargate.sh load --graph-id g-new`.
   Scale it to 128 m-NCU for a full load; 32 is the serving floor.
4. Validate against it without touching production:
   `NEPTUNE_GRAPH_ID=g-new` for the retrieval harness and
   `tools/ingestion/ops/run_recall_probe.py --graph-id g-new`.
5. Promote: change `neptuneGraphId` in `cdk.json` to `g-new`, commit, deploy.
   Only the Lambda env and two IAM statements change.
6. Rollback: set `neptuneGraphId` back to the previous id and deploy again.
   Keep the old graph alive for about a week before deleting it.

A routine load needs none of this: `run_fargate.sh load` with no `--graph-id`
targets the pinned graph, because `load.py` defaults to `$NEPTUNE_GRAPH_ID` from
the task definition.

## Commands

Run from this directory. `AWS_PROFILE` / `AWS_REGION` come from your shell.

```bash
bun run bundle           # from the repo root — CDK synthesizes from infra/bundle/
cdk diff   -c stackName=WisconsinBotGraphRAG
cdk deploy -c stackName=WisconsinBotGraphRAG --require-approval never
bunx tsc --noEmit && bunx jest
```

Context flags: `stackName`, plus `domainName` / `hostedZoneName` /
`hostedZoneId` for a custom domain. Override the pin for a one-off synth with
`-c neptuneGraphId=g-xxxxxxxx`.

Always `cdk diff` before deploying and read the resource list: the graph is
outside CloudFormation precisely so that an unrelated change can never take the
corpus with it.

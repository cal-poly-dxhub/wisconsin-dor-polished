# Fargate Ingestion Pipeline

The ingestion pipeline (extract → embed → load) runs on AWS Fargate instead of a
local machine. This provides reliable, reproducible ingestion without depending
on a developer's laptop staying online for hours.

> **Paths note.** The pipeline lives under `tools/ingestion/` (it was formerly
> `tools/graphrag/`). The Python phases are `tools/ingestion/{extract,embed,load}.py`,
> the run scripts are in `tools/ingestion/scripts/`, and the Docker assets are in
> `tools/ingestion/docker/`.

## Architecture

```
┌──────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Developer   │────▶│  ECS Fargate     │────▶│  AWS Services   │
│  (CLI)       │     │  (2 vCPU / 8 GB) │     │  S3, Bedrock,   │
└──────────────┘     └──────────────────┘     │  Neptune,       │
                                               │  Textract       │
                                               └─────────────────┘
```

A single Docker image contains all three ingestion phases. The `PHASE`
environment variable selects which one runs. `docker/entrypoint.sh` maps it to a
Python module invocation:

| `PHASE` | Command | What it does |
|---------|---------|--------------|
| `extract` | `python -m tools.ingestion.extract` | Pulls PDFs from S3, extracts text (PyMuPDF/Textract), classifies, chunks |
| `embed` | `python -m tools.ingestion.embed` | Generates Titan Embed v2 vectors for each chunk |
| `load` | `python -m tools.ingestion.load` | Writes nodes, edges, and vectors into Neptune Analytics |
| `full` | runs all three sequentially in one container | extract → embed → load, in-process |

`extract`/`embed`/`load` `exec` the module (replacing PID 1). `full` runs them as
sequential commands in one task. The container hard-codes
`CONFIG=/app/tools/ingestion/config/ingest_config.yaml`. Env vars map to CLI
flags via `${VAR:+--flag}` expansion: `SOURCE_FILTER`, `FORCE`, `SMART`,
`RECLASSIFY`, `START_PHASE`, `STOP_AFTER_PHASE`, `MAX_WORKERS`.

> `run_fargate.sh` only launches `extract | embed | load`. `full` exists in the
> container but has no wrapper script — to use it you'd craft the `run-task`
> override by hand. `run_full_ingest.sh` instead runs three separate tasks (see
> below).

## Prerequisites

1. **CDK stack deployed** — `IngestionStack` creates the ECS cluster, task
   definition, ECR repo, VPC, and IAM roles.
2. **Docker image pushed** — the ingestion container image must be in ECR before
   you can run tasks.

## First-time Setup

After deploying the CDK stack, build and push the image:

```bash
cd tools/ingestion/docker
./build_and_push.sh
```

`build_and_push.sh` reads the `EcrRepositoryUri` from the deployed stack's
CloudFormation outputs (it errors if the stack isn't deployed), builds
`--platform linux/amd64` from the **repo root** (the Dockerfile `COPY`s
`tools/...` paths), and pushes the `:latest` tag. Defaults: AWS profile from
`$AWS_PROFILE` (else the CLI `default` profile), region `us-east-1`, stack
`WisconsinBotGraphRAG`.

## Running Ingestion

### Single Phase — `run_fargate.sh`

```bash
# Run just the extract phase
./tools/ingestion/scripts/run_fargate.sh extract

# Extract only WPAM documents, forcing re-extraction
./tools/ingestion/scripts/run_fargate.sh extract --source-filter wpam- --force

# Run load starting from sub-phase 5
./tools/ingestion/scripts/run_fargate.sh load --start-phase 5
```

`run_fargate.sh` resolves the cluster and task-definition ARNs from the stack's
CloudFormation outputs (`IngestionClusterArn` / `IngestionTaskDefinitionArn`) —
it does **not** hard-code names — then calls `aws ecs run-task --launch-type
FARGATE` with `assignPublicIp: ENABLED`. It is **fire-and-forget**: it prints the
task ARN plus the `aws logs tail` and `aws ecs describe-tasks` commands for you to
monitor, then exits. It does not poll.

### Full Pipeline — `run_full_ingest.sh`

```bash
# Run all three phases sequentially
./tools/ingestion/scripts/run_full_ingest.sh

# Full pipeline for a specific source
./tools/ingestion/scripts/run_full_ingest.sh --source-filter wpam- --force
```

`run_full_ingest.sh` orchestrates **three separate Fargate tasks** (extract,
then embed, then load — not the container's `full` mode). For each phase it calls
`run_fargate.sh`, polls `describe-tasks` every 15 s until the task reaches
`STOPPED`, checks `containers[0].exitCode`, and only proceeds to the next phase on
exit code 0. Any non-zero exit prints the error plus a log-tail command and stops
the run. It forwards all CLI args verbatim to every phase.

### Options

`run_fargate.sh` parses a fixed option list (an unknown flag is an error, not a
pass-through):

| Option | Phases | Description |
|--------|--------|-------------|
| `--source-filter <prefix>` | all | Only process doc IDs matching this prefix (e.g. `wpam-`) |
| `--force` | extract, embed | Ignore cache, re-process everything |
| `--smart` | extract, embed | Only re-process docs whose upstream artifact is newer than its cache |
| `--reclassify` | extract | Force LLM reclassification even if the classification cache is warm |
| `--aliases` | extract | Generate chunk aliases (document expansion); also on by default via `ingest_config.yaml` |
| `--aliases-only` | extract | Skip extraction; backfill aliases onto existing `extracted/` JSONs |
| `--embed-input <mode>` | embed | `plain` or `enriched` (default comes from `alias_enrichment.embed_input`) |
| `--max-workers <N>` | extract, embed | Override concurrency |
| `--start-phase <N>` | load | Resume from load sub-phase N (**1–10**) |
| `--stop-after-phase <N>` | load | Stop after sub-phase N completes |
| `--cache-prefix <p>` | all | Namespace every work-bucket key (e.g. `staging/`) so a staging run never touches production caches |
| `--graph-id <id>` | load | **Blue/green only.** A routine load needs this flag omitted — the task definition sets `NEPTUNE_GRAPH_ID` from the `neptuneGraphId` pin in `infra/cdk.json` and `load.py` defaults to it. A non-pinned graph also needs its ARN added to the task role first (see [`infra/README.md`](../infra/README.md)). |

The script warns and pauses 10 s if you pass `--cache-prefix` to `load` **without**
`--graph-id`, because that would push staging embeddings into the production graph.

> **Concurrency defaults are pinned by the task def.** `extract.py` defaults to
> `--max-workers 3` and `embed.py` to `5`, but the CDK task definition always sets
> `MAX_WORKERS=3` in the container environment, which wins. So on Fargate **embed
> runs with 3 workers unless you pass `--max-workers` explicitly**. `load` takes
> no `--max-workers` (its per-phase concurrency is a code constant).

## Load Sub-Phases

`load.py` runs **10 sequential sub-phases**, numbered 1–10 with a 1:1 mapping to their
`phase_N_*` functions (there is no CLI-step vs. function offset).
`--start-phase`/`--stop-after-phase` take integers in `[1, 10]`.

**The phase-by-phase table lives in one place:
[graphrag-engineering-guide § Load phases](graphrag-engineering-guide.md#load-phases).**
It also records what Phase 4 and Phase 9 stopped doing on 2026-09-19 (no
`HAS_SUBSECTION` edges, no orphan-`Topic` GC) and why old notes mention "phases 1–11".

Phase 10 (integrity checks, added 2026-09-14) exits 1 on a failed assertion — do not
re-run blindly after a `Phase 10 FAILED` line.

## Monitoring

### Logs

All output goes to CloudWatch log group `/ecs/wis-dor-ingestion` (stream prefix
`ingestion`):

```bash
# Follow logs in real-time
aws logs tail /ecs/wis-dor-ingestion --follow --profile <your-profile> --region us-east-1

# View logs for a specific time range
aws logs filter-log-events \
  --log-group-name /ecs/wis-dor-ingestion \
  --start-time $(date -v-2H +%s000) \
  --profile <your-profile> --region us-east-1
```

### Task Status

```bash
aws ecs describe-tasks \
  --cluster wis-dor-ingestion \
  --tasks <task-arn> \
  --profile <your-profile> --region us-east-1 \
  --query 'tasks[0].{status:lastStatus,exit:containers[0].exitCode}'
```

## Updating the Image

After changing any ingestion code (`extract.py`, `embed.py`, `load.py`,
anything under `chunking/`, or `docker/requirements.txt`):

```bash
cd tools/ingestion/docker
./build_and_push.sh
```

The next `run-task` call pulls the updated `:latest` image automatically. No CDK
deploy needed for code-only changes. **Forgetting to rebuild is the #1 cause of
"my fix didn't take effect on Fargate"** — the task runs the ECR image, never your
local filesystem.

## Resource Sizing

Defined in `infra/stacks/ingestion-stack.ts` (`FargateTaskDefinition`):

| Resource | Value | Reason |
|----------|-------|--------|
| CPU | 2 vCPU (`cpu: 2048`) | PDF extraction runs several workers in parallel |
| Memory | 8 GB (`memoryLimitMiB: 8192`) | Load phase holds batches of chunk text in memory |
| VPC | 2 public subnets, no NAT (`natGateways: 0`) | All AWS services reached via public endpoints |

### Cost

~$0.12/hour when running. A full ingestion run (roughly 3–4 hours across all
three sequential phase-tasks) costs on the order of $0.35–0.47. The VPC and
cluster cost nothing when no tasks are running.

## Infrastructure (CDK)

`IngestionStack` (`infra/stacks/ingestion-stack.ts`) provisions:

- **VPC** — 2 public subnets, no NAT gateways, `maxAzs: 2`
- **ECS Cluster** — `wis-dor-ingestion`
- **ECR Repository** — `wis-dor-ingestion`, keeps the last **5** images (`removalPolicy: DESTROY`, `emptyOnDelete: true`)
- **Fargate Task Definition** — 2 vCPU / 8 GB, container name `ingestion`, pre-set env: `AWS_REGION`, `RAW_BUCKET`, `WORK_BUCKET`, `NEPTUNE_GRAPH_ID` (the graph pinned by the `neptuneGraphId` CDK context; `load.py` defaults to it), `MAX_WORKERS=3`. `TEXTRACT_STAGING_BUCKET` is intentionally unset: the Textract fallback is skipped (PyMuPDF result kept) until a staging bucket is configured.
- **IAM Task Role** — S3 (raw + work buckets), Bedrock (`InvokeModel`), Neptune Graph (execute/read/write/delete/get) on the pinned graph, Textract (analyze/detect/start/get)
- **CloudWatch Log Group** — `/ecs/wis-dor-ingestion`, `ONE_MONTH` (30-day) retention
- **Security Group** — outbound-only (`allowAllOutbound: true`, no ingress)

The parent stack (`infra/stacks/stack.ts`) re-exports the cluster and
task-definition ARNs as top-level outputs (`IngestionClusterArn`,
`IngestionTaskDefinitionArn`); subnet IDs, security-group ID, and the ECR URI come
from the nested stack's own outputs. The run scripts discover all of these via
`describe-stacks` — nothing is typed literally.

## Differences from Local Execution

| Aspect | Local | Fargate |
|--------|-------|---------|
| Auth | `AWS_PROFILE=<your-profile>` | IAM task role (automatic) |
| SSL certs | `AWS_CA_BUNDLE=$CERT` | Not needed (base image has certs) |
| `state_laws_dir` | Local statute PDFs used for section-level refs | Degrades gracefully to chapter-only refs |
| Textract staging | `TEXTRACT_STAGING_BUCKET`, unset by default | Not set on the task def — so the Textract fallback is **skipped** and the PyMuPDF result is kept |
| Logs | Terminal stdout | CloudWatch `/ecs/wis-dor-ingestion` |
| Failure recovery | Restart manually | Re-run the same command; caching skips completed work |

## Troubleshooting

**Task exits immediately with code 1:**
`PHASE` was unset or not one of `extract|embed|load|full` (entrypoint prints the
valid values and exits 1). Also confirm the image is pushed.

**Out of memory (exit code 137):**
The pressure is in the batched chunk writes (Phase 5) and the vector upserts (Phase 8) —
scale Neptune to 128 m-NCU before a full load. The batch sizes are guarded by module
constants in `load.py`: `PHASE_5_BATCH_SIZE`, `PHASE_5_MAX_PAIRS_PER_FLUSH`, and
`PHASE_5_MAX_BYTES_PER_FLUSH = 50_000` (a cumulative-text-byte cap per flush, because a
count-only cap does not prevent per-query OOM). These are code constants, not env vars:
to change them, edit `load.py` and rebuild the image. If the corpus grows substantially,
also consider raising `memoryLimitMiB` in `ingestion-stack.ts`.

**Neptune throttling (slow load phase):**
`load.py`'s `execute_query` retries up to **8 times** with exponential backoff capped at
**60 s**, catching both `ThrottlingException` and `UnprocessableException`. (The
retrieval Lambda's client is deliberately more impatient — 3 attempts, 30 s cap.) Scale
the graph to 128 m-NCU for a full re-ingestion, then back down to 32 afterward; 16 m-NCU
is rejected by the service even on a fresh graph.

**Image not found error:**
Run `./build_and_push.sh` to push the latest code to ECR.

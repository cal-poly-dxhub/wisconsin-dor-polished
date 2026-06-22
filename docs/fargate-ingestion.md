# Fargate Ingestion Pipeline

The GraphRAG ingestion pipeline (extract → embed → load) runs on AWS Fargate instead of a local machine. This provides reliable, reproducible ingestion without depending on a developer's laptop staying online for hours.

## Architecture

```
┌──────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Developer   │────▶│  ECS Fargate     │────▶│  AWS Services   │
│  (CLI)       │     │  (2 vCPU / 8 GB) │     │  S3, Bedrock,   │
└──────────────┘     └──────────────────┘     │  Neptune,       │
                                               │  Textract       │
                                               └─────────────────┘
```

A single Docker image contains all three ingestion phases. The `PHASE` environment variable selects which script runs:

| Phase | Script | What it does |
|-------|--------|--------------|
| `extract` | `tools/graphrag/extract.py` | Pulls PDFs from S3, extracts text (PyMuPDF/Textract), classifies via LLM, chunks |
| `embed` | `tools/graphrag/embed.py` | Generates Titan Embed v2 vectors for each chunk |
| `load` | `tools/graphrag/load.py` | Writes nodes, edges, and vectors into Neptune Analytics |

## Prerequisites

1. **CDK stack deployed** — the `IngestionStack` creates the ECS cluster, task definition, ECR repo, VPC, and IAM roles.
2. **Docker image pushed** — the ingestion container image must be in ECR before you can run tasks.

## First-time Setup

After deploying the CDK stack:

```bash
cd tools/graphrag

# Build and push the Docker image to ECR
./build_and_push.sh
```

This builds the image from the repo root and pushes it to the ECR repository created by the stack.

## Running Ingestion

### Single Phase

```bash
# Run just the extract phase
./tools/graphrag/run_fargate.sh extract

# Extract only WPAM documents, forcing re-extraction
./tools/graphrag/run_fargate.sh extract --source-filter wpam- --force

# Run load starting from sub-phase 5
./tools/graphrag/run_fargate.sh load --start-phase 5
```

### Full Pipeline (extract → embed → load)

```bash
# Run all three phases sequentially
./tools/graphrag/run_full_ingest.sh

# Full pipeline for a specific source
./tools/graphrag/run_full_ingest.sh --source-filter wpam- --force
```

The full pipeline script starts each phase, polls until it completes, checks the exit code, and proceeds to the next. If any phase fails, it exits immediately with the error.

### Options

| Option | Phases | Description |
|--------|--------|-------------|
| `--source-filter <prefix>` | all | Only process doc IDs matching this prefix |
| `--force` | extract, embed | Ignore cache, re-process everything |
| `--max-workers <N>` | extract, embed | Override concurrency (default: 3 for extract, 5 for embed) |
| `--start-phase <N>` | load | Resume from a specific sub-phase (1-11) |
| `--stop-after-phase <N>` | load | Stop after this sub-phase completes |

## Monitoring

### Logs

All output goes to CloudWatch:

```bash
# Follow logs in real-time
aws logs tail /ecs/wis-dor-ingestion --follow --profile widor --region us-east-1

# View logs for a specific time range
aws logs filter-log-events \
  --log-group-name /ecs/wis-dor-ingestion \
  --start-time $(date -v-2H +%s000) \
  --profile widor --region us-east-1
```

### Task Status

```bash
# Check if a task is still running
aws ecs describe-tasks \
  --cluster wis-dor-ingestion \
  --tasks <task-arn> \
  --profile widor --region us-east-1 \
  --query 'tasks[0].{status:lastStatus,exit:containers[0].exitCode}'
```

## Updating the Image

After changing any ingestion script (`extract.py`, `embed.py`, `load.py`, `pdfChunker.py`, etc.):

```bash
cd tools/graphrag
./build_and_push.sh
```

The next `run-task` call will pull the updated `:latest` image automatically. No CDK deploy needed for code-only changes.

## Resource Sizing

| Resource | Value | Reason |
|----------|-------|--------|
| CPU | 2 vCPU | PDF extraction runs 3 workers in parallel; load phase does CPU-heavy cosine similarity |
| Memory | 8 GB | Load phase holds all doc embeddings in memory for pairwise comparison |
| VPC | Public subnets, no NAT | All AWS services (S3, Bedrock, Neptune, Textract) are accessed via public endpoints |

### Cost

~$0.12/hour when running. A full ingestion run (3-4 hours across all phases) costs approximately $0.35-0.47. The VPC and cluster cost nothing when no tasks are running.

## Infrastructure (CDK)

The `IngestionStack` (`infra/stacks/ingestion-stack.ts`) provisions:

- **VPC** — 2 public subnets, no NAT gateways
- **ECS Cluster** — named `wis-dor-ingestion`
- **ECR Repository** — stores the Docker image (keeps last 5 images)
- **Fargate Task Definition** — 2 vCPU / 8 GB, pre-configured with bucket names, graph ID, and region
- **IAM Task Role** — S3 (raw + work buckets), Bedrock (invoke model), Neptune Graph (read/write/execute), Textract
- **CloudWatch Log Group** — `/ecs/wis-dor-ingestion`, 30-day retention
- **Security Group** — outbound-only (no inbound)

## Differences from Local Execution

| Aspect | Local | Fargate |
|--------|-------|---------|
| Auth | `AWS_PROFILE=widor` | IAM task role (automatic) |
| SSL certs | `AWS_CA_BUNDLE=$CERT` | Not needed (base image has certs) |
| `state_laws_dir` | Local statute PDFs used for section-level refs | Degrades gracefully to chapter-only refs |
| Textract staging | Uses hardcoded bucket | Same bucket, configured via `TEXTRACT_STAGING_BUCKET` env var |
| Logs | Terminal stdout | CloudWatch `/ecs/wis-dor-ingestion` |
| Failure recovery | Restart manually | Re-run the same command; caching skips completed work |

## Troubleshooting

**Task exits immediately with code 1:**
Check that the Docker image is pushed (`build_and_push.sh`) and the `PHASE` env var is set correctly.

**Out of memory (exit code 137):**
The load phase's semantic edge discovery (phase 11) does in-memory pairwise cosine similarity. If the corpus has grown significantly, increase `memoryLimitMiB` in `ingestion-stack.ts`.

**Neptune throttling (slow load phase):**
The scripts have built-in exponential backoff for Neptune throttling. If it's excessive, reduce `PHASE_10_WORKERS` in `load.py` or run during off-peak hours.

**Image not found error:**
Run `./build_and_push.sh` to push the latest code to ECR.

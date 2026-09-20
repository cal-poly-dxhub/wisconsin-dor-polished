# Annual Data Refresh Runbook — Wisconsin DOR Property Tax Chatbot

**Who this is for:** a DOR technology-services staff member who can run commands in a
terminal and has AWS command-line access, but who is not a software engineer and has
not seen this code before.

**What it covers:** everything needed to keep the chatbot's *data* current — the yearly
refresh of statutes, rules, the WPAM, DOR publications, FAQs, and the hand-maintained
side files — without changing any code. This is the "how to update the data" document.
Anything that requires changing code is listed in Section 7 ("When to call an engineer").

**Time budget:** the yearly refresh is roughly one working day of hands-on time spread
over 2–3 calendar days (most of the wall-clock time is waiting for jobs to finish), plus
the manual flowchart re-check (2–4 hours). Mid-year single-document additions take about
an hour each.

**Money budget:** the yearly refresh costs on the order of **$40–$80** in AWS usage on
top of the normal monthly bill (see Section 6). The single biggest lever is remembering
to scale the graph database back down when you are done (Step 3.9).

---

## Table of contents

1. [What the system is made of](#1-what-the-system-is-made-of)
2. [Before you start](#2-before-you-start)
3. [The annual refresh, step by step](#3-the-annual-refresh-step-by-step)
4. [Adding a single new document mid-year](#4-adding-a-single-new-document-mid-year)
5. [Backup and rollback](#5-backup-and-rollback)
6. [Monitoring and cost](#6-monitoring-and-cost)
7. [When to call an engineer](#7-when-to-call-an-engineer)
8. [Quick reference — all commands in order](#8-quick-reference--all-commands-in-order)

---

## 1. What the system is made of

The chatbot answers questions by searching a **knowledge graph** — a database of every
document it knows about, cut into small passages, with links showing which statute a
passage cites, which manual chapter it belongs to, and so on. You never edit the graph
directly. Instead you put source documents into a storage bucket and run a three-stage
pipeline that rebuilds the graph from them.

```
  document_manifest.yaml                 (the list of URLs — you edit this)
          │
          ▼  scrape_documents.py  (downloads each URL; uploads only if changed)
  ┌────────────────────────────┐
  │  RAW BUCKET (S3)           │  wis-raw-bucket-c8e69250
  │  raw/<category>-<name>/    │  one PDF or HTML page per document
  └────────────┬───────────────┘
               │
               ▼  Stage 1: EXTRACT   (runs on Fargate — an AWS "rented computer")
  ┌────────────────────────────┐   • pulls text out of each PDF
  │  WORK BUCKET (S3)          │   • cuts it into passages ("chunks")
  │  classified/  extracted/   │   • asks an AI model to write plain-language
  │  aliases/     embedded/    │     synonyms/questions for each passage ("aliases")
  └────────────┬───────────────┘
               │
               ▼  Stage 2: EMBED     (Fargate)
               │   • turns each passage into a 1,024-number "fingerprint" (a vector)
               │     so the chatbot can find passages by meaning, not just by keyword
               │
               ▼  Stage 3: LOAD      (Fargate)
  ┌────────────────────────────┐
  │  NEPTUNE ANALYTICS GRAPH   │  g-svphgiu4k6  (us-east-1)
  │  documents, chunks, edges, │  ← the chatbot reads from here at runtime
  │  vectors                   │
  └────────────────────────────┘

  Separate, smaller pieces the chatbot also reads:

  ┌────────────────────────┐   FAQ files live in a second bucket and are indexed by an
  │ FAQ KNOWLEDGE BASE     │   AWS "Bedrock Knowledge Base". Updated by sync_faq_bucket.sh.
  └────────────────────────┘
  ┌────────────────────────┐   The chatbot's instructions ("prompts") live in a DynamoDB
  │ PROMPTS (DynamoDB)     │   table. Source of truth is config/model_configs.toml.
  └────────────────────────┘   Only changes when an engineer edits the prompt.
  ┌────────────────────────┐   Three hand-maintained "sidecar" files in the raw bucket:
  │ SIDECARS (S3)          │   • flowcharts/  — 6 WPAM decision flowcharts, hand-typed JSON
  │                        │   • worksheets/  — 4 TID Excel forms, converted to JSON by a script
  └────────────────────────┘   • gold tester queries — optional, attached to passages
```

**Terms used throughout (defined once here):**

| Term | Plain meaning |
|---|---|
| **S3 bucket** | An AWS folder in the cloud. Two matter: the **raw bucket** (`wis-raw-bucket-c8e69250`, the original documents) and the **work bucket** (`wis-work-bucket-c8e69250`, the pipeline's intermediate files, called "caches"). |
| **Fargate** | AWS service that runs a job on a temporary cloud computer so your laptop does not have to stay on for hours. Each pipeline stage is one Fargate "task". |
| **Docker image** | The packaged copy of the pipeline code that Fargate runs. It is a *snapshot*; if the code changes, the image has to be rebuilt (Section 7). |
| **Neptune Analytics** | The graph database. It is billed by size ("m-NCU", memory units) per hour whether or not anyone is using it. |
| **m-NCU** | Neptune's size setting. Normal is **32** (≈ $3.20/hour). A full load needs **128** (≈ $12.80/hour). |
| **Chunk** | One passage of a document (a few paragraphs). The unit the chatbot retrieves. |
| **Embedding / vector** | A list of 1,024 numbers that represents a chunk's meaning. Similar passages have similar numbers. |
| **Aliases** | AI-generated plain-language questions and synonyms attached to each chunk so that everyday wording ("camping trailer") finds legal wording ("recreational prefabricated structure"). |
| **Smart mode** (`--smart`) | "Only redo what changed." Extract compares each raw file's date to its cache; embed compares extraction date to embedding date. |
| **Sidecar** | A small JSON file that sits beside the main data and describes a document the graph cannot represent well (a flowchart or an Excel form). |
| **CloudWatch** | AWS's log viewer. Every pipeline job writes its progress there. |

---

## 2. Before you start

### 2.1 Access you need

- An AWS login for the DOR account that hosts the chatbot, with permission to use S3,
  ECS/Fargate, Neptune Analytics, Bedrock, DynamoDB and CloudFormation. Ask whoever
  administers the account to confirm you have the same permissions the DxHub engineers
  used.
- Access to this code repository (a copy on your machine — `git clone` it, or download it).

### 2.2 Software to install (one time, ~30 minutes)

| Tool | What it is | How to install (macOS) | Check it works |
|---|---|---|---|
| AWS CLI v2 | Command-line access to AWS | https://docs.aws.amazon.com/cli/ | `aws --version` |
| `uv` | Runs the Python scripts with the right libraries | `curl -LsSf https://astral.sh/uv/install.sh \| sh` | `uv --version` |
| `bun` | Runs the JavaScript tooling (only needed for `bun install`) | `curl -fsSL https://bun.sh/install \| bash` | `bun --version` |
| Docker Desktop | Only needed if an engineer changes pipeline code (Section 7). Not needed for a data-only refresh. | https://docker.com | `docker --version` |
| `jq` | Formats JSON output (optional but handy) | `brew install jq` | `jq --version` |

Then, from the repository folder:

```bash
cd /path/to/wisconsin-dor-polished
bun install          # ~1 minute; installs shared tooling
uv sync              # ~2 minutes; installs Python libraries into .venv/
```

### 2.3 Your AWS profile

Every command below uses `<your-profile>` as a placeholder. Replace it with the name of
your AWS CLI profile (the one set up by `aws configure` or `aws configure sso`). To avoid
typing it each time, set it once per terminal session:

```bash
export AWS_PROFILE=<your-profile>
export AWS_REGION=us-east-1
```

Everything in this system is in **us-east-1**. If your shell has `AWS_REGION` set to
anything else from other work, commands will silently point at the wrong region — so
always set it explicitly as above.

Check that it works:

```bash
aws sts get-caller-identity
# Expected: a JSON block with your Account number and user ARN. If you get
# "Unable to locate credentials" or "Token has expired", log in again
# (aws sso login --profile <your-profile>) and retry.
```

### 2.4 The SSL certificate variable (macOS only)

The Python scripts make thousands of calls to AWS. On macOS with recent Python versions
these calls can start failing after a couple of hundred with a confusing
`SSLError: [Errno 2] No such file or directory`. The fix is to tell Python where its
certificate bundle is. Do this once per terminal session, after `uv sync`:

```bash
export CERT=$(.venv/bin/python3 -c "import certifi; print(certifi.where())")
export AWS_CA_BUNDLE=$CERT
```

(Fargate jobs do not need this — it is only for scripts you run locally.)

### 2.5 See what is currently loaded

Before changing anything, record how big the graph is now. This gives you a baseline to
compare against after the load, and a quick "is the database up?" check.

```bash
aws neptune-graph execute-query \
  --graph-identifier g-svphgiu4k6 \
  --language OPEN_CYPHER \
  --query-string "MATCH (c:Chunk) RETURN count(c) AS chunks" \
  /dev/stdout
```

Expected output (numbers will differ):

```json
{"results":[{"chunks":47812}]}
```

Two more useful counts — documents and the newest WPAM edition loaded:

```bash
aws neptune-graph execute-query --graph-identifier g-svphgiu4k6 --language OPEN_CYPHER \
  --query-string "MATCH (c:Chunk)-[:EXTRACTED_FROM]->(d) RETURN count(DISTINCT d) AS documents" /dev/stdout

aws neptune-graph execute-query --graph-identifier g-svphgiu4k6 --language OPEN_CYPHER \
  --query-string "MATCH (d) WHERE d.id STARTS WITH 'wpam-' RETURN d.id ORDER BY d.id DESC LIMIT 3" /dev/stdout
```

Also confirm the graph is at its normal size and idle:

```bash
aws neptune-graph get-graph --graph-identifier g-svphgiu4k6 \
  --query '{status:status, memory:provisionedMemory}'
# Expected: { "status": "AVAILABLE", "memory": 32 }
```

> **Where `g-svphgiu4k6` comes from.** The graph is not a CloudFormation resource.
> It is pinned by the `neptuneGraphId` context in `infra/cdk.json`, which CDK
> feeds to both the retrieval Lambda and the Fargate ingestion task. If that pin
> has moved since this runbook was written, read the current value first and use
> it everywhere below:
>
> ```bash
> jq -r '.context.neptuneGraphId' infra/cdk.json
> ```
>
> `run_fargate.sh load` needs no `--graph-id`: the task definition carries the
> pinned id as `NEPTUNE_GRAPH_ID` and `load.py` defaults to it. **Nothing in this
> runbook should ever pass `--graph-id`** — that flag exists only for the
> blue/green case below.
>
> **Loading a different (blue/green) graph is an engineer job, not a refresh step.**
> The Fargate task role is scoped to the *pinned* graph only, so a load onto a new
> graph fails with an access-denied error until someone adds that graph's ARN to
> the `neptune-graph` statement in `infra/stacks/ingestion-stack.ts`, deploys, and
> reverts that line after promotion. (The alternative is running `load` locally
> with your own credentials.) The full create → grant → load → validate → promote →
> rollback procedure is in [`infra/README.md`](../infra/README.md). The annual
> refresh does **not** need it: it reloads the graph that is already pinned.

Write these numbers down — you will compare them in Step 3.10.

---

## 3. The annual refresh, step by step

**Order matters.** Run the steps in this order. Do not run Steps 3.4–3.8 in parallel.

**Do this in a calm window.** The chatbot keeps working during Steps 3.1–3.6. During the
load (Step 3.8) answers may be briefly inconsistent for ~15 minutes. Schedule the load
outside peak hours if you can.

**Do a backup first (Section 5.1, ~5 minutes).** Every step is re-runnable, but a copy of
the caches makes any mistake a 15-minute fix instead of a 4-hour one.

### Step 3.1 — Add or verify the URLs in the document manifest

**What:** `tools/ingestion/config/document_manifest.yaml` is the *single list* of every
document the chatbot should know about. Each yearly refresh you add the new editions and
remove anything DOR has retired.

**Time:** 30–60 minutes of reading and editing. **Cost:** none.

Open the file in any text editor. It is grouped by category; each category has a few
lines of settings you never touch, then a list of URLs:

```yaml
  wpam:
    framework_id: FW-WPAM          # leave alone
    authority_level: 5             # leave alone
    doc_type: assessment_manual    # leave alone
    urls:
      - https://www.revenue.wi.gov/documents/wpam25.pdf
      - https://www.revenue.wi.gov/documents/wpam26.pdf
      - https://www.revenue.wi.gov/documents/wpam27.pdf     # <-- add the new edition here
```

The categories, in authority order:

| Category key | What it holds | Typical yearly change |
|---|---|---|
| `constitution` | WI constitution | none |
| `statutes` | One PDF per statute chapter (ch. 70, 74, …) | none — same URL, content refreshes automatically |
| `admin_rules` | One PDF per Tax chapter (Tax 6, 12, …) | none — same URL |
| `wpam` | One PDF per WPAM edition | **add the new year's PDF** |
| `faq_pages` | revenue.wi.gov FAQ pages | add any new FAQ page |
| `gov_publications` | DOR guides and publications | add new-year guides; remove retired ones |
| `form_instructions` | Form instruction PDFs | add new-year versions |
| `complex_inquiry_pages` | DOR advisory pages | occasionally |
| `news_pages` | Assessor-News items | **add each new news item URL** (one line each) |
| `iaao`, `uspap` | Professional standards | rarely |

Rules for editing:

1. **Add** a document by appending one line: `      - https://...` under the right
   category's `urls:`. Indentation is 6 spaces then `- `. Copy an existing line.
2. **Do not delete** a URL just because a new edition exists. Old WPAM editions stay in
   the graph on purpose (the chatbot knows which year is current). Only delete a URL if
   the page is gone from DOR's site for good.
3. **Statutes and admin rules** use stable legislature URLs — the content behind them
   updates, and the scraper detects that automatically. No edit needed.
4. **News pages:** if DOR's filename has a typo so the date cannot be read from the URL,
   use the two-line form with an explicit date (there is an example at the top of the
   `news_pages` list):
   ```yaml
      - url: https://www.revenue.wi.gov/Pages/SLF/Assessor-News/20203-05-15.aspx
        effective_date: "2023-05-15"
   ```
5. The four **TID Excel worksheets** are deliberately listed only as comments in
   `form_instructions` — they are handled by Step 3.13, not by the scraper.

Check your edit did not break the file's format:

```bash
uv run python -c "import yaml; d=yaml.safe_load(open('tools/ingestion/config/document_manifest.yaml')); print(sum(len(c['urls']) for c in d['categories'].values()), 'URLs OK')"
# Expected: a number and "URLs OK". If you see "yaml.scanner.ScannerError", the
# indentation is wrong on the line it names.
```

### Step 3.2 — Scrape: dry run

**What:** shows what the scraper *would* download, without touching anything. Use it to
confirm your new URLs were picked up and spelled correctly.

Manifest entries are plain URL strings. Use the dict form `{url, doc_id, title,
effective_date}` only when the derived id or title would be wrong — e.g. WPAM Volume 2 is
registered with a year-less `doc_id` so it is never treated as a competing *edition* of
Volume 1, and with an explicit `title` because its opening pages don't name it. To re-upload
a single document even if unchanged: `--only <doc_id> --force` (with its `--category`).

**Time:** under 1 minute. **Cost:** none.

```bash
uv run python tools/ingestion/scrape_documents.py \
  --bucket wis-raw-bucket-c8e69250 --dry-run
```

Expected output (abridged):

```
Scraping 1003 documents across 11 categories

=== constitution (authority level 1) ===
  [1/1003] Would scrape: constitution-wi-unannotated <- https://docs.legis.wisconsin.gov/constitution/wi_unannotated
=== statutes (authority level 2) ===
  [2/1003] Would scrape: statutes-17 <- https://docs.legis.wisconsin.gov/document/statutes/ch.%2017.pdf
...
=== wpam (authority level 5) ===
  [63/1003] Would scrape: wpam-wisconsin-property-assessment-manual-2027 <- https://www.revenue.wi.gov/documents/wpam27.pdf
...
DRY RUN Complete: 1003 processed
  New: 0, Changed: 0, Forced: 0, Unchanged: 0, Failed: 0
```

Find your new line(s) in the list. The name before `<-` is the **document ID** the system
will use everywhere from now on (e.g. `wpam-wisconsin-property-assessment-manual-2027`).
Note it down; you will need it in Steps 3.10 and 3.14.

> The dry run does not download, so the New/Changed counts are always 0 here. It only
> proves the manifest parses and shows the IDs.

To restrict the dry run to one category: add `--category wpam` (repeatable:
`--category wpam --category news_pages`).

### Step 3.3 — Scrape for real

**What:** downloads every URL, compares it byte-for-byte to what is already in the raw
bucket, and uploads only the ones that are new or changed.

**Time:** 15–40 minutes for the whole manifest (it pauses half a second between
downloads to be polite to the legislature's and DOR's web servers). **Cost:** effectively
nothing (a few cents of S3 storage).

```bash
uv run python tools/ingestion/scrape_documents.py \
  --bucket wis-raw-bucket-c8e69250 2>&1 | tee scrape-$(date +%Y%m%d).log
```

(`tee` keeps a copy of the output in a dated log file next to you.)

Expected output (abridged):

```
=== statutes (authority level 2) ===
  [2/1003] statutes-17 — unchanged
  [12/1003] statutes-70 — CHANGED -> s3://wis-raw-bucket-c8e69250/raw/statutes-70/statutes-70.pdf
...
=== wpam (authority level 5) ===
  [63/1003] wpam-wisconsin-property-assessment-manual-2027 — NEW -> s3://wis-raw-bucket-c8e69250/raw/wpam-wisconsin-property-assessment-manual-2027/wpam-wisconsin-property-assessment-manual-2027.pdf
...
Complete: 1003 processed
  New: 4, Changed: 37, Forced: 0, Unchanged: 960, Failed: 2

Failed (2):
  news_pages-2027-03-01: HTTPError 404 ...

41 documents uploaded — run extract with --force to re-process them.
```

**What "pass" looks like:** `Failed: 0`, and New + Changed matches roughly what you
expect (the statutes and admin rules usually all show CHANGED after the legislature
republishes; the WPAM and new guides show NEW).

> **Ignore the last line's advice to use `--force`.** The workflow below uses
> `extract --smart`, which re-processes exactly the uploaded documents. `--force` would
> re-process all ~1,000 documents (4+ hours, ~$10 of AI calls) for no benefit.

**Common problems**

| Symptom | Cause | Fix |
|---|---|---|
| `FAILED: HTTPError 404` for a URL you added | Typo in the URL, or DOR moved the page | Open the URL in a browser, fix the manifest line, re-run. Re-running is safe: unchanged files are skipped. |
| Many `FAILED: ... timed out` in a row | DOR/legislature site slow or blocking | Wait an hour and re-run with `--sleep 2` (slower, gentler). |
| A statute shows CHANGED every single run | Legislature's server re-generates the PDF with a new timestamp each time | Harmless; it just means that chapter gets re-extracted. |
| `Unknown --category values` | Category name misspelled | Use one of the keys from the table in Step 3.1. |

If you only want to force one category back up (e.g. after a bad partial run), add
`--force --category <name>`; without `--category`, `--force` re-uploads *everything*.

### Step 3.4 — Extract (smart mode)

**What:** starts a Fargate job that reads every raw document whose file is newer than
its extraction cache, pulls out the text, chunks it, classifies it, and generates
aliases for the changed chunks. Unchanged documents are skipped.

**Time:** depends entirely on how many documents changed. Rough guide: 1–3 minutes per
ordinary PDF; the WPAM (~700 pages) alone takes 20–40 minutes; statute chapters 5–10
minutes each. A typical yearly refresh of ~40 changed documents is **1–3 hours**. (A
full re-extract of everything is ~4 hours.)

**Cost:** Fargate ≈ $0.12/hour (so ~$0.40). AI calls: classification of each new
document ≈ pennies; alias generation ≈ **$2.70 even if every chunk in the corpus changed**
(aliases are only generated for statutes, admin rules and DOR news pages — the doc types
the embed actually uses — so the other ~72% of chunks cost nothing). Smart mode only
regenerates aliases for changed chunks, so a normal refresh is well under a dollar.

> **Scanned PDFs are not OCR'd today.** The AWS Textract fallback needs a staging bucket
> (`TEXTRACT_STAGING_BUCKET`) which is deliberately not set on the Fargate task, so when a
> PDF defeats the text extractor the job logs the skip and keeps whatever text it got
> rather than failing. DOR documents are digital PDFs, so this effectively never fires — but
> if a newly added document comes out nearly empty in Step 3.10's validation, this is why.
> Turning it on is an engineer change (add the bucket to `ingestion-stack.ts`).

```bash
./tools/ingestion/scripts/run_fargate.sh extract --smart
```

Expected output:

```
Fetching stack outputs from WisconsinBotGraphRAG...

Running ingestion phase: extract
  Cluster: arn:aws:ecs:us-east-1:123456789012:cluster/wis-dor-ingestion
  Task Definition: arn:aws:ecs:us-east-1:123456789012:task-definition/...
  Smart: true

Task started: arn:aws:ecs:us-east-1:123456789012:task/wis-dor-ingestion/0a1b2c3d...

Monitor logs:
  aws logs tail /ecs/wis-dor-ingestion --follow --profile <your-profile> --region us-east-1

Check status:
  aws ecs describe-tasks --cluster ... --tasks ... --query 'tasks[0].lastStatus'
```

The script returns immediately — the job keeps running in AWS. **Copy the two commands it
prints.** Then watch the logs:

```bash
aws logs tail /ecs/wis-dor-ingestion --follow
```

Lines to look for (in order):

```
Found 1003 documents in raw bucket
Smart mode: 41/1003 documents have stale extractions        <- should match your scrape count
Processing statutes-70 (2411893 bytes)
  Reusing cached classification for statutes-70              <- changed doc, same type: no AI call
  Extracted statutes-70: 412 chunks, type=statute
Processing wpam-wisconsin-property-assessment-manual-2027 (14022311 bytes)
  Extracted wpam-wisconsin-property-assessment-manual-2027: 3180 chunks, type=assessment_manual
  ...
Extraction complete: 1003 documents after dedup             <- total corpus, not just changed
Manifest saved to s3://wis-work-bucket-c8e69250/manifest.json
```

Alias generation runs quietly inside each document's processing; you only see lines
about it if something goes wrong (`alias gen failed (...)`, which is retried and, if
persistent, leaves that chunk without aliases — not fatal).

The job is finished when the log goes quiet and the status command prints `STOPPED`.
Confirm it exited cleanly (exit code 0):

```bash
aws ecs describe-tasks --cluster wis-dor-ingestion --tasks <task-arn-from-above> \
  --query 'tasks[0].{status:lastStatus, exit:containers[0].exitCode}'
# Expected: { "status": "STOPPED", "exit": 0 }
```

(Press Ctrl-C to stop `logs tail`; that does not stop the job.)

**Common problems**

| Symptom | Cause | Fix |
|---|---|---|
| `Smart mode: 0/1003 documents have stale extractions` | Scrape uploaded nothing, or you ran extract before scrape finished | Check the scrape log's New/Changed counts. |
| Exit code 1 within a minute, log says `ERROR: PHASE must be one of` | Script invoked wrong | Use exactly `run_fargate.sh extract --smart`. |
| Exit code 137 | Out of memory (a very large PDF) | Re-run the same command; the finished documents are cached and skipped. If it repeats on the same document, call an engineer (Section 7). |
| `CannotPullContainerError` | The Docker image is missing from the registry | An engineer must run `build_and_push.sh` (Section 7). |
| Job runs but a document you expect is not mentioned | Its raw file date is older than its cache (scrape said "unchanged") | That is correct behavior — nothing changed. To force one document: `run_fargate.sh extract --source-filter <doc-id> --force`. |
| Log says `TEXTRACT_STAGING_BUCKET is not set; skipping the Textract fallback` | A PDF defeated the text extractor and there is no OCR staging bucket configured | Not an error — the job keeps the partial text and continues. Check that document in Step 3.10; if it really is a scanned image, call an engineer. |

Re-running the extract command is always safe: it skips anything already done.

### Step 3.5 — Embed (smart mode)

**What:** a second Fargate job turns every chunk whose extraction is newer than its
embedding into a vector.

**Time:** 5–20 minutes for a typical refresh (full corpus ≈ 15–30 minutes).
**Cost:** Fargate pennies; the Titan embedding model ≈ **$0.50–2** for a refresh.

```bash
./tools/ingestion/scripts/run_fargate.sh embed --smart
```

Watch the logs the same way. Lines to look for:

```
Smart mode: 41/1003 documents have stale embeddings
Total chunks to embed: 9814
  statutes-70: embedded 100/412 chunks
  ...
Embedding complete: 9814/9814 chunks embedded
GC: no stale embedded files found
```

**What "pass" looks like:** `Embedding complete: N/N` with the two numbers equal, and the
task exits 0.

> If the first line says something like `Smart mode: 1166/2364 documents have stale
> embeddings` — far more than you changed — it means earlier extract runs were never
> embedded (this happened in 2026, see "Task 64" in `docs/tasks.md`). It is safe to let
> it run; it just takes longer (~30 minutes) and costs a few dollars more. Everything it
> produces is picked up by the full load in Step 3.8.

**Common problems**

| Symptom | Fix |
|---|---|
| `FAILED embedding <doc>: ThrottlingException` | Bedrock rate limit. Re-run the same command; done documents are skipped. If it persists, add `--max-workers 2`. |
| `Embedding complete: 9700/9814` (not equal) | Some documents failed — search the log for `FAILED embedding`, then re-run the command. |

### Step 3.6 — Back up the caches (if you skipped it earlier)

See Section 5.1. Takes ~5 minutes. Do it now, before the graph is touched.

### Step 3.7 — Scale the graph database UP to 128

**What:** the full load writes ~50,000 vectors into Neptune. At the normal size (32
m-NCU) the vector step (load Phase 8) runs out of memory and fails. So you temporarily
make the database four times bigger.

**Time:** the resize itself takes **10–20 minutes**. The database stays readable during
the resize, so the chatbot keeps working. **Cost:** 128 m-NCU ≈ **$12.80/hour** versus
$3.20/hour normally — that is why Step 3.9 (scale back down) is not optional.

```bash
aws neptune-graph update-graph --graph-identifier g-svphgiu4k6 --provisioned-memory 128
```

Expected: a JSON block with `"status": "UPDATING"` and `"provisionedMemory": 128`.

Wait until it is available again. Check every couple of minutes:

```bash
aws neptune-graph get-graph --graph-identifier g-svphgiu4k6 \
  --query '{status:status, memory:provisionedMemory}'
# Wait for: { "status": "AVAILABLE", "memory": 128 }
```

Or let the terminal wait for you:

```bash
until [ "$(aws neptune-graph get-graph --graph-identifier g-svphgiu4k6 --query status --output text)" = "AVAILABLE" ]; do
  echo "$(date +%H:%M) still resizing..."; sleep 60
done; echo "Graph is AVAILABLE"
```

**Do not start the load until it says AVAILABLE at 128.**

### Step 3.8 — Load the graph

**What:** the third Fargate job rebuilds the graph from the work-bucket caches: document
records, chunks, citation links, then vectors, then a cleanup of anything orphaned. It
runs 10 numbered internal phases (the last is a self-check). It is a *full* load — every
document, not just the changed ones — which is what keeps the graph consistent.

**Time:** **~11 minutes** at 128 m-NCU with the 2026 corpus. (Older notes in this repo
say 30–90 minutes; that was before batch tuning. If it takes more than an hour, look at
the log — it is probably being throttled, which is slow but not fatal.)

**Cost:** Fargate pennies. Neptune at 128 for the load window + the two resizes ≈ 1–1.5
hours ≈ **$13–20**.

**Impact on users:** during Phase 5 (chunks) and Phase 8 (vectors) the chatbot may
briefly find fewer passages than usual. Expect ~15 minutes of slightly weaker answers.

```bash
./tools/ingestion/scripts/run_fargate.sh load
```

Watch the log for the ten phases in order. Each starts with a banner line
(`Phase N: Name` between rows of `=`), followed by that phase's own progress lines:

```
Graph: g-svphgiu4k6; cache prefix: ''
Loaded 1003 documents for graph loading
============================================================
Phase 1: Scaffold
============================================================
Phase 1: Creating framework scaffold...
...
Phase 2: Document Nodes
Phase 2: Creating document nodes...
  Phase 2 progress: 500/1003 document nodes
...
Phase 3: Statute Hierarchy
Phase 4: Hierarchy Links
Phase 5: Chunk Nodes
Phase 5: Creating chunk nodes with headings + chunk-level CITES edges (batch size 10)...
  Phase 5 progress: 12000 chunks, 3100 CITES edges
Phase 6: Case Law CITES
Phase 7: Stub Resolution
Phase 8: Vector Upserts
Phase 8: Upserting chunk vectors (parallel workers=8)...
Phase 9: Orphan Cleanup
Phase 9: Cleaning up orphan stubs...
Phase 10: Integrity Checks

Graph loading complete!
```

**What "pass" looks like:** all ten phase banners appear, the last line is
`Graph loading complete!`, no `Traceback` or `Phase 10 FAILED` lines, and the task exits 0.

> Phase 10 is a self-check (for example, it refuses a graph where a court case ended up
> with a doubled title). If it prints `Phase 10 FAILED: ...` the task exits 1 on purpose.
> The graph is still usable, but call an engineer with the message before doing anything
> else.

**Common problems**

| Symptom | Cause | Fix |
|---|---|---|
| Phase 8 fails with memory / `UnprocessableException` errors | Graph is not at 128 | Confirm Step 3.7 shows `AVAILABLE` / `128`, then resume: `run_fargate.sh load --start-phase 8` |
| Repeated `Throttling ... retrying in 30s` | Normal under heavy write; the loader backs off automatically | Wait. Only intervene if no progress for 20+ minutes. |
| `AccessDeniedException` on a MERGE/DELETE | IAM permission missing from the Fargate role | Engineer (Section 7) — infrastructure change. |
| `Phase 2: WPAM doc '...' has no extractable edition_year` | New WPAM file name did not match the expected `wpamNN.pdf` pattern | Warning only; the doc loads but may not be treated as "latest". Tell an engineer. |
| Job died mid-way (exit 1 or 137) | See the log for the last `Phase N` banner | Re-run with `--start-phase N` (1–10) for that phase. Phases are safe to repeat. |
| `Phase 10 FAILED: ...` | The self-check found an inconsistency (e.g. a doubled case-law title) | Do not re-run blindly; call an engineer with the message. |

To resume after a failure in, say, Phase 5:

```bash
./tools/ingestion/scripts/run_fargate.sh load --start-phase 5
```

### Step 3.9 — Scale the graph database back DOWN to 32

**Do this as soon as the load exits 0.** Every hour you forget costs ~$9.60 extra.

```bash
aws neptune-graph update-graph --graph-identifier g-svphgiu4k6 --provisioned-memory 32
```

Expected: `"status": "UPDATING"`, `"provisionedMemory": 32`. It takes 10–20 minutes to
settle; you do not have to wait — the chatbot keeps working throughout. Check later that
it reads `AVAILABLE` / `32`.

> **Do not use 16.** Neptune rejects it for this graph ("cannot be scaled down to [16]
> m-NCUs due to storage memory constraints"). 32 is the floor.

### Step 3.10 — Validate

Three checks, from cheapest to most thorough. Do at least the first two.

**(a) Counts (1 minute, free).** Re-run the queries from Section 2.5. Expect the chunk
count to have gone *up* a little (new WPAM edition, new guides) and the new document IDs
from Step 3.2 to be present:

```bash
aws neptune-graph execute-query --graph-identifier g-svphgiu4k6 --language OPEN_CYPHER \
  --query-string "MATCH (d) WHERE d.id = 'wpam-wisconsin-property-assessment-manual-2027' RETURN d.id, d.title" /dev/stdout
# Expected: one row with the title. Empty "results":[] means the doc did not load.
```

**(b) Smoke test in the browser (10 minutes, free).** Open the chatbot and ask 4–5
questions you know the answers to, including one that should cite the new WPAM year and
one FAQ. Check that the citation cards open to the right page.

**(c) Recall probe — "does raw search still find the right document?" (10 minutes,
< $1).** This runs 118 real tester questions against the graph and reports where the
expected document ranked. It is a *before/after* tool, so the honest way to use it is:
run `--mode baseline` **before** Step 3.7 and `--mode after` now. If you did not run the
baseline, run `after` alone and compare against the numbers below.

```bash
# BEFORE the load (ideally right after Step 2.5):
uv run python tools/ingestion/ops/run_recall_probe.py --mode baseline \
  --graph-id g-svphgiu4k6 --label "pre-refresh $(date +%Y-%m-%d)"

# AFTER Step 3.9:
uv run python tools/ingestion/ops/run_recall_probe.py --mode after \
  --graph-id g-svphgiu4k6 --label "post-refresh $(date +%Y-%m-%d)"

uv run python tools/ingestion/ops/run_recall_probe.py --compare-only
```

The comparison prints a summary block for each run, then a per-question table:

```
baseline: pre-refresh 2027-04-02 (graph g-svphgiu4k6, 2027-04-02T14:03:11Z)
after:    post-refresh 2027-04-02 (graph g-svphgiu4k6, 2027-04-02T19:40:52Z)

== baseline ==
  doc        n=118, recall@10=0.7119, recall@30=0.8644, recall@60=0.9153, mrr=0.5231, not_in_top_60=10
  chunk      n=41, ...
  subsection n=17, ...
  doc/gold_cited    n=96, recall@10=..., ...
  doc/gold_missing  n=22, recall@10=..., ...

== after ==
  doc        n=118, recall@10=0.7203, recall@30=0.8729, recall@60=0.9237, mrr=0.5310, not_in_top_60=9
  ...
```

Read the `doc` row of each block. **What "pass" looks like:** `recall@10` and
`recall@30` within **±0.03** of baseline and `not_in_top_60` not higher by more than 2–3
cases. Small movement in either direction is normal because documents genuinely changed.
A drop of 0.05 or more in `recall@30`, or `not_in_top_60` jumping by 10+, means
something did not load — check the log from Step 3.8 and consider the rollback in
Section 5.

Results are saved as `tools/ingestion/ops/recall_probe_baseline.json` /
`recall_probe_after.json` (not committed to git).

**(d) Regression harness — "are the answers still right?" (30–45 minutes, ≈ $5–15
in AI calls).** This asks the real chatbot pipeline 43 curated questions and grades each
answer: did it cite the required document, did it satisfy a DOR-written rubric, did it
invent a case citation. It needs the same settings the live chatbot uses. Get them from
the deployed function once:

```bash
FN=$(aws lambda list-functions --query "Functions[?contains(FunctionName,'AgenticRetrievalFunction')].FunctionName" --output text)
aws lambda get-function-configuration --function-name "$FN" \
  --query 'Environment.Variables.{NEPTUNE_GRAPH_ID:NEPTUNE_GRAPH_ID, FAQ_KNOWLEDGE_BASE_ID:FAQ_KNOWLEDGE_BASE_ID, RAW_BUCKET:RAW_BUCKET, AGENTIC_MODEL_ID:AGENTIC_MODEL_ID, FAQ_URL_TABLE_NAME:FAQ_URL_TABLE_NAME}'
```

Export each of the printed values (`export NEPTUNE_GRAPH_ID=g-svphgiu4k6` and so on), then:

```bash
# Match production: the adequacy judge is ON in prod, so run the harness with it on.
export ADEQUACY_JUDGE_ENABLED=true

# BEFORE the load:
uv run python tools/ingestion/ops/run_graph_regression.py --mode baseline
# AFTER the load:
uv run python tools/ingestion/ops/run_graph_regression.py --mode after
uv run python tools/ingestion/ops/run_graph_regression.py --compare-only
```

The golden set is **63 cases**. Three of them are not clean signals and should not be
treated as refresh failures: `gq-full-value-annual` and `gq-prior-year-roll` are flaky,
and `gq-bor-interpreter-waiver` never passes by design (it is a DOR content question, not
a retrieval bug). See [testing.md](testing.md) for the detail.

Each run ends with a grade summary:

```
=== GRADE SUMMARY (after) ===
  [B] gr-0007: OK  [judge PASS: answer states the 30-day objection window ...]
  [D] gr-0012: FAIL missing must_cite ['statutes-70']
  ...
39/45 passed intra-run gates (flagged cases listed above).
Gate = must_cite AND judge(rubric) AND no-hallucination.
```

And `--compare-only` prints one block per question, marking `⚠️ REGRESSION` where an
answer lost a required citation or fact it had at baseline, and ends with a regression
count.

**What "pass" looks like:** the `after` pass count is **equal to or higher than** the
baseline's, and `--compare-only` reports **0 regressions** (or only regressions you can
explain by the content change — e.g. a guide DOR retired). One or two flakes on
long-tail questions do happen; re-run just those with `--ids gr-0012` before worrying.

If the harness reports several regressions you cannot explain: roll back (Section 5) and
call an engineer with the two JSON files
(`tools/ingestion/ops/graph_regression_baseline.json` / `_after.json`).

### Step 3.11 — FAQ sync

**What:** the FAQs are *not* in the graph. They live as files in a separate bucket
(`wis-faq-bucket`, in us-west-2 — the bucket DOR's FAQ export lands in) and are indexed
by a Bedrock Knowledge Base. This script copies any changed files to the chatbot's FAQ
bucket and tells the Knowledge Base to re-index.

**When:** whenever DOR publishes updated FAQ files to `wis-faq-bucket`. Do it at least
at the yearly refresh.

**Time:** 5–15 minutes (the re-index waits for AWS). **Cost:** < $1.

```bash
./tools/ingestion/scripts/sync_faq_bucket.sh
```

Expected output:

```
==> Looking up GraphRAG FAQ bucket and KB IDs from CloudFormation stack outputs...
  Source:      s3://wis-faq-bucket (us-west-2)
  Target:      s3://wisconsinbotgraphrag-...-faqbucket... (us-east-1)
  KB ID:       ABCDEF1234
  DataSource:  GHIJKL5678
==> Syncing FAQ files...
copy: s3://wis-faq-bucket/... to s3://.../...
  212 files in target bucket.
==> Starting Bedrock KB ingestion job...
  Ingestion job started: 0a1b2c3d-...
==> Waiting for ingestion to complete...
  Status: STARTING
  Status: IN_PROGRESS
  Status: COMPLETE
==> Ingestion complete!
```

**What "pass" looks like:** `Status: COMPLETE`. If it prints `FAILED`, open the AWS
console → Bedrock → Knowledge bases → the KB → Data source → "Sync history" for the
reason (usually a file that is not valid text/JSON).

If FAQ *questions or their source URLs* changed (not just answers), also refresh the
question→URL lookup table so citation cards link correctly. This needs the
`documents/faqs.json` export that the FAQ pipeline produces — ask the engineer who
maintains the FAQ export where the current copy lives, then:

```bash
TABLE=$(aws cloudformation describe-stacks --stack-name WisconsinBotGraphRAG \
  --query "Stacks[0].Outputs[?contains(OutputKey,'FaqUrlTable')].OutputValue" --output text)
uv run python tools/ingestion/ops/seed_faq_url_table.py --table "$TABLE" --faqs /path/to/faqs.json --dry-run
uv run python tools/ingestion/ops/seed_faq_url_table.py --table "$TABLE" --faqs /path/to/faqs.json
```

### Step 3.12 — Case law (optional, engineer-assisted)

Court decisions are discovered automatically from the hyperlinks in the statute PDFs
and enriched from CourtListener. This step is **optional at the yearly refresh** — the
existing ~1,150 cases stay in the graph regardless — and it has two known sharp edges
(duplicate detection and a Google Scholar fallback that can attach the wrong opinion;
see Tasks 45 and 49 in `docs/tasks.md`). Run it only with an engineer available:

```bash
uv run python tools/ingestion/ingest_case_law.py \
  --bucket wis-raw-bucket-c8e69250 --from-s3 --resume
```

New cases it uploads are then picked up by `extract --smart` → `embed --smart` → `load`
(Steps 3.4–3.9). If you run this, run it **before** Step 3.4.

### Step 3.13 — TID worksheets

**What:** the four TID base-value Excel forms (`tidbase`, `tidsub`, `decrement`,
`tidbase-ppremoval`) are data-entry spreadsheets, not PDFs, so they are not in the graph.
A script reads each `.xlsx` from the raw bucket and writes a JSON description (labels,
instructions, the meaningful formulas — described, never calculated) that the chatbot's
`get_worksheet` tool reads.

**When:** whenever DOR re-issues those forms (typically yearly).

**Where the script gets the spreadsheets:** it first looks in the raw bucket at
`raw/form_instructions-<name>/form_instructions-<name>.xlsx`; if a file is *not* there it
downloads the public copy from `revenue.wi.gov/DORForms/<name>.xlsx`. The scraper never
touches these files (they are listed only as comments in the manifest). So, to make sure
you convert the **new** forms and not a stale copy left in the bucket, either upload the
new files to those keys, or simply delete the old bucket copies so the script fetches
fresh from DOR's site:

```bash
for n in tidbase tidsub decrement tidbase-ppremoval; do
  aws s3 rm s3://wis-raw-bucket-c8e69250/raw/form_instructions-$n/form_instructions-$n.xlsx
done
# ("delete: ..." per file, or a silent no-op if the file was never there)
```

**Time:** 1 minute. **Cost:** none.

```bash
uv run --with openpyxl python -m tools.ingestion.worksheets.extract_worksheets \
  --raw-bucket wis-raw-bucket-c8e69250
```

Expected output (four blocks like this, no `Traceback`):

```
Extracting tidbase (tidbase.xlsx)
  not in bucket at raw/form_instructions-tidbase/form_instructions-tidbase.xlsx; fetching public URL
  [CoMuniData] skipped (hidden)
  uploaded s3://wis-raw-bucket-c8e69250/worksheets/tidbase.json
Extracting tidsub (tidsub.xlsx)
  ...
```

(`--with openpyxl` is required; without it you get
`ModuleNotFoundError: No module named 'openpyxl'`.)

To preview locally without uploading: `--local-dir <folder-with-xlsx> --out-dir <folder>`.

### Step 3.14 — Flowchart re-verify checklist (manual)

**What:** the WPAM contains six decision flowcharts (agricultural classification,
manufacturing classification, mobile-home exemption, §70.11 exemption, Bible camps,
property held in trust). They are printed as pictures, so a person typed each one into a
JSON file by hand. The chatbot uses them to walk a user through the decision and to link
to the exact WPAM page.

**Each year, two things can go stale, and no automated test can catch either:**

1. **Page numbers shift.** The new WPAM edition almost always re-paginates. Each JSON
   has a `source` block with `pdf_page` (the page in the PDF file), `wpam_page` (the
   printed page number like `18-22`), and `source_url` ending in `#page=N`. If these are
   wrong, the citation card opens to the wrong page.
2. **Branch logic changes.** Statutes get amended; DOR redraws a chart. If a step's
   question or a yes/no branch changed, the chatbot walks users through the old rule.

**Time:** 20–40 minutes per chart, **2–4 hours** for all six. **Cost:** none.

The files are in `tools/ingestion/flowcharts/data/`:

| File | Chart |
|---|---|
| `flowcharts-ag-classification.json` | Determining Agricultural Classification |
| `flowcharts-manufacturing.json` | Manufacturing Classification (Construction-Related Establishment) |
| `flowcharts-mobile-home.json` | Determining if a Mobile Home is Exempt or Taxable |
| `flowcharts-exempt-70-11.json` | Determining if Property is Exempt under sec. 70.11 |
| `flowcharts-bible-camp.json` | Bible Camps — Determining if Property is Exempt |
| `flowcharts-trust-public.json` | Property Held in Trust in Public Interest |

**Checklist — repeat for each of the six files:**

- [ ] Open the new WPAM PDF. Find the chart (search the PDF for the chart's title).
- [ ] Note the PDF page number (what your PDF viewer shows) and the printed page number
      (e.g. `18-22` in the footer).
- [ ] In the JSON, update `source.pdf_page`, `source.wpam_page`, and the `#page=N` at the
      end of `source.source_url`. Update `source.source_url` to the new edition's file
      (`wpam27.pdf`) and `source.doc_id` to the new document ID from Step 3.2.
- [ ] Compare the chart's boxes to the JSON `nodes` list, one by one:
      every `decision` node's `question` text still matches; every `outcome` still
      matches; the statute cites in `authorities` still match.
- [ ] Compare the arrows to the JSON `edges`: each `from` → `to` with the right
      `branch` (`yes` / `no`). Any added, removed, or re-routed arrow must be reflected.
- [ ] If the chart was **removed** from the new WPAM: leave the file alone and tell an
      engineer (the chart registry in code has to change).
- [ ] If a **new** chart appears in the WPAM: tell an engineer (new chart = new code entry).

After editing, run the structure test — it catches broken JSON, dangling edges, and
missing start nodes (it cannot catch a wrong page number or an outdated question):

```bash
uv run pytest backend/lambdas/agentic_retrieval/tests/test_flowcharts.py -q
# Expected: "N passed" and no "failed".
```

Then upload:

```bash
uv run python -m tools.ingestion.flowcharts.upload_flowcharts \
  --raw-bucket wis-raw-bucket-c8e69250 --dry-run     # preview
uv run python -m tools.ingestion.flowcharts.upload_flowcharts \
  --raw-bucket wis-raw-bucket-c8e69250               # write
```

Expected output:

```
uploaded s3://wis-raw-bucket-c8e69250/flowcharts/flowcharts-ag-classification.json
uploaded s3://wis-raw-bucket-c8e69250/flowcharts/flowcharts-bible-camp.json
uploaded s3://wis-raw-bucket-c8e69250/flowcharts/flowcharts-exempt-70-11.json
uploaded s3://wis-raw-bucket-c8e69250/flowcharts/flowcharts-manufacturing.json
uploaded s3://wis-raw-bucket-c8e69250/flowcharts/flowcharts-mobile-home.json
uploaded s3://wis-raw-bucket-c8e69250/flowcharts/flowcharts-trust-public.json
6 flowchart sidecar(s) processed
```

(The dry run prints the same six lines prefixed `[dry-run] would upload`.)

Finally, in the browser, ask the chatbot a question that triggers one chart (e.g.
"Is my camping trailer exempt from property tax?") and confirm the "Walk the flowchart"
panel appears and its WPAM link opens the right page.

Commit the edited JSON files to git (or hand them to an engineer to commit) — they are
the source of truth; the bucket copy is just a deployment.

### Step 3.15 — Optional: attach gold tester queries

**What:** if reviewers rated chatbot answers thumbs-up in the admin dashboard, those real
questions can be attached to the passages they cited so that future users' phrasing
retrieves the same passage. This is an optional quality boost, **not** required for the
refresh. It writes into the `extracted/` cache, so it must be followed by
`embed --smart` and a load — i.e. do it **between Steps 3.4 and 3.5**, or as its own
mini-cycle later.

**Time:** 10–20 minutes. **Cost:** < $1 (embedding calls).

```bash
# 1. Rebuild the list of rated questions from chat history (no --graph-id: it reads
#    the ChatHistoryTable, not the graph):
uv run python tools/ingestion/ops/build_recall_eval.py
#    (writes tools/ingestion/tests/recall_eval_queries.yaml)

# 2. Preview, then attach:
uv run python -m tools.ingestion.ops.attach_gold_queries \
  --work-bucket wis-work-bucket-c8e69250 --eval-yaml tools/ingestion/tests/recall_eval_queries.yaml --dry-run
uv run python -m tools.ingestion.ops.attach_gold_queries \
  --work-bucket wis-work-bucket-c8e69250 --eval-yaml tools/ingestion/tests/recall_eval_queries.yaml

# 3. Then: run_fargate.sh embed --smart  → scale up → run_fargate.sh load → scale down
```

Note: a full re-extract of a document (Step 3.4 when that document changed) drops its
old attachments, so re-run this after the yearly extract if you want to keep them.

### Step 3.16 — Prompts (normally unchanged)

The chatbot's instructions are in `config/model_configs.toml` and are copied into a
DynamoDB table by a script. A data refresh does **not** change them. You only touch this
if an engineer has edited the TOML file (for example, to mention the new WPAM year in
the instructions). In that case:

```bash
uv run python tools/upload_model_configs.py --dry-run                 # validates only
uv run python tools/upload_model_configs.py --only agenticRetrieval   # pushes one entry
```

Expected output of the real upload:

```
Parsing: config/model_configs.toml
Found 4 configuration(s):
  - agenticRetrieval (model: us.anthropic.claude-sonnet-4-6)
  ...
Uploading 1 configuration(s) to table: WisconsinBotGraphRAG-...-ModelConfigTable...
  ✓ agenticRetrieval
✓ Done
```

(The dry run ends with `Dry run — not uploading.` instead.) The change is live for the
next chatbot conversation — no deployment needed.

### Step 3.17 — Wrap up

- [ ] Graph is back at 32 m-NCU (`get-graph` shows `"memory": 32`).
- [ ] Manifest edit and flowchart JSON edits are committed to git (or handed to an
      engineer with a note).
- [ ] Keep your scrape log and the recall/regression JSON files somewhere for next year's
      comparison.
- [ ] Delete the rollback copy (Section 5.3) after a couple of weeks of normal operation.

---

## 4. Adding a single new document mid-year

This is the scoped cycle used in September 2026 to add a treaty publication and one FAQ
page ("Task 61" in `docs/tasks.md`). It loads only the new document(s) and leaves the
rest of the graph untouched, so **the graph does not need to be scaled up**.

**Time:** ~45–60 minutes, mostly waiting. **Cost:** ~$1–3.

1. **Add the URL** to the right category in `document_manifest.yaml` (Step 3.1).

2. **Scrape only that category** and note the new document ID:
   ```bash
   uv run python tools/ingestion/scrape_documents.py --bucket wis-raw-bucket-c8e69250 \
     --category gov_publications --dry-run          # confirm the ID
   uv run python tools/ingestion/scrape_documents.py --bucket wis-raw-bucket-c8e69250 \
     --category gov_publications
   ```
   > Scoping to a category still re-checks every URL *in that category*, so a few
   > existing documents may show CHANGED too. That is fine: they get re-extracted and
   > re-embedded into the caches, and they will be loaded at the next full load. If you
   > want them in the graph now, load them too in step 5.

3. **Extract and embed, smart mode** (only the changed docs are processed):
   ```bash
   ./tools/ingestion/scripts/run_fargate.sh extract --smart
   # wait for STOPPED / exit 0 (Step 3.4), then:
   ./tools/ingestion/scripts/run_fargate.sh embed --smart
   ```

4. **Load only the new document.** `--source-filter` matches document IDs by *prefix*,
   so use the full ID to hit exactly one document:
   ```bash
   ./tools/ingestion/scripts/run_fargate.sh load --source-filter gov_publications-napt-treaty-update
   ```
   Repeat once per new document. Graph-wide phases (1, 3, 7, 9) still run but only
   touch what is missing, and a single-document vector upsert fits comfortably at 32
   m-NCU.

5. **Verify** with the count query for that ID (Step 3.10a) and by asking the chatbot a
   question the new document should answer. Confirm the citation card opens the right URL.

6. **Commit** the manifest change.

A prefix filter also works for a family: `--source-filter news_pages-2027-` loads every
2027 news item at once.

---

## 5. Backup and rollback

The graph is rebuilt from the four cache folders in the work bucket. If you have a copy
of those folders, you can always get back to a known-good graph with one load. The
convention is a dated `rollback-YYYYMMDD/` folder in the work bucket — an example from
the 2026 alias promotion exists at `s3://wis-work-bucket-c8e69250/rollback-20260912/`.

### 5.1 Take a backup (before any refresh) — ~5 minutes, ~$1/month storage

```bash
STAMP=$(date +%Y%m%d)
for d in classified extracted aliases embedded; do
  aws s3 sync s3://wis-work-bucket-c8e69250/$d/ s3://wis-work-bucket-c8e69250/rollback-$STAMP/$d/ --only-show-errors
done
aws s3 cp s3://wis-work-bucket-c8e69250/manifest.json s3://wis-work-bucket-c8e69250/rollback-$STAMP/manifest.json
aws s3 ls s3://wis-work-bucket-c8e69250/rollback-$STAMP/
# Expected: four PRE lines (classified/ extracted/ aliases/ embedded/) and manifest.json
```

Also snapshot the raw bucket's sidecars (tiny):

```bash
aws s3 sync s3://wis-raw-bucket-c8e69250/flowcharts/ s3://wis-work-bucket-c8e69250/rollback-$STAMP/flowcharts/ --only-show-errors
aws s3 sync s3://wis-raw-bucket-c8e69250/worksheets/ s3://wis-work-bucket-c8e69250/rollback-$STAMP/worksheets/ --only-show-errors
```

### 5.2 Roll back — ~45 minutes, ~$15

Use this if validation (Step 3.10) shows real regressions or the load left the graph
broken.

```bash
STAMP=20270402   # the backup you want to restore

# 1. Put the caches back exactly as they were (--delete removes anything newer):
for d in classified extracted aliases embedded; do
  aws s3 sync s3://wis-work-bucket-c8e69250/rollback-$STAMP/$d/ s3://wis-work-bucket-c8e69250/$d/ --delete --only-show-errors
done
aws s3 cp s3://wis-work-bucket-c8e69250/rollback-$STAMP/manifest.json s3://wis-work-bucket-c8e69250/manifest.json

# 2. Scale up (Step 3.7), wait for AVAILABLE
aws neptune-graph update-graph --graph-identifier g-svphgiu4k6 --provisioned-memory 128

# 3. Full load (Step 3.8)
./tools/ingestion/scripts/run_fargate.sh load

# 4. Scale down (Step 3.9)
aws neptune-graph update-graph --graph-identifier g-svphgiu4k6 --provisioned-memory 32
```

The load's Phase 9 removes chunks and documents that are no longer in the caches, so the
graph ends up matching the backup.

Sidecars roll back with a plain copy:

```bash
aws s3 sync s3://wis-work-bucket-c8e69250/rollback-$STAMP/flowcharts/ s3://wis-raw-bucket-c8e69250/flowcharts/ --delete
aws s3 sync s3://wis-work-bucket-c8e69250/rollback-$STAMP/worksheets/ s3://wis-raw-bucket-c8e69250/worksheets/ --delete
```

The raw bucket is **not** rolled back by this procedure (the newer source PDFs stay).
That is intentional: next time you run `extract --smart`, it will see those raw files
are newer than the restored caches and re-process them — which is exactly what you want
once the underlying problem is fixed.

### 5.3 Clean up old backups

Each backup is a few GB. After a refresh has been in production for a few weeks:

```bash
aws s3 rm s3://wis-work-bucket-c8e69250/rollback-20260912/ --recursive
```

Keep at least the most recent one.

---

## 6. Monitoring and cost

### 6.1 What runs every month with no action from you

| Item | Approximate monthly cost | Notes |
|---|---|---|
| Neptune Analytics at 32 m-NCU | **≈ $2,300** ($3.20/hr × 730 hr) | The dominant cost. Fixed regardless of usage. It only changes if someone scales it (Step 3.7/3.9) — check it is at 32. |
| Bedrock (the AI models) | usage-based; typically **$50–300** | Each chatbot answer = one Claude Sonnet conversation (a few cents) plus embeddings/FAQ lookups. Scales with question volume. |
| Bedrock Knowledge Base (FAQ) | small | Vector store for the FAQs. |
| Lambda, API Gateway, DynamoDB, CloudFront, S3 | **$10–40** | Web app, chat API, chat history, document storage. |
| Fargate | $0 when idle | Only bills while a pipeline job runs (~$0.12/hr). |

### 6.2 Where to look

- **Overall bill:** AWS console → Billing → Cost Explorer, group by *Service*. Neptune
  shows as "Amazon Neptune"; the AI models as "Amazon Bedrock".
- **Is the graph at the right size?** Section 2.5's `get-graph` command. If it shows 128
  and nobody is loading, scale it down (Step 3.9).
- **Pipeline job logs:** CloudWatch log group `/ecs/wis-dor-ingestion` (30-day
  retention). `aws logs tail /ecs/wis-dor-ingestion --since 2h`.
- **Chatbot runtime logs:** log group
  `/aws/lambda/WisconsinBotGraphRAG-Wisc-AgenticRetrievalFunction-AsC0c2SWW4Hf`.
- **Bedrock usage:** console → Bedrock → Model invocation metrics, or Cost Explorer
  filtered to Amazon Bedrock with *Usage type* grouping.

### 6.3 Seeing what testers and users are asking

Two ways:

**Admin dashboard (easiest).** Open the chatbot site at `/admin/activity` (requires a
Cognito login). It lists every question, the answer, thumbs up/down and comments, with
filters for date range, feedback status, and text search.

**Direct database query.** Every question and answer is a row in the DynamoDB chat
history table. To pull everything from the last week into a file:

```bash
aws dynamodb scan \
  --table-name "WisconsinBotGraphRAG-WisconsinSessionsStackNestedStackWisconsinSessionsStackNestedStac-1P3H46X50M51H-ChatHistoryTableA22BA13C-GTH2UH9SGD0W" \
  --filter-expression "#ts >= :cutoff" \
  --expression-attribute-names '{"#ts": "timestamp"}' \
  --expression-attribute-values "{\":cutoff\": {\"S\": \"$(date -v-7d +%Y-%m-%d)\"}}" \
  --output json > chat_last_week.json
jq '.Items | length' chat_last_week.json         # how many
jq -r '.Items[] | .query.S' chat_last_week.json  # just the questions
```

Only the thumbs-down ones:

```bash
aws dynamodb scan \
  --table-name "WisconsinBotGraphRAG-WisconsinSessionsStackNestedStackWisconsinSessionsStackNestedStac-1P3H46X50M51H-ChatHistoryTableA22BA13C-GTH2UH9SGD0W" \
  --filter-expression "thumbUp = :val" \
  --expression-attribute-values '{":val": {"BOOL": false}}' \
  --output json | jq -r '.Items[] | "\(.timestamp.S)  \(.query.S)\n    -> \(.feedback.S // "")"'
```

Thumbs-down items that say "it should have cited X" are the raw material for Step 3.15
and for deciding what to add to the manifest next year.

---

## 7. When to call an engineer

This runbook covers *data* updates. The following are **not** data updates. Do not try to
work around them — each one requires changing code or infrastructure, and the code side
has a hidden step (rebuild the Docker image) that is the number-one cause of "I fixed it
but nothing changed."

| Situation | Why it needs an engineer |
|---|---|
| Any file under `tools/ingestion/` changes (a fix to the chunker, the scraper, a new category, a new document type) | Fargate runs a **packaged image**, not the files on disk. After a code change someone must run `cd tools/ingestion/docker && ./build_and_push.sh` (needs Docker Desktop, ~10 min) or every Fargate job silently uses the old code. |
| A new category of document (not just a new URL in an existing category) | Needs entries in `ingest_config.yaml`, possibly a new chunking strategy, and the image rebuild. |
| The chatbot's instructions ("prompt") should say something different | Editing `config/model_configs.toml` changes behavior in ways that need the regression harness run against the candidate prompt (`--candidate-agenticretrieval`) before upload. |
| A new or removed WPAM flowchart | The flowchart registry lives in code (`backend/lambdas/agentic_retrieval/flowcharts.py`). |
| A new TID worksheet (a fifth form), or a worksheet whose layout changed so the JSON looks wrong | The worksheet registry lives in code (`extract_worksheets.py` `WORKSHEETS` and the Lambda's `WORKSHEET_REGISTRY`). |
| Anything about the website itself (wording, layout, login, the admin dashboard) | Frontend code + a CDK deploy. |
| `AccessDeniedException`, `CannotPullContainerError`, a missing CloudFormation output, or any command that says "stack" is wrong | Infrastructure (CDK). Needs `bun run bundle` + `cdk diff` + `cdk deploy`. |
| Repeated exit code 137 on the same document, or a load that never finishes | Memory/batch constants in `load.py` (code). |
| The regression harness shows regressions you cannot explain by content changes | Retrieval logic; hand over `graph_regression_baseline.json` / `_after.json`. |
| Case-law duplicates or a case citing the wrong opinion | `ops/dedup_case_law_docket.py` / `purge_corrupt_case_law.py` — engineer-run one-off scripts. |
| Neptune refuses to scale, or the graph shows `status` other than `AVAILABLE` for over an hour | AWS support / engineer. |

What to send when you call: the exact command you ran, the task ARN (from `Task started:`),
the last ~50 lines of `aws logs tail /ecs/wis-dor-ingestion`, and the output of the
`get-graph` command.

---

## 8. Quick reference — all commands in order

```bash
# ---------- 0. Session setup (every terminal) ----------
cd /path/to/wisconsin-dor-polished
export AWS_PROFILE=<your-profile> AWS_REGION=us-east-1
export AWS_CA_BUNDLE=$(.venv/bin/python3 -c "import certifi; print(certifi.where())")
aws sts get-caller-identity

# ---------- 1. Baseline ----------
aws neptune-graph get-graph --graph-identifier g-svphgiu4k6 --query '{status:status,memory:provisionedMemory}'
aws neptune-graph execute-query --graph-identifier g-svphgiu4k6 --language OPEN_CYPHER \
  --query-string "MATCH (c:Chunk) RETURN count(c) AS chunks" /dev/stdout
uv run python tools/ingestion/ops/run_recall_probe.py --mode baseline --graph-id g-svphgiu4k6 --label "pre-refresh"
# (optional, ~40 min) uv run python tools/ingestion/ops/run_graph_regression.py --mode baseline

# ---------- 2. Backup caches ----------
STAMP=$(date +%Y%m%d)
for d in classified extracted aliases embedded; do
  aws s3 sync s3://wis-work-bucket-c8e69250/$d/ s3://wis-work-bucket-c8e69250/rollback-$STAMP/$d/ --only-show-errors
done

# ---------- 3. Manifest + scrape ----------
#   edit tools/ingestion/config/document_manifest.yaml
uv run python tools/ingestion/scrape_documents.py --bucket wis-raw-bucket-c8e69250 --dry-run
uv run python tools/ingestion/scrape_documents.py --bucket wis-raw-bucket-c8e69250 2>&1 | tee scrape-$STAMP.log

# ---------- 4. Extract + embed (Fargate; wait for each to reach STOPPED / exit 0) ----------
./tools/ingestion/scripts/run_fargate.sh extract --smart
aws logs tail /ecs/wis-dor-ingestion --follow
./tools/ingestion/scripts/run_fargate.sh embed --smart
aws logs tail /ecs/wis-dor-ingestion --follow

# ---------- 5. Scale up, load, scale down ----------
aws neptune-graph update-graph --graph-identifier g-svphgiu4k6 --provisioned-memory 128
until [ "$(aws neptune-graph get-graph --graph-identifier g-svphgiu4k6 --query status --output text)" = "AVAILABLE" ]; do sleep 60; done
./tools/ingestion/scripts/run_fargate.sh load
aws logs tail /ecs/wis-dor-ingestion --follow           # wait for "Graph loading complete!" + exit 0
aws neptune-graph update-graph --graph-identifier g-svphgiu4k6 --provisioned-memory 32

# ---------- 6. Validate ----------
aws neptune-graph execute-query --graph-identifier g-svphgiu4k6 --language OPEN_CYPHER \
  --query-string "MATCH (c:Chunk) RETURN count(c) AS chunks" /dev/stdout
uv run python tools/ingestion/ops/run_recall_probe.py --mode after --graph-id g-svphgiu4k6 --label "post-refresh"
uv run python tools/ingestion/ops/run_recall_probe.py --compare-only
# (optional) uv run python tools/ingestion/ops/run_graph_regression.py --mode after
#            uv run python tools/ingestion/ops/run_graph_regression.py --compare-only

# ---------- 7. FAQs ----------
./tools/ingestion/scripts/sync_faq_bucket.sh

# ---------- 8. TID worksheets (after uploading the new .xlsx files to the raw bucket) ----------
uv run --with openpyxl python -m tools.ingestion.worksheets.extract_worksheets --raw-bucket wis-raw-bucket-c8e69250

# ---------- 9. Flowcharts (after the manual re-verify of tools/ingestion/flowcharts/data/*.json) ----------
uv run pytest backend/lambdas/agentic_retrieval/tests/test_flowcharts.py -q
uv run python -m tools.ingestion.flowcharts.upload_flowcharts --raw-bucket wis-raw-bucket-c8e69250

# ---------- 10. Prompts — ONLY if config/model_configs.toml was edited ----------
uv run python tools/upload_model_configs.py --dry-run
uv run python tools/upload_model_configs.py --only agenticRetrieval

# ---------- Mid-year single document ----------
uv run python tools/ingestion/scrape_documents.py --bucket wis-raw-bucket-c8e69250 --category <category>
./tools/ingestion/scripts/run_fargate.sh extract --smart
./tools/ingestion/scripts/run_fargate.sh embed --smart
./tools/ingestion/scripts/run_fargate.sh load --source-filter <full-doc-id>      # no scale-up needed

# ---------- Rollback ----------
for d in classified extracted aliases embedded; do
  aws s3 sync s3://wis-work-bucket-c8e69250/rollback-$STAMP/$d/ s3://wis-work-bucket-c8e69250/$d/ --delete --only-show-errors
done
#   then: scale up → run_fargate.sh load → scale down
```

**Time and cost at a glance (typical yearly refresh):**

| Step | Hands-on | Wall clock | Cost |
|---|---|---|---|
| Manifest edit + dry run | 45 min | 45 min | $0 |
| Scrape | 5 min | 30 min | $0 |
| Extract `--smart` | 5 min | 1–3 h | $1–5 |
| Embed `--smart` | 5 min | 10–30 min | $1–2 |
| Scale up + load + scale down | 15 min | 45–60 min | $13–20 (Neptune at 128) |
| Validation (counts + probe + harness) | 30 min | 1 h | $5–15 |
| FAQ sync | 5 min | 15 min | < $1 |
| TID worksheets | 10 min | 10 min | $0 |
| Flowchart re-verify | 2–4 h | 2–4 h | $0 |
| **Total** | **~1 day** | **2–3 days elapsed** | **≈ $40–80** |

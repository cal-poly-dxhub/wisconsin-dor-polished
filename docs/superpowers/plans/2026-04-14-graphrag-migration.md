# GraphRAG Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Neptune Analytics GraphRAG backend alongside the existing Bedrock Knowledge Base backend, so the current chatbot continues working while the new graph-based retrieval can be tested and validated independently. The frontend/UI/UX is completely unchanged.

**Architecture:** The new GraphRAG infrastructure is deployed as a **new nested stack** alongside the existing `KnowledgeBaseStack`. A **new Step Functions state machine** (`GraphRAGStateMachine`) is created alongside the existing `ChatStreamingStateMachine`. A **new EventBridge rule** routes to it, but the two rules are **mutually exclusive** -- when `useGraphRAG=true` is set in CDK context, the new rule is enabled and the old rule is automatically disabled (and vice versa). This prevents both state machines from firing on the same event, which would corrupt WebSocket sessions. The existing streaming and resource streaming Lambdas are **shared** between both pipelines since their input contracts (`GenerateResponseJob`, `StreamResourcesJob`) are identical. Neptune Analytics is provisioned with `publicConnectivity: true` and IAM auth so the Lambda can reach it without VPC configuration.

**Tech Stack:** AWS Neptune Analytics (OpenCypher + vector search), Amazon Bedrock (Claude Sonnet for agentic loop, Titan Embed Text v2 for embeddings), AWS CDK (TypeScript), Python 3.12 Lambdas, Pydantic, existing Textract-based PDF chunking pipeline (reused), boto3.

---

## Critical Constraint: No Breaking Changes

**DO NOT modify or delete any of these existing resources:**
- `packages/knowledge-base/` - KnowledgeBaseStack, S3 buckets, Bedrock KBs
- `packages/messages/lambdas/classifier/` - Classifier Lambda
- `packages/messages/lambdas/retrieval/` - Retrieval Lambda

**Files with MINIMAL additive changes only (no existing logic modified):**
- `packages/messages/infra/messages-stack.ts` - Add 2 public readonly properties + assignments to expose existing Lambda references; the existing EventBridge rule gains a CDK context toggle
- `packages/infra/lib/stack.ts` - Add imports + instantiate new stacks
- `bundles.toml` - Add 1 new bundle entry for `agentic_retrieval`

**The existing backend must continue working at all times.**

---

## Current Architecture (unchanged, continues serving traffic)

```
Frontend (Next.js) <-> WebSocket API Gateway <-> DynamoDB Sessions
                                                       |
                                                  EventBridge
                                                       |
                                          ChatStreamingStateMachine (EXISTING)
                                                       |
                                    +------------------+------------------+
                                    |                                     |
                              Classifier Lambda                     Retrieval Lambda
                              (FAQ KB lookup)                    (RAG KB vector search)
                                    |                                     |
                              Parallel:                             Parallel:
                              - Resource Streaming Lambda      - Resource Streaming Lambda
                              - Response Streaming Lambda      - Response Streaming Lambda
```

## New GraphRAG Architecture (deployed alongside, traffic routed via config)

```
                                                  EventBridge
                                                       |
                                       GraphRAGStateMachine (NEW)
                                                       |
                                           Agentic Retrieval Lambda (NEW)
                                           (Neptune vector + graph traversal)
                                                       |
                                                   Parallel:
                                      - Resource Streaming Lambda (SHARED, existing)
                                      - Response Streaming Lambda (SHARED, existing)
```

## Document Hierarchy (from wisco-doc-links.docx)

Ordered by legal precedence (section 1 = highest authority):

1. **State Constitution** - Wisconsin Constitution (uniform taxation clause)
2. **State Laws (Statutes)** - WI Statutes Chapters 17, 19, 33, 38, 59-62, 66, 69-77, 79, 120-121, 165, 200, 706, 757, 943
3. **Administrative Rules** - Tax Chapters 6, 12, 15, 16, 18, 19, 20
4. **Property Assessment Manual (WPAM)** - Required by state law, assessors must follow
5. **Property Tax Common Questions (FAQs)** - DOR FAQ pages (~60+ FAQ page URLs)
6. **Government Publications** - Guides, manuals (pb060, pb061, pb056, pb062, pa502, tif-manual, etc.)
7. **External Standards** - IAAO standards, USPAP (Appraisal Foundation)

This hierarchy maps directly to the GraphRAG framework `DERIVED_FROM` chain.

## File Structure

### New files to create (all additive, no collisions with existing code)

```
packages/graphrag/                                  # NEW package (entire directory is new)
  package.json
  tsconfig.json
  infra/
    graphrag-stack.ts                               # CDK: Neptune graph + S3 raw/work buckets
    graphrag-messages-stack.ts                      # CDK: Agentic retrieval Lambda + new state machine
  lambdas/
    agentic_retrieval/
      __init__.py
      main.py                                       # Agentic retrieval Lambda handler
      neptune_client.py                             # Neptune Analytics query client
      tools.py                                      # Tool definitions for Claude's agentic loop
      requirements.txt
    test/
      test_agentic_retrieval.py
      test_neptune_client.py
      test_tools.py

scripts/graphrag/                                   # NEW directory (ingestion pipeline)
  requirements.txt
  ingest_config.yaml
  scrape_documents.py                               # Download all docs from wisco-doc-links URLs
  extract.py                                        # Textract + LLM classification
  embed.py                                          # Titan Embed v2 embeddings
  load.py                                           # 11-phase Neptune graph loading
```

### Files to modify (additive changes only)

```
packages/infra/lib/stack.ts                         # ADD imports + instantiate GraphRAG stacks
packages/messages/infra/messages-stack.ts            # ADD 2 public readonly properties to expose streaming Lambdas
                                                    # ADD CDK context toggle on existing EventBridge rule
bundles.toml                                        # ADD agentic_retrieval bundle entry
packages/graphrag/lambdas/test/conftest.py          # pytest path configuration
```

### Files NOT touched

```
packages/knowledge-base/                            # UNTOUCHED
packages/messages/lambdas/                          # UNTOUCHED (all existing Lambda code)
packages/webapp/                                    # UNTOUCHED
packages/sessions/                                  # UNTOUCHED
packages/shared/lambda_layers/websocket_utils/      # UNTOUCHED
packages/shared/lambda_layers/step_function_types/  # UNTOUCHED
config/model_configs.toml                           # UNTOUCHED
documents/                                          # UNTOUCHED
```

---

## Task 1: Web Scraper for Document Links

**Files:**
- Create: `scripts/graphrag/scrape_documents.py`
- Create: `scripts/graphrag/requirements.txt`

This task builds a scraper that downloads all documents referenced in `docs/wisco-doc-links.docx` and uploads them to S3 with metadata. Many are PDFs on `revenue.wi.gov` and `docs.legis.wisconsin.gov`, plus HTML FAQ pages.

- [ ] **Step 1: Create the scraper requirements file**

```
# scripts/graphrag/requirements.txt
boto3>=1.35.0
requests>=2.31.0
beautifulsoup4>=4.12.0
pydantic>=2.0.0
pyyaml>=6.0.0
```

- [ ] **Step 2: Create the document scraper**

```python
# scripts/graphrag/scrape_documents.py
"""
Scrape all documents from wisco-doc-links.docx URLs and upload to S3.

Usage:
    python scripts/graphrag/scrape_documents.py \
        --bucket <raw-bucket-name> \
        --prefix raw/
"""

import argparse
import json
import re
import time
from pathlib import Path
from urllib.parse import urlparse

import boto3
import requests

s3 = boto3.client("s3")

# All document URLs extracted from docs/wisco-doc-links.docx, organized by
# authority level (1 = highest precedence).
DOCUMENT_SOURCES = {
    "constitution": {
        "framework_id": "FW-CONSTITUTION",
        "authority_level": 1,
        "doc_type": "constitution",
        "urls": [
            "https://docs.legis.wisconsin.gov/constitution/wi_unannotated",
        ],
    },
    "statutes": {
        "framework_id": "FW-STATUTES",
        "authority_level": 2,
        "doc_type": "statute",
        "urls": [
            "https://docs.legis.wisconsin.gov/document/statutes/ch.%2017.pdf",
            "https://docs.legis.wisconsin.gov/document/statutes/ch.%2019.pdf",
            "https://docs.legis.wisconsin.gov/document/statutes/ch.%2033.pdf",
            "https://docs.legis.wisconsin.gov/document/statutes/ch.%2038.pdf",
            "https://docs.legis.wisconsin.gov/document/statutes/ch.%2059.pdf",
            "https://docs.legis.wisconsin.gov/document/statutes/ch.%2060.pdf",
            "https://docs.legis.wisconsin.gov/document/statutes/ch.%2061.pdf",
            "https://docs.legis.wisconsin.gov/document/statutes/ch.%2062.pdf",
            "https://docs.legis.wisconsin.gov/document/statutes/ch.%2066.pdf",
            "https://docs.legis.wisconsin.gov/document/statutes/ch.%2069.pdf",
            "https://docs.legis.wisconsin.gov/document/statutes/ch.%2070.pdf",
            "https://docs.legis.wisconsin.gov/document/statutes/ch.%2073.pdf",
            "https://docs.legis.wisconsin.gov/document/statutes/ch.%2074.pdf",
            "https://docs.legis.wisconsin.gov/document/statutes/ch.%2075.pdf",
            "https://docs.legis.wisconsin.gov/document/statutes/ch.%2076.pdf",
            "https://docs.legis.wisconsin.gov/document/statutes/ch.%2077.pdf",
            "https://docs.legis.wisconsin.gov/document/statutes/ch.%2079.pdf",
            "https://docs.legis.wisconsin.gov/document/statutes/ch.%20120.pdf",
            "https://docs.legis.wisconsin.gov/document/statutes/ch.%20121.pdf",
            "https://docs.legis.wisconsin.gov/document/statutes/ch.%20165.pdf",
            "https://docs.legis.wisconsin.gov/document/statutes/ch.%20200.pdf",
            "https://docs.legis.wisconsin.gov/document/statutes/ch.%20706.pdf",
            "https://docs.legis.wisconsin.gov/document/statutes/ch.%20757.pdf",
            "https://docs.legis.wisconsin.gov/document/statutes/ch.%20943.pdf",
        ],
    },
    "admin_rules": {
        "framework_id": "FW-ADMIN-RULES",
        "authority_level": 3,
        "doc_type": "admin_rule",
        "urls": [
            "https://docs.legis.wisconsin.gov/document/administrativecode/ch.%20Tax%206.pdf",
            "https://docs.legis.wisconsin.gov/document/administrativecode/ch.%20Tax%2012.pdf",
            "https://docs.legis.wisconsin.gov/document/administrativecode/ch.%20Tax%2015.pdf",
            "https://docs.legis.wisconsin.gov/document/administrativecode/ch.%20Tax%2016.pdf",
            "https://docs.legis.wisconsin.gov/document/administrativecode/ch.%20Tax%2018.pdf",
            "https://docs.legis.wisconsin.gov/document/administrativecode/ch.%20Tax%2019.pdf",
            "https://docs.legis.wisconsin.gov/document/administrativecode/ch.%20Tax%2020.pdf",
        ],
    },
    "wpam": {
        "framework_id": "FW-WPAM",
        "authority_level": 4,
        "doc_type": "assessment_manual",
        "urls": [
            "https://www.revenue.wi.gov/documents/wpam25.pdf",
        ],
    },
    "faq_pages": {
        "framework_id": "FW-FAQ",
        "authority_level": 5,
        "doc_type": "faq_page",
        "urls": [
            "https://www.revenue.wi.gov/Pages/FAQS/slf-agfores6.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-agfores2.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-agforest.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-agfores3.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-agfores5.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-agfores4.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-aar.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-bor5.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-bor.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-bor3.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-bor4.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-bor2.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-lottcr.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-fdolcred.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-ptrecred.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-slevytcr.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-useassmt.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-usevalue.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-chargebk.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-finrep.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-ead.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-excmptraid.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-levy.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-newconst.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/Act-12-Personal-Property-Aid.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-ppaid.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-wirmtxrpt.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-soa.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-sot.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-nmomittx.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-taxempt.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-tiw.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-telco.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-setsh.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-waste.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-tif-pp-exemption.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-tif-allocation-amendments.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-tif-annexations.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-tif-audreport.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-tif-basevalue.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-tif-creation.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-tif-devagree.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-tif-extensions.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-tif-general.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-tif-jrboard.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-tif-money.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-tif-muniown.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-tif-overlaps.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-tif-parcels.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-tif-projexp.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-tif-projplan.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-tif-pubnotif.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-tif-taxincre.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-tif-audterm.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-tif-amends.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-tif-tid-sect-6023.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-tif-vallimit.aspx",
            "https://www.revenue.wi.gov/Pages/FAQS/slf-tif-internal.aspx",
        ],
    },
    "gov_publications": {
        "framework_id": "FW-GOV-PUBS",
        "authority_level": 6,
        "doc_type": "guide",
        "urls": [
            "https://www.revenue.wi.gov/DOR%20Publications/prop066.pdf",
            "https://www.revenue.wi.gov/DOR%20Publications/pb065.pdf",
            "https://www.revenue.wi.gov/DOR%20Publications/pr115.pdf",
            "https://www.revenue.wi.gov/DOR%20Publications/pb061.pdf",
            "https://www.revenue.wi.gov/DOR%20Publications/tax18.pdf",
            "https://www.revenue.wi.gov/DOR%20Publications/pa502.pdf",
            "https://www.revenue.wi.gov/DOR%20Publications/pb056.pdf",
            "https://www.revenue.wi.gov/DOR%20Publications/mobhme.pdf",
            "https://www.revenue.wi.gov/DOR%20Publications/pb062.pdf",
            "https://www.revenue.wi.gov/DOR%20Publications/chargeback-steps.pdf",
            "https://www.revenue.wi.gov/DOR%20Publications/omitted-taxes-steps.pdf",
            "https://www.revenue.wi.gov/DOR%20Publications/pb060.pdf",
            "https://www.revenue.wi.gov/DOR%20Publications/pa600.pdf",
            "https://www.revenue.wi.gov/DOR%20Publications/tif-manual.pdf",
            "https://www.revenue.wi.gov/DOR%20Publications/pb218.pdf",
            "https://www.revenue.wi.gov/DOR%20Publications/pb238.pdf",
            "https://www.revenue.wi.gov/DOR%20Publications/pm-201.pdf",
        ],
    },
    "complex_inquiry_pages": {
        "framework_id": "FW-GOV-PUBS",
        "authority_level": 6,
        "doc_type": "advisory",
        "urls": [
            "https://www.revenue.wi.gov/Pages/SLF/COTVC-News/2024-03-29.aspx",
            "https://www.revenue.wi.gov/Pages/SLF/Assessor-News/2025-04-24.aspx",
            "https://www.revenue.wi.gov/Pages/SLF/COTVC-News/2025-03-19.aspx",
            "https://www.revenue.wi.gov/Pages/SLF/Assessor-News/2023-10-27.aspx",
            "https://www.revenue.wi.gov/Pages/SLF/Assessor-News/2023-03-02.aspx",
            "https://www.revenue.wi.gov/Pages/Manufacturing/home.aspx",
            "https://www.revenue.wi.gov/Pages/RETr/Home.aspx",
            "https://www.revenue.wi.gov/Pages/Training/assessor-certification.aspx",
            "https://www.revenue.wi.gov/Pages/Training/assess-recert.aspx",
            "https://www.revenue.wi.gov/Pages/Apps/assessor-inquiry.aspx",
        ],
    },
}


def make_doc_id(category: str, url: str) -> str:
    """Generate a stable document ID from category and URL."""
    path = urlparse(url).path
    filename = Path(path).stem
    clean = re.sub(r"[%\s.]+", "-", filename).strip("-").lower()
    return f"{category}-{clean}"


def download_file(url: str, max_retries: int = 3) -> tuple[bytes, str]:
    """Download file with retries. Returns (content_bytes, content_type)."""
    headers = {"User-Agent": "Mozilla/5.0 (WI-DOR-Bot/1.0)"}
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, headers=headers, timeout=60)
            resp.raise_for_status()
            ct = resp.headers.get("Content-Type", "application/octet-stream")
            return resp.content, ct
        except requests.RequestException as e:
            if attempt == max_retries - 1:
                raise
            print(f"  Retry {attempt + 1}/{max_retries} for {url}: {e}")
            time.sleep(2 ** attempt)
    raise RuntimeError("unreachable")


def scrape_html_page(url: str) -> tuple[bytes, str]:
    """Scrape an HTML page and return the main content as UTF-8 text."""
    from bs4 import BeautifulSoup

    headers = {"User-Agent": "Mozilla/5.0 (WI-DOR-Bot/1.0)"}
    resp = requests.get(url, headers=headers, timeout=60)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")

    # WI DOR pages use this content container
    content = soup.find("div", id="ctl00_PlaceHolderMain_ctl01__ControlWrapper_RichHtmlField")
    if not content:
        content = soup.find("div", class_="ms-rtestate-field")
    if not content:
        content = soup.find("main") or soup.find("article") or soup.body

    text = content.get_text("\n", strip=True) if content else soup.get_text("\n", strip=True)
    return text.encode("utf-8"), "text/plain"


def upload_to_s3(bucket: str, prefix: str, doc_id: str, data: bytes, content_type: str, metadata: dict):
    """Upload document + metadata JSON to S3."""
    ext = ".pdf" if "pdf" in content_type else ".txt"
    doc_key = f"{prefix}{doc_id}/{doc_id}{ext}"
    meta_key = f"{prefix}{doc_id}/{doc_id}{ext}.metadata.json"

    s3.put_object(Bucket=bucket, Key=doc_key, Body=data, ContentType=content_type)
    meta_json = json.dumps({"metadataAttributes": metadata}, indent=2).encode("utf-8")
    s3.put_object(Bucket=bucket, Key=meta_key, Body=meta_json, ContentType="application/json")

    return doc_key


def main():
    parser = argparse.ArgumentParser(description="Scrape all WI DOR documents to S3")
    parser.add_argument("--bucket", required=True, help="S3 raw bucket name")
    parser.add_argument("--prefix", default="raw/", help="S3 prefix (default: raw/)")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be scraped without downloading")
    args = parser.parse_args()

    total = sum(len(cat["urls"]) for cat in DOCUMENT_SOURCES.values())
    print(f"Scraping {total} documents across {len(DOCUMENT_SOURCES)} categories\n")

    processed = 0
    failed = []

    for category, config in DOCUMENT_SOURCES.items():
        print(f"\n=== {category} (authority level {config['authority_level']}) ===")

        for url in config["urls"]:
            doc_id = make_doc_id(category, url)
            processed += 1

            if args.dry_run:
                print(f"  [{processed}/{total}] Would scrape: {doc_id} <- {url}")
                continue

            print(f"  [{processed}/{total}] {doc_id}")

            try:
                is_html = not url.lower().endswith(".pdf")
                if is_html:
                    data, ct = scrape_html_page(url)
                else:
                    data, ct = download_file(url)

                metadata = {
                    "doc_id": doc_id,
                    "source_url": url,
                    "doc_type": config["doc_type"],
                    "framework_id": config["framework_id"],
                    "authority_level": str(config["authority_level"]),
                    "category": category,
                }

                doc_key = upload_to_s3(args.bucket, args.prefix, doc_id, data, ct, metadata)
                print(f"    -> s3://{args.bucket}/{doc_key}")

            except Exception as e:
                print(f"    FAILED: {e}")
                failed.append({"doc_id": doc_id, "url": url, "error": str(e)})

    print(f"\n{'DRY RUN ' if args.dry_run else ''}Complete: {processed - len(failed)}/{processed} succeeded")
    if failed:
        print(f"\nFailed ({len(failed)}):")
        for f in failed:
            print(f"  {f['doc_id']}: {f['error']}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Test the scraper in dry-run mode**

Run: `cd /Users/jonahchan/dev/dxhub/wisco && python scripts/graphrag/scrape_documents.py --bucket test --dry-run`
Expected: Lists all ~120 documents that would be scraped, no downloads.

- [ ] **Step 4: Commit**

```bash
git add scripts/graphrag/scrape_documents.py scripts/graphrag/requirements.txt
git commit -m "feat: add document scraper for all WI DOR source URLs"
```

---

## Task 2: Ingestion Pipeline Configuration

**Files:**
- Create: `scripts/graphrag/ingest_config.yaml`

- [ ] **Step 1: Create the pipeline configuration**

```yaml
# scripts/graphrag/ingest_config.yaml
# GraphRAG ingestion pipeline configuration for Wisconsin DOR

# --- Chunking ---
chunk_size: 2000
chunk_overlap: 200
large_doc_threshold: 10000
max_split_sections: 15

# --- Semantic Thresholds ---
semantic_similarity_threshold: 0.55
semantic_batch_size: 15

# --- Concurrency ---
concurrency:
  file_extraction: 5
  chunk_embedding: 10
  vector_upserts: 20
  doc_embedding: 10
  semantic_llm: 5

# --- Document Types -> Graph Node Labels ---
doc_types:
  constitution:       Constitution
  statute:            Statute
  admin_rule:         AdminRule
  assessment_manual:  AssessmentManual
  faq_page:           FAQ
  guide:              Guide
  advisory:           Advisory
  template:           Template

# --- Framework Definitions (ordered by legal precedence) ---
frameworks:
  - id: FW-CONSTITUTION
    title: "Wisconsin State Constitution"
    authority_level: 1

  - id: FW-STATUTES
    title: "Wisconsin Statutes"
    authority_level: 2
    parent: FW-CONSTITUTION

  - id: FW-ADMIN-RULES
    title: "Administrative Rules (Tax Chapters)"
    authority_level: 3
    parent: FW-STATUTES

  - id: FW-WPAM
    title: "Wisconsin Property Assessment Manual"
    authority_level: 4
    parent: FW-ADMIN-RULES

  - id: FW-FAQ
    title: "Property Tax Common Questions"
    authority_level: 5
    parent: FW-WPAM

  - id: FW-GOV-PUBS
    title: "Government Publications & Guides"
    authority_level: 6
    parent: FW-WPAM

# --- Source-to-Framework Mapping ---
source_to_framework:
  "raw/constitution-":           FW-CONSTITUTION
  "raw/statutes-":               FW-STATUTES
  "raw/admin_rules-":            FW-ADMIN-RULES
  "raw/wpam-":                   FW-WPAM
  "raw/faq_pages-":              FW-FAQ
  "raw/gov_publications-":       FW-GOV-PUBS
  "raw/complex_inquiry_pages-":  FW-GOV-PUBS

# --- Statute Section Families ---
statute_families:
  - code: "CH-17"
    title: "Resignations, Vacancies, Removals"
  - code: "CH-70"
    title: "General Property Taxes"
  - code: "CH-73"
    title: "Revenue - General Provisions"
  - code: "CH-74"
    title: "Property Tax Collection"
  - code: "CH-75"
    title: "Land Sold for Taxes"
  - code: "CH-76"
    title: "Taxation of Certain Utilities"
  - code: "CH-77"
    title: "Taxation of Forest Croplands"

# --- AWS ---
bedrock_embed_model: "amazon.titan-embed-text-v2:0"
bedrock_llm_model: "us.anthropic.claude-sonnet-4-20250514"
embed_dimension: 1024
```

- [ ] **Step 2: Commit**

```bash
git add scripts/graphrag/ingest_config.yaml
git commit -m "feat: add GraphRAG ingestion pipeline configuration"
```

---

## Task 3: Extraction Pipeline

**Files:**
- Create: `scripts/graphrag/extract.py`

Reuses the existing `pdf_chunking/` module for Textract-based PDF extraction, adds LLM classification on top. Does NOT modify `pdf_chunking/` in any way.

- [ ] **Step 1: Create the extraction script**

This script: lists docs from S3 raw bucket -> routes PDFs through existing `process_pdf_from_s3` -> routes text through sliding-window chunking -> classifies each doc via Bedrock LLM -> caches results to S3 work bucket.

The full script is ~180 lines. Key structure:
- `load_config()` - reads ingest_config.yaml
- `classify_document(text, model_id)` - Bedrock converse call with classification prompt
- `list_documents(bucket, prefix)` - S3 paginator listing raw docs
- `process_document(doc, raw_bucket, work_bucket, config)` - per-doc extraction + classification
- `deduplicate(documents)` - keep longest text per doc_id
- `main()` - CLI entry point with ThreadPoolExecutor

The LLM classification prompt asks for: `doc_type`, `title`, `statute_refs`, `admin_rule_refs`, `topics`, `summary` -- all fields needed for graph node creation and edge extraction.

- [ ] **Step 2: Commit**

```bash
git add scripts/graphrag/extract.py
git commit -m "feat: add document extraction and LLM classification pipeline"
```

---

## Task 4: Embedding Pipeline

**Files:**
- Create: `scripts/graphrag/embed.py`

- [ ] **Step 1: Create the embedding script**

Reads extracted docs from S3 work bucket, embeds each chunk + a doc-level embedding using Titan Embed v2 (1024 dimensions), saves back to `embedded/` prefix. Uses ThreadPoolExecutor with exponential backoff on Bedrock throttling (6 retries, 30s max).

- [ ] **Step 2: Commit**

```bash
git add scripts/graphrag/embed.py
git commit -m "feat: add chunk and document embedding pipeline"
```

---

## Task 5: Graph Loading Pipeline

**Files:**
- Create: `scripts/graphrag/load.py`

The most complex script -- implements all 11 loading phases from the replication guide, adapted to the WI DOR domain.

- [ ] **Step 1: Create the graph loading script**

11 phases, each a function:

1. **Scaffold** - Create Framework nodes with DERIVED_FROM hierarchy (Constitution -> Statutes -> Admin Rules -> WPAM -> FAQ -> Gov Pubs). Create statute family nodes (CH-70, CH-73, etc.) with BELONGS_TO Framework.
2. **Document Nodes** - MERGE each doc with label from `doc_types` map. SET title, source_url, authority_level. MERGE BELONGS_TO framework edge.
3. **Cross-Reference Edges** - For each doc's `statute_refs`, `admin_rule_refs`, and `implements_refs`, create stub nodes if needed and CITES or IMPLEMENTS edges. IMPLEMENTS is for documents that operationalize a statute (e.g., a DOR policy implements a statute), while CITES is for casual references. The LLM classification prompt (Task 3) extracts both types.
4. **Statute Hierarchy** - Parse WIS-STAT-70.32 -> PART_OF CH-70. Handle sub-sections: 70.32(2)(c) -> 70.32(2) -> 70.32.
5. **Topic Merging** - Collect all raw topics, batch 200 at a time to LLM for synonym clustering, create Topic nodes + COVERS_TOPIC edges.
6. **Sub-Document Links** - For docs with `_parent_id`, create HAS_SUBSECTION edges.
7. **Universal Hierarchy Post-Pass** - Connect orphan stubs to parent frameworks.
8. **Chunk Nodes** - Create Chunk nodes with EXTRACTED_FROM edges to parent docs.
9. **Stub Resolution** - Match stubs to real document nodes by ID pattern.
10. **Vector Upserts** - Push chunk embeddings to Neptune's vector index via `neptune.algo.vectors.upsert`.
11. **Semantic Edges** - Pairwise cosine similarity on doc embeddings -> filter > 0.55 -> LLM confirmation in batches of 15 -> create RELATED_TO/SUPPLEMENTS/SUPERSEDES/CONFLICTS_WITH edges.

Supports `--start-phase` flag to resume from a specific phase.

- [ ] **Step 2: Commit**

```bash
git add scripts/graphrag/load.py
git commit -m "feat: add 11-phase Neptune Analytics graph loading pipeline"
```

---

## Task 6: CDK GraphRAG Infrastructure Stack (NEW, Alongside Existing)

**Files:**
- Create: `packages/graphrag/package.json`
- Create: `packages/graphrag/tsconfig.json`
- Create: `packages/graphrag/infra/graphrag-stack.ts`

This stack provisions Neptune Analytics + new S3 buckets. It does NOT touch the existing KnowledgeBaseStack.

- [ ] **Step 1: Create package.json and tsconfig.json**

Standard CDK package configuration matching the style of other packages in the monorepo.

- [ ] **Step 2: Create the GraphRAG CDK stack**

```typescript
// packages/graphrag/infra/graphrag-stack.ts
import * as cdk from 'aws-cdk-lib';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as neptune from 'aws-cdk-lib/aws-neptunegraph';
import { Construct } from 'constructs';

export class GraphRAGStack extends cdk.NestedStack {
  public readonly rawBucketName: string;
  public readonly workBucketName: string;
  public readonly neptuneGraphId: string;
  public readonly neptuneGraphEndpoint: string;

  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    const uid = cdk.Fn.select(
      0,
      cdk.Fn.split('-', cdk.Fn.select(2, cdk.Fn.split('/', this.stackId)))
    );

    // NEW S3 buckets (separate from existing FAQ/RAG buckets)
    const rawBucket = new s3.Bucket(this, 'WisDorRawDocs', {
      bucketName: cdk.Fn.join('-', ['wis-raw-bucket', uid]),
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      autoDeleteObjects: false,
    });

    const workBucket = new s3.Bucket(this, 'WisDorWorkBucket', {
      bucketName: cdk.Fn.join('-', ['wis-work-bucket', uid]),
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });

    // Neptune Analytics Graph (with vector search enabled)
    // publicConnectivity: true so Lambdas can reach it via IAM auth
    // without VPC configuration (no existing VPC in this project)
    const graph = new neptune.CfnGraph(this, 'WisDorGraph', {
      graphName: 'wis-dor-graphrag',
      provisionedMemory: 32,
      vectorSearchConfiguration: {
        vectorSearchDimension: 1024,
      },
      publicConnectivity: true,
      replicaCount: 0,
      deletionProtection: false,
    });

    this.rawBucketName = rawBucket.bucketName;
    this.workBucketName = workBucket.bucketName;
    this.neptuneGraphId = graph.attrGraphId;
    this.neptuneGraphEndpoint = graph.attrEndpoint;

    new cdk.CfnOutput(this, 'RawBucketName', {
      value: rawBucket.bucketName,
      description: 'S3 bucket for raw source documents',
    });
    new cdk.CfnOutput(this, 'WorkBucketName', {
      value: workBucket.bucketName,
      description: 'S3 bucket for intermediate processing cache',
    });
    new cdk.CfnOutput(this, 'NeptuneGraphId', {
      value: graph.attrGraphId,
      description: 'Neptune Analytics Graph ID',
    });
    new cdk.CfnOutput(this, 'NeptuneGraphEndpoint', {
      value: graph.attrEndpoint,
      description: 'Neptune Analytics Graph Endpoint',
    });
  }
}
```

- [ ] **Step 3: Commit**

```bash
git add packages/graphrag/package.json packages/graphrag/tsconfig.json packages/graphrag/infra/graphrag-stack.ts
git commit -m "feat: add GraphRAG infrastructure stack (Neptune Analytics + S3)"
```

---

## Task 7: Neptune Client Library

**Files:**
- Create: `packages/graphrag/lambdas/agentic_retrieval/__init__.py`
- Create: `packages/graphrag/lambdas/agentic_retrieval/neptune_client.py`
- Create: `packages/graphrag/lambdas/agentic_retrieval/requirements.txt`
- Create: `packages/graphrag/lambdas/test/test_neptune_client.py`

- [ ] **Step 1: Create the Neptune client**

`NeptuneClient` class wrapping `boto3.client("neptune-graph")` with methods:
- `query(cypher, parameters)` - Execute OpenCypher with retry
- `vector_search(embedding, top_k)` - `neptune.algo.vectors.topKByEmbedding`
- `get_document(doc_id)` - Fetch doc node by ID
- `get_neighbors(node_id, edge_types, direction)` - Graph edge traversal
- `get_authority_chain(node_id, max_depth)` - Variable-length path: `PART_OF|BELONGS_TO|DERIVED_FROM*1..N`
- `list_framework_docs(framework_id)` - List docs in a framework
- `get_chunks_for_doc(doc_id)` - All chunks for a doc

All methods use exponential backoff (3 retries, 30s max) for throttling.

- [ ] **Step 2: Create unit tests (mocked boto3)**

Tests verify: query returns results, get_document returns None for missing, vector_search passes embedding correctly.

- [ ] **Step 3: Commit**

```bash
git add packages/graphrag/lambdas/agentic_retrieval/ packages/graphrag/lambdas/test/test_neptune_client.py
git commit -m "feat: add Neptune Analytics client library with vector search"
```

---

## Task 8: Agentic Retrieval Tool Definitions

**Files:**
- Create: `packages/graphrag/lambdas/agentic_retrieval/tools.py`
- Create: `packages/graphrag/lambdas/test/test_tools.py`

- [ ] **Step 1: Create tool definitions for Claude's agentic loop**

Six tools in Bedrock Converse `toolConfig` format:

| Tool | Purpose |
|------|---------|
| `vector_search` | Embed query -> Neptune vector index -> relevant chunks |
| `get_document` | Fetch document metadata by ID |
| `get_neighbors` | Traverse graph edges (CITES, IMPLEMENTS, SUPERSEDES, etc.) |
| `get_authority_chain` | Trace governance hierarchy up to root Framework |
| `list_framework_docs` | List all docs in a framework |
| `answer` | Terminal tool -- return final response with citations |

`execute_tool(tool_name, tool_input, neptune_client)` dispatches to the appropriate `NeptuneClient` method.

- [ ] **Step 2: Create unit tests**

Tests verify: vector_search calls embed + neptune, get_document handles found/not-found, answer is pass-through terminal, unknown tool returns error.

- [ ] **Step 3: Commit**

```bash
git add packages/graphrag/lambdas/agentic_retrieval/tools.py packages/graphrag/lambdas/test/test_tools.py
git commit -m "feat: add agentic retrieval tool definitions for Neptune GraphRAG"
```

---

## Task 9: Agentic Retrieval Lambda Handler

**Files:**
- Create: `packages/graphrag/lambdas/agentic_retrieval/main.py`
- Create: `packages/graphrag/lambdas/test/test_agentic_retrieval.py`

This Lambda replaces the classifier + retrieval flow for the GraphRAG path. It runs Claude's agentic loop with Neptune-backed tools.

- [ ] **Step 1: Create the agentic retrieval handler**

Key function: `run_agentic_loop(query)` which:
1. Sends user query to Claude with tools + system prompt
2. Loop (max 10 turns): parse tool_use blocks -> execute against Neptune -> return results
3. When Claude calls `answer` tool -> extract response + cited_doc_ids
4. Build `RAGDocument` list from collected chunks
5. Return `RetrieveResult` (same shape as existing retrieval Lambda output)

System prompt instructs Claude to:
- Start with `vector_search`
- Follow graph edges for authoritative sources
- Trace authority chains (Constitution > Statutes > Admin Rules > WPAM > FAQs > Guides)
- Check SUPERSEDES edges for outdated guidance
- Cite specific document IDs and statute references

The `handler()` function:
- Accepts a clean `UserQuery` dict (`{query, query_id, session_id}`) -- the EventBridge rule uses `$.detail` extraction so the Lambda receives only the payload, not the full EventBridge envelope
- Validates input via `UserQuery.model_validate(event)` (existing Pydantic model, no new models needed)
- Returns `RetrieveResult` with `generate_response_job` + `stream_documents_job`
- This output shape is **identical** to what the existing retrieval Lambda returns, so the downstream streaming Lambdas work without changes

- [ ] **Step 2: Create handler tests**

Tests: handler validates clean UserQuery dict, handler rejects malformed input, _build_rag_documents aggregates chunks by doc_id. (Note: the handler only needs to handle flat `{query, query_id, session_id}` dicts since the EventBridge rule extracts `$.detail` before it reaches the Lambda.)

- [ ] **Step 3: Commit**

```bash
git add packages/graphrag/lambdas/agentic_retrieval/main.py packages/graphrag/lambdas/test/test_agentic_retrieval.py
git commit -m "feat: add agentic retrieval Lambda handler with Neptune-backed tool loop"
```

---

## Task 10: GraphRAG Messages Stack (NEW State Machine) + Bundle Config

**Files:**
- Create: `packages/graphrag/infra/graphrag-messages-stack.ts`
- Create: `packages/graphrag/lambdas/test/conftest.py`
- Modify: `bundles.toml` (add 1 entry)

This creates a **NEW** Step Functions state machine alongside the existing one. The existing `ChatStreamingStateMachine` continues to work. The two EventBridge rules are **mutually exclusive** -- controlled by CDK context.

Note: We do NOT modify `step_function_types/models.py`. The agentic retrieval Lambda uses the existing `UserQuery` model (which has the same fields) and returns `RetrieveResult` (also existing).

- [ ] **Step 1: Add agentic_retrieval to bundles.toml**

Append to `bundles.toml`:

```toml
[[bundles]]
dest = "agentic_retrieval"
sources = ["./packages/graphrag/lambdas/agentic_retrieval"]
```

- [ ] **Step 2: Create test conftest.py**

```python
# packages/graphrag/lambdas/test/conftest.py
import sys
import os

# Add the agentic_retrieval source directory to sys.path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agentic_retrieval"))
```

- [ ] **Step 3: Create the GraphRAG messages stack**

```typescript
// packages/graphrag/infra/graphrag-messages-stack.ts
import * as cdk from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as sfn from 'aws-cdk-lib/aws-stepfunctions';
import * as tasks from 'aws-cdk-lib/aws-stepfunctions-tasks';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import { Construct } from 'constructs';

export interface GraphRAGMessagesStackProps extends cdk.StackProps {
  stepFunctionTypesLayer: lambda.LayerVersion;
  websocketUtilsLayer: lambda.LayerVersion;
  sessionsTable: cdk.aws_dynamodb.ITable;
  websocketCallbackUrl: string;
  neptuneGraphId: string;
  neptuneGraphEndpoint: string;
  // Reuse existing streaming Lambdas from MessagesStack
  responseStreamingFunction: lambda.IFunction;
  resourceStreamingFunction: lambda.IFunction;
  // Feature flag: when true, this state machine handles traffic
  // and the old state machine's EventBridge rule is disabled
  enabled: boolean;
}

export class GraphRAGMessagesStack extends cdk.NestedStack {
  public readonly graphragStateMachine: sfn.StateMachine;

  constructor(scope: Construct, id: string, props: GraphRAGMessagesStackProps) {
    super(scope, id, props);

    // ============================================================
    // Agentic Retrieval Lambda (NEW - replaces classifier+retrieval)
    // ============================================================
    const agenticRetrievalHandler = new lambda.Function(
      this,
      'AgenticRetrievalFunction',
      {
        runtime: lambda.Runtime.PYTHON_3_12,
        handler: 'main.handler',
        code: lambda.Code.fromAsset('bundle/agentic_retrieval', {
          bundling: {
            image: lambda.Runtime.PYTHON_3_12.bundlingImage,
            command: [
              'bash',
              '-c',
              [
                'pip install --platform manylinux2014_x86_64 --only-binary=:all: -r requirements.txt -t /asset-output',
                'cp -r . /asset-output',
              ].join(' && '),
            ],
          },
        }),
        layers: [props.stepFunctionTypesLayer, props.websocketUtilsLayer],
        description:
          'Agentic retrieval Lambda: Neptune graph + vector search with Claude tool loop',
        timeout: cdk.Duration.seconds(120), // Agentic loop needs more time
        memorySize: 512,
        environment: {
          WEBSOCKET_CALLBACK_URL: props.websocketCallbackUrl,
          NEPTUNE_GRAPH_ID: props.neptuneGraphId,
          AGENTIC_MODEL_ID: 'us.anthropic.claude-sonnet-4-20250514',
        },
      }
    );

    // Neptune Analytics permissions (scoped to specific graph)
    agenticRetrievalHandler.addToRolePolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: [
          'neptune-graph:ExecuteQuery',
          'neptune-graph:ReadDataViaQuery',
          'neptune-graph:GetQueryStatus',
        ],
        resources: [
          `arn:aws:neptune-graph:${cdk.Stack.of(this).region}:${cdk.Stack.of(this).account}:graph/${props.neptuneGraphId}`,
        ],
      })
    );

    // Bedrock permissions (scoped to specific models)
    agenticRetrievalHandler.addToRolePolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: [
          'bedrock:InvokeModel',
          'bedrock:InvokeModelWithResponseStream',
        ],
        resources: [
          `arn:aws:bedrock:${cdk.Stack.of(this).region}::foundation-model/us.anthropic.claude-sonnet-4-20250514`,
          `arn:aws:bedrock:${cdk.Stack.of(this).region}::foundation-model/amazon.titan-embed-text-v2:0`,
        ],
      })
    );

    // ============================================================
    // NEW Step Functions State Machine (GraphRAG flow)
    // ============================================================
    // Flow: AgenticRetrieval -> Parallel(ResourceStreaming, ResponseStreaming)
    //
    // The agentic retrieval Lambda returns a RetrieveResult with both
    // generate_response_job and stream_documents_job, same as the
    // existing retrieval Lambda. So the downstream Lambdas are reused.

    const agenticRetrievalTask = new tasks.LambdaInvoke(
      this,
      'AgenticRetrievalTask',
      {
        lambdaFunction: agenticRetrievalHandler,
        outputPath: '$.Payload',
      }
    );

    const selectResourceStreamingJob = new sfn.Pass(
      this,
      'SelectResourceStreamingJob',
      {
        parameters: { 'job.$': '$.stream_documents_job' },
        outputPath: '$.job',
      }
    );

    const selectGenerateResponseJob = new sfn.Pass(
      this,
      'SelectGenerateResponseJob',
      {
        parameters: { 'job.$': '$.generate_response_job' },
        outputPath: '$.job',
      }
    );

    // Reuse the EXISTING streaming Lambdas (passed in as props)
    const resourceStreamingTask = new tasks.LambdaInvoke(
      this,
      'ResourceStreamingTask',
      {
        lambdaFunction: props.resourceStreamingFunction,
        outputPath: '$.Payload',
      }
    );

    const responseStreamingTask = new tasks.LambdaInvoke(
      this,
      'ResponseStreamingTask',
      {
        lambdaFunction: props.responseStreamingFunction,
        outputPath: '$.Payload',
      }
    );

    const checkSuccess = new sfn.Choice(this, 'CheckRetrievalSuccess')
      .when(
        sfn.Condition.booleanEquals('$.successful', false),
        new sfn.Fail(this, 'RetrievalFailed', {
          error: 'Agentic retrieval failed',
          cause: 'The agentic retrieval lambda returned successful=false',
        })
      )
      .otherwise(
        new sfn.Parallel(this, 'ParallelGraphRAGStreaming')
          .branch(selectResourceStreamingJob.next(resourceStreamingTask))
          .branch(selectGenerateResponseJob.next(responseStreamingTask))
      );

    const definition = agenticRetrievalTask.next(checkSuccess);

    this.graphragStateMachine = new sfn.StateMachine(
      this,
      'GraphRAGStateMachine',
      {
        definition,
        stateMachineName: 'GraphRAGStreamingStateMachine',
        timeout: cdk.Duration.minutes(5),
        tracingEnabled: true,
        logs: {
          destination: new cdk.aws_logs.LogGroup(
            this,
            'GraphRAGStateMachineLogs',
            {
              logGroupName: `/aws/states/GraphRAGStreamingStateMachine`,
              retention: cdk.aws_logs.RetentionDays.ONE_WEEK,
            }
          ),
          level: sfn.LogLevel.ALL,
          includeExecutionData: true,
        },
      }
    );

    // ============================================================
    // EventBridge Rule (NEW, disabled by default)
    // ============================================================
    // IMPORTANT: This rule and the existing rule are MUTUALLY EXCLUSIVE.
    // Only ONE should be enabled at a time to prevent both state machines
    // from firing on the same event (which would corrupt WebSocket sessions
    // by sending duplicate/interleaved responses).
    //
    // The old rule's enabled state is controlled by the root stack via
    // the same CDK context flag (useGraphRAG). When this rule is enabled,
    // the old rule is disabled, and vice versa.
    //
    // Input uses $.detail to extract just the UserQuery payload,
    // so the Lambda receives a clean {query, query_id, session_id}
    // without needing to unwrap the EventBridge envelope.
    const triggerGraphRAGProcessing = new events.Rule(
      this,
      'TriggerGraphRAGProcessing',
      {
        ruleName: 'TriggerGraphRAGMessageProcessing',
        eventPattern: {
          source: ['wisconsin-dor.chat-api'],
          detailType: ['ChatMessageReceived'],
        },
        enabled: props.enabled,
      }
    );

    triggerGraphRAGProcessing.addTarget(
      new targets.SfnStateMachine(this.graphragStateMachine, {
        input: events.RuleTargetInput.fromEventPath('$.detail'),
      })
    );

    // ============================================================
    // Outputs
    // ============================================================
    new cdk.CfnOutput(this, 'AgenticRetrievalFunctionArn', {
      value: agenticRetrievalHandler.functionArn,
      description: 'ARN of the Agentic Retrieval Lambda function',
    });

    new cdk.CfnOutput(this, 'GraphRAGStateMachineArn', {
      value: this.graphragStateMachine.stateMachineArn,
      description: 'ARN of the GraphRAG Step Functions state machine',
    });
  }
}
```

- [ ] **Step 4: Commit**

```bash
git add packages/graphrag/infra/graphrag-messages-stack.ts packages/graphrag/lambdas/test/conftest.py bundles.toml
git commit -m "feat: add GraphRAG messages stack with new state machine (disabled by default)"
```

---

## Task 11: Wire GraphRAG Stacks into Root Stack + Mutual Exclusion

**Files:**
- Modify: `packages/infra/lib/stack.ts` (additive changes only)
- Modify: `packages/messages/infra/messages-stack.ts` (expose 2 Lambda refs + toggle EventBridge rule)

- [ ] **Step 1: Add GraphRAG stack imports and instantiation**

Add these imports at the top of `packages/infra/lib/stack.ts`:

```typescript
import { GraphRAGStack } from '../../graphrag/infra/graphrag-stack';
import { GraphRAGMessagesStack } from '../../graphrag/infra/graphrag-messages-stack';
```

Add the `USE_GRAPHRAG` constant **BEFORE** the existing `messagesStack` instantiation (it must be defined before it's referenced in the messagesStack props):

```typescript
// === GraphRAG feature flag ===
// When useGraphRAG=true: GraphRAG EventBridge rule is ENABLED, old rule is DISABLED
// When useGraphRAG=false (default): Old rule is ENABLED, GraphRAG rule is DISABLED
// This ensures ONLY ONE state machine fires per event (mutual exclusion).
const USE_GRAPHRAG = this.node.tryGetContext('useGraphRAG') === 'true';
```

Then add `useGraphRAG: USE_GRAPHRAG` to the EXISTING `messagesStack` props object (this is the one necessary modification to an existing block -- adding a single prop):

```typescript
const messagesStack = new MessagesStack(this, 'WisconsinMessagesStack', {
  // ... all existing props unchanged ...
  useGraphRAG: USE_GRAPHRAG, // ADD this single line
});
```

Then add the GraphRAG stacks **after** the existing `messagesStack`:

```typescript

const graphRAGStack = new GraphRAGStack(this, 'WisconsinGraphRAGStack', {
  description: 'Stack providing GraphRAG services (Neptune Analytics + S3).',
});

const graphRAGMessagesStack = new GraphRAGMessagesStack(
  this,
  'WisconsinGraphRAGMessagesStack',
  {
    description: 'GraphRAG messaging services (agentic retrieval + state machine).',
    stepFunctionTypesLayer: lambdaLayersStack.stepFunctionTypesLayer,
    websocketUtilsLayer: lambdaLayersStack.websocketUtilsLayer,
    sessionsTable: sessionsStack.sessionsTable,
    websocketCallbackUrl: sessionsStack.websocketCallbackUrl,
    neptuneGraphId: graphRAGStack.neptuneGraphId,
    neptuneGraphEndpoint: graphRAGStack.neptuneGraphEndpoint,
    responseStreamingFunction: messagesStack.responseStreamingFunction,
    resourceStreamingFunction: messagesStack.resourceStreamingFunction,
    enabled: USE_GRAPHRAG,
  }
);
```

Add new outputs (after existing outputs, do NOT modify them):

```typescript
new cdk.CfnOutput(this, 'GraphRAGRawBucketName', {
  value: graphRAGStack.rawBucketName,
  description: 'S3 bucket for GraphRAG raw documents',
  exportName: 'WisconsinBot-GraphRAGRawBucketName',
});

new cdk.CfnOutput(this, 'GraphRAGWorkBucketName', {
  value: graphRAGStack.workBucketName,
  description: 'S3 bucket for GraphRAG work data',
  exportName: 'WisconsinBot-GraphRAGWorkBucketName',
});

new cdk.CfnOutput(this, 'GraphRAGNeptuneGraphId', {
  value: graphRAGStack.neptuneGraphId,
  description: 'Neptune Analytics Graph ID',
  exportName: 'WisconsinBot-NeptuneGraphId',
});

new cdk.CfnOutput(this, 'GraphRAGStateMachineArn', {
  value: graphRAGMessagesStack.graphragStateMachine.stateMachineArn,
  description: 'ARN of the GraphRAG Step Functions state machine',
  exportName: 'WisconsinBot-GraphRAGStateMachineArn',
});
```

- [ ] **Step 2: Modify MessagesStack (minimal additive changes)**

Three changes to `packages/messages/infra/messages-stack.ts`:

**a) Expose streaming Lambda references** - add public readonly properties (after existing public properties):

```typescript
// ADD to class properties (after existing public readonly declarations)
public readonly responseStreamingFunction: lambda.Function;
public readonly resourceStreamingFunction: lambda.Function;
```

Assign in constructor (after existing Lambda creation, no existing code modified):

```typescript
// ADD after streamingHandler and resourceStreamingHandler are created
this.responseStreamingFunction = streamingHandler;
this.resourceStreamingFunction = resourceStreamingHandler;
```

**b) Make the existing EventBridge rule toggleable** - add a `useGraphRAG` prop and use it:

Add to `MessagesStackProps` interface:

```typescript
// ADD to MessagesStackProps (after existing props)
/** When true, disable this stack's EventBridge rule (traffic goes to GraphRAG instead). */
useGraphRAG?: boolean;
```

Modify the existing EventBridge rule to respect the flag (this is the only behavioral change to existing code -- it defaults to enabled=true, matching current behavior):

```typescript
// The existing rule creation at the bottom of the constructor:
// Change: new events.Rule(this, 'TriggerMessageProcessing', { eventPattern: {...} })
// To:     new events.Rule(this, 'TriggerMessageProcessing', { eventPattern: {...}, enabled: props.useGraphRAG !== true })
```

Note: `props.useGraphRAG !== true` is used instead of `!props.useGraphRAG` for explicit boolean handling -- when the prop is `undefined` (not passed), the expression evaluates to `true` (rule enabled), matching current behavior exactly.

This means `cdk deploy` with no context (the default) leaves the old rule enabled. Only `cdk deploy -c useGraphRAG=true` disables it.

**c) Pass the flag from root stack** - update the `messagesStack` instantiation in `stack.ts` to include `useGraphRAG: USE_GRAPHRAG` in its props (add this single line to the existing prop object).

- [ ] **Step 3: Verify CDK diff shows no resource changes to existing stack (default mode)**

Run: `cd packages/infra && npx cdk diff 2>&1 | head -40`
Expected: New resources from GraphRAG stacks. NO changes to existing MessagesStack resources (the EventBridge rule enabled state defaults to `true`, matching current behavior).

- [ ] **Step 4: Commit**

```bash
git add packages/infra/lib/stack.ts packages/messages/infra/messages-stack.ts
git commit -m "feat: wire GraphRAG stacks into root stack with mutually exclusive EventBridge rules"
```

---

## Task 12: Integration Verification

- [ ] **Step 1: Verify CDK synthesizes without errors (default mode)**

Run: `cd packages/infra && npx cdk synth --quiet 2>&1 | tail -20`
Expected: Clean synthesis. The template should contain:
- Existing: KnowledgeBaseStack, ChatStreamingStateMachine, Classifier, Retrieval Lambdas
- New: GraphRAGStack, GraphRAGStreamingStateMachine, AgenticRetrieval Lambda

- [ ] **Step 2: Verify CDK diff shows no changes to existing resources (default mode)**

Run: `cd packages/infra && npx cdk diff 2>&1 | head -50`
Expected: Only NEW resources from GraphRAG stacks. The existing MessagesStack EventBridge rule should show no change (it defaults to enabled=true, matching current behavior).

- [ ] **Step 3: Verify bundles.toml includes agentic_retrieval**

Run: `grep agentic_retrieval bundles.toml`
Expected: Entry present.

- [ ] **Step 4: Verify Python tests pass**

Run: `cd packages/graphrag && python -m pytest lambdas/test/ -v`
Expected: All tests pass.

- [ ] **Step 5: Verify existing WebSocket contract is unchanged**

Run: `grep -r "responseType" packages/shared/lambda_layers/websocket_utils/models.py`
Expected: Same message types: `documents`, `faq`, `fragment`, `answer-event`, `error`

- [ ] **Step 6: Verify step_function_types models are unchanged**

Run: `git diff packages/shared/lambda_layers/step_function_types/models.py`
Expected: No changes. The agentic retrieval Lambda uses the existing `UserQuery` model.

- [ ] **Step 7: Verify messages-stack changes are minimal**

Run: `git diff packages/messages/infra/messages-stack.ts`
Expected: Only these additive changes:
- 2 new `public readonly` property declarations
- 2 assignment lines
- 1 new prop in interface (`useGraphRAG?: boolean`)
- 1 change to EventBridge rule: `enabled: props.useGraphRAG !== true` (defaults to enabled)

- [ ] **Step 8: Verify mutual exclusion works**

Run: `cd packages/infra && npx cdk synth -c useGraphRAG=true --quiet 2>&1 | tail -20`
Expected: Synthesis succeeds. The old EventBridge rule should show `enabled: false`, the new one `enabled: true`.

- [ ] **Step 9: Commit any fixes**

```bash
git add -A
git commit -m "chore: verify integration of GraphRAG alongside existing backend"
```

---

## Switching Backends (Post-Deployment)

Once the GraphRAG backend is deployed and the Neptune graph is populated:

**Default (existing backend):**
```bash
cdk deploy
# Old EventBridge rule: ENABLED, GraphRAG rule: DISABLED
```

**Switch to GraphRAG:**
```bash
cdk deploy -c useGraphRAG=true
# Old EventBridge rule: DISABLED, GraphRAG rule: ENABLED
```

**Rollback to existing backend:**
```bash
cdk deploy
# Reverts to default: old rule ENABLED, GraphRAG rule DISABLED
# Existing KBs, Lambdas, and state machine are all still there
```

**WARNING:** Never enable both rules simultaneously. The CDK context flag enforces mutual exclusion -- when one is enabled, the other is disabled. If both fire on the same event, duplicate/interleaved responses will corrupt WebSocket sessions.

---

## Summary: Data Flow After Migration

```
User sends message via WebSocket
    |
    v
EventBridge (same event source: wisconsin-dor.chat-api)
    |
    | (MUTUALLY EXCLUSIVE -- only one rule enabled at a time)
    |
    +----> [Rule 1: EXISTING, enabled by default] ChatStreamingStateMachine
    |          Classifier -> Retrieval -> Parallel(ResourceStream, ResponseStream)
    |
    +----> [Rule 2: NEW, enabled when useGraphRAG=true] GraphRAGStreamingStateMachine
               AgenticRetrieval -> Parallel(ResourceStream, ResponseStream)
                                              ^                ^
                                              |                |
                                     SHARED existing Lambdas (same code)
```

Both state machines produce identical output shapes (`RetrieveResult` with `generate_response_job` + `stream_documents_job`), so the downstream streaming Lambdas work with either backend. Only one rule fires per event -- the CDK context flag enforces this.

---

## Ingestion Pipeline Usage (One-Time Setup, after CDK deploy)

```bash
# 1. Scrape all documents to S3
python scripts/graphrag/scrape_documents.py --bucket <raw-bucket-from-cdk-output>

# 2. Extract and classify
python scripts/graphrag/extract.py \
  --raw-bucket <raw-bucket> \
  --work-bucket <work-bucket-from-cdk-output>

# 3. Embed chunks and documents
python scripts/graphrag/embed.py --work-bucket <work-bucket>

# 4. Load into Neptune graph
python scripts/graphrag/load.py \
  --work-bucket <work-bucket> \
  --graph-id <neptune-graph-id-from-cdk-output>
```

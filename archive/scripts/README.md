# Ingestion Pipelines

This directory contains manual ingestion pipelines used to populate the Wisconsin DOR chatbot’s knowledge bases.
These scripts prepare and upload data to CDK-managed S3 buckets, which are later indexed by Amazon Bedrock Knowledge Bases.

Important:
- S3 buckets are created and owned by CDK.
- Ingestion scripts must not create buckets.
- Always use bucket names from CloudFormation outputs.

---

## Prerequisites

Before running any ingestion steps:

- Backend infrastructure has been deployed (bun run deploy)
- You have AWS credentials with access to:
  - CDK-created S3 buckets
  - Textract (for chunking)
  - Bedrock Knowledge Base ingestion
- You know the following values from CloudFormation outputs:
  - FaqBucketName
  - RagBucketName
  - FaqKnowledgeBaseId
  - RagKnowledgeBaseId
  - FaqDataSourceId
  - RagDataSourceId

---

## 1. Scrape Wisconsin DOR FAQ Web Pages

Scrapes the Wisconsin Department of Revenue (DOR) FAQ page, extracts structured question/answer pairs, and writes them to a JSON file.

**Default output:**
documents/faqs.json

**Command:**
```bash
python3 -m scripts.scrape_FAQ \
  --url https://www.revenue.wi.gov/Pages/FAQS/home-pt.aspx
```

**Optional custom output:**

```bash
python3 -m scripts.scrape_FAQ \
  --url https://www.revenue.wi.gov/Pages/FAQS/home-pt.aspx \
  --out documents/faqs.json
```

---

## 2. Ingest FAQs for the FAQ Knowledge Base

Uploads each FAQ as an individual text file into the FAQ knowledge base bucket.

**Input:**

documents/faqs.json

**Command:**

```bash 
python3 -m scripts.ingest_FAQ \
  --bucket <FaqBucketName> \
  --file documents/faqs.json
```
**Use the exact bucket name output by CDK (FaqBucketName).**

---

## 3. Ingest RAG Documents (PDFs)

Downloads PDF documents listed in a descriptor file and uploads them as raw source files.
These PDFs are not directly ingested by the knowledge base.

**Input:**

documents/document_descs.json

**Command:**

```bash
python3 -m scripts.ingest_documents \
  --bucket <PDF_SOURCE_BUCKET> \
  --prefix sources/ \
  --input-file documents/document_descs.json
```
**This bucket is used as an intermediate storage location for raw PDFs.**

---

## 4. Generate and Ingest Custom Chunks for the RAG Knowledge Base

Runs Textract and the custom chunking pipeline on the raw PDFs and uploads the resulting text chunks and metadata into the RAG Knowledge Base bucket.

**Command:**

```bash
python3 -m scripts.ingest_chunks \
  --source-bucket <PDF_SOURCE_BUCKET> \
  --prefix sources/ \
  --dest-bucket <RagBucketName>
  ```
  
**Use the exact bucket name output by CDK (RagBucketName).**

---

## 5. Sync / Re-Ingest the Bedrock Knowledge Bases

After new documents or chunks are uploaded, you must trigger ingestion so Bedrock re-indexes the data.

**Command:**

```bash
aws bedrock-agent start-ingestion-job \
  --knowledge-base-id <FaqKnowledgeBaseId> \
  --data-source-id <FaqDataSourceId>

aws bedrock-agent start-ingestion-job \
  --knowledge-base-id <RagKnowledgeBaseId> \
  --data-source-id <RagDataSourceId>
```
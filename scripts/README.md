# Ingestion Pipelines

This directory contains scripts for scraping and ingesting FAQs, documents, and processed chunks into their respective knowledge base buckets.

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
  --bucket <FAQ_KB_BUCKET> \
  --file documents/faqs.json
```

---

## 3. Ingest RAG Documents (PDFs)

Downloads documents listed in the descriptor file and uploads them to S3 with metadata.

**Input:**

documents/document_descs.json

**Command:**

```bash
python3 -m scripts.ingest_documents \
  --bucket <PDF_BUCKET> \
  --prefix sources/ \
  --input-file documents/document_descs.json
```

---

## 4. Ingest Custom Chunks for the RAG Knowledge Base

Runs Textract and the custom chunking pipeline on the uploaded PDFs and writes the
resulting custom chunks to the RAG knowledge base bucket.

**Command:**

```bash
python3 -m scripts.ingest_chunks \
  --source-bucket <PDF_BUCKET> \
  --prefix sources/ \
  --dest-bucket <RAG_KB_BUCKET>
  ```

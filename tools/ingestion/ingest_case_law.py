"""
Single-pass case law ingestion: extract citations → enrich via CourtListener → upload to S3.

Replaces the old multi-step pipeline:
  extract_case_citations.py → fetch_case_law.py → backfill_cl_urls.py

This script does everything in one shot, producing correct metadata from the start:
  1. Extract citations from statute PDF hyperlinks (local or S3)
  2. Search CourtListener for each citation (case_name, opinion URL, cluster_id)
  3. Deduplicate parallel reporters (same case cited as wis-2d AND n-w-2d)
  4. Download full opinion text (CourtListener primary, Scholar fallback)
  5. Upload to S3: raw/case-law/{reporter}/{slug}.txt + .metadata.json

Prerequisites:
    export COURTLISTENER_TOKEN="your_token_here"

Usage:
    # Full pipeline from local statute PDFs:
    python tools/ingestion/ingest_case_law.py --bucket wis-raw-bucket-c8e69250

    # From S3 statute PDFs:
    python tools/ingestion/ingest_case_law.py --bucket wis-raw-bucket-c8e69250 --from-s3

    # Metadata stubs only (no opinion text download):
    python tools/ingestion/ingest_case_law.py --bucket wis-raw-bucket-c8e69250 --stubs-only

    # With Google Scholar fallback for CL misses:
    python tools/ingestion/ingest_case_law.py --bucket wis-raw-bucket-c8e69250 --scholar-fallback

    # Dry run (extract + enrich, no S3 writes):
    python tools/ingestion/ingest_case_law.py --bucket wis-raw-bucket-c8e69250 --dry-run

    # Resume after interruption:
    python tools/ingestion/ingest_case_law.py --bucket wis-raw-bucket-c8e69250 --resume
"""

import argparse
import json
import logging
import os
import random
import re
import sys
import tempfile
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote, unquote

import boto3
import fitz  # PyMuPDF
import requests
from bs4 import BeautifulSoup

# Force unbuffered output so progress shows in real time
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)


STATE_LAWS_DIR = Path(__file__).resolve().parent.parent.parent / "docs" / "state-laws"
PROGRESS_FILE = Path(__file__).resolve().parent / ".ingest-case-law-progress.json"

CL_SEARCH_URL = "https://www.courtlistener.com/api/rest/v4/search/"
CL_OPINION_URL = "https://www.courtlistener.com/api/rest/v4/opinions/{}/"

SCHOLAR_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

_REPORTER_PATTERNS = [
    ("f-supp-3d", re.compile(r"\d+-f-supp-3d-\d+")),
    ("f-supp-2d", re.compile(r"\d+-f-supp-2d-\d+")),
    ("f-supp", re.compile(r"\d+-f-supp-\d+")),
    ("f-4th", re.compile(r"\d+-f-4th-\d+")),
    ("f-3d", re.compile(r"\d+-f-3d-\d+")),
    ("f-2d", re.compile(r"\d+-f-2d-\d+")),
    ("l-ed-2d", re.compile(r"\d+-l-ed-2d-\d+")),
    ("n-w-3d", re.compile(r"\d+-n-w-3d-\d+")),
    ("n-w-2d", re.compile(r"\d+-n-w-2d-\d+")),
    ("wis-2d", re.compile(r"\d+-wis-2d-\d+")),
    ("wi-app", re.compile(r"\d+-wi-app-\d+")),
    ("wi", re.compile(r"\d+-wi-\d+")),
    ("s-ct", re.compile(r"\d+-s-ct-\d+")),
    ("u-s", re.compile(r"\d+-u-s-\d+")),
]

REPORTER_PRIORITY = [
    "wis-2d",
    "wi-app",
    "wi",
    "n-w-2d",
    "n-w-3d",
    "s-ct",
    "u-s",
    "f-supp-3d",
    "f-supp-2d",
    "f-supp",
    "f-4th",
    "f-3d",
    "f-2d",
    "l-ed-2d",
]


# ---------------------------------------------------------------------------
# Slug / reporter helpers
# ---------------------------------------------------------------------------


def _citation_to_slug(citation: str) -> str:
    lowered = citation.lower()
    normalized = re.sub(r"[^a-z0-9]", " ", lowered)
    return "-".join(normalized.split())


def _reporter_for_slug(slug: str) -> str:
    for group, pattern in _REPORTER_PATTERNS:
        if pattern.fullmatch(slug):
            return group
    return "misc"


def _reporter_priority(slug: str) -> int:
    reporter = _reporter_for_slug(slug)
    try:
        return REPORTER_PRIORITY.index(reporter)
    except ValueError:
        return len(REPORTER_PRIORITY)


def _scholar_url(citation: str) -> str:
    q = quote(citation)
    return f"http://scholar.google.com/scholar?hl=en&as_sdt=4&as_sdts=50&as_vis=1&q={q}"


# ---------------------------------------------------------------------------
# Phase 1: Extract citations from statute PDFs
# ---------------------------------------------------------------------------


def _normalize_citation(raw: str) -> str:
    citation = raw.strip().rstrip("/")
    if not citation or len(citation) < 3:
        return ""
    if not re.search(r"\d", citation):
        return ""
    return citation


def _key_to_chapter_label(key: str) -> str:
    filename = key.split("/")[-1]
    m = re.match(r"statutes-(\d+)", filename)
    if m:
        return f"{m.group(1)}.pdf"
    return filename


def extract_citations_from_pdf(pdf_path: str, chapter_label: str) -> list[dict]:
    doc = fitz.open(pdf_path)
    results = []
    for page_num in range(doc.page_count):
        page = doc[page_num]
        for link in page.get_links():
            uri = link.get("uri", "")
            if "/document/courts/" not in uri:
                continue
            raw_citation = unquote(uri.split("/document/courts/")[-1])
            citation = _normalize_citation(raw_citation)
            if not citation:
                continue
            results.append(
                {
                    "citation": citation,
                    "legis_url": uri,
                    "page": page_num + 1,
                    "chapter_file": chapter_label,
                }
            )
    doc.close()
    return results


def extract_from_local(state_laws_dir: Path) -> list[dict]:
    pdf_files = sorted(state_laws_dir.glob("*.pdf"))
    if not pdf_files:
        logger.error(f"No PDFs found in {state_laws_dir}")
        return []

    logger.info(f"Scanning {len(pdf_files)} statute PDFs for case citations...")
    all_entries = []
    for pdf_path in pdf_files:
        entries = extract_citations_from_pdf(str(pdf_path), pdf_path.name)
        if entries:
            unique = len({e["citation"] for e in entries})
            logger.info(f"  {pdf_path.name}: {unique} unique citations")
        all_entries.extend(entries)
    return all_entries


def extract_from_s3(bucket: str, s3_client) -> list[dict]:
    paginator = s3_client.get_paginator("list_objects_v2")
    statute_keys = []
    for page in paginator.paginate(Bucket=bucket, Prefix="raw/statutes-"):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith(".pdf") and ".metadata" not in obj["Key"]:
                statute_keys.append(obj["Key"])

    logger.info(f"Found {len(statute_keys)} statute PDFs in s3://{bucket}/")

    all_entries = []
    with tempfile.TemporaryDirectory() as tmpdir:
        for key in sorted(statute_keys):
            filename = key.split("/")[-1]
            local_path = os.path.join(tmpdir, filename)
            s3_client.download_file(bucket, key, local_path)
            chapter_label = _key_to_chapter_label(key)
            entries = extract_citations_from_pdf(local_path, chapter_label)
            if entries:
                unique = len({e["citation"] for e in entries})
                logger.info(f"  {chapter_label}: {unique} unique citations")
            all_entries.extend(entries)
    return all_entries


def consolidate_citations(raw_entries: list[dict]) -> list[dict]:
    """Deduplicate and group by citation, collecting all source pages."""
    by_citation = defaultdict(lambda: {"legis_url": "", "sources": defaultdict(set)})

    for entry in raw_entries:
        citation = entry["citation"]
        record = by_citation[citation]
        if not record["legis_url"]:
            record["legis_url"] = entry["legis_url"]
        record["sources"][entry["chapter_file"]].add(entry["page"])

    results = []
    for citation, record in sorted(by_citation.items()):
        sources = [
            {"file": chapter, "pages": sorted(pages)}
            for chapter, pages in sorted(record["sources"].items())
        ]
        results.append(
            {
                "citation": citation,
                "legis_url": record["legis_url"],
                "scholar_url": _scholar_url(citation),
                "sources": sources,
            }
        )
    return results


# ---------------------------------------------------------------------------
# Phase 2: CourtListener enrichment
# ---------------------------------------------------------------------------


def search_courtlistener(citation: str, session: requests.Session) -> dict | None:
    """Search CourtListener for a citation. Returns enrichment dict or None."""
    params = {"q": f'"{citation}"', "type": "o", "page_size": 5}
    for attempt in range(4):
        try:
            resp = session.get(CL_SEARCH_URL, params=params, timeout=20)
            if resp.status_code == 429 and attempt < 3:
                retry_after = int(resp.headers.get("Retry-After", 10))
                logger.warning(f"CL rate limited, sleeping {retry_after}s")
                time.sleep(retry_after)
                continue
            resp.raise_for_status()
            data = resp.json()
            break
        except (requests.RequestException, ValueError) as e:
            if attempt == 3:
                logger.warning(f"CL search failed for '{citation}': {e}")
                return None
            time.sleep(2**attempt)
    else:
        return None

    if not data.get("results"):
        return None

    # Prefer exact citation match
    result = None
    for r in data["results"]:
        if citation in r.get("citation", []):
            result = r
            break
    if not result:
        result = data["results"][0]

    opinions = result.get("opinions", [])
    opinion_id = None
    for pref in ("combined-opinion", "lead-opinion"):
        for op in opinions:
            if op.get("type") == pref:
                opinion_id = op["id"]
                break
        if opinion_id:
            break
    if not opinion_id and opinions:
        opinion_id = opinions[0]["id"]

    return {
        "case_name": result.get("caseName") or result.get("caseNameFull") or "",
        "source_url": f"https://www.courtlistener.com{result['absolute_url']}",
        "cluster_id": result.get("cluster_id") or result.get("id") or "",
        "opinion_id": opinion_id,
        "all_citations": result.get("citation", []),
    }


def fetch_opinion_text(opinion_id: int, session: requests.Session) -> str | None:
    """Fetch full opinion text from CourtListener."""
    url = CL_OPINION_URL.format(opinion_id)
    try:
        resp = session.get(url, timeout=15)
        if resp.status_code == 401:
            logger.error("CL auth failed — check COURTLISTENER_TOKEN")
            return None
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", 30))
            logger.warning(f"CL rate limited on opinion fetch, sleeping {retry_after}s")
            time.sleep(retry_after)
            resp = session.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning(f"Failed to fetch opinion {opinion_id}: {e}")
        return None

    for field in (
        "plain_text",
        "html_with_citations",
        "html",
        "html_lawbox",
        "html_columbia",
        "xml_harvard",
    ):
        text = data.get(field, "")
        if text and text.strip():
            if field.startswith("html") or field.startswith("xml"):
                text = re.sub(r"<[^>]+>", " ", text)
                text = re.sub(r"\s+", " ", text).strip()
            return text
    return None


def fetch_scholar_opinion(scholar_url: str, delay: float) -> tuple[str, str] | None:
    """Fallback: scrape opinion text from Google Scholar."""
    time.sleep(delay + random.uniform(0, delay * 0.5))
    try:
        resp = requests.get(scholar_url, headers=SCHOLAR_HEADERS, timeout=30)
        if resp.status_code == 429 or "captcha" in resp.text.lower():
            return None

        soup = BeautifulSoup(resp.text, "html.parser")
        case_link = None
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/scholar_case?" in href and "about=" not in href:
                case_link = href
                break
        if not case_link:
            return None

        time.sleep(delay * 0.5 + random.uniform(0, 3))
        case_url = (
            f"https://scholar.google.com{case_link}" if case_link.startswith("/") else case_link
        )
        resp2 = requests.get(case_url, headers=SCHOLAR_HEADERS, timeout=30)
        if resp2.status_code == 429 or "captcha" in resp2.text.lower():
            return None

        soup2 = BeautifulSoup(resp2.text, "html.parser")
        opinion = soup2.find("div", id="gs_opinion")
        if not opinion:
            return None

        case_name = ""
        h1 = soup2.find("h1")
        if h1:
            case_name = h1.get_text(strip=True)
        return case_name, opinion.get_text("\n", strip=True)
    except requests.RequestException:
        return None


# ---------------------------------------------------------------------------
# S3 cache: reuse enrichment from existing extracted/ records
# ---------------------------------------------------------------------------


def load_extracted_cache(work_bucket: str, s3_client) -> dict:
    """Load existing extracted/case-law-*.json records, indexed by doc_id AND citation.

    After dedup, only winner doc_ids remain. But statutes cite both sides of a
    parallel reporter pair (e.g., '457 N.W.2d 514' whose winner is '109 Wis. 2d 290').
    We index by citation and alternate_citations so either side resolves to the winner.

    Returns: {doc_id_or_citation_slug: metadata_dict}
    """
    cache = {}
    paginator = s3_client.get_paginator("list_objects_v2")
    keys = []
    for page in paginator.paginate(Bucket=work_bucket, Prefix="extracted/case-law-"):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith(".json"):
                keys.append(obj["Key"])

    if not keys:
        return cache

    logger.info(
        f"Loading {len(keys)} cached extracted records from s3://{work_bucket}/extracted/..."
    )

    def fetch(key):
        obj = s3_client.get_object(Bucket=work_bucket, Key=key)
        return json.loads(obj["Body"].read())

    records = []
    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = {pool.submit(fetch, k): k for k in keys}
        for future in as_completed(futures):
            try:
                records.append(future.result())
            except Exception:
                pass

    for d in records:
        cache[d["doc_id"]] = d
        # Also index by citation slug so parallel-reporter lookups hit
        if d.get("citation"):
            alt_doc_id = f"case-law-{_citation_to_slug(d['citation'])}"
            cache[alt_doc_id] = d
        # Index alternate citations too
        for alt in d.get("alternate_citations", []):
            alt_doc_id = f"case-law-{_citation_to_slug(alt)}"
            cache[alt_doc_id] = d

    logger.info(f"Loaded {len(records)} cached records ({len(cache)} index entries)")
    return cache


# ---------------------------------------------------------------------------
# Phase 3: Deduplication
# ---------------------------------------------------------------------------


def deduplicate_by_cluster(enriched: list[dict]) -> list[dict]:
    """Group citations by CourtListener cluster_id OR source_url, keep highest-priority reporter.

    Citations without a cluster_id or source_url (CL misses) are kept as-is.
    """
    by_cluster = defaultdict(list)
    no_cluster = []

    for entry in enriched:
        cluster_id = entry.get("cluster_id")
        source_url = entry.get("source_url", "")
        group_key = str(cluster_id) if cluster_id else (source_url if source_url else None)
        if group_key:
            by_cluster[group_key].append(entry)
        else:
            no_cluster.append(entry)

    kept = []
    total_dupes = 0

    for _cluster_id, group in by_cluster.items():
        group.sort(key=lambda e: _reporter_priority(e["slug"]))
        winner = group[0]

        # Merge sources from all duplicates
        all_sources = defaultdict(set)
        alternate_citations = []
        for entry in group:
            for src in entry["sources"]:
                all_sources[src["file"]].update(src["pages"])
            if entry["citation"] != winner["citation"]:
                alternate_citations.append(entry["citation"])

        winner["sources"] = [
            {"file": f, "pages": sorted(pages)} for f, pages in sorted(all_sources.items())
        ]
        if alternate_citations:
            winner["alternate_citations"] = alternate_citations

        kept.append(winner)
        total_dupes += len(group) - 1

    kept.extend(no_cluster)
    if total_dupes:
        logger.info(f"Dedup: {total_dupes} parallel-reporter duplicates removed")
    return kept


# ---------------------------------------------------------------------------
# Phase 4: Upload to S3
# ---------------------------------------------------------------------------


def upload_case(
    entry: dict,
    bucket: str,
    s3_client,
    stubs_only: bool,
    cl_session: requests.Session | None,
    scholar_delay: float,
) -> dict:
    """Upload a single case to S3 with complete metadata.

    Returns a status dict for progress tracking.
    """
    slug = entry["slug"]
    reporter = _reporter_for_slug(slug)
    doc_id = f"case-law-{slug}"
    citation = entry["citation"]

    text = None
    source = "stub"

    if not stubs_only:
        # Try CourtListener opinion text
        opinion_id = entry.get("opinion_id")
        if opinion_id and cl_session:
            text = fetch_opinion_text(opinion_id, cl_session)
            if text:
                source = "courtlistener"

        # Scholar fallback
        if not text and scholar_delay > 0:
            result = fetch_scholar_opinion(entry["scholar_url"], scholar_delay)
            if result:
                entry.setdefault("case_name", result[0])
                text = result[1]
                source = "scholar"

    if text:
        content = text.encode("utf-8")
        content_type = "text/plain"
        ext = ".txt"
    else:
        content = json.dumps(
            {
                "citation": citation,
                "case_name": entry.get("case_name") or citation,
                "note": "Full opinion text not available. See source_url or scholar_url.",
                "scholar_url": entry["scholar_url"],
            },
            indent=2,
        ).encode("utf-8")
        content_type = "application/json"
        ext = ".json"

    doc_key = f"raw/case-law/{reporter}/{slug}{ext}"
    meta_key = f"raw/case-law/{reporter}/{slug}.metadata.json"

    s3_client.put_object(Bucket=bucket, Key=doc_key, Body=content, ContentType=content_type)

    metadata = {
        "metadataAttributes": {
            "doc_id": doc_id,
            "doc_type": "case_law",
            "framework_id": "FW-CASE-LAW",
            "authority_level": "3",
            "category": "case_law",
            "citation": citation,
            "case_name": entry.get("case_name") or citation,
            "source_url": entry.get("source_url") or entry["legis_url"],
            "legis_url": entry["legis_url"],
            "scholar_url": entry["scholar_url"],
            "citing_statutes": json.dumps(entry["sources"]),
        }
    }
    if entry.get("alternate_citations"):
        metadata["metadataAttributes"]["alternate_citations"] = json.dumps(
            entry["alternate_citations"]
        )

    s3_client.put_object(
        Bucket=bucket,
        Key=meta_key,
        Body=json.dumps(metadata, indent=2).encode("utf-8"),
        ContentType="application/json",
    )

    return {
        "doc_id": doc_id,
        "s3_key": doc_key,
        "source": source,
        "has_text": text is not None,
    }


# ---------------------------------------------------------------------------
# Progress management
# ---------------------------------------------------------------------------


def load_progress(path: Path) -> dict:
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {"completed": {}, "stats": {"courtlistener": 0, "scholar": 0, "stub": 0}}


def save_progress(progress: dict, path: Path):
    with open(path, "w") as f:
        json.dump(progress, f, indent=2)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Single-pass case law ingestion: extract → enrich → dedup → upload"
    )
    parser.add_argument("--bucket", required=True, help="S3 raw bucket")
    parser.add_argument("--from-s3", action="store_true", help="Read statute PDFs from S3")
    parser.add_argument("--state-laws-dir", type=Path, default=STATE_LAWS_DIR)
    parser.add_argument(
        "--stubs-only", action="store_true", help="Upload metadata stubs only, skip text download"
    )
    parser.add_argument(
        "--scholar-fallback",
        action="store_true",
        help="Enable Google Scholar as fallback for CL misses",
    )
    parser.add_argument(
        "--scholar-delay", type=float, default=30, help="Delay between Scholar requests"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Extract + enrich only, no S3 writes"
    )
    parser.add_argument("--resume", action="store_true", help="Skip already-completed cases")
    parser.add_argument("--limit", type=int, default=0, help="Process at most N cases (0=all)")
    parser.add_argument(
        "--concurrency", type=int, default=3, help="Parallel CL requests (default: 3)"
    )
    parser.add_argument(
        "--work-bucket",
        default="wis-work-bucket-c8e69250",
        help="Work bucket with extracted/ cache",
    )
    parser.add_argument(
        "--no-cache", action="store_true", help="Skip S3 cache, force CL lookup for all"
    )
    parser.add_argument(
        "--clean-losers",
        action="store_true",
        help="After dedup, delete parallel-reporter losers from raw + work S3 buckets",
    )
    parser.add_argument("--profile", default="widor")
    parser.add_argument("--region", default="us-east-1")
    args = parser.parse_args()

    token = os.environ.get("COURTLISTENER_TOKEN", "")
    if not token and not args.stubs_only and not args.dry_run:
        logger.error(
            "Set COURTLISTENER_TOKEN env var. "
            "Register free at https://www.courtlistener.com/sign-in/register/"
        )
        return

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    s3 = session.client("s3")

    cl_session = requests.Session()
    if token:
        cl_session.headers["Authorization"] = f"Token {token}"
    cl_session.headers["User-Agent"] = "wisconsin-dor-case-law-ingest/2.0"

    progress = (
        load_progress(PROGRESS_FILE)
        if args.resume
        else {"completed": {}, "stats": {"courtlistener": 0, "scholar": 0, "stub": 0}}
    )

    # --- Phase 1: Extract citations from statute PDFs ---
    logger.info("=" * 60)
    logger.info("PHASE 1: Extracting citations from statute PDFs")
    logger.info("=" * 60)

    if args.from_s3:
        raw_entries = extract_from_s3(args.bucket, s3)
    else:
        raw_entries = extract_from_local(args.state_laws_dir)

    if not raw_entries:
        logger.error("No citations extracted. Aborting.")
        return

    citations = consolidate_citations(raw_entries)
    logger.info(f"Extracted {len(citations)} unique citations from {len(raw_entries)} links")

    # --- Phase 2: Enrich via CourtListener (with S3 cache) ---
    logger.info("")
    logger.info("=" * 60)
    logger.info("PHASE 2: Enriching citations")
    logger.info("=" * 60)

    # Load cache from work bucket
    cache = {}
    if not args.no_cache:
        cache = load_extracted_cache(args.work_bucket, s3)

    enriched = []
    cache_hits = 0
    cl_hits = 0
    cl_misses = 0
    need_cl = []

    for entry in citations:
        citation = entry["citation"]
        slug = _citation_to_slug(citation)
        doc_id = f"case-law-{slug}"

        enriched_entry = {
            "citation": citation,
            "slug": slug,
            "legis_url": entry["legis_url"],
            "scholar_url": entry["scholar_url"],
            "sources": entry["sources"],
        }

        cached = cache.get(doc_id)
        if cached and cached.get("source_url"):
            enriched_entry["case_name"] = cached.get("case_name", "")
            enriched_entry["source_url"] = cached.get("source_url", "")
            enriched_entry["cluster_id"] = cached.get("courtlistener_cluster_id", "")
            enriched_entry["opinion_id"] = None
            cache_hits += 1
            enriched.append(enriched_entry)
        else:
            need_cl.append((enriched_entry, entry))

    logger.info(f"  Cache hits: {cache_hits}, need CL lookup: {len(need_cl)}")

    if need_cl:
        logger.info(
            f"  Querying CourtListener for {len(need_cl)} citations ({args.concurrency} workers)..."
        )

        def enrich_one(pair):
            enriched_entry, _ = pair
            cl_result = search_courtlistener(enriched_entry["citation"], cl_session)
            if cl_result:
                enriched_entry["case_name"] = cl_result["case_name"]
                enriched_entry["source_url"] = cl_result["source_url"]
                enriched_entry["cluster_id"] = cl_result["cluster_id"]
                enriched_entry["opinion_id"] = cl_result["opinion_id"]
            return enriched_entry

        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            futures = {pool.submit(enrich_one, pair): pair for pair in need_cl}
            for i, future in enumerate(as_completed(futures), 1):
                result = future.result()
                enriched.append(result)
                if result.get("cluster_id"):
                    cl_hits += 1
                else:
                    cl_misses += 1
                    logger.warning(f"  No CL link for: {result['citation']}")
                if i % 50 == 0:
                    logger.info(
                        f"    CL progress: {i}/{len(need_cl)} (hits: {cl_hits}, misses: {cl_misses})"
                    )

    logger.info(
        f"Enrichment complete: {cache_hits} cached, {cl_hits} CL hits, {cl_misses} CL misses"
    )

    # --- Phase 3: Deduplicate parallel reporters ---
    logger.info("")
    logger.info("=" * 60)
    logger.info("PHASE 3: Deduplicating parallel reporters")
    logger.info("=" * 60)

    before_count = len(enriched)
    deduped = deduplicate_by_cluster(enriched)
    logger.info(f"After dedup: {len(deduped)} cases (from {before_count} citations)")

    if args.clean_losers:
        winner_slugs = {entry["slug"] for entry in deduped}
        loser_slugs = [entry["slug"] for entry in enriched if entry["slug"] not in winner_slugs]
        if loser_slugs:
            logger.info(f"Cleaning {len(loser_slugs)} parallel-reporter losers from S3...")
            deleted_raw, deleted_work = 0, 0
            for slug in loser_slugs:
                reporter = _reporter_for_slug(slug)
                for ext in (".txt", ".json", ".metadata.json"):
                    key = f"raw/case-law/{reporter}/{slug}{ext}"
                    try:
                        s3.delete_object(Bucket=args.bucket, Key=key)
                        deleted_raw += 1
                    except Exception:  # noqa: BLE001
                        pass
                work_key = f"extracted/case-law-{slug}.json"
                try:
                    s3.delete_object(Bucket=args.work_bucket, Key=work_key)
                    deleted_work += 1
                except Exception:  # noqa: BLE001
                    pass
            logger.info(
                f"  Deleted {deleted_raw} raw files, {deleted_work} extraction caches"
            )
        else:
            logger.info("No losers to clean — all enriched entries are winners")

    if args.dry_run:
        logger.info("")
        logger.info("=" * 60)
        logger.info(
            "[DRY RUN] Would upload %d cases to s3://%s/raw/case-law/", len(deduped), args.bucket
        )
        logger.info("=" * 60)

        by_reporter = defaultdict(int)
        for entry in deduped:
            by_reporter[_reporter_for_slug(entry["slug"])] += 1
        for reporter, count in sorted(by_reporter.items(), key=lambda x: -x[1]):
            logger.info(f"  {reporter}: {count}")

        save_progress(progress, PROGRESS_FILE)
        return

    # --- Phase 4: Upload to S3 ---
    logger.info("")
    logger.info("=" * 60)
    logger.info("PHASE 4: Uploading to S3")
    logger.info("=" * 60)

    scholar_delay = args.scholar_delay if args.scholar_fallback else 0
    uploaded = 0
    skipped = 0

    for _i, entry in enumerate(deduped):
        doc_id = f"case-law-{entry['slug']}"

        if args.resume and doc_id in progress["completed"]:
            skipped += 1
            continue

        if args.limit and uploaded >= args.limit:
            logger.info(f"Reached --limit={args.limit}, stopping.")
            break

        result = upload_case(entry, args.bucket, s3, args.stubs_only, cl_session, scholar_delay)
        progress["completed"][doc_id] = result
        progress["stats"][result["source"]] += 1
        uploaded += 1

        if uploaded % 50 == 0:
            save_progress(progress, PROGRESS_FILE)
            s = progress["stats"]
            logger.info(
                f"  Uploaded {uploaded}/{len(deduped) - skipped} "
                f"(CL: {s['courtlistener']}, Scholar: {s['scholar']}, Stubs: {s['stub']})"
            )

        time.sleep(0.3)

    save_progress(progress, PROGRESS_FILE)

    # --- Summary ---
    logger.info("")
    logger.info("=" * 60)
    logger.info("DONE")
    logger.info("=" * 60)
    s = progress["stats"]
    logger.info(f"Uploaded: {uploaded} cases ({skipped} skipped as already done)")
    logger.info(f"  With full text (CourtListener): {s['courtlistener']}")
    logger.info(f"  With full text (Scholar):       {s['scholar']}")
    logger.info(f"  Metadata stubs only:            {s['stub']}")
    logger.info(f"Progress saved to {PROGRESS_FILE}")
    logger.info("")
    logger.info("Next steps:")
    logger.info(
        "  1. Run extraction:  ./tools/ingestion/scripts/run_fargate.sh extract --source-filter case-law --force"
    )
    logger.info(
        "  2. Run load:        ./tools/ingestion/scripts/run_fargate.sh load --start-phase 1 --stop-after-phase 2"
    )


if __name__ == "__main__":
    main()

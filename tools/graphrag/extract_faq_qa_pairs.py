"""Extract Q/A pairs from Wisconsin DOR SharePoint FAQ pages and upload
them to the Bedrock FAQ knowledge base bucket.

The agentic retrieval loop hits the Bedrock FAQ KB before touching Neptune
(see packages/graphrag/lambdas/agentic_retrieval/main.py:679). When the top
score clears 0.70, the graph is skipped entirely. So the faster path for
answering common questions is ensuring the KB has the SharePoint content
split into single-Q/A files matching the existing bucket format.

Usage:
    # Dry-run: print what would be generated, no network writes
    python scripts/graphrag/extract_faq_qa_pairs.py --dry-run

    # Full upload (us-west-2 is canonical; sync_faq_bucket.sh copies east)
    python scripts/graphrag/extract_faq_qa_pairs.py \\
        --bucket wis-faq-bucket --region us-west-2 --start-id 457

    # After uploading, run sync + Bedrock ingestion
    ./scripts/graphrag/sync_faq_bucket.sh
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import time
from collections.abc import Iterable
from pathlib import Path

import boto3
import requests
from bs4 import BeautifulSoup, NavigableString, Tag

# Ensure the sibling faq_url_map module is importable regardless of CWD.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from faq_url_map import normalize_question  # noqa: E402

logger = logging.getLogger(__name__)

FAQ_URLS = [
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
]

USER_AGENT = "Mozilla/5.0 (WI-DOR-Bot/1.0)"
MAIN_DIV_ID = "ctl00_PlaceHolderMain_ctl01__ControlWrapper_RichHtmlField"

# Filename slug: drop non-word chars, cap at 100 chars, match existing `qa_N_slug`
_SLUG_DROP = re.compile(r"[^A-Za-z0-9]+")
_MAX_SLUG_LEN = 100


def _clean(text: str) -> str:
    """Collapse SharePoint zero-width/nbsp noise and whitespace runs."""
    cleaned = text.replace("​", "").replace(" ", " ").replace("﻿", "")
    return re.sub(r"\s+", " ", cleaned).strip()


def _slugify(question: str) -> str:
    slug = _SLUG_DROP.sub("_", question).strip("_")
    return slug[:_MAX_SLUG_LEN]


def extract_qa_pairs(html: str) -> list[tuple[str, str]]:
    """Extract (question, answer) pairs from a SharePoint FAQ page.

    Algorithm: anchor positions in the DOM (`<a name="qN">`) mark each
    question block. The first `<strong>` inside an anchor's slot is the
    question; remaining text until the next anchor is the answer. This
    handles both `<ol class="listLinks">`-wrapped pages and loosely nested
    `<ol>` structures (e.g., slf-agforest.aspx).
    """
    soup = BeautifulSoup(html, "html.parser")
    main = soup.find("div", id=MAIN_DIV_ID)
    if not main:
        return []

    # Flatten DOM to a list of descendants in document order. Using the
    # list index as a position key is simpler than comparing sourceline/
    # sourcepos tuples, which bs4 doesn't always populate reliably.
    nodes = list(main.descendants)

    # <hr/> separates the TOC (before) from the answer region (after). On
    # the rare page without one, fall through and process the whole div.
    hr_idx = next(
        (i for i, n in enumerate(nodes) if isinstance(n, Tag) and n.name == "hr"),
        -1,
    )
    answer_start = hr_idx + 1 if hr_idx >= 0 else 0
    answer_nodes = nodes[answer_start:]

    anchor_positions = [
        (i, n)
        for i, n in enumerate(answer_nodes)
        if isinstance(n, Tag) and n.name == "a" and n.has_attr("name")
    ]

    pairs: list[tuple[str, str]] = []
    for idx, (start_i, _anchor) in enumerate(anchor_positions):
        end_i = (
            anchor_positions[idx + 1][0]
            if idx + 1 < len(anchor_positions)
            else len(answer_nodes)
        )
        slot = answer_nodes[start_i + 1 : end_i]

        # First <strong> in the slot is the question.
        strong_idx = next(
            (j for j, n in enumerate(slot) if isinstance(n, Tag) and n.name == "strong"),
            None,
        )
        if strong_idx is None:
            continue

        strong_tag = slot[strong_idx]
        question = _clean(strong_tag.get_text(" ", strip=True))
        if len(question) < 5:
            continue

        # Answer: every NavigableString after the strong, excluding text
        # that's a descendant of the strong tag (which is the question
        # itself; we don't want it duplicated into the answer).
        strong_descendants = {id(x) for x in strong_tag.descendants}
        answer_parts = [
            str(n)
            for n in slot[strong_idx + 1 :]
            if isinstance(n, NavigableString) and id(n) not in strong_descendants
        ]
        answer = _clean(" ".join(answer_parts))

        if len(answer) >= 10:
            pairs.append((question, answer))

    # Dedup by normalized question. Some pages have multiple anchors
    # pointing at the same question block (TOC anchor vs answer anchor).
    seen: set[str] = set()
    unique: list[tuple[str, str]] = []
    for q, a in pairs:
        key = q.lower().rstrip("?.").strip()
        if key not in seen:
            seen.add(key)
            unique.append((q, a))
    return unique


def fetch_page(url: str) -> str:
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=60)
    resp.raise_for_status()
    return resp.text


def format_qa_file(question: str, answer: str) -> str:
    """Match the existing wis-faq-bucket format exactly: two lines, no trailing newline."""
    return f"Q: {question}\nA: {answer}\n"


def iter_qa_records(
    urls: Iterable[str], sleep_seconds: float
) -> list[tuple[str, str, str]]:
    """Return a list of (source_url, question, answer) triples."""
    records: list[tuple[str, str, str]] = []
    total_urls = len(list(urls)) if isinstance(urls, list) else None
    for i, url in enumerate(urls, start=1):
        prefix = f"[{i}/{total_urls}]" if total_urls else f"[{i}]"
        try:
            html = fetch_page(url)
        except requests.RequestException as e:
            logger.warning(f"{prefix} FETCH FAILED {url}: {e}")
            continue
        pairs = extract_qa_pairs(html)
        logger.info(f"{prefix} {url} -> {len(pairs)} Q/A pairs")
        for q, a in pairs:
            records.append((url, q, a))
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)
    return records


def fetch_existing_slugs(bucket: str, region: str, profile: str | None) -> set[str]:
    """Return the set of filename slugs already in the FAQ bucket."""
    session = boto3.Session(profile_name=profile) if profile else boto3.Session()
    client = session.client("s3", region_name=region)
    paginator = client.get_paginator("list_objects_v2")
    slugs: set[str] = set()
    for page in paginator.paginate(Bucket=bucket):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            name = Path(key).stem  # e.g. qa_457_How_to_file_objection
            # Strip the leading `qa_N_` or `faq_N_` prefix to compare slug portion
            m = re.match(r"^(qa|faq)_\d+(?:_(.*))?$", name)
            if m and m.group(2):
                slugs.add(m.group(2))
    return slugs


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--bucket",
        default="wis-faq-bucket",
        help="Target FAQ bucket (default: wis-faq-bucket, the us-west-2 master)",
    )
    parser.add_argument("--region", default="us-west-2")
    parser.add_argument("--profile", default=None, help="AWS profile; falls back to default chain")
    parser.add_argument(
        "--start-id",
        type=int,
        default=457,
        help=(
            "Starting N for qa_N_slug.txt filenames. Check existing bucket with "
            "`aws s3 ls s3://wis-faq-bucket/ | grep -oE 'qa_[0-9]+' | sort -t_ -k2 -n | tail -1` "
            "before choosing this to avoid collisions."
        ),
    )
    parser.add_argument("--dry-run", action="store_true", help="Print without uploading")
    parser.add_argument("--sleep", type=float, default=0.5, help="Seconds between fetches")
    parser.add_argument("--limit", type=int, default=None, help="Process only first N URLs (debug)")
    parser.add_argument(
        "--skip-existing-check",
        action="store_true",
        help="Skip the slug-dedup check against the bucket",
    )
    parser.add_argument(
        "--faq-url-table",
        default=None,
        help="If set, upsert normalized-question -> source_url into this DynamoDB table",
    )
    parser.add_argument(
        "--table-region",
        default="us-east-1",
        help="Region of --faq-url-table (FAQ master bucket is us-west-2; the table is us-east-1)",
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(message)s")

    urls = FAQ_URLS[: args.limit] if args.limit else FAQ_URLS
    logger.info(f"Processing {len(urls)} FAQ pages")

    records = iter_qa_records(urls, sleep_seconds=args.sleep)
    logger.info(f"Extracted {len(records)} total Q/A pairs")

    if not args.skip_existing_check:
        existing = fetch_existing_slugs(args.bucket, args.region, args.profile)
        logger.info(f"Existing FAQ bucket has {len(existing)} slugs")
        filtered: list[tuple[str, str, str]] = []
        skipped_dupes = 0
        for source_url, q, a in records:
            slug = _slugify(q)
            if slug in existing:
                skipped_dupes += 1
                continue
            filtered.append((source_url, q, a))
        logger.info(
            "After slug-dedup against existing bucket: "
            f"{len(filtered)} new, {skipped_dupes} skipped"
        )
        records = filtered

    # Write phase
    session = boto3.Session(profile_name=args.profile) if args.profile else boto3.Session()
    s3 = session.client("s3", region_name=args.region)

    faq_url_table = None
    if args.faq_url_table:
        faq_url_table = session.resource(
            "dynamodb", region_name=args.table_region
        ).Table(args.faq_url_table)

    next_id = args.start_id
    uploaded = 0
    for source_url, q, a in records:
        slug = _slugify(q)
        key = f"qa_{next_id}_{slug}.txt"
        body = format_qa_file(q, a)
        if args.dry_run:
            print(f"[DRY] {key}  <- {source_url}")
            print(f"       Q: {q[:90]}")
            print(f"       A: {a[:150]}")
        else:
            s3.put_object(
                Bucket=args.bucket,
                Key=key,
                Body=body.encode("utf-8"),
                ContentType="text/plain",
            )
            logger.info(f"uploaded s3://{args.bucket}/{key} ({len(body)} bytes)")
            if faq_url_table is not None:
                faq_url_table.put_item(
                    Item={
                        "normalized_question": normalize_question(q),
                        "source_url": source_url,
                    }
                )
        next_id += 1
        uploaded += 1

    logger.info(f"{'DRY RUN ' if args.dry_run else ''}Complete: {uploaded} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())

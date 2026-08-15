"""Extract prescriptive structure from DOR TIF worksheet spreadsheets (.xlsx).

The 4 TIF worksheets on the master document list are Excel *data-entry forms*
(tidbase, tidsub, decrement, tidbase-ppremoval) rather than levy-style
calculators. Their prescriptive content is:

  1. Field / column labels (what a preparer must enter)
  2. The "Instructions (will not print)" prose at the bottom of each sheet
  3. A handful of *meaningful* calculation formulas (e.g. decrement's
     ``Decline = Change / base value``)

Everything else is plumbing we deliberately drop: the hidden ``CoMuniData``
county/municipality lookup table (~1,900 rows of VLOOKUP fodder), per-row
``=SUM(K12,L12)`` totals, and cross-sheet ``='PE-606'!J4`` cell references.

This is an OFFLINE step run at the annual content refresh. It reads each
``.xlsx`` and emits a structured JSON sidecar to
``s3://{raw-bucket}/worksheets/{worksheet_id}.json`` (or a local dir with
``--out-dir``). The retrieval-side ``list_worksheet_lines`` /
``get_worksheet_line`` tools read that JSON — no spreadsheet engine ships to
the Lambda, and formulas are described, never evaluated.

Usage:
    uv run python -m tools.ingestion.worksheets.extract_worksheets \\
        --raw-bucket wis-raw-bucket-c8e69250            # download from S3 + upload JSON
    uv run python -m tools.ingestion.worksheets.extract_worksheets \\
        --local-dir /tmp/wsxlsx --out-dir /tmp/wsjson   # local files, local output
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import re

import boto3
import openpyxl

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("extract_worksheets")

REGION = os.environ.get("AWS_REGION", "us-east-1")

# The 4 TIF worksheets on the master document list. `title` and `source_url`
# mirror the manifest / form-instruction metadata so citation cards resolve.
WORKSHEETS = {
    "worksheets-tidbase": {
        "form_name": "tidbase.xlsx",
        "title": "TID Base Value Workbook",
        "source_url": "https://www.revenue.wi.gov/DORForms/tidbase.xlsx",
    },
    "worksheets-tidsub": {
        "form_name": "tidsub.xlsx",
        "title": "TID Subtraction (Base Value) Workbook",
        "source_url": "https://www.revenue.wi.gov/DORForms/tidsub.xlsx",
    },
    "worksheets-decrement": {
        "form_name": "decrement.xlsx",
        "title": "TID Base Redetermination (Decrement) Worksheet",
        "source_url": "https://www.revenue.wi.gov/DORForms/decrement.xlsx",
    },
    "worksheets-tidbase-ppremoval": {
        "form_name": "tidbase-ppremoval.xlsx",
        "title": "TID Base Value — Personal Property Removal Workbook",
        "source_url": "https://www.revenue.wi.gov/DORForms/tidbase-ppremoval.xlsx",
    },
}

# Sheets that hold lookup data, not form content. Skip entirely.
SKIP_SHEETS = {"comunidata"}

# Plumbing formula patterns — present for spreadsheet mechanics, not prescriptive
# methodology. We drop these so the meaningful formulas stand out.
_PLUMBING_RE = re.compile(
    r"""^=\s*(
        SUM\([A-Z]+\d+[,:][A-Z]+\d+\)      # per-row / column totals: =SUM(K12,L12)
        | \+?[A-Z]+\d+$                      # bare cell echo: =+E11  /  =K39
        | '[^']+'!\$?[A-Z]+\$?\d+            # cross-sheet ref: ='PE-606'!J4
        | VLOOKUP\(                          # code-table lookups
        | [A-Z]+\d+\s*&                      # string concatenation for headers
    )""",
    re.VERBOSE | re.IGNORECASE,
)

_INSTRUCTION_MARKERS = ("instruction", "will not print", "general information")


def _translate_formula(formula: str) -> str:
    """Render an Excel formula as a human-readable, row-relative description.

    We only call this on formulas that survived the plumbing filter, so the
    goal is legibility, not a full parser: strip the leading ``=`` and rewrite
    same-column cell refs into "the value in <cell>". The agent reads the
    original alongside this gloss, so partial translation is fine.
    """
    body = formula.lstrip("=").strip()
    # A1-style refs → "cell A1" so the model does not mistake them for prose.
    body = re.sub(r"\b([A-Z]{1,2}\d{1,4})\b", r"cell \1", body)
    return body


def _is_labelish(value: str) -> bool:
    """True when a string cell reads like a field label / instruction, not data."""
    v = value.strip()
    if len(v) < 3:
        return False
    # Pure numbers / codes are entered data, not labels.
    if re.fullmatch(r"[\d.,$%\-/ ]+", v):
        return False
    return True


def extract_workbook(data: bytes, worksheet_id: str, meta: dict) -> dict:
    """Parse one .xlsx into the structured JSON sidecar shape."""
    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=False, read_only=True)
    sheets_out = []

    for ws in wb.worksheets:
        if ws.title.strip().lower() in SKIP_SHEETS:
            logger.info("  [%s] skipped (lookup/data sheet)", ws.title)
            continue
        if ws.sheet_state != "visible":
            logger.info("  [%s] skipped (hidden)", ws.title)
            continue

        labels: list[dict] = []
        formulas: list[dict] = []
        instructions: list[str] = []
        in_instructions = False

        for row in ws.iter_rows():
            for cell in row:
                val = cell.value
                if val is None:
                    continue
                text = str(val).replace("\n", " ").strip()
                if not text:
                    continue

                if isinstance(val, str) and val.startswith("="):
                    if _PLUMBING_RE.match(val):
                        continue
                    formulas.append(
                        {
                            "cell": cell.coordinate,
                            "formula": val,
                            "description": _translate_formula(val),
                        }
                    )
                    continue

                low = text.lower()
                if any(m in low for m in _INSTRUCTION_MARKERS):
                    in_instructions = True
                if in_instructions:
                    instructions.append(text)
                elif _is_labelish(text):
                    labels.append({"cell": cell.coordinate, "label": text})

        if not (labels or formulas or instructions):
            continue

        sheets_out.append(
            {
                "sheet": ws.title,
                "labels": labels,
                "formulas": formulas,
                "instructions": instructions,
            }
        )
        logger.info(
            "  [%s] %d labels, %d formulas, %d instruction lines",
            ws.title,
            len(labels),
            len(formulas),
            len(instructions),
        )

    return {
        "worksheet_id": worksheet_id,
        "title": meta["title"],
        "source_url": meta["source_url"],
        "form_name": meta["form_name"],
        "sheets": sheets_out,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--raw-bucket", help="S3 raw bucket: download xlsx from and upload JSON to")
    ap.add_argument("--local-dir", help="Read .xlsx from this local dir instead of S3")
    ap.add_argument("--out-dir", help="Write JSON here instead of uploading to S3")
    args = ap.parse_args()

    s3 = boto3.client("s3", region_name=REGION) if (args.raw_bucket) else None

    for worksheet_id, meta in WORKSHEETS.items():
        form_name = meta["form_name"]
        logger.info("Extracting %s (%s)", worksheet_id, form_name)

        if args.local_dir:
            with open(os.path.join(args.local_dir, form_name), "rb") as fh:
                data = fh.read()
        else:
            # Raw bucket stores the scraped form under the form_instructions path.
            key = f"raw/form_instructions-{form_name[:-5]}/form_instructions-{form_name}"
            try:
                data = s3.get_object(Bucket=args.raw_bucket, Key=key)["Body"].read()
            except Exception:  # noqa: BLE001
                # Fall back to fetching the public URL if not yet in the bucket.
                import urllib.request

                logger.info("  not in bucket at %s; fetching public URL", key)
                req = urllib.request.Request(
                    meta["source_url"], headers={"User-Agent": "Mozilla/5.0"}
                )
                data = urllib.request.urlopen(req).read()  # noqa: S310

        result = extract_workbook(data, worksheet_id, meta)
        payload = json.dumps(result, indent=2, ensure_ascii=False)

        if args.out_dir:
            os.makedirs(args.out_dir, exist_ok=True)
            path = os.path.join(args.out_dir, f"{worksheet_id}.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(payload)
            logger.info("  wrote %s (%d bytes)", path, len(payload))
        if args.raw_bucket:
            out_key = f"worksheets/{worksheet_id}.json"
            s3.put_object(
                Bucket=args.raw_bucket,
                Key=out_key,
                Body=payload.encode("utf-8"),
                ContentType="application/json",
            )
            logger.info("  uploaded s3://%s/%s", args.raw_bucket, out_key)


if __name__ == "__main__":
    main()

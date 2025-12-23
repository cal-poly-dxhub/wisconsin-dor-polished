import os
import re
import json
import boto3
from datetime import datetime
import botocore
from botocore.config import Config
from typing import List, Dict, Tuple, Any
from textractor.data.text_linearization_config import TextLinearizationConfig
from pdf_chunking.aws_utils import *
from pdf_chunking.table_tools import *
from pdf2image import convert_from_path
from PIL import Image
from pdf_chunking.flowchart_tools import extract_flowcharts_from_document

config = Config(read_timeout=600, retries=dict(max_attempts=5))

s3 = boto3.client("s3")
session = boto3.session.Session()
REGION_NAME = session.region_name

MEDIA_BUCKET_NAME = "textract-chunk-result-dhgoel"

# Debug flag to control chunk logging
DEBUG = True  # Set to False to disable chunk logging
logging_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

def ensure_bucket_exists(s3_client, bucket_name: str):
    try:
        s3_client.head_bucket(Bucket=bucket_name)
        print(f"Bucket '{bucket_name}' exists")
    except botocore.exceptions.ClientError:
        print(f"Bucket '{bucket_name}' does not exist. Creating it...")
        s3_client.create_bucket(
            Bucket=bucket_name,
            CreateBucketConfiguration={
                "LocationConstraint": REGION_NAME
            }
        )

ensure_bucket_exists(s3, MEDIA_BUCKET_NAME)
       
def encode_image_to_base64(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def get_chunk_logs_dir():
    """Get the chunk logs directory path and create it if DEBUG is enabled."""
    if not DEBUG:
        return None

    chunk_logs_dir = "./pdf_chunking/chunk_logs"
    os.makedirs(chunk_logs_dir, exist_ok=True)
    return chunk_logs_dir


def strip_newline(cell: Any) -> str:
    """Remove newline characters from a cell value."""
    return str(cell).strip()


def sub_header_content_splitter(string: str) -> List[str]:
    """Split content by XML tags and return relevant segments."""
    pattern = re.compile(r"<<[^>]+>>")
    segments = re.split(pattern, string)
    result = []
    for segment in segments:
        if segment.strip():
            if (
                "<header>" not in segment
                and "<list>" not in segment
                and "<table>" not in segment
            ):
                segment = [x.strip() for x in segment.split("\n") if x.strip()]
                result.extend(segment)
            else:
                result.append(segment)
    return result


def split_list_items_(items: str) -> List[str]:
    """Split a string into a list of items, handling nested lists."""
    parts = re.split("(<<list>><list>|</list><</list>>)", items)
    output = []

    inside_list = False
    list_item = ""

    for p in parts:
        if p == "<<list>><list>":
            inside_list = True
            list_item = p
        elif p == "</list><</list>>":
            inside_list = False
            list_item += p
            output.append(list_item)
            list_item = ""
        elif inside_list:
            list_item += p.strip()
        else:
            output.extend(p.split("\n"))
    return output

import os
import re

def process_document(document, local_pdf_path: str):
    """
    Hybrid extraction:
    - Use Textractor's get_text() for structured, layout-aware text (<titles>, <headers>, etc.)
    - Simultaneously build (line_text, page_num) tuples for accurate page tracking.
    """
    filename = os.path.basename(local_pdf_path).lower()
    is_statute = "wi-admin" in filename or "wi-statute" in filename

    if is_statute:
        config = TextLinearizationConfig(
            hide_figure_layout=False,
            hide_table_layout=False,
            hide_header_layout=False,
            hide_footer_layout=False,
            hide_page_num_layout=False
        )
    else:
        config = TextLinearizationConfig(
            hide_figure_layout=False,
            hide_table_layout=False,
            title_prefix="<titles><<title>><title>",
            title_suffix="</title><</title>>",
            hide_header_layout=True,
            section_header_prefix="<headers><<header>><header>",
            section_header_suffix="</header><</header>>",
            table_prefix="<tables><table>",
            table_suffix="</table>",
            list_layout_prefix="<<list>><list>",
            list_layout_suffix="</list><</list>>",
            hide_footer_layout=True,
            hide_page_num_layout=True,
        )

    structured_text_lines = []           # all structured lines (for chunking)
    line_page_mapping = []               # flat list of (text, page_num) for exact mapping

    for page in document.pages:
        page_text = page.get_text(config=config)
        lines = [x.strip() for x in page_text.split("\n") if x.strip()]
        structured_text_lines.extend(lines)

        # record which page these lines came from
        for line in lines:
            line_page_mapping.append((line, page.page_num))

    # join structured text for chunking
    result = "\n".join(structured_text_lines)

    if is_statute:
        header_split = [result]
    else:
        header_split = result.split("<titles>")

    '''flowchart_chunks = extract_flowcharts_from_document(
        document, bedrock_runtime, os.path.basename(local_pdf_path)
    )'''

    flowchart_chunks = []

    return header_split, line_page_mapping, flowchart_chunks

def chunk_document(header_split, file, BUCKET, line_page_mapping):
    """
    Combine Textractor's structured chunking (<titles>) with exact page numbers.
    Splits by both Roman numerals (I., II., XII., etc.)
    and capital letter subsections (A., B., C., etc.).
    """
    max_words = 1200
    overlap_size = 30
    chunks = []
    doc_id = os.path.basename(file)

    roman_pattern = re.compile(r"^(?:[IVXLCDM]+)\s*[\.\-–:]")
    capital_pattern = re.compile(r"^[A-Z]\s*[\.\-–:]")
    number_pattern = re.compile(r"^\d+\s*[\.\-–:]")
    paren_number_pattern = re.compile(r"^\d+\)")

    def clean_line(line):
        return re.sub(r"<[^>]+>", "", line).strip()

    def count_words(lines):
        return sum(len(re.findall(r"\w+", l)) for l in lines)

    def get_pages_for_chunk(chunk_lines):
        pages = set()
        line_set = set([clean_line(l) for l in chunk_lines if l.strip()])
        for text, pnum in line_page_mapping:
            cleaned = clean_line(text)
            if cleaned in line_set:
                pages.add(pnum)
        return (min(pages), max(pages)) if pages else (1, 1)

    def flush_chunk(local_buffer, heading, subheading=None):
        """Flush buffer into a new chunk."""
        if not local_buffer:
            return
        prefix = f"{heading}\n{subheading}" if subheading else heading
        chunk_text = "\n".join([prefix] + local_buffer) if prefix else "\n".join(local_buffer)
        start_page, end_page = get_pages_for_chunk(local_buffer)
        chunks.append({
            "text": chunk_text.strip(),
            "metadata": {
                "doc_id": doc_id,
                "heading": heading,
                "subheading": subheading,
                "start_page": start_page,
                "end_page": end_page
            }
        })

    roman_heading = ""
    sub_heading = ""
    local_buffer = []

    for items in header_split:
        lines = sub_header_content_splitter(items)
        for raw_line in lines:
            line = clean_line(raw_line)
            if not line:
                continue

            # Detect new Roman numeral section
            if roman_pattern.match(line):
                flush_chunk(local_buffer, roman_heading, sub_heading)
                roman_heading = line
                sub_heading = ""
                local_buffer = []
                continue

            # Detect new capital-letter subsection (A., B., C., etc.)
            if capital_pattern.match(line) and len(line.split()) > 1:
                # flush if buffer already has content (avoid duplicates)
                if local_buffer:
                    flush_chunk(local_buffer, roman_heading, sub_heading)
                    local_buffer = []
                sub_heading = line
                continue

            # Add content
            local_buffer.append(line)

            # Flush when chunk too long
            if count_words(local_buffer) > max_words:
                flush_chunk(local_buffer, roman_heading, sub_heading)
                local_buffer = []

    # Final flush
    flush_chunk(local_buffer, roman_heading, sub_heading)
    return chunks

def chunk_document_statute(header_split, file, BUCKET, line_page_mapping):
    """
    Chunk WI Statute / Administrative Code PDFs.
    Each 'Tax XX.XX' rule becomes its own chunk, using the same page mapping logic
    that gives good page numbers for manuals/publications.
    """
    doc_id = os.path.basename(file)
    chunks = []

    # Pattern for statute sections like "Tax 16.01" or "Tax 18.05"
    if "statute" in doc_id.lower():
        rule_pattern = re.compile(r"(\d+\.\d+[A-Za-z\-]*)")
    else:
        rule_pattern = re.compile(r"(Tax\s\d+\.\d+[^ \n]*)")

    heading, local_buffer = None, []

    def flush_chunk(heading, buffer):
        """Flush one statute rule into a chunk with correct page metadata."""
        if heading and buffer:
            pages = {p for _, p in buffer}
            start_page, end_page = min(pages), max(pages)
            chunk_text = f"{heading}\n" + "\n".join(txt for txt, _ in buffer).strip()
            chunks.append({
                "text": chunk_text,
                "metadata": {
                    "doc_id": doc_id,
                    "heading": heading,
                    "start_page": start_page,
                    "end_page": end_page
                }
            })

    # Walk through line–page mapping directly
    for line, page_num in line_page_mapping:
        clean = line.strip()
        if not clean:
            continue

        if rule_pattern.match(clean):
            # Found a new rule — flush the previous one
            flush_chunk(heading, local_buffer)
            heading, local_buffer = clean, []
        else:
            local_buffer.append((clean, page_num))

    # Flush final rule
    flush_chunk(heading, local_buffer)

    # Merge multi-page duplicates
    merged_chunks, last_heading, last_lines, last_start, last_end = [], None, [], None, None
    for ch in chunks:
        h = ch["metadata"]["heading"]
        sp, ep = ch["metadata"]["start_page"], ch["metadata"]["end_page"]

        if h == last_heading:
            last_lines.append(ch["text"].split("\n", 1)[1])
            last_end = ep
        else:
            if last_heading:
                merged_chunks.append({
                    "text": f"{last_heading}\n{'\n'.join(last_lines).strip()}",
                    "metadata": {
                        "doc_id": doc_id,
                        "heading": last_heading,
                        "start_page": last_start,
                        "end_page": last_end
                    }
                })
            last_heading, last_start, last_end = h, sp, ep
            last_lines = [ch["text"].split("\n", 1)[1]]

    if last_heading:
        merged_chunks.append({
            "text": f"{last_heading}\n{'\n'.join(last_lines).strip()}",
            "metadata": {
                "doc_id": doc_id,
                "heading": last_heading,
                "start_page": last_start,
                "end_page": last_end
            }
        })

    return merged_chunks

def chunk_document_wpam(header_split, file, BUCKET, line_page_mapping):
    """
    Chunk Wisconsin Property Assessment Manual (WPAM) PDFs.

    - Detects and skips Table of Contents or mini-TOC fragments.
    - Groups by Chapter headings and Section titles.
    - Automatically merges small related sections.
    - Returns final, ready-to-use chunks (like other chunkers).
    """
    doc_id = os.path.basename(file)
    chunks = []

    # --- Patterns ---
    chapter_pattern = re.compile(r"^Chapter\s+\d+", re.IGNORECASE)
    section_header_pattern = re.compile(r"^[A-Z][A-Za-z\s]{3,}$")
    max_words = 1200
    min_merge_words = 80     # merge chunks smaller than this
    max_merge_total = 500    # only merge if result < this many words

    # --- Helpers ---

    def clean_line(line: str) -> str:
        if not line:
            return ""
        return re.sub(r"<[^>]+>", "", str(line)).strip()

    def count_words(lines_or_text):
        if isinstance(lines_or_text, str):
            return len(re.findall(r"\w+", lines_or_text))
        return sum(len(re.findall(r"\w+", l)) for l in lines_or_text)

    def get_pages_for_chunk(chunk_lines):
        pages = set()
        line_set = set([clean_line(l) for l in chunk_lines if l.strip()])
        for text, pnum in line_page_mapping:
            cleaned = clean_line(text)
            if cleaned in line_set:
                pages.add(pnum)
        return (min(pages), max(pages)) if pages else (1, 1)

    def is_probably_toc(text: str) -> bool:
        """Detect full or mini TOCs."""
        lowered = text.lower()
        if any(k in lowered for k in ["table of contents", "appendix", "glossary", "revisions"]):
            return True
        lines = text.splitlines()
        if len(lines) < 2:
            return False
        page_refs = sum(1 for l in lines if re.search(r"\b\d+-\d+\b", l))
        if page_refs / max(1, len(lines)) > 0.3:
            return True
        if re.match(r"^Chapter\s+\d+", text) and re.search(r"\b\d+-\d+\b", text):
            return True
        return False

    # --- Chunk Collector ---
    def flush_chunk(buffer, chapter=None, section=None):
        if not buffer:
            return

        buffer = [clean_line(b) for b in buffer if isinstance(b, str) and b]
        chapter = chapter or ""
        section = section or ""
        heading = f"{chapter}\n{section}".strip()
        text = "\n".join(([heading] + buffer) if heading else buffer).strip()
        if not text or is_probably_toc(text):
            return

        sp, ep = get_pages_for_chunk(buffer)
        chunks.append({
            "text": text,
            "metadata": {
                "doc_id": doc_id,
                "heading": chapter or "Untitled",
                "subheading": section or None,
                "start_page": sp,
                "end_page": ep
            }
        })

    # --- Main Chunk Loop ---
    current_chapter, current_section, buffer = None, None, []

    for part in header_split:
        lines = sub_header_content_splitter(part)

        for raw_line in lines:
            line = clean_line(raw_line)
            if not line:
                continue

            if chapter_pattern.match(line):
                flush_chunk(buffer, current_chapter, current_section)
                current_chapter, current_section, buffer = line, None, []
                continue

            if section_header_pattern.match(line) and len(line.split()) < 8:
                flush_chunk(buffer, current_chapter, current_section)
                current_section, buffer = line, []
                continue

            buffer.append(line)

            if count_words(buffer) > max_words:
                flush_chunk(buffer, current_chapter, current_section)
                buffer = []

    # Final flush
    flush_chunk(buffer, current_chapter, current_section)

    # --- Merge Small Chunks (internal like statute merging) ---
    merged_chunks = []
    i = 0
    while i < len(chunks):
        chunk = chunks[i]
        text = chunk["text"]
        word_count = count_words(text)

        # try merging with next if both share same chapter
        if word_count < min_merge_words and i + 1 < len(chunks):
            next_chunk = chunks[i + 1]
            same_heading = (
                chunk["metadata"]["heading"] == next_chunk["metadata"]["heading"]
            )
            combined = text.strip() + "\n\n" + next_chunk["text"].strip()
            if same_heading and count_words(combined) <= max_merge_total:
                merged_chunks.append({
                    "text": combined,
                    "metadata": {
                        **chunk["metadata"],
                        "end_page": next_chunk["metadata"].get("end_page", chunk["metadata"]["end_page"])
                    }
                })
                i += 2
                continue

        merged_chunks.append(chunk)
        i += 1

    return merged_chunks

def parse_s3_uri(s3_uri):
    # Ensure the URI starts with "s3://"
    if not s3_uri.startswith("s3://"):
        raise ValueError("Invalid S3 URI")

    # Remove the "s3://" prefix
    s3_path = s3_uri[5:]

    # Split the path into bucket and key
    bucket_name, *key_parts = s3_path.split("/", 1)
    file_key = key_parts[0] if key_parts else ""

    return bucket_name, file_key

def extract_clean_plaintext(doc_chunks, doc_id=None):
    all_cleaned_content = []
    removed_chunks = []

    heading_pattern = re.compile(r"^(?:[IVXLCDM]+\.)|^[A-Z]\.|^Tax\s\d+\.\d+")

    def clean_line(line):
        try:
            if line is None:
                return ""
            return re.sub(r"<[^>]+>", "", str(line)).strip()
        except Exception as e:
            print("⚠️ clean_line failed on line:", repr(line))
            return ""

    
    def looks_like_index(text: str) -> bool:
        """Detect statute chapter headers/index pages that should be removed."""
        short = len(text.split()) < 15
        return short

    for idx, chunk in enumerate(doc_chunks):
        if not isinstance(chunk, dict) or "text" not in chunk:
            continue
        chunk_text = chunk["text"]

        # normalize lines
        lines = [clean_line(l) for l in chunk_text.split("\n") if clean_line(l)]
        lines = [l for l in lines if isinstance(l, str)]
        if not lines:
            removed_chunks.append({"text": chunk_text, "reason": "Empty"})
            continue
        text = "\n\n".join(lines)

        # stats
        word_count = sum(len(l.split()) for l in lines)
        sentence_count = sum(1 for l in lines if l.endswith((".", "?", "!")))

        if looks_like_index(text):
            removed_chunks.append({"text": text, "reason": "index/title"})
            continue

        # === Always keep if heading-style ===
        if heading_pattern.match(lines[0]):
            all_cleaned_content.append((idx, text))
            continue

        # === Adaptive thresholds ===
        if word_count < 50 and sentence_count == 1:
            removed_chunks.append({"text": text, "reason": f"Too short ({word_count} words, {sentence_count} sentences)"})
            continue

        all_cleaned_content.append((idx, text))
    return all_cleaned_content, removed_chunks

def extract_raw_text_from_document(document) -> str:
    """
    Extract raw text from a Textract document without any filtering or chunking.
    This is used as a fallback when the filtered approach returns no chunks.

    Args:
        document: Textract document object

    Returns:
        str: Raw text content from the document
    """
    # Simple configuration for raw text extraction
    simple_config = TextLinearizationConfig(
        hide_figure_layout=True,
        hide_table_layout=False,  # Keep tables as text
        hide_header_layout=True,
        hide_footer_layout=True,
        hide_page_num_layout=True,
    )

    all_text = []
    for page in document.pages:
        page_text = page.get_text(config=simple_config)
        if page_text.strip():
            all_text.append(page_text.strip())

    # Join all pages with double newlines
    raw_text = "\n\n".join(all_text)

    # Basic cleanup - remove excessive whitespace and XML tags
    raw_text = re.sub(r"<[^>]+>", "", raw_text)  # Remove XML tags
    raw_text = re.sub(r"\n\s*\n\s*\n+", "\n\n", raw_text)  # Collapse multiple newlines
    raw_text = re.sub(r"[ \t]+", " ", raw_text)  # Normalize spaces

    return raw_text.strip()


def extract_raw_text_from_pdf_s3(bucket_name: str, s3_file_path: str) -> str:
    """
    Extract raw text from a PDF in S3 as a fallback when filtered chunking fails.

    Args:
        bucket_name (str): The S3 bucket name.
        s3_file_path (str): The object key for the PDF file.

    Returns:
        str: Raw text content from the PDF
    """
    s3_uri = f"s3://{bucket_name}/{s3_file_path}"
    print(f"Extracting raw text from {os.path.basename(s3_file_path)}")

    if not MEDIA_BUCKET_NAME:
        raise ValueError("MEDIA_BUCKET_NAME environment variable is not set.")

    textract_output_path = None
    try:
        document, local_pdf_path, textract_output_path = extract_textract_data(
            s3, s3_uri, bucket_name, MEDIA_BUCKET_NAME
        )
        raw_text = extract_raw_text_from_document(document)

        print(f"Extracted raw text from {os.path.basename(s3_file_path)} successfully.")
        return raw_text
    finally:
        # Clean up Textract output from the media bucket
        if textract_output_path:
            media_bucket, prefix = parse_s3_uri(textract_output_path)
            print("fallback-->deleting Textract output from s3")
            delete_s3_prefix(s3, media_bucket, prefix)


def process_pdf_from_s3(
    bucket_name: str, s3_file_path: str, document_url: str = "n/a", source_id: str = "n/a"
) -> list:
    """
    Processes a PDF from S3 and returns a list of cleaned text + flowchart chunks.

    Args:
        bucket_name (str): The S3 bucket name.
        s3_file_path (str): The object key for the PDF file.
        document_url (str, optional): The source URL of the document. Defaults to "n/a".

    Returns:
        list: A list of cleaned text + flowchart chunks (dicts).
    """
    s3_uri = f"s3://{bucket_name}/{s3_file_path}"
    doc_id = os.path.basename(s3_file_path)
    is_statute = "wi" in doc_id.lower()
    print(f"Processing {doc_id}")

    if not MEDIA_BUCKET_NAME:
        raise ValueError("MEDIA_BUCKET_NAME environment variable is not set.")

    
    textract_output_path = None
    try:
        # --- Extract with Textract ---
        document, local_pdf_path, textract_output_path = extract_textract_data(
            s3, s3_uri, bucket_name, MEDIA_BUCKET_NAME
        )

        # --- Convert to header-split + page mapping (and flowcharts) ---
        header_split, line_page_mapping, flowchart_chunks = process_document(document, local_pdf_path)

        # --- Run chunking ---
        if is_statute:
            raw_chunks = chunk_document_statute(header_split, s3_file_path, bucket_name, line_page_mapping)
        elif "wpam" in doc_id.lower():
            raw_chunks = chunk_document_wpam(header_split, s3_file_path, bucket_name, line_page_mapping)
        else:
            raw_chunks = chunk_document(header_split, s3_file_path, bucket_name, line_page_mapping)

        chunk_logs_dir = get_chunk_logs_dir()
        if chunk_logs_dir:
            # Save raw chunks
            raw_chunks_dir = os.path.join(chunk_logs_dir, "raw_chunks")
            os.makedirs(raw_chunks_dir, exist_ok=True)
            raw_chunks_path = os.path.join(
                raw_chunks_dir, f"{doc_id}_{logging_timestamp}.jsonl"
            )

            with open(raw_chunks_path, "w") as f:
                for idx, chunk in enumerate(raw_chunks):
                    record = {
                        "text": chunk["text"],
                        "metadata": {"doc_id": doc_id, "chunk_index": idx},
                    }
                    f.write(json.dumps(record, indent=2) + "\n")
            print(f"✅ Saved raw chunks to {raw_chunks_path}")

        # --- Clean text chunks ---
        cleaned_text_chunks, removed_chunks = extract_clean_plaintext(raw_chunks, doc_id=doc_id)

                # --- Save removed chunks (for debugging/QA) ---
        if chunk_logs_dir:
            removed_chunks_dir = os.path.join(chunk_logs_dir, "removed")
            os.makedirs(removed_chunks_dir, exist_ok=True)
            removed_chunks_path = os.path.join(
                removed_chunks_dir, f"{doc_id}_{logging_timestamp}.jsonl"
            )
            with open(removed_chunks_path, "w") as f:
                for chunk in removed_chunks:
                    f.write(json.dumps(chunk, indent=2) + "\n")
            print(f"✅ Saved {len(removed_chunks)} removed chunks to {removed_chunks_path}")


        # --- Merge cleaned chunks + flowcharts ---
        all_chunks = []
        total_chunks = len(cleaned_text_chunks) + len(flowchart_chunks)

        # Add text chunks
        for out_idx, (raw_idx,chunk) in enumerate(cleaned_text_chunks):
            start_page = raw_chunks[raw_idx]["metadata"].get("start_page", 1)
                
            all_chunks.append({
                "chunk_id": f"{doc_id}_final_{out_idx}",
                "text": chunk,
                "metadata": {
                    "doc_id": doc_id,
                    "source": s3_file_path,
                    "source_url": f"{document_url}#page={start_page}" if start_page else document_url,
                    "chunk_index": out_idx,
                    "total_chunks": total_chunks,
                    "source_id": source_id
                },
            })

        # Add flowchart chunks
        for idx, fc in enumerate(flowchart_chunks, start=len(cleaned_text_chunks)):
            all_chunks.append({
                "chunk_id": f"{doc_id}_flowchart_{idx}",
                "text": fc["text"],
                "metadata": fc["metadata"] | {
                    "source": s3_file_path,
                    "source_url": document_url,
                    "chunk_index": idx,
                    "total_chunks": total_chunks,
                    "source_id": source_id
                },
            })

        if chunk_logs_dir:
            # Save cleaned + flowcharts together
            final_chunks_dir = os.path.join(chunk_logs_dir, "final_chunks")
            os.makedirs(final_chunks_dir, exist_ok=True)
            final_chunks_path = os.path.join(
                final_chunks_dir, f"{doc_id}_{logging_timestamp}.jsonl"
            )
            with open(final_chunks_path, "w") as f:
                for chunk in all_chunks:
                    f.write(json.dumps(chunk, indent=2) + "\n")
            print(f"✅ Saved {len(all_chunks)} final chunks (including flowcharts) to {final_chunks_path}")

        return all_chunks

    finally:
        # Cleanup temporary Textract output
        if textract_output_path:
            media_bucket, prefix = parse_s3_uri(textract_output_path)
            delete_s3_prefix(s3, media_bucket, prefix)
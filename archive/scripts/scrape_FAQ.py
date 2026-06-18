import argparse
import json
import os
import re
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

DEFAULT_OUTPUT_PATH = "documents/faqs.json"

def is_probably_html_page(url: str) -> bool:
    """Skip obvious non-HTML docs (pdf, doc, etc)."""
    path = urlparse(url).path.lower()
    return not any(path.endswith(ext) for ext in [".pdf", ".doc", ".docx", ".xls", ".xlsx"])


def fetch_soup(url: str, timeout: int = 30) -> BeautifulSoup | None:
    try:
        resp = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except requests.RequestException as e:
        print(f"[ERROR] Fetch failed: {url} :: {e}")
        return None


def extract_nested_links(hub_url: str) -> list[str]:
    """
    Extract nested FAQ page links from the hub page.
    - Ignores links under 'Common Questions Category Headings'
    - Captures links inside <ul> lists and <h3><a href=...> headers
    - Keeps only /Pages/FAQS/ links and skips PDFs
    """
    print(f"[INFO] Fetching hub page: {hub_url}")

    resp = requests.get(hub_url, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    content = soup.find("div", id="ctl00_PlaceHolderMain_ctl01__ControlWrapper_RichHtmlField")
    if not content:
        raise RuntimeError("Could not find main FAQ content container")

    links: list[str] = []
    seen: set[str] = set()
    skip_section = False

    for elem in content.children:
        if not hasattr(elem, "name"):
            continue

        # --- Section headers (including <h3><a href=...>) ---
        if elem.name == "h3":
            header_text = elem.get_text(" ", strip=True).lower()

            # Start skipping
            if "common questions category headings" in header_text:
                skip_section = True
                continue

            # End skipping once we hit the next header
            if skip_section:
                skip_section = False

            # Capture <h3><a href="..."> links (e.g., Annual Assessment Report)
            a = elem.find("a", href=True)
            if a and not skip_section:
                full = urljoin(hub_url, a["href"].strip())
                if "/Pages/FAQS/" in full and is_probably_html_page(full):
                    if full not in seen:
                        seen.add(full)
                        links.append(full)

            continue

        if skip_section:
            continue

        # --- Links under lists ---
        if elem.name == "ul":
            for a in elem.find_all("a", href=True):
                full = urljoin(hub_url, a["href"].strip())
                if "/Pages/FAQS/" not in full:
                    continue
                if not is_probably_html_page(full):
                    continue
                if full not in seen:
                    seen.add(full)
                    links.append(full)

    print(f"[INFO] Found {len(links)} FAQ page links")
    return links


def extract_qa_pairs_from_faq_page(page_url: str) -> list[dict]:
    """
    Extract Q/A pairs from an FAQ page.
    Supports:
      - <ol class="listLinks"> / <ul class="listLinks">
      - Fallback: plain <ol> that contains <li><strong>... (Q) ...</strong> ...</li>

    Skips pages that appear to be "questions-only" (no <p> answers and no nested lists).
    """
    soup = fetch_soup(page_url)
    if not soup:
        return []

    list_container = soup.find("ol", class_="listLinks") or soup.find("ul", class_="listLinks")

    if not list_container:
        # Fallback: pick the <ol> that contains <li><strong> (the actual Q/A section)
        for candidate in soup.find_all("ol"):
            if candidate.find("li") and candidate.find("strong"):
                list_container = candidate
                break

    if not list_container:
        print(f"  WARN: No FAQ list container found (listLinks or strong-in-ol): {page_url}")
        return []

    li_elements = list_container.find_all("li", recursive=False)

    # Skip pages with no obvious answer blocks
    has_paragraph_answers = list_container.find("p") is not None
    has_nested_lists = any(li.find(["ul", "ol"]) for li in li_elements)
    if not has_paragraph_answers and not has_nested_lists:
        print(f"  SKIP (questions-only page, no answers): {page_url}")
        return []

    qa_pairs: list[dict] = []

    for li in li_elements:
        strong = li.find("strong")
        if not strong:
            continue

        question = strong.get_text(" ", strip=True)
        strong.extract()  # remove question from the DOM before extracting answer text

        answer_parts: list[str] = []

        # Paragraph answers
        for p in li.find_all("p"):
            txt = p.get_text(" ", strip=True)
            if txt:
                answer_parts.append(txt)

        # List answers
        for ul in li.find_all(["ul", "ol"]):
            for item in ul.find_all("li"):
                t = item.get_text(" ", strip=True)
                if t:
                    answer_parts.append(f"- {t}")

        # Fallback: remaining text directly under li
        raw = li.get_text("\n", strip=True)
        raw = re.sub(r"\n{2,}", "\n", raw).strip()
        if raw:
            joined = "\n".join(answer_parts)
            if raw not in joined:
                answer_parts.append(raw)

        # De-dupe while preserving order
        answer = "\n".join(dict.fromkeys([a for a in answer_parts if a])).strip()

        if question and answer:
            qa_pairs.append({"Q": question, "A": answer, "source_url": page_url})

    return qa_pairs


def main():
    parser = argparse.ArgumentParser(
        description="Scrape a FAQ hub page: discover nested FAQ links, extract all Q/A, output one combined JSON."
    )
    parser.add_argument("--url", required=True, help="FAQ hub URL (the page that contains links to FAQ pages)")
    parser.add_argument(
        "--out",
        default=DEFAULT_OUTPUT_PATH,
        help=f"Output JSON filename (default: {DEFAULT_OUTPUT_PATH})",
    )

    args = parser.parse_args()
    hub_url = args.url
    out_path = args.out

    print(f"[INFO] URL: {hub_url}")
    nested_links = extract_nested_links(hub_url)

    print(f"[INFO] Found {len(nested_links)} nested link(s) to crawl.")
    all_qas: list[dict] = []
    total_pages_with_qas = 0

    for idx, link in enumerate(nested_links, start=1):
        print(f"[INFO] ({idx}/{len(nested_links)}) Crawling: {link}")
        qas = extract_qa_pairs_from_faq_page(link)
        if qas:
            total_pages_with_qas += 1
            print(f"       Extracted {len(qas)} Q/A")
            all_qas.extend(qas)
        else:
            print("       No Q/A found (skipping)")

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(all_qas, f, indent=2, ensure_ascii=False)

    print(f"\n[DONE] Pages with Q/A: {total_pages_with_qas}")
    print(f"[DONE] Total Q/A extracted: {len(all_qas)}")
    print(f"[DONE] Wrote combined JSON -> {out_path}")


if __name__ == "__main__":
    main()
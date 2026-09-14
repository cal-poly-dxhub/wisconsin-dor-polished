"""Document expansion ("aliases") for chunk embeddings.

Before embedding, each chunk's text can be prefixed with a short block of:
  (a) real gold queries attached to the chunk (from the recall eval set),
  (b) 2-3 generated plain-language questions the chunk answers,
  (c) generated everyday synonyms for the chunk's terms of art.

The STORED chunk text never changes — only the string sent to Titan does.
Aliases are generated once per chunk *content* (see ``alias_cache_key``) with
Nova 2 Lite and cached in S3 under ``{cache_prefix}aliases/{doc_id}.json``.

Everything here is pure (no S3); the callers in extract.py / embed.py own the
caching and flag gating.
"""

from __future__ import annotations

import ast
import hashlib
import json
import logging
import os
import random
import re
import time

logger = logging.getLogger(__name__)

DEFAULT_ALIAS_MODEL = "us.amazon.nova-2-lite-v1:0"
# Bump whenever _PROMPT changes: cached entries with an older version are regenerated.
PROMPT_VERSION = "v2"

MAX_QUESTIONS = 3
MAX_ALIASES = 6
MAX_QUESTION_CHARS = 160
MIN_CHUNK_CHARS = 200
TITAN_MAX_CHARS = 8000
# Passage length sent to the alias model. Chunks are ≤7500 chars already.
MAX_PASSAGE_CHARS = 6000

# Generic terms whose "everyday words" add no retrieval signal (they appear in
# nearly every chunk). Compared after lowercasing + whitespace normalization.
STOPLIST: list[str] = [
    "assessment",
    "assessments",
    "exemption",
    "exemptions",
    "exempt",
    "property",
    "tax",
    "taxes",
    "corporation",
    "parcel",
    "real property",
    "personal property",
]

# Real tester phrasings (from tools/ingestion/tests/graph_regression_queries.yaml)
# used purely as STYLE examples — the model must not answer them.
STYLE_EXAMPLES: list[str] = [
    "What exemptions can apply to a mobile home?",
    "can the town clerk be the chairman of board of review",
    "how do you determine the tax increment for a tid",
    "If a property is exempted in error can the assessor assess it as omitted the following year",
    "Can a municipality use a prior year assessment roll for the current year?",
    "Does my 501c3 property qualify for exemption?",
    "How are my property taxes determined?",
    "Does my land qualify as agricultural?",
    "what is the process to appeal an assessed value?",
    "We need to hire an assessor, how do we start the process?",
    "how do you measure depreciation",
    "Is native American property taxable or exempt?",
]

_PROMPT = """You write retrieval aliases for a Wisconsin property-tax assistant. Given ONE passage \
from an official source, produce:

1. "questions": {max_q} short questions that THIS passage answers, phrased the way an ordinary \
property owner, municipal clerk, or assessor would actually type them into a chatbot. Use \
everyday words (camper, RV, trailer, mobile home, my house, my land, the town, the county) \
instead of legal terms unless the legal term is the only name. Each question under \
{max_q_chars} characters.
2. "aliases": up to {max_a} entries of the form "legal term = everyday words", giving \
plain-language synonyms or related words for terms of art that appear VERBATIM in the passage. \
The left-hand side must be copied exactly from the passage. Only include terms whose meaning \
the passage itself supports. Skip generic words (assessment, exemption, property, tax, parcel).

Style examples of how real users phrase questions (do NOT answer or reuse these; match their \
tone and vocabulary):
{examples}

Rules:
- Never invent facts, dates, numbers, dollar amounts, or statute/rule numbers that are not in \
the passage.
- Stay within Wisconsin property tax.
- Keep the whole output under 100 words.
- Respond with a single JSON object and nothing else: {{"questions": [...], "aliases": [...]}}
- Every array item must be a double-quoted JSON string. Do not put quotation marks inside a \
string; rephrase instead.

SOURCE: {title}
HEADING: {heading}
PASSAGE:
{text}"""


def build_prompt(
    chunk_text: str, doc_title: str, heading: str | None, examples: list[str] | None = None
) -> str:
    examples = examples if examples is not None else STYLE_EXAMPLES
    example_block = "\n".join(f"- {q}" for q in examples[:12])
    return _PROMPT.format(
        max_q=MAX_QUESTIONS,
        max_q_chars=MAX_QUESTION_CHARS,
        max_a=MAX_ALIASES,
        examples=example_block,
        title=(doc_title or "").strip()[:300],
        heading=(heading or "").strip()[:300],
        text=chunk_text[:MAX_PASSAGE_CHARS],
    )


# --------------------------------------------------------------------------
# JSON parsing (lenient)
# --------------------------------------------------------------------------

_SMART_QUOTES = {"“": '"', "”": '"', "‘": "'", "’": "'"}


def _strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _extract_array_items(blob: str, key: str) -> list[str]:
    """Regex fallback: pull string items out of ``"key": [ ... ]`` even when the
    items contain unescaped inner quotes (the prototype's failure mode)."""
    m = re.search(rf'"{key}"\s*:\s*\[(.*?)\]\s*(?:,\s*"|\}}|$)', blob, re.DOTALL)
    if not m:
        return []
    body = m.group(1).strip()
    if not body:
        return []
    # Split on `",` / `',` boundaries followed by an opening quote of the same kind
    # (Nova sometimes single-quotes array items).
    parts = re.split(r"""(?:"\s*,\s*"|'\s*,\s*')""", body)
    items = []
    for p in parts:
        p = p.strip().strip(",").strip()
        if p[:1] in ('"', "'"):
            p = p[1:]
        if p[-1:] in ('"', "'"):
            p = p[:-1]
        p = p.replace('\\"', '"').strip()
        if p:
            items.append(p)
    return items


def parse_alias_json(raw: str) -> dict:
    """Parse model output into {"questions": [...], "aliases": [...]}.

    Tries strict JSON, then light repair (fences, smart quotes, trailing
    commas), then a regex fallback per array. Never raises; returns empty
    lists when nothing usable is found.
    """
    empty = {"questions": [], "aliases": []}
    if not raw or not isinstance(raw, str):
        return empty
    text = _strip_fences(raw)
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        text = text[start : end + 1]

    candidates = [text]
    repaired = "".join(_SMART_QUOTES.get(ch, ch) for ch in text)
    repaired = re.sub(r",\s*([\]}])", r"\1", repaired)
    candidates.append(repaired)

    def _loads(s: str):
        try:
            return json.loads(s)
        except (json.JSONDecodeError, TypeError):
            pass
        # Python-literal parse tolerates single-quoted items mixed with JSON keys.
        try:
            return ast.literal_eval(s)
        except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
            return None

    for cand in candidates:
        data = _loads(cand)
        if isinstance(data, dict):
            return {
                "questions": _as_str_list(data.get("questions")),
                "aliases": _as_str_list(data.get("aliases")),
            }

    questions = _extract_array_items(repaired, "questions")
    aliases = _extract_array_items(repaired, "aliases")
    if questions or aliases:
        logger.debug("alias JSON repaired via regex fallback")
    return {"questions": questions, "aliases": aliases}


def _as_str_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        if isinstance(item, str):
            s = item.strip()
            if s:
                out.append(s)
        elif isinstance(item, dict):
            # Tolerate {"term": ..., "everyday": ...} shapes.
            vals = [str(v).strip() for v in item.values() if v]
            if len(vals) >= 2:
                out.append(f"{vals[0]} = {', '.join(vals[1:])}")
            elif vals:
                out.append(vals[0])
    return out


# --------------------------------------------------------------------------
# Bedrock call
# --------------------------------------------------------------------------

_THROTTLE_CODES = {
    "ThrottlingException",
    "TooManyRequestsException",
    "ServiceUnavailableException",
    "ModelNotReadyException",
    "ServiceQuotaExceededException",
    "InternalServerException",
}

_bedrock_client = None


def _get_bedrock():
    global _bedrock_client
    if _bedrock_client is None:
        import boto3

        _bedrock_client = boto3.client(
            "bedrock-runtime", region_name=os.environ.get("AWS_REGION", "us-east-1")
        )
    return _bedrock_client


def _error_code(exc: Exception) -> str:
    resp = getattr(exc, "response", None)
    if isinstance(resp, dict):
        return str(resp.get("Error", {}).get("Code", ""))
    return type(exc).__name__


def generate_aliases(
    chunk_text: str,
    doc_title: str,
    heading: str | None,
    model_id: str = DEFAULT_ALIAS_MODEL,
    examples: list[str] | None = None,
    *,
    bedrock_client=None,
    max_retries: int = 6,
) -> dict:
    """Generate {"questions", "aliases", "usage"} for one chunk via Bedrock converse.

    Never raises: on non-retryable failure (or exhausted retries) returns empty
    lists with ``"error"`` set so callers can count failures. Throttling is
    retried with exponential backoff + jitter.
    """
    client = bedrock_client or _get_bedrock()
    prompt = build_prompt(chunk_text, doc_title, heading, examples)
    empty = {"questions": [], "aliases": [], "usage": {}}

    for attempt in range(max_retries):
        try:
            resp = client.converse(
                modelId=model_id,
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                inferenceConfig={"temperature": 0.0, "maxTokens": 400},
            )
        except Exception as exc:  # noqa: BLE001 — never raise out of alias gen
            code = _error_code(exc)
            if code in _THROTTLE_CODES and attempt < max_retries - 1:
                wait = min(30, 2**attempt) + random.uniform(0, 1)
                logger.warning(
                    f"alias gen throttled ({code}), "
                    f"retry {attempt + 1}/{max_retries} in {wait:.1f}s"
                )
                time.sleep(wait)
                continue
            logger.warning(f"alias gen failed ({code}): {exc}")
            return {**empty, "error": code or "unknown"}

        blocks = resp.get("output", {}).get("message", {}).get("content", [])
        raw = " ".join(b.get("text", "") for b in blocks if b.get("text"))
        parsed = parse_alias_json(raw)
        usage = resp.get("usage", {}) or {}
        return {
            "questions": parsed["questions"],
            "aliases": parsed["aliases"],
            "usage": {
                "input_tokens": usage.get("inputTokens"),
                "output_tokens": usage.get("outputTokens"),
            },
        }
    return {**empty, "error": "retries_exhausted"}


# --------------------------------------------------------------------------
# Mechanical guard
# --------------------------------------------------------------------------

_WS = re.compile(r"\s+")


def _norm(s: str) -> str:
    return _WS.sub(" ", (s or "").lower()).strip()


def _norm_term(s: str) -> str:
    # Strip surrounding quotes/punctuation and a leading article for stoplist checks.
    t = _norm(s).strip("\"'“”‘’.,;:()[]")
    for art in ("the ", "a ", "an "):
        if t.startswith(art):
            t = t[len(art) :]
    return t.strip()


def filter_aliases(result: dict, chunk_text: str) -> dict:
    """Drop ungrounded / generic / over-cap items.

    - alias must be ``"legal term = everyday words"``; the left-hand term must
      appear in the chunk text (case-insensitive, whitespace-normalized) and
      must not be in STOPLIST.
    - questions over MAX_QUESTION_CHARS are dropped.
    - caps: MAX_QUESTIONS / MAX_ALIASES; duplicates removed (order preserved).
    """
    text_norm = _norm(chunk_text)
    stop = {_norm_term(s) for s in STOPLIST}

    questions: list[str] = []
    seen_q: set[str] = set()
    for q in _as_str_list(result.get("questions")):
        q = _WS.sub(" ", q).strip()
        if not q or len(q) > MAX_QUESTION_CHARS:
            continue
        key = q.lower()
        if key in seen_q:
            continue
        seen_q.add(key)
        questions.append(q)
        if len(questions) >= MAX_QUESTIONS:
            break

    aliases: list[str] = []
    seen_a: set[str] = set()
    for a in _as_str_list(result.get("aliases")):
        if "=" not in a:
            continue
        lhs, rhs = a.split("=", 1)
        lhs_n = _norm_term(lhs)
        rhs_c = _WS.sub(" ", rhs).strip().strip("\"'")
        if not lhs_n or not rhs_c:
            continue
        if lhs_n in stop:
            continue
        if lhs_n not in text_norm:
            continue
        if lhs_n in seen_a:
            continue
        seen_a.add(lhs_n)
        aliases.append(f"{lhs.strip().strip(chr(34))} = {rhs_c}")
        if len(aliases) >= MAX_ALIASES:
            break

    return {"questions": questions, "aliases": aliases}


# --------------------------------------------------------------------------
# Embed-input composition
# --------------------------------------------------------------------------


def alias_cache_key(chunk_text: str) -> str:
    """Stable key per chunk CONTENT (so re-chunking reuses aliases for unchanged text)."""
    return hashlib.sha256((chunk_text or "").encode("utf-8")).hexdigest()[:16]


def _dedup_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        if not isinstance(it, str):
            continue
        s = _WS.sub(" ", it).strip()
        if not s:
            continue
        k = s.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(s)
    return out


def compose_embed_input(
    doc: dict,
    chunk: dict,
    aliases: dict | None,
    gold_queries: list[str] | None,
    max_chars: int = TITAN_MAX_CHARS,
) -> str:
    """Build the string sent to Titan for one chunk.

    Layout (deterministic):
        {doc_title} > {heading}[ > {subheading}]
        Q: <gold query 1>          (real queries first, in given order)
        Q: <generated question 1>
        ...
        <alias 1>; <alias 2>; ...
        <blank line>
        <chunk text — truncated at the tail so the total ≤ max_chars>
    The prefix is never truncated.
    """
    meta = chunk.get("metadata", {}) or {}
    title = _WS.sub(" ", str(doc.get("title") or doc.get("doc_id") or "")).strip()[:300]
    heading = _WS.sub(" ", str(meta.get("heading") or "")).strip()[:200]
    subheading = _WS.sub(" ", str(meta.get("subheading") or "")).strip()[:200]

    header = title
    if heading:
        header += f" > {heading}"
    if subheading:
        header += f" > {subheading}"

    lines: list[str] = []
    if header:
        lines.append(header)

    aliases = aliases or {}
    gold = _dedup_keep_order(list(gold_queries or []))
    gen_q = _dedup_keep_order(list(aliases.get("questions") or []))
    gold_lower = {g.lower() for g in gold}
    for q in gold + [q for q in gen_q if q.lower() not in gold_lower]:
        lines.append(f"Q: {q}")

    alias_terms = _dedup_keep_order(list(aliases.get("aliases") or []))
    if alias_terms:
        lines.append("; ".join(alias_terms))

    prefix = "\n".join(lines) + "\n\n" if lines else ""
    body = chunk.get("text") or ""
    room = max(0, max_chars - len(prefix))
    return prefix + body[:room]

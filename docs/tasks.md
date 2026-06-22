# Task List

## TODO

| # | Task | Related Responses |
|---|------|-------------------|
| 1 | Disambiguate generic queries before full retrieval | [Response A](#response-a) |
| 2 | Fixing linking issues | — |
| 3 | Tune model tone — reduce overconfident statements | — |
| 4 | Replace Step Function with direct Lambda invoke | — |
| 5 | Replace LLM classification with structural parsers | — |
| 6 | Reduce PDF chunk size for consistency and precision | — |
| 8 | Add boilerplate stripping before chunking | — |
| 9 | Reduce topic clustering batch size | — |
| 12 | Fix WebSocket streaming hang on background tabs | — |
| 13 | Harden authority hierarchy enforcement beyond prompt compliance | — |

## Done

| # | Task |
|---|------|
| 7 | Refactor agentic retrieval Lambda (main.py) |
| 10 | Externalize prompts from Lambda code |
| 11 | Add managed compute for ingestion (Fargate) |

---

## Task Details

### Task 1: Disambiguate generic queries before full retrieval

**Problem:** For generic queries that don't specify a certain type/classification of land, the system adds classification-specific detail to the answer (e.g., citing the manufacturing manual when the user never asked about manufacturing).

**Suggested Direction:** Add a structural change to the retrieval path — after FAQ semantic search, first gather enough information against the query to understand if the answer depends on the user's specific needs (e.g., different answer for agricultural vs. manufacturing vs. residential land). If so, before doing the full retrieval path, ask a follow-up back to the user: "There are multiple types of X — which one best suits your needs?" Once the user responds, we have a much more targeted query to retrieve against.

---

### Task 2: Fixing linking issues

**Problem:** Multiple issues with how source links are generated and presented to users.

**Sub-issues:**

- [ ] **Link TTL** — Harden presigned URL expiration so links don't expire, or find an alternative solution so sources are always accessible to the user.
- [ ] **Page anchors** — Ensure all source documents can link to correct page numbers and anchors. Testers reported also an off-by-one issue where the link took them to the page after the relevant page. May need to reingest if chunk metadata (start_page/end_page) is sloppy.
- [ ] **Inline citations** — All sources cited as cards at the bottom of a response must also be mentioned as inline links within the answer text.
- [ ] **Remove redundant source list** — The chatbot should not additionally list sources as a line of text at the end of its response (the cards are sufficient).

---

### Task 11: Add managed compute for ingestion (Fargate)

**Problem:** The ingestion pipeline runs as local Python scripts with manual AWS credentials. No Fargate task, no managed compute. This means:
- Dependent on the developer's machine staying online
- No automatic retry or recovery
- Concurrency limited by local resources

**Direction:** Add a Fargate task definition to the CDK stack for ingestion. Package the scripts as a container and trigger via `aws ecs run-task`. This makes ingestion reproducible, observable (CloudWatch), and eliminates points of failure that occur without beefy machines.

---

### Task 10: Externalize prompts from Lambda code

**Problem:** The system prompt in `prompt.py` is a Python string in code. Changing it requires re-bundling and redeploying the Lambda. Minor issue but adds friction to prompt iteration.

**Direction:** Load the prompt from S3 or SSM Parameter Store at cold-start. This allows iterating on prompt wording without any deployment. For ingestion prompts (`LLM_CLASSIFY_PROMPT` in `extract.py`), externalize to YAML or `.txt` files.

---

### Task 9: Reduce topic clustering batch size

**Problem:** The app sends 200 topics per LLM call for synonym clustering. The output can easily exceed `maxTokens: 4096` and get truncated.

**Direction:** Reduce batch size to 50–80 topics per LLM call.

---

### Task 8: Add boilerplate stripping before chunking

**Problem:** No boilerplate removal before chunking. Common headers, footers, navigation text, and repeated legal disclaimers from PDF/HTML sources get embedded and pollute vector search. This caused major issues in retrieval — polishing this step heavily boosts performance and reduces quantity of chunks.

**Direction:** Add boilerplate patterns specific to Wisconsin sources — DOR headers/footers, publication dates repeated on every page, "Department of Revenue" navigation text, etc. Strip them before chunking. Will require reingestion.

---

### Task 7: Refactor agentic retrieval Lambda (main.py)

**Problem:** `main.py` is massive and mixes agent orchestration, citation resolution, FAQ handling, chat history, logging/tracing, resource building, and WebSocket management in one file. This makes iteration expensive — any change requires redeploying the entire Lambda.

**Notes:** The prompt is already externalized to `prompt.py`, but `_build_tool_result_summary`, `_build_tool_call_summary`, tracing infrastructure, and FAQ handling are overhead that should be separated into dedicated modules.

---

### Task 6: Reduce PDF chunk size for consistency and precision

**Problem:** `pdfChunker.py` uses `CHUNK_MAX_CHARS=7500` for PDFs (most of the corpus) while `extract.py` uses 2000 for non-PDFs. Large tool results bloat the agent's context window per turn, and each `vector_search` result returns massive chunks that consume tokens.

**Direction:** Reduce PDF chunk size to 2000–2500 chars max. More chunks but they'll be fully embedded, more precise for retrieval, and cheaper at query time (fewer tokens in tool results). The existing approach of keeping page metadata (`start_page`/`end_page`) is already good — just be more granular. Will require reingestion.

---

### Task 5: Replace LLM classification with structural parsers

**Problem:** Every non-PDF document goes through a full `bedrock.converse()` call with Claude Sonnet for classification (doc_type, title, statute_refs, topics, summary). With hundreds of documents, that's significant cost and a large extraction bottleneck.

**Direction:** Build dedicated structural/procedural parsers for known document types and formats. Extract metadata via regex/structure first; only fall back to LLM for ambiguous docs. The existing `extract_chunk_citations()` regex function is a good pattern — extend that approach to full document classification for known formats.

---

### Task 4: Replace Step Function with direct Lambda invoke

**Problem:** The Step Function architecture adds cold-start latency, state-machine transition costs, and unnecessary complexity. The multi-hop orchestration (agentic retrieval → separate ResponseStreaming + ResourceStreaming Lambdas) means the agent's output is re-serialized, re-transmitted, and re-parsed.

**Direction:** Collapse the Step Function into a direct async Lambda invoke. The agentic retrieval Lambda already streams via WebSocket — stream directly from it instead of routing through separate streaming Lambdas.

---

### Task 3: Tune model tone — reduce overconfident statements

**Problem:** The model makes aggressively confident statements like "the bottom line is...X" when property assessment is most of the time not that definitive or concrete. Answers should reflect the nuance and conditionality inherent in property tax guidance.

**Direction:** Adjust the system prompt to discourage definitive/absolute phrasing and encourage hedging where appropriate (e.g., "generally," "depending on your situation," "the manual states..."). The tone should be informative and helpful without overstating certainty.

---

### Task 12: Fix WebSocket streaming hang on background tabs

**Problem:** Sometimes the agentic retrieval streams traces all the way to "Answering with N cited docs" but then hangs — the answer never streams to the frontend. On page refresh the answer is there (persisted in DynamoDB). Reproduces most often when the browser tab is not in focus.

**Root Cause:** Browser-backgrounded tabs close WebSocket connections (TCP idle timeout or browser suspension). When the Step Function transitions from AgenticRetrieval → ResponseStreaming, the ResponseStreaming Lambda looks up the now-stale `connectionId` from DynamoDB and calls `post_to_connection`. API Gateway returns 410 GoneException (connection dead). The code has no GoneException handling — it raises `MessageDeliveryError`, ResponseStreaming returns `successful: false`, and the frontend stays stuck in "streaming" state. The answer IS saved because `save_chat_history()` runs inside the agentic retrieval Lambda (before the Step Function transition), so refresh loads it from DynamoDB.

**Contributing factors:**
- `websocket_utils/utils.py` has no handling for 410 GoneException — treats all `post_to_connection` failures the same
- PartySocket reconnects with a new `connectionId`, but by then the Step Function already failed
- No frontend mechanism to detect "stuck in streaming state" and recover

**Direction:** Multi-layer fix:
1. **Frontend recovery**: On WebSocket reconnect, if there's an in-flight query stuck in "streaming" status, poll the chat history API for the answer and hydrate the UI.
2. **Backend resilience**: In ResponseStreaming, catch 410 GoneException specifically — the answer is already in DynamoDB, so log and exit gracefully instead of crashing.
3. **Longer term (Task 4)**: Collapsing the Step Function eliminates the transition gap entirely — the agentic retrieval Lambda would stream the answer directly.

---

### Task 13: Harden authority hierarchy enforcement beyond prompt compliance

**Problem:** The authority hierarchy (Constitution > Statutes > Case Law > Admin Rules > WPAM > FAQs > Guides > IAAO > USPAP) is currently enforced entirely through system prompt instructions. The agent is told the hierarchy in prose and trusted to apply it when selecting citations and framing answers. There is no programmatic check that higher-authority sources are prioritized, no re-ranking by authority level, and no guardrail preventing the agent from citing a FAQ over a controlling statute.

Each Neptune Document node already carries an `authority_level` integer (1–9), and `get_authority_chain` lets the agent inspect the governance tree — but nothing in the code validates or adjusts the agent's final output based on these signals.

**Risk:** If the model drifts, misweighs, or ignores the prompt hierarchy (especially under complex multi-source queries), the user could receive an answer grounded in informal guidance (FAQ, IAAO) when binding law (statute, admin rule) is directly on point.

**Direction (investigation needed):**
- **Post-loop validation**: After the agent calls the `answer` tool, inspect `cited_doc_ids` and their `authority_level` properties. Flag or log when the answer cites only low-authority sources for a query where higher-authority documents were retrieved but not cited.
- **Re-ranking tool results**: Weight vector search results by authority level before returning them to the agent, so higher-authority chunks appear first and are more likely to be selected.
- **Structured output enforcement**: Require the `answer` tool to include a `primary_authority_level` field and reject answers where the stated level doesn't match the cited documents.
- **Prompt-level reinforcement**: Add worked examples to the system prompt showing correct authority selection, or inject a turn-N reminder about hierarchy when the agent appears to be citing only low-authority sources.

---

## Flawed Responses

### Response A

**Query:** What Information is Used to Determine my assessment?

**Issue:** Cites manufacturing manual when question didn't specifically ask about manufacturing.

<details>
<summary>Full response</summary>

> What Information Is Used to Determine Your Assessment?
>
> The information used depends on whether your property is real estate or involves a general assessment cycle. Here's what the documents say:
>
> **Approaches to Value** — According to the Guide to Wisconsin Manufacturing Property Assessment – 2026, appraisers consider one or more of the following approaches:
> - Sales Comparison Approach
> - Cost Approach
> - Income Approach
>
> **Current Market Activity** — The Guide to Wisconsin Manufacturing Property Assessment – 2026 also notes current market activity is considered (improved/vacant land sales, listings, lease rates, vacancy rates, economic changes, lending practices, new construction).
>
> **Property Record Data** — The 2025 Wisconsin Property Assessment Manual outlines data sources (physical view, public area view, owner-requested data, other info such as listings/permits).
>
> **Sales Ratio Studies and Market Analysis** — assessors analyze sales through assessment/sales ratio studies, monitor zoning changes, building permits, and land use factors.

</details>

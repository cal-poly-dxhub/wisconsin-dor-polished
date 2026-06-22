# Task List

## TODO

| # | Task | Related Responses |
|---|------|-------------------|
| 1 | Disambiguate generic queries before full retrieval | [Response A](#response-a) |
| 2 | Fixing linking issues | — |
| 3 | Tune model tone — reduce overconfident statements | — |
| 5 | Replace LLM classification with structural parsers | — |
| 6 | Reduce PDF chunk size for consistency and precision | — |
| 8 | Add boilerplate stripping before chunking | — |
| 9 | Reduce topic clustering batch size | — |
| 14 | Improve case law discovery in vector_search auto-enrichment | [Response B](#response-b) |

## Done

| # | Task |
|---|------|
| 4 | Replace Step Function with direct Lambda invoke |
| 7 | Refactor agentic retrieval Lambda (main.py) |
| 10 | Externalize prompts from Lambda code |
| 11 | Add managed compute for ingestion (Fargate) |
| 12 | Fix WebSocket streaming hang on background tabs |
| 13 | Harden authority hierarchy enforcement (authority-aware re-ranking) |
| 15 | Add settings modal with detailed trace toggle |

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

### Task 14: Improve case law discovery in vector_search auto-enrichment

**Problem:** The `vector_search` tool's auto-enrichment does not reliably surface relevant case law — especially recent decisions connected to statutes via `CITES` edges. The current approach depends on: (1) text regex citation extraction from retrieved chunks, and (2) `get_neighbors` on parent docs with a hard cap of 50 and no ordering. For heavily-connected nodes like `statutes-70` (hundreds of CITES edges), which 50 neighbors are returned is non-deterministic. This causes critical case law (e.g., *Children's Hospital of Wisconsin v. City of Wauwatosa*, 2025 WI App 43) to appear in some runs but not others for the same query.

**Evidence:** Same query ("Is the expansion of a non-profit hospital that is under construction on the assessment date qualify for an exemption from property tax?") run on 06/18 and 06/21. On 06/18 the agent happened to call `get_neighbors` on `WIS-STAT-70.11` with `title_filter: "hospital"`, found the 2025 case, fetched its full opinion from S3, and produced an excellent answer citing the case's facts and holding. On 06/21 the agent took a different tool path, never discovered the case, and produced a weaker generic answer. The auto-enrichment surfaced the same 5 generic cases both times — Children's Hospital was never in them because it's connected via graph edge (CITES), not mentioned in the chunk text.

**Root Cause:** The auto-enrichment resolves cases from *text mentions* (regex) but not from *graph structure* (CITES edges to statute subsections). The `get_neighbors` call on parent docs is too broad (entire statute doc) and too small (50 cap, no ordering) to reliably capture the right cases.

**Direction (ranked by effectiveness):**

1. **Chunk → Statute subsection → CaseLaw path** — Retrieved chunks already have `CITES` edges to specific statute subsection nodes (e.g., `WIS-STAT-70.11`). Add a step: collect the statute subsection IDs cited by the top chunks, then query for CaseLaw nodes that also have `CITES` edges to those same subsections. This is deterministic, targeted, and uses exactly the graph structure built during ingestion.

2. **Order the 50-cap by recency** — Add `ORDER BY n.effective_date DESC` to the `get_neighbors` query. Newer cases (2025) always beat older ones (1967) for a slot in the cap. Simple one-line change.

3. **Label-aware budget splitting** — Reserve a portion of the 50 neighbor slots specifically for CaseLaw nodes. Currently the 50 slots may be dominated by Documents/Frameworks, pushing case law out entirely.

4. **Prompt nudge** — Instruct the agent to always call `get_neighbors` with `title_filter` on relevant statutes. Cheapest to implement but fundamentally unreliable — the same prompt produced different behavior on 06/18 vs 06/21.

**Key files:**
- `backend/lambdas/agentic_retrieval/tools.py` — auto-enrichment logic (lines 665–791)
- `backend/lambdas/agentic_retrieval/neptune_client.py` — `get_neighbors()` (line 279), `resolve_case_citations()`, would need new method for subsection→CaseLaw traversal

---

### Task 15: Add settings modal with detailed trace toggle

**Problem:** The agent trace UI streams detailed expanded steps (tool calls, reasoning, metadata) to the frontend. This is useful for developers but noisy for end users in production.

**Solution (implemented):** Two-tier trace display controlled by a user-facing setting:
- **Dev mode (localhost):** Trace starts expanded by default — detailed steps stream in real-time
- **Prod/default:** Trace starts collapsed — user sees "Thought for Xs" with a chevron to expand manually
- **User override:** Settings modal (accessible from profile menu) lets users toggle "Detailed agent trace" on/off, persisted in localStorage

**Files added:**
- `frontend/src/stores/settings-store.ts` — Zustand store with localStorage persistence (`wisco:settings`)
- `frontend/src/components/ui/dialog.tsx` — Radix Dialog primitive
- `frontend/src/components/ui/switch.tsx` — Radix Switch primitive
- `frontend/src/components/settings/settings-modal.tsx` — Settings modal with toggle

**Files modified:**
- `frontend/src/components/layout/sessions-sidebar.tsx` — Added "Settings" item to profile popover
- `frontend/src/components/messages/chat-message.tsx` — `stepsOpen` defaults to `detailedTrace` setting

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

---

### Response B

**Query:** Is the expansion of a non-profit hospital that is under construction on the assessment date qualify for an exemption from property tax?

**Issue:** On 06/21, the system failed to discover and cite *Children's Hospital of Wisconsin, Inc. v. City of Wauwatosa* (2025 WI App 43) — the most directly on-point case — despite it existing in the graph with a CITES edge to WIS-STAT-70.11. The 06/18 run of the same query correctly found and cited it, producing a substantially better answer with the case's specific facts and holding.

<details>
<summary>06/21 response (missing Children's Hospital)</summary>

> # Nonprofit Hospital Expansion Under Construction: Property Tax Exemption
>
> ## Short Answer
>
> **Generally, no.** A hospital expansion that is still under construction on the January 1 assessment date typically does **not** qualify for the property tax exemption under Wis. Stat. § 70.11(4m)(a), because the property is not yet being **"owned and used"** exclusively for hospital purposes on that date. However, the analysis is fact-specific and depends on the degree to which the property is actually being put to hospital use.
>
> [cites Covenant Healthcare (2011 WI 80), Saint Joseph's Hospital (276 Wis. 2d 574), but NOT Children's Hospital (2025 WI App 43)]

</details>

<details>
<summary>06/18 response (correctly cites Children's Hospital)</summary>

> # Non-Profit Hospital Expansion Under Construction: Property Tax Exemption
>
> ## Short Answer
>
> **Generally, no** — a non-profit hospital expansion that is only partially constructed on the January 1 assessment date does **not** qualify for the property tax exemption under Wis. Stat. § 70.11(4m). However, a fully constructed expansion that is in the **final stages of being readied** for hospital use (but not yet operational) **may** qualify under the "readying rule."
>
> ### The 2025 Court of Appeals Decision — Directly On Point
>
> In ***Children's Hospital of Wisconsin, Inc. v. City of Wauwatosa*, 2025 WI App 43**, the Court of Appeals addressed precisely the question you are asking: whether a non-profit hospital's **expansion tower under construction** on the assessment date qualifies for the § 70.11(4m) exemption.
>
> **Facts:** Children's Hospital of Wisconsin (CHW) began constructing the "North Tower" addition to its existing hospital complex. On January 1, 2020 (the assessment date), the North Tower was only approximately **14% completed** (excavation, footings, foundation walls, some exterior walls). CHW argued the property was exempt because it was being "readied" for use as a nonprofit hospital.
>
> **Holding:** The Court of Appeals **affirmed** the City of Wauwatosa's denial of the exemption...

</details>

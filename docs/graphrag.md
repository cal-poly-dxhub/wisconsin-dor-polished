# Graph RAG — What Makes It Actually Work

This document breaks down the specific techniques that make this system produce accurate, well-sourced answers instead of hallucinated garbage. The focus is on the things that would silently break the system if removed.

---

## 1. The Core Insight: Graph Traversal Over Vector Search

Plain RAG retrieves text chunks by similarity and hopes the LLM can synthesize an answer. That fails for compliance questions because:

- A single policy requirement might span 5 documents across 3 governance layers
- Vector similarity finds "similar text" but misses "authoritative source"

**Graph RAG solves this by making the agent traverse the governance hierarchy.** Every vector search automatically enriches results with graph neighbors — the agent sees not just "similar text" but also what implements it, what it cites, and what authority it derives from.

### How the Agent Is Forced to Traverse

The system prompt prescribes a specific multi-turn strategy:

```
Turn 1: vector_search for the topic. Results include connected documents — use those connections.
Turn 2: get_neighbors or get_authority_chain on the most relevant docs from Turn 1.
Turn 3: Call the answer tool with thorough citations.
```

Key lines from the prompt that enforce this:

- **"ALWAYS explore the graph — don't just vector search."**
- **"PREFER graph traversal (get_neighbors, get_authority_chain) over get_document with guessed IDs."**
- **"Only use get_document when you see the exact ID in a previous tool result."**

Without these instructions, the agent defaults to vector search → answer, skipping the graph entirely. The explicit traversal mandate is what makes it GraphRAG.

### Auto-Enrichment: The Single Most Impactful Technique

Every `vector_search` call automatically queries graph neighbors for the top-3 parent documents. The agent doesn't have to decide to explore — it gets graph context for free on every search.

```python
# In _exec_tool, after vector search returns chunks:
for doc_id in top_parent_ids[:3]:
    neighbors = _query_neighbors(doc_id)
    result_text += f"\n\nConnected to {doc_id}:\n{neighbors}"
```

A single vector search for "incident response" returns:

1. Relevant text chunks (from vector similarity)
2. parent doc of top chunks
3. edge(s) from that parent doc (all the different types of edges)
   The agent now has the full governance chain without making a single explicit traversal call.

---

## 2. Framework Applicability Checking

The system prompt contains a complete applicability matrix for every framework in the graph. This prevents the agent from citing unrelated sources/sources that don't apply to a given situation.

Examples from the domain of healthcare/PII:

- **FedRAMP**: "Only applies to cloud service providers (CSPs) seeking federal authorization. Does NOT apply to on-premise state systems."
- **MARS-E**: "Only applies to ACA health insurance exchange systems. Does NOT apply to general Medicaid."
- **CMS ARS**: "Only applies to CMS systems. Does NOT apply to non-CMS state systems."

The prompt also says: **"If the user hasn't specified enough context to determine applicability, either ask or state your assumption explicitly."**

Without this, the agent treats every framework as equally applicable and dumps irrelevant controls into answers.

---

## 3. Anti-Hallucination Techniques

### In the Agent (Query Time)

1. **"ONLY cite documents you actually retrieved via tools."** — The most important rule, stated first.
2. **Out-of-scope awareness.** The prompt must list what's NOT in the graph and says to acknowledge the gap rather than improvise.
3. **Requires vs Recommends.** "Distinguish between what a document REQUIRES vs what it RECOMMENDS." Prevents presenting recommendations as mandates.
4. **Precision over guessing.** "If you're unsure of the exact number, say so rather than guessing."
5. **Repeated at the end:** "Do NOT guess or hallucinate document content — only use what the tools return."

### In Ingestion (Build Time)

- **"Only explicitly mentioned" — repeated 5 times in the parse prompt.** The LLM must not infer references that aren't in the text. Without this, the graph fills with phantom edges.
- **Validate everything the LLM returns.** Every LLM output is parsed, regex-validated, and normalized. Bad IDs get fallbacks. Unknown doc_types become "guidance." Invalid NIST codes are dropped.
- **LLM as classifier, not generator.** The LLM classifies doc_type, extracts references, and confirms relationships. It never generates policy content. Hallucination risk is contained to metadata.

---

## 4. Source Tracking and LLM-Driven Citation

The agent's `answer` tool requires a `source_ids` array — the LLM must explicitly list which documents informed its answer.

The tool description says:

- **"Include ALL document IDs that informed the answer — err on the side of including more sources rather than fewer."**
- **"Omit docs that were retrieved but turned out not to be useful."**

This means the LLM decides relevance, not a hardcoded filter. A fallback filter (fetched + vector-search + graph-neighbor) only kicks in if the LLM provides no source IDs, and that fallback is logged as `[sources] HARDCODED fallback` in CloudWatch for monitoring.

Every document the agent touches is tracked with how it was discovered:

- `fetched` — agent explicitly requested by ID
- `vector-search` — found via semantic similarity
- `graph-neighbor` — discovered through graph traversal
- `framework-list` — seen when listing a framework

---

## 5. Conflict Resolution via Authority Hierarchy

The prompt encodes a precedence order: **statute > regulation > policy > standard > guidance**.

When two documents give different requirements (e.g., different breach notification timelines), the agent is instructed to flag the conflict and explain which takes precedence. This is only possible because the graph encodes the governance hierarchy as explicit DERIVED_FROM edges between framework nodes.

The `get_authority_chain` tool traces these edges up and down, giving the agent the full chain: SAM-5340 → FW-SAM → FW-CA-STATUTE (upward) and SAM-5340 → SIMM-5340-A → DHCS procedures (downward).

---

## 6. Ingestion Techniques That Enable Good Retrieval

### Stub-Then-Resolve

Cross-references create stub nodes immediately (preserving the citation), then a post-pass resolves stubs to real nodes using embedding similarity + LLM confirmation. This decouples ingestion order from graph correctness — documents can be ingested in any order.

### ID Normalization

`normalize_id()` handles format variations in document names. Called everywhere to ensure consistent graph node IDs regardless of how different documents reference the same thing. Without this, the graph fragments into duplicate nodes.

### 6 Typed Citation Edges

Instead of a generic "REFERENCES" edge, the graph has edges like IMPLEMENTS, CITES_STANDARD, CITES_SECTION, CITES_TL, CITES_REGULATION, and CITES. Each has regex validation on the target ID format. This lets the agent distinguish — critical for authority chain traversal.

### Section-Aware Chunking

Chunks split on section boundaries (headers, numbered sections) rather than arbitrary character counts. Each chunk inherits ALL metadata from its parent doc. This means vector search results carry structured metadata, not just raw text.

### Semantic Edge Discovery

A two-phase approach finds relationships between documents that don't explicitly cite each other:

1. Embedding pre-filter (cosine similarity > 0.55 threshold)
2. LLM confirmation classifying into RELATED_TO, SUPPLEMENTS, SUPERSEDES, CONFLICTS_WITH

These edges are what let the agent discover that two documents from different frameworks address the same requirement.

---

## 7. Turn Budget and Graceful Degradation

- **Turn 3-4 target.** The prompt says to answer by turn 3-4. This prevents the agent from endlessly exploring.
- **Turn 8 warning.** A text message is injected: "You are running low on turns. Call the answer tool NOW."
- **Turn 10 hard limit.** Extracts the last assistant text as a fallback answer with all tracked sources.
- **max_tokens truncation recovery.** If the response is cut off mid-answer, the code extracts partial text and appends "_(Response may be incomplete)_".
- **ID not found fallback.** `get_document` falls back to vector search on the ID string, handling typos and format mismatches.

---

## 8. Streaming Architecture

The async invocation pattern is critical for the agentic loop:

1. Chat Lambda receives WebSocket message, invokes Query Lambda **asynchronously**, sends `processing` signal immediately.
2. Query Lambda runs the multi-turn agent loop (up to 120s), pushing directly to the client via WebSocket:
   - `turn` messages with action summaries after each agent turn
   - `chunk` messages with 80-char answer fragments
   - `sources` and `done` signals
3. Thinking steps accumulated and saved to DynamoDB for the "show thinking" UI.

The async pattern means the chat Lambda returns immediately — no timeout risk even for complex 10-turn agent loops.

---

## 9. What Would Break If Removed

| Technique                                 | What breaks without it                                                         |
| ----------------------------------------- | ------------------------------------------------------------------------------ |
| Auto-enrichment on vector search          | Agent only sees text chunks, misses governance chain. Becomes plain RAG.       |
| "PREFER graph traversal" in prompt        | Agent defaults to vector search → answer, never explores the graph             |
| Framework applicability section           | Agent cites FedRAMP for on-premise systems, MARS-E for non-exchange systems    |
| "ONLY cite documents retrieved via tools" | Agent fabricates section numbers and requirements from training data           |
| ID normalization                          | SAM-0500 and SAM-500 become separate nodes, graph fragments                    |
| Stub-then-resolve                         | Cross-references to not-yet-ingested docs are lost                             |
| Typed citation edges                      | Agent can't distinguish "implements" from "references", authority chain breaks |
| Turn budget + fallback chain              | Agent loops forever or returns nothing on complex questions                    |
| Source tracking with relevance tags       | No way to distinguish directly-used sources from incidentally-seen ones        |
| Applicability checking prompt             | Every answer dumps every framework regardless of relevance                     |

---

## 10. Development Practices That Made This Work

### Understand Your Data Before Writing a Single Parser

The biggest time sink in this project wasn't code — it was understanding document structure. Every source format has quirks:

Before writing each parser, we read samples at multiple points in the file (beginning, middle, end) to catch structural changes. The SAM file has 1,374 sections across 60+ chapters — the header pattern that works for chapter 5300 also had to work for chapter 8000.

**Lesson:** Spend extra time reading the raw data before writing any code. The parser you write for the first 100 lines might not work for line 10,000.

### Test Small, Then Scale

The ingestion pipeline processes ~200 files through LLM calls, embedding, and graph writes. A full run takes 30+ minutes and costs real money in Bedrock API calls.

Development pattern:

1. **Test parsers on a single file first.** Run the parser function directly in a Python REPL against one file. Check the output dict — are IDs normalized? Are cross-references extracted? Is the text clean?
2. **Test with 5-10 files.** Upload a small subset to S3 and run ingestion. Check the graph in Neptune — are edges correct? Are stubs being created for cross-references?
3. **Use the parse cache.** After a successful extract step, the parsed docs are cached to S3. Use `--skip-to embed` to iterate on embedding and loading without re-running the expensive LLM extraction.

**Lesson:** Never run the full pipeline to test a parser change. Test the parser in isolation, then test with a small batch, then run full.

### Iterate on Prompts Separately from Code

The system prompt, parse prompt, and relate prompt are all external files (`system_prompt.txt`, `prompts/parse.txt`, `prompts/relate.txt`). This was a deliberate design choice.

- Prompt changes don't require code changes or redeployment
- You can A/B test prompts by swapping files
- The prompt is readable as a standalone document, not buried in a Python string

The system prompt went through 5+ major revisions based on testing real queries. Each revision addressed a specific failure mode observed in actual responses (e.g., citing a source that didn't apply to the given situation, fabricating breach notification timelines, dumping every framework instead of checking applicability).

**Lesson:** Externalize prompts from day one. You will iterate on them far more than on the code.

### Test with Adversarial Queries

- **Cross-domain:**
- **Ambiguous applicability:** (tests assumption flagging)
- **Multi-doc:**
- **Specific timelines:** "How many days do I have...?"
- **Follow-ups:** Ask a question, then say "what about for a...?" (tests conversation context handling)

Export the chat responses and evaluate: Are sources accurate? Are any fabricated? Did it cite frameworks that don't apply? Did it miss frameworks that do apply?

**Lesson:** Your system is only as good as the hardest question you've tested it with. Test the edge cases, not the happy path.

### Config-Driven Everything

If you find yourself editing Python to change a threshold or add a document type mapping, move it to config.

### Watch the Logs

Log the decisions, not just the errors. The system works correctly when the LLM makes good decisions — you need visibility into those decisions to know if it's working.

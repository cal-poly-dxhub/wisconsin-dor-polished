"""Bedrock Converse tool specs for the agentic retrieval loop."""

TOOL_DEFINITIONS = [
    {
        "toolSpec": {
            "name": "faq_search",
            "description": (
                "Already run before this loop — results are in your first "
                "toolResult message. Do NOT call this tool again."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The search query to find relevant FAQs",
                        },
                    },
                    "required": ["query"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "vector_search",
            "description": (
                "Search for relevant document chunks using semantic similarity. "
                "Returns the most relevant text chunks from Wisconsin DOR documents. "
                "Always start with this tool to find relevant content. "
                "The query is automatically refined for retrieval (casual wording "
                "translated to assessment vocabulary, follow-up context resolved) — "
                "just pass the user's question directly."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": (
                                "The search query — pass the user's question directly "
                                "or a specific topic to search for"
                            ),
                        },
                        "top_k": {
                            "type": "integer",
                            "description": "Number of results to return (default: 10, max: 25)",
                            "default": 10,
                        },
                    },
                    "required": ["query"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "search_document",
            "description": (
                "Search within a specific document's chunks using semantic similarity. "
                "Consider list_sections + get_section first for multi-chapter documents "
                "(WPAM, large guides) — they are more reliable. Use search_document only "
                "when you cannot identify the right section from headings alone. "
                "Returns the top chunks from that document matching your sub-query."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "doc_id": {
                            "type": "string",
                            "description": (
                                "The document ID to search within (from a previous tool result)"
                            ),
                        },
                        "query": {
                            "type": "string",
                            "description": (
                                "A targeted sub-query for the specific section you need. "
                                "IMPORTANT: Always include the document's title or distinguishing "
                                "keywords in your query (e.g., 'manufacturing property assessment "
                                "guide valuation methods' not just 'valuation methods'). "
                                "This is a global search filtered to the target doc — without "
                                "document-specific terms, larger documents will dominate results "
                                "and this tool will return nothing."
                            ),
                        },
                        "top_k": {
                            "type": "integer",
                            "description": "Number of chunks to return (default: 5, max: 10)",
                            "default": 5,
                        },
                    },
                    "required": ["doc_id", "query"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "list_sections",
            "description": (
                "List all section headings (table of contents) for a document. "
                "Returns each heading with its chunk count and page range. "
                "Use this when you know which document is relevant but need to "
                "find the right section — much more reliable than guessing "
                "search terms for search_document. Especially useful for large "
                "multi-chapter documents like the WPAM."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "doc_id": {
                            "type": "string",
                            "description": (
                                "The document ID to list sections for (from a previous tool result)"
                            ),
                        },
                    },
                    "required": ["doc_id"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "get_section",
            "description": (
                "Retrieve chunks from a specific section of a document. "
                "Use after list_sections to fetch content from a chapter or "
                "section by its exact heading. When a query is provided, "
                "returns only the most relevant chunks (ranked by semantic "
                "similarity); without a query, returns all chunks in document "
                "order. ALWAYS provide a query when the section has more than "
                "~10 chunks — it dramatically reduces noise. Omit the query "
                "only when you need the full sequential text of a short section."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "doc_id": {
                            "type": "string",
                            "description": ("The document ID containing the section"),
                        },
                        "heading": {
                            "type": "string",
                            "description": (
                                "The exact section heading from list_sections "
                                "(e.g., 'Chapter 12 Residential Property Valuation')"
                            ),
                        },
                        "query": {
                            "type": "string",
                            "description": (
                                "A natural-language question describing what you "
                                "need from this section. Used to rank and filter "
                                "chunks by relevance. Be specific — include the "
                                "property type, assessment concept, or statute "
                                "reference you are investigating."
                            ),
                        },
                        "top_k": {
                            "type": "integer",
                            "description": (
                                "Max chunks to return when query is provided "
                                "(default: 5, max: 10). Ignored without a query."
                            ),
                            "default": 5,
                        },
                    },
                    "required": ["doc_id", "heading"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "get_document",
            "description": (
                "Fetch a specific document's metadata by its ID. "
                "Use this when you have a document ID from vector_search results "
                "and need more details like title, summary, authority level."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "doc_id": {
                            "type": "string",
                            "description": "The document ID to look up",
                        }
                    },
                    "required": ["doc_id"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "get_neighbors",
            "description": (
                "Traverse graph edges from a document to find related nodes. "
                "Use this to find what a document CITES, IMPLEMENTS, or is "
                "PART_OF. Critical for finding authoritative sources — most "
                "importantly, the interpreting case law that hangs off a "
                "statute stub via CITES edges."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "node_id": {
                            "type": "string",
                            "description": "The node ID to get neighbors for",
                        },
                        "edge_types": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Filter by edge types. Options: CITES, IMPLEMENTS, PART_OF, "
                                "BELONGS_TO, DERIVED_FROM, COVERS_TOPIC, EXTRACTED_FROM"
                            ),
                        },
                        "direction": {
                            "type": "string",
                            "enum": ["outgoing", "incoming", "both"],
                            "description": "Edge direction (default: both)",
                            "default": "both",
                        },
                        "query": {
                            "type": "string",
                            "description": (
                                "Optional. When provided, semantically ranks neighbors "
                                "by relevance to your query and returns only the most "
                                "relevant results. Use this to improve accuracy when a "
                                "node has many neighbors."
                            ),
                        },
                        "top_k": {
                            "type": "integer",
                            "description": (
                                "Max neighbors to return when query is provided "
                                "(default: 5, max: 10). Ignored without a query."
                            ),
                            "default": 5,
                        },
                        "target_wpam_year": {
                            "type": ["integer", "null"],
                            "description": (
                                "Optional. If the user explicitly asked about a "
                                "specific WPAM edition year, pass it here so dedup "
                                "returns chunks from that edition instead of the "
                                "most recent."
                            ),
                        },
                    },
                    "required": ["node_id"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "get_authority_chain",
            "description": (
                "Trace the governance hierarchy from a document up to the root authority. "
                "Returns the chain: Document -> Section -> Chapter -> Framework -> Constitution. "
                "Use this to understand what level of authority backs a "
                "particular rule or guidance."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "node_id": {
                            "type": "string",
                            "description": "The node ID to trace authority from",
                        }
                    },
                    "required": ["node_id"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "list_framework_docs",
            "description": (
                "List all documents belonging to a framework/authority level. "
                "Framework IDs: FW-CONSTITUTION, FW-STATUTES, FW-ADMIN-RULES, "
                "FW-WPAM, FW-FAQ, FW-GOV-PUBS"
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "framework_id": {
                            "type": "string",
                            "description": "The framework ID to list documents for",
                        }
                    },
                    "required": ["framework_id"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "find_case_law",
            "description": (
                "Search for a specific court case by name or citation. "
                "Use this when: (1) the user's question names a specific case, "
                "OR (2) retrieved chunks mention a case by name but you need "
                "the case's node ID for citing. Searches CaseLaw node titles "
                "and citations. Optionally scope to cases connected to a "
                "specific statute for more targeted results."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "search_text": {
                            "type": "string",
                            "description": (
                                "Case name, party name, or citation to search for "
                                "(e.g., 'Markarian', '45 Wis. 2d 683', 'Lowe's v. Delavan')."
                            ),
                        },
                        "statute_id": {
                            "type": "string",
                            "description": (
                                "Optional. Statute node ID to scope the search "
                                "(e.g., 'WIS-STAT-70.32'). Only returns cases "
                                "connected to this statute via CITES edges."
                            ),
                        },
                    },
                    "required": ["search_text"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "fetch_case_opinion",
            "description": (
                "Fetch the full text of a Wisconsin court opinion by citation. "
                "Use this ONLY as a LAST resort: (1) the primary sources "
                "(statutes, admin rules, WPAM, FAQs) you've already gathered "
                "are insufficient, AND (2) the case's ANNOTATION chunks "
                "already in context do not contain enough detail, AND (3) the "
                "user's question turns on the court's specific analysis or "
                "holding. Do NOT call this to 'confirm' information the "
                "annotation already shows. Case-law documents include the "
                "citation you need (e.g., '109 Wis. 2d 290'). Returns opinion "
                "text if available in our S3 archive, otherwise a Google "
                "Scholar search URL."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "citation": {
                            "type": "string",
                            "description": (
                                "Legal citation exactly as it appears on the "
                                "CaseLaw document, e.g. '109 Wis. 2d 290' or "
                                "'2000 WI App 182'."
                            ),
                        }
                    },
                    "required": ["citation"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "list_worksheets",
            "description": (
                "List the DOR Excel worksheets whose structure is available "
                "for lookup. These are the Tax Incremental District (TID) "
                "base-value and redetermination workbooks. Use this when a "
                "user asks how to complete a TID worksheet, which form or "
                "sheet to use, what a specific column or line captures, or how "
                "a TID base value / decrement is determined. Returns each "
                "worksheet_id with its title and the sheets it contains."
            ),
            "inputSchema": {"json": {"type": "object", "properties": {}}},
        }
    },
    {
        "toolSpec": {
            "name": "get_worksheet",
            "description": (
                "Retrieve the structured contents of one DOR TID worksheet: "
                "its field/column labels, the calculation formulas it applies "
                "(described, not computed), and the preparer instructions "
                "printed on each sheet. Use after list_worksheets to explain "
                "how to complete a worksheet or how a value is derived. Pair "
                "the returned structure with the matching instruction PDF "
                "(form_instructions-* / tif-manual) for the narrative rules. "
                "Cite the worksheet by its worksheet_id."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "worksheet_id": {
                            "type": "string",
                            "description": (
                                "The worksheet ID from list_worksheets "
                                "(e.g., 'worksheets-decrement')."
                            ),
                        },
                        "sheet": {
                            "type": "string",
                            "description": (
                                "Optional. Return only this sheet/tab (e.g., "
                                "'PE-608 (local RE)'). Omit to return all sheets."
                            ),
                        },
                    },
                    "required": ["worksheet_id"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "prepare_answer",
            "description": (
                "Signal that research is complete and you are ready to write "
                "your answer. Declare which documents you will cite. Do NOT "
                "write the answer text here — it will be generated in a "
                "follow-up step. Just list the document IDs and optionally "
                "outline the answer structure."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "cited_doc_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Document IDs that will be cited in the answer",
                        },
                        "answer_plan": {
                            "type": "string",
                            "description": (
                                "Brief outline of the answer structure and key points "
                                "to cover (2-3 sentences). This helps the answer "
                                "generation step stay focused."
                            ),
                        },
                    },
                    "required": ["cited_doc_ids"],
                }
            },
        }
    },
]

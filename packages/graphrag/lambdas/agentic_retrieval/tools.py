"""
Tool definitions for Claude's agentic retrieval loop.
Maps Neptune capabilities to Bedrock Converse tool_use format.
"""

import json
import logging

import boto3

from neptune_client import NeptuneClient

logger = logging.getLogger(__name__)

bedrock = boto3.client("bedrock-runtime", region_name="us-west-2")

TOOL_DEFINITIONS = [
    {
        "toolSpec": {
            "name": "vector_search",
            "description": (
                "Search for relevant document chunks using semantic similarity. "
                "Returns the most relevant text chunks from Wisconsin DOR documents. "
                "Always start with this tool to find relevant content."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The search query to find relevant chunks",
                        },
                        "top_k": {
                            "type": "integer",
                            "description": "Number of results to return (default: 10, max: 20)",
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
                "Use this to find what a document CITES, IMPLEMENTS, is PART_OF, "
                "SUPPLEMENTS, SUPERSEDES, or is RELATED_TO. "
                "Critical for finding authoritative sources and newer guidance."
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
                                "BELONGS_TO, DERIVED_FROM, COVERS_TOPIC, EXTRACTED_FROM, "
                                "HAS_SUBSECTION, SUPPLEMENTS, SUPERSEDES, CONFLICTS_WITH, RELATED_TO"
                            ),
                        },
                        "direction": {
                            "type": "string",
                            "enum": ["outgoing", "incoming", "both"],
                            "description": "Edge direction (default: both)",
                            "default": "both",
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
                "Use this to understand what level of authority backs a particular rule or guidance."
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
            "name": "answer",
            "description": (
                "Provide the final answer to the user's question with citations. "
                "Call this tool when you have gathered enough information. "
                "Include specific document references and section numbers."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "response": {
                            "type": "string",
                            "description": "The complete answer with citations in Markdown",
                        },
                        "cited_doc_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of document IDs cited in the response",
                        },
                    },
                    "required": ["response", "cited_doc_ids"],
                }
            },
        }
    },
]


def embed_query(query: str, model_id: str = "amazon.titan-embed-text-v2:0") -> list[float]:
    """Embed a query string for vector search."""
    body = json.dumps({
        "inputText": query[:8000],
        "dimensions": 1024,
        "normalize": True,
    })
    response = bedrock.invoke_model(
        modelId=model_id, body=body,
        contentType="application/json", accept="application/json",
    )
    return json.loads(response["body"].read())["embedding"]


def execute_tool(tool_name: str, tool_input: dict, neptune: NeptuneClient) -> dict:
    """Execute a tool call and return the result."""

    if tool_name == "vector_search":
        embedding = embed_query(tool_input["query"])
        top_k = min(tool_input.get("top_k", 10), 20)
        results = neptune.vector_search(embedding, top_k=top_k)
        return {"chunks": results}

    elif tool_name == "get_document":
        doc = neptune.get_document(tool_input["doc_id"])
        if doc:
            return {"document": doc}
        return {"error": f"Document '{tool_input['doc_id']}' not found"}

    elif tool_name == "get_neighbors":
        neighbors = neptune.get_neighbors(
            tool_input["node_id"],
            edge_types=tool_input.get("edge_types"),
            direction=tool_input.get("direction", "both"),
        )
        return {"neighbors": neighbors}

    elif tool_name == "get_authority_chain":
        chain = neptune.get_authority_chain(tool_input["node_id"])
        return {"authority_chain": chain}

    elif tool_name == "list_framework_docs":
        docs = neptune.list_framework_docs(tool_input["framework_id"])
        return {"documents": docs}

    elif tool_name == "answer":
        return tool_input

    else:
        return {"error": f"Unknown tool: {tool_name}"}

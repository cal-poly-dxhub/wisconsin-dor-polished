"""Shared dataclasses for the vector_search pipeline stages.

Each stage is a module exposing a single ``run(ctx: StageContext) ->
StageResult`` function. Stages mutate ``ctx`` in place (accumulating state)
and return a ``StageResult`` carrying an optional CloudWatch trace event for
the pipeline runner to log. See agent_tools/pipeline.py for the runner and
docs/spec-retrieval-pipeline-refactor.md for the design.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class StageContext:
    """Shared, mutable state flowing through the vector_search pipeline.

    Fields are populated incrementally as stages run — most start empty/None
    and are filled in by the stage responsible for them (documented inline).
    """

    # Inputs (set once, at pipeline start)
    query: str
    neptune: Any
    chat_history: list[dict[str, str]] | None = None
    original_user_query: str | None = None
    top_k: int = 10
    tool_name: str = "vector_search"
    started: float = 0.0

    # auto_refine
    refined_query: str = ""
    target_wpam_year: int | None = None

    # neptune_search
    embedding: list[float] | None = None
    fetch_k: int = 0
    chunks: list[dict] = field(default_factory=list)
    pre_dedup_count: int = 0

    # diversity_cap
    max_per_doc: int = 5

    # authority_tiebreak / statute_backfill source snapshot
    backfill_source_chunks: list[dict] = field(default_factory=list)

    # auto_enrichment (internal-only; not surfaced to the model)
    graph_context: dict[str, list[dict]] = field(default_factory=dict)

    # citation_extraction
    related_case_law: list[dict] = field(default_factory=list)

    # statute_backfill
    statute_backfill: list[dict] = field(default_factory=list)

    # caselaw_backfill
    caselaw_backfill: list[dict] = field(default_factory=list)
    caselaw_backfill_meta: dict[str, Any] = field(default_factory=dict)

    # broad_discovery
    broad_discovery: list[dict] = field(default_factory=list)
    broad_trace: dict[str, Any] | None = None
    broad_query_used: str = ""
    broad_skipped: bool = True

    # Timing + tracing
    timings: dict[str, float] = field(default_factory=dict)


@dataclass
class StageResult:
    """What a stage returns to the pipeline runner.

    ``trace`` is a fully-formed CloudWatch log event dict (event name +
    fields) that the runner passes straight to ``_log_tool_event``. Stages
    that have nothing to log (or that log internally for legacy-shape
    reasons) may return ``StageResult()``.
    """

    trace: tuple[str, dict[str, Any]] | None = None

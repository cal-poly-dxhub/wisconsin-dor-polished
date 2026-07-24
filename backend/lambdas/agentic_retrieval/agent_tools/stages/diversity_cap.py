"""Stage: cap chunks-per-document for diversity.

Limits how many chunks any single document can contribute to the candidate
pool. The top_k selection and backfill snapshot are handled by the next
stage (authority_quota) which applies authority-aware slot reservation.
"""

import os
import time

from agent_tools.stages.base import StageContext, StageResult


def run(ctx: StageContext) -> StageResult:
    started = time.perf_counter()
    max_per_doc = int(os.environ.get("DIVERSITY_CAP_PER_DOC", "3"))
    ctx.max_per_doc = max_per_doc
    if max_per_doc > 0:
        doc_counts: dict[str, int] = {}
        diverse_chunks: list[dict] = []
        for chunk in ctx.chunks:
            doc_id = chunk.get("doc_id", "unknown")
            if doc_counts.get(doc_id, 0) >= max_per_doc:
                continue
            doc_counts[doc_id] = doc_counts.get(doc_id, 0) + 1
            diverse_chunks.append(chunk)
        ctx.chunks = diverse_chunks

    ctx.timings["diversity_cap"] = (time.perf_counter() - started) * 1000
    return StageResult()

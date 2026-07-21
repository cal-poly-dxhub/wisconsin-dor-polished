"""Stage: follow chunk-level CITES edges to surface cited statute text.

For the top-N most-relevant retrieved chunks, follows their chunk-level
CITES edges to the statute stub, resolves the stub to the real statute-text
chunk (DEFINED_BY), and hands that text to the model alongside the vector
hits. This lets the agent ground a WPAM / guide passage in the underlying
statute without a separate list_sections + get_section round-trip.

v1 defaults are DATA-DERIVED (28 statute-citing queries): the cited statute
appears within the top-3 chunks in 75% of queries (top-5 adds 0 recall);
top-3 sources yield a p90 of 3 distinct chapters. Rank is a more robust gate
than an absolute score floor (on/off-topic score separation was only ~0.04).
Env-configurable for production tuning.
"""

import logging
import os
import time

from agent_tools.stages.base import StageContext, StageResult

logger = logging.getLogger(__name__)


def run(ctx: StageContext) -> StageResult:
    from agent_tools import executor as _executor

    started = time.perf_counter()
    backfill_source_gate = int(os.environ.get("STATUTE_BACKFILL_SOURCE_GATE", "3"))
    backfill_cap = int(os.environ.get("STATUTE_BACKFILL_CAP", "3"))
    statute_backfill: list[dict] = []

    if backfill_source_gate > 0 and backfill_cap > 0:
        try:
            # Source chunks are the top-N by TRUE relevance (the pre-resort
            # snapshot taken in diversity_cap). Remember each source chunk's
            # rank (for ranking below), and skip chunks that ARE statutes —
            # no point backfilling a statute from a statute; the model has
            # it already.
            source_rank: dict[str, int] = {}
            source_chunk_ids: list[str] = []
            for rank, c in enumerate(ctx.backfill_source_chunks):
                cid = c.get("chunk_id")
                if not cid:
                    continue
                source_rank[cid] = rank
                if c.get("framework_id") != "FW-STATUTES" and c.get("authority_level") != 2:
                    source_chunk_ids.append(cid)
            # Dedup at the CHUNK level, not the doc level: a statute chapter
            # (statutes-70) may already be in the results via one section
            # (70.48) while the backfill surfaces a DIFFERENT section
            # (70.47) the model does not yet have. Only skip a backfilled
            # statute chunk if that exact chunk is already present.
            already_have = {c.get("chunk_id") for c in ctx.chunks}
            rows = ctx.neptune.get_statute_backfill(source_chunk_ids) if source_chunk_ids else []
            # Rank candidate statute chunks: (1) consensus — how many top-N
            # source chunks cite this statute; (2) best (lowest) source
            # rank. Dedup by statute chunk_id.
            agg: dict[str, dict] = {}
            for r in rows:
                scid = r.get("chunk_id")
                if not scid:
                    continue
                src_rank = source_rank.get(r.get("source_chunk_id"), 999)
                entry = agg.get(scid)
                if entry is None:
                    agg[scid] = {
                        "row": r,
                        "consensus": 1,
                        "best_source_rank": src_rank,
                        "stubs": {r.get("stub_id")},
                    }
                else:
                    entry["consensus"] += 1
                    entry["best_source_rank"] = min(entry["best_source_rank"], src_rank)
                    entry["stubs"].add(r.get("stub_id"))
            ranked = sorted(
                agg.values(),
                key=lambda e: (-e["consensus"], e["best_source_rank"]),
            )
            for e in ranked:
                row = e["row"]
                # Skip a backfilled statute chunk only if the model already
                # has that exact chunk (chunk-level dedup — see above).
                if row.get("chunk_id") in already_have:
                    continue
                statute_backfill.append(
                    {
                        "chunk_id": row.get("chunk_id"),
                        "text": row.get("text"),
                        "doc_id": row.get("doc_id"),
                        "source_url": row.get("source_url"),
                        "s3_key": row.get("s3_key"),
                        "start_page": row.get("start_page"),
                        "end_page": row.get("end_page"),
                        "heading": row.get("heading"),
                        "subheading": row.get("subheading"),
                        "authority_level": row.get("authority_level"),
                        "cited_by_source_rank": e["best_source_rank"] + 1,
                        "cited_stubs": sorted(s for s in e["stubs"] if s),
                    }
                )
                if len(statute_backfill) >= backfill_cap:
                    break
            _executor._log_tool_event(
                "vector_search_statute_backfill",
                tool_name=ctx.tool_name,
                source_gate=backfill_source_gate,
                cap=backfill_cap,
                source_chunks_considered=len(source_chunk_ids),
                candidate_statute_chunks=len(agg),
                backfilled=len(statute_backfill),
                backfill_doc_ids=[b["doc_id"] for b in statute_backfill],
                backfill_stubs=sorted({s for b in statute_backfill for s in b["cited_stubs"]}),
                latency_ms=round((time.perf_counter() - started) * 1000),
            )
        except Exception:  # noqa: BLE001 — best-effort enrichment
            logger.warning("statute backfill failed; continuing", exc_info=True)

    ctx.statute_backfill = statute_backfill
    ctx.timings["statute_backfill"] = (time.perf_counter() - started) * 1000
    return StageResult()

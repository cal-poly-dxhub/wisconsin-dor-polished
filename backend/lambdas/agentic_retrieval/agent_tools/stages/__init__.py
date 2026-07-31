"""Composable pipeline stages for vector_search.

Each module exposes ``run(ctx: StageContext) -> StageResult``. See
agent_tools/pipeline.py for how they are wired together and
docs/auto-backfill.md for the stage-by-stage description.
"""

"""Naive-RAG baseline (spec section 16, Step 9).

Build BEFORE the agent. Behavior:

    one embed-and-retrieve -> one LLM call -> answer JSON

Gets the same company corpus, checklist item, model, item-answer schema, and
citation requirements as the agent. Does NOT get: planning loop, multiple
retrieval rounds, get_pages, calculator, tool use, or any gold/expected fields.

Output must be compatible with memo.json + trace.jsonl + the eval harness, so the
agent-vs-baseline comparison is fair. The baseline may abstain.

TODO(Step 9).
"""

from __future__ import annotations

from .trace import TraceWriter


def run_baseline(run_id: str, company: str, item_ids: list[str] | None, trace: TraceWriter) -> None:
    """Single retrieve-then-answer pass per checklist item, streaming events into `trace`."""
    raise NotImplementedError("baseline: implement in Step 9 (spec section 16).")

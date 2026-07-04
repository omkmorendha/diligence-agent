"""Agent loop (spec sections 14-15, Step 12).

Per run:
  1. Load company checklist from subset.json.
  2. Strip all gold/eval fields (schemas.agent_visible_item) before the prompt.
  3. Emit `plan`.
  4. For each checklist item: run the tool-use loop (cap ~12 tool calls),
     record exactly one answer or abstention.
  5. Memo assembly over recorded answers only (no new claims/numbers/citations).
  6. Emit `verdict`; persist trace.jsonl, memo.json, memo.md.

Built against the selected ToolProtocol (config.selected_tool_protocol()).

TODO(Step 12).
"""

from __future__ import annotations

from .trace import TraceWriter


def run_agent(run_id: str, company: str, item_ids: list[str] | None, trace: TraceWriter) -> None:
    """Execute the agent over a company's checklist, streaming events into `trace`."""
    raise NotImplementedError("agent loop: implement in Step 12 (spec sections 14-15).")

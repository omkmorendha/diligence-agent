"""Naive-RAG baseline (spec section 16, Step 9).

Build BEFORE the agent. Behavior:

    one embed-and-retrieve -> one LLM call -> answer JSON

Gets the same company corpus, checklist item, model, item-answer schema, and
citation requirements as the agent. Does NOT get: planning loop, multiple
retrieval rounds, get_pages, calculator, tool use, or any gold/expected fields.

Output must be compatible with memo.json + trace.jsonl + the eval harness, so the
agent-vs-baseline comparison is fair. The baseline may abstain.

Per item:
    1. `search_filing` once (query = the raw question). No re-querying, no
       `get_pages`, no `calculate` -- if the single retrieval doesn't contain
       enough evidence, the baseline abstains via `flag_outstanding`.
    2. One `llm.chat` call (json_mode) over the retrieved excerpts, asking for
       an `ItemAnswer`-shaped JSON object. The LLM never invents citations: any
       `chunk_id` it names must be one of the retrieved chunks, and the quoted
       text is independently re-anchored into that chunk's own text so
       `citation_provenance`/`arithmetic_integrity` (evals/scorers.py) always
       see a real, in-corpus quote span rather than an invented one.
    3. `record_answer` / `flag_outstanding` (backend/app/tools.py) so the trace
       shape matches the agent's exactly (tool_call -> item_answer -> tool_result).

A single `plan` event (item_id=None) precedes the per-item loop so
`evals/scorers.trace_shape` (plan before every item's first retrieval) passes
for every item without a per-item plan.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Optional

from . import config, llm
from .schemas import (
    AgentVisibleItem,
    Chunk,
    Citation,
    Confidence,
    Memo,
    MemoItem,
    SubsetItem,
    Unit,
    agent_visible_item,
)
from .tools import flag_outstanding, record_answer, search_filing
from .trace import TraceWriter

_VALID_UNITS = {"USD millions", "percent", "ratio", "text", "other"}
_VALID_STATUSES = {"answered", "abstained"}

_SYSTEM_PROMPT = """You are a financial diligence analyst answering exactly one due-diligence \
checklist question about {company} from a single batch of retrieved filing excerpts. \
This is a naive retrieve-then-answer baseline: you get ONE retrieval pass, no tools, \
no follow-up searches, and no calculator -- you must reason and answer from the \
excerpts below only.

Rules:
- Base your answer strictly on the excerpts. Never use outside knowledge, never invent numbers.
- Every citation "quote" must be copied verbatim (word-for-word) from one of the excerpts below.
- Every citation "chunk_id" must be exactly one of the excerpt ids shown below.
- If the excerpts do not contain enough information to answer confidently, set \
"status" to "abstained", put the reason in "answer", and return an empty "citations" list.
- Respond with EXACTLY one JSON object matching this schema and nothing else \
(no prose, no markdown code fence):
{{
  "answer": "string -- plain-language answer, or the abstention reason",
  "value": number or null -- the primary numeric answer, if the question has one,
  "unit": "USD millions" | "percent" | "ratio" | "text" | "other",
  "status": "answered" | "abstained",
  "citations": [{{"chunk_id": "string, one of the excerpt ids below", "quote": "verbatim excerpt text"}}],
  "confidence": {{"grounded_inputs": integer, "assumed_inputs": integer}}
}}"""


def _excerpts_block(chunks: list[Chunk]) -> str:
    parts = []
    for c in chunks:
        parts.append(f"[{c.chunk_id}] ({c.doc_name}, p.{c.page}):\n{c.text}")
    return "\n\n".join(parts)


def _clean_json_text(text: str) -> str:
    cleaned = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON object found in model output")
    return cleaned[start : end + 1]


def _ask_llm(item: AgentVisibleItem, chunks: list[Chunk]) -> Optional[dict[str, Any]]:
    """One LLM call over the retrieved excerpts. Retries once on unparsable JSON."""
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT.format(company=item.company)},
        {
            "role": "user",
            "content": f"Question: {item.question}\n\nRetrieved excerpts:\n{_excerpts_block(chunks)}",
        },
    ]
    for attempt in range(2):
        try:
            text = llm.chat_text(messages, json_mode=True)
            obj = json.loads(_clean_json_text(text))
            if not isinstance(obj, dict):
                raise ValueError("model output is not a JSON object")
            return obj
        except (json.JSONDecodeError, ValueError) as exc:
            if attempt == 0:
                messages.append({"role": "assistant", "content": text})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"That was not valid JSON ({exc}). Respond again with EXACTLY one JSON "
                            "object matching the schema above, no prose, no code fence."
                        ),
                    }
                )
                continue
            return None
    return None


def _locate_quote(chunk: Chunk, proposed_quote: str) -> tuple[str, int, int]:
    """Re-anchor a model-proposed quote inside its chunk's own text.

    Returns (quote, char_start, char_end) as PAGE offsets (matching `Chunk.char_start`
    / `char_end`, which are already page-relative -- see ingest.py). Falls back to the
    whole chunk span when the proposed quote isn't found verbatim, so every citation
    this baseline emits always points at a real, in-corpus quote span.
    """
    proposed = (proposed_quote or "").strip()
    if proposed:
        idx = chunk.text.find(proposed)
        if idx != -1:
            start = chunk.char_start + idx
            return proposed, start, start + len(proposed)
    return chunk.text, chunk.char_start, chunk.char_end


def _coerce_unit(value: Any) -> Unit:
    return value if value in _VALID_UNITS else "text"


def _coerce_value(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _process_item(trace: TraceWriter, company: str, item: AgentVisibleItem) -> MemoItem:
    """Single retrieve-then-answer pass for one checklist item."""
    events_before = len(trace.events)
    try:
        chunks = search_filing(trace, company, item.question, k=config.RETRIEVAL_DEFAULT_K, item_id=item.item_id)
    except Exception as exc:  # noqa: BLE001 -- retrieval failure is a valid abstention reason
        reason = f"Retrieval failed: {exc}"
        flag_outstanding(trace, item.item_id, reason, question=item.question)
        return _abstained_memo_item(item, reason)

    retrieval_event = next((e for e in trace.events[events_before:] if e.type == "retrieval"), None)
    source_event_seq = retrieval_event.seq if retrieval_event else None

    if not chunks:
        reason = "No relevant filing excerpts were retrieved for this question."
        flag_outstanding(trace, item.item_id, reason, question=item.question)
        return _abstained_memo_item(item, reason)

    chunk_by_id = {c.chunk_id: c for c in chunks}

    try:
        obj = _ask_llm(item, chunks)
    except Exception as exc:  # noqa: BLE001 -- any LLM-call failure is a valid abstention reason
        obj = None
        trace.emit(
            type="error",
            title="baseline LLM call failed",
            detail=str(exc),
            item_id=item.item_id,
            payload={"message": str(exc), "recoverable": True, "where": "agent_loop"},
        )

    if obj is None:
        reason = "Model response could not be parsed into a valid answer."
        trace.emit(
            type="error",
            title="baseline answer unparsable",
            detail=reason,
            item_id=item.item_id,
            payload={"message": reason, "recoverable": True, "where": "agent_loop"},
        )
        flag_outstanding(trace, item.item_id, reason, question=item.question)
        return _abstained_memo_item(item, reason)

    status = obj.get("status") if obj.get("status") in _VALID_STATUSES else "answered"
    if status == "abstained":
        reason = str(obj.get("answer") or "Model abstained: insufficient evidence.")
        flag_outstanding(trace, item.item_id, reason, question=item.question)
        return _abstained_memo_item(item, reason)

    citations: list[Citation] = []
    for i, raw_citation in enumerate(obj.get("citations") or [], start=1):
        if not isinstance(raw_citation, dict):
            continue
        chunk = chunk_by_id.get(raw_citation.get("chunk_id"))
        if chunk is None:
            continue  # hallucinated chunk_id -- drop rather than fabricate provenance
        quote, char_start, char_end = _locate_quote(chunk, str(raw_citation.get("quote") or ""))
        citation = Citation(
            citation_id=f"citation_{item.item_id}_{i:03d}",
            claim=item.question,
            doc_id=chunk.doc_id,
            doc_name=chunk.doc_name,
            doc_type=chunk.doc_type,
            filing_period=chunk.filing_period,
            pdf_page=chunk.page,
            page_label=str(chunk.page),
            chunk_id=chunk.chunk_id,
            quote=quote,
            char_start=char_start,
            char_end=char_end,
            source_event_seq=source_event_seq,
        )
        trace.emit(
            type="citation",
            title="Cite retrieved excerpt",
            detail=quote[:200],
            item_id=item.item_id,
            payload=citation.model_dump(),
        )
        citations.append(citation)

    if not citations:
        reason = "Model produced no citation grounded in the retrieved excerpts."
        flag_outstanding(trace, item.item_id, reason, question=item.question)
        return _abstained_memo_item(item, reason)

    raw_confidence = obj.get("confidence") if isinstance(obj.get("confidence"), dict) else {}
    confidence = Confidence(
        grounded_inputs=len(citations),
        assumed_inputs=max(0, int(raw_confidence.get("assumed_inputs") or 0)),
    )
    answer_text = str(obj.get("answer") or "").strip() or "No answer text produced."

    record_answer(
        trace,
        {
            "item_id": item.item_id,
            "question": item.question,
            "answer": answer_text,
            "value": _coerce_value(obj.get("value")),
            "unit": _coerce_unit(obj.get("unit")),
            "citations": [c.model_dump() for c in citations],
            "status": "answered",
            "confidence": confidence.model_dump(),
        },
    )
    return MemoItem(
        item_id=item.item_id,
        question=item.question,
        answer=answer_text,
        value=_coerce_value(obj.get("value")),
        unit=_coerce_unit(obj.get("unit")),
        citations=citations,
        status="answered",
        confidence=confidence,
    )


def _abstained_memo_item(item: AgentVisibleItem, reason: str) -> MemoItem:
    return MemoItem(
        item_id=item.item_id,
        question=item.question,
        answer=reason,
        value=None,
        unit="text",
        citations=[],
        status="abstained",
        confidence=Confidence(grounded_inputs=0, assumed_inputs=0),
    )


def _load_company_items(company: str, item_ids: Optional[list[str]]) -> list[AgentVisibleItem]:
    raw = json.loads(config.SUBSET_PATH.read_text())
    rows = raw if isinstance(raw, list) else raw.get("items", [])
    wanted = set(item_ids) if item_ids else None
    items = [
        agent_visible_item(SubsetItem.model_validate(row))
        for row in rows
        if row.get("company") == company and (wanted is None or row.get("item_id") in wanted)
    ]
    return items


def _render_memo_md(memo: Memo) -> str:
    lines = [
        f"# Diligence Memo -- {memo.company}",
        "",
        f"Run: `{memo.run_id}` | Status: **{memo.status}** | "
        f"Created: {memo.created_at} | Completed: {memo.completed_at or '-'}",
        "",
        "## Summary",
        "",
        f"- Items answered: {memo.summary.items_answered}/{memo.summary.items_total}",
        f"- Items abstained: {memo.summary.items_abstained}",
        f"- Citations: {memo.summary.citations_total}",
        f"- Calculate calls: {memo.summary.calculate_calls}",
        "",
        "## Items",
        "",
    ]
    for item in memo.items:
        lines.append(f"### {item.item_id} -- {item.question}")
        lines.append("")
        lines.append(f"**Status:** {item.status}")
        lines.append("")
        lines.append(f"**Answer:** {item.answer}")
        if item.value is not None:
            lines.append("")
            lines.append(f"**Value:** {item.value} ({item.unit})")
        if item.citations:
            lines.append("")
            lines.append("**Citations:**")
            for c in item.citations:
                quote = re.sub(r"\s+", " ", c.quote).strip()
                lines.append(f"- [{c.citation_id}] {c.doc_name}, p.{c.pdf_page}: \"{quote}\"")
        lines.append("")
    return "\n".join(lines) + "\n"


def run_baseline(run_id: str, company: str, item_ids: list[str] | None, trace: TraceWriter) -> None:
    """Single retrieve-then-answer pass per checklist item, streaming events into `trace`."""
    created_at = datetime.now(timezone.utc).isoformat()
    items = _load_company_items(company, item_ids)

    trace.emit(
        type="plan",
        title="Plan the baseline pass",
        detail=(
            f"Naive-RAG baseline: one retrieval + one answer call per item, "
            f"across {len(items)} checklist item(s) for {company}."
        ),
        item_id=None,
        payload={
            "items": [
                {
                    "item_id": item.item_id,
                    "question": item.question,
                    "strategy": "single_lookup",
                    "planned_inputs": [item.question],
                }
                for item in items
            ]
        },
    )

    memo_items: list[MemoItem] = []
    for item in items:
        try:
            memo_items.append(_process_item(trace, company, item))
        except Exception as exc:  # noqa: BLE001 -- one item's failure must not sink the run
            reason = f"Unexpected baseline error: {exc}"
            trace.emit(
                type="error",
                title="baseline item failed",
                detail=reason,
                item_id=item.item_id,
                payload={"message": reason, "recoverable": True, "where": "agent_loop"},
            )
            memo_items.append(_abstained_memo_item(item, reason))

    items_answered = sum(1 for m in memo_items if m.status == "answered")
    items_abstained = sum(1 for m in memo_items if m.status == "abstained")
    citations_total = sum(len(m.citations) for m in memo_items)

    memo = Memo(
        run_id=run_id,
        company=company,
        status="completed",
        created_at=created_at,
        completed_at=datetime.now(timezone.utc).isoformat(),
        items=memo_items,
        summary={
            "items_total": len(memo_items),
            "items_answered": items_answered,
            "items_abstained": items_abstained,
            "citations_total": citations_total,
            "calculate_calls": 0,
        },
    )

    (trace.run_dir / "memo.json").write_text(memo.model_dump_json(indent=2) + "\n")
    (trace.run_dir / "memo.md").write_text(_render_memo_md(memo))

    trace.emit(
        type="verdict",
        title="Run complete",
        detail=f"{items_answered}/{len(memo_items)} items answered.",
        item_id=None,
        payload={
            "memo_path": f"runs/{run_id}/memo.json",
            "summary_stats": memo.summary.model_dump(),
        },
    )
    trace.close()


__all__ = ["run_baseline"]

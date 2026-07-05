"""Agent tools (spec section 13, Step 11).

Five tools, all emitting trace events per spec sections 9-12:
    search_filing     tool_call -> retrieval -> tool_result
    get_pages         tool_call -> retrieval -> tool_result
    calculate         tool_call -> tool_result
    record_answer     tool_call -> item_answer -> tool_result
    flag_outstanding  decision -> tool_call -> item_answer -> tool_result

The LLM NEVER performs arithmetic — every derived number comes from `calculate`,
which uses a restricted AST evaluator (NO eval, NO imports, NO mutation, NO
ungrounded inputs -- every named input must carry a citation_id).

Trace event payload shapes here are load-bearing: `evals/scorers.py` and the
frontend both read `tool_call.payload.tool`, `tool_result.payload.{tool,output}`,
`retrieval.payload.chunks[].chunk_id`, and `item_answer.payload` directly, so
these must match evals/fixtures/*/trace.jsonl exactly (see e.g.
evals/fixtures/correct_calculation/trace.jsonl).

`citation` events are NOT emitted by these tools -- deciding which retrieved
chunk supports which claim is an agent-loop concern (Step 12), not a tool
concern; a citation always references a `chunk_id` that appeared in some prior
`retrieval` event these tools already emitted.

Every tool takes the run's `TraceWriter` (and, where relevant, the run's
`item_id`) as an explicit argument rather than relying on ambient state, so each
is independently callable/testable (spec section 25 Step 11 acceptance
criteria: "each tool callable standalone").
"""

from __future__ import annotations

import ast
import json
import operator
import re
from pathlib import Path
from typing import Any, Optional

from pydantic import ValidationError

from . import config
from .ingest import slugify
from .retrieval import IndexNotFoundError, search
from .schemas import CalculationResult, Chunk, Citation, Confidence, FinancialInput, ItemAnswer
from .trace import TraceWriter

SNIPPET_CHARS = 240

__all__ = [
    "search_filing",
    "get_pages",
    "calculate",
    "compute_calculation",
    "recompute_check",
    "record_answer",
    "flag_outstanding",
]


def _snippet(text: str, limit: int = SNIPPET_CHARS) -> str:
    """Collapse whitespace and truncate to a short human-readable preview."""
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 1].rstrip() + "…"


def _chunk_trace_payload(chunk: Chunk) -> dict[str, Any]:
    """Reduced chunk shape used in `retrieval`/`tool_result` payloads (spec section
    11: chunk_id, company, doc_id, doc_name, doc_type, filing_period, page, score,
    snippet) -- distinct from the full `Chunk` (which also carries text/char
    offsets) returned to the caller."""
    return {
        "chunk_id": chunk.chunk_id,
        "company": chunk.company,
        "doc_id": chunk.doc_id,
        "doc_name": chunk.doc_name,
        "doc_type": chunk.doc_type,
        "filing_period": chunk.filing_period,
        "page": chunk.page,
        "score": round(float(chunk.score), 4),
        "snippet": _snippet(chunk.text),
    }


# --- search_filing -----------------------------------------------------------
def search_filing(
    trace: TraceWriter,
    company: str,
    query: str,
    k: int = 6,
    doc_filter: Optional[list[str]] = None,
    item_id: Optional[str] = None,
) -> list[Chunk]:
    """Cosine search over the run's company corpus (spec section 13).

    Emits `tool_call` -> `retrieval` -> `tool_result`, in that order (section 12).
    Returns the full `Chunk` list (with `text`/`char_start`/`char_end`) so the
    caller can build citations; the trace payloads carry a reduced snippet form.
    """
    tool_input: dict[str, Any] = {"query": query, "k": k}
    if doc_filter:
        tool_input["doc_filter"] = doc_filter
    trace.emit(
        type="tool_call",
        title="search_filing",
        detail=f"Search the filing corpus for: {query!r}",
        item_id=item_id,
        payload={"tool": "search_filing", "input": tool_input},
    )
    try:
        chunks = search(company, query, k=k, doc_filter=doc_filter)
    except IndexNotFoundError as exc:
        message = str(exc)
        trace.emit(
            type="error",
            title="search_filing failed",
            detail=message,
            item_id=item_id,
            payload={"message": message, "recoverable": True, "where": "tool"},
        )
        raise

    chunk_payloads = [_chunk_trace_payload(c) for c in chunks]
    trace.emit(
        type="retrieval",
        title="Retrieval results",
        detail=f"Found {len(chunks)} chunk(s) for '{query}'.",
        item_id=item_id,
        payload={"query": query, "k": k, "chunks": chunk_payloads},
    )
    trace.emit(
        type="tool_result",
        title="search_filing result",
        detail=f"Returned {len(chunks)} chunk(s).",
        item_id=item_id,
        payload={"tool": "search_filing", "output": {"chunks": chunk_payloads}},
    )
    return chunks


# --- get_pages -----------------------------------------------------------
def _pages_path(company: str, doc_id: str) -> Path:
    return config.PAGES_DIR / slugify(company) / f"{doc_id}.json"


def get_pages(
    trace: TraceWriter,
    company: str,
    doc_id: str,
    pages: list[int],
    item_id: Optional[str] = None,
) -> dict[str, Any]:
    """Return raw page text for targeted reads (spec section 13).

    Used after `search_filing` localizes a relevant page or table. Emits
    `tool_call` -> `retrieval` -> `tool_result`: each fetched page is surfaced as
    a citable synthetic chunk ("page:<doc_id>:<page>") in the `retrieval` event so
    a quote copied from a full-page read can be cited with valid provenance
    (IMP-1), not just the narrower search_filing chunks.
    """
    trace.emit(
        type="tool_call",
        title="get_pages",
        detail=f"Read page(s) {pages} of {doc_id}.",
        item_id=item_id,
        payload={"tool": "get_pages", "input": {"doc_id": doc_id, "pages": pages}},
    )
    path = _pages_path(company, doc_id)
    if not path.exists():
        message = f"get_pages: no parsed pages for doc_id '{doc_id}' (company '{company}') under {path}"
        trace.emit(
            type="error",
            title="get_pages failed",
            detail=message,
            item_id=item_id,
            payload={"message": message, "recoverable": True, "where": "tool"},
        )
        raise FileNotFoundError(message)

    doc = json.loads(path.read_text())
    doc_name = doc.get("doc_name") or doc_id
    text_by_page = {p["page"]: (p.get("text") or "") for p in doc.get("pages", [])}
    # Each fetched page is exposed with a synthetic, citable `chunk_id`
    # ("page:<doc_id>:<page>") so a quote copied from a full-page read can be
    # cited just like a search chunk (IMP-1). We emit a `retrieval` event
    # carrying those chunk_ids -- evals/scorers.py::_retrieval_chunk_ids only
    # scans `retrieval` events, so this is what keeps citation_provenance passing
    # for page-sourced citations. The agent loop resolves the offsets against the
    # full page text it registers in item state.
    result_pages = [
        {"chunk_id": f"page:{doc_id}:{p}", "page": p, "text": text_by_page.get(p, "")}
        for p in pages
    ]
    chunk_payloads = [
        {
            "chunk_id": rp["chunk_id"],
            "company": company,
            "doc_id": doc_id,
            "doc_name": doc_name,
            "page": rp["page"],
            "score": 0.0,
            "snippet": _snippet(rp["text"]),
        }
        for rp in result_pages
    ]
    trace.emit(
        type="retrieval",
        title="Page read results",
        detail=f"Read {len(result_pages)} page(s) of {doc_id} as citable chunk(s).",
        item_id=item_id,
        payload={"query": f"get_pages:{doc_id}", "k": len(result_pages), "chunks": chunk_payloads},
    )
    output = {"doc_id": doc_id, "doc_name": doc_name, "pages": result_pages}
    trace.emit(
        type="tool_result",
        title="get_pages result",
        detail=f"Returned {len(result_pages)} page(s) from {doc_id}.",
        item_id=item_id,
        payload={"tool": "get_pages", "output": output},
    )
    return output


# --- calculate: safe arithmetic (spec section 13: allowed ops only) ---------
_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def _safe_eval(node: ast.AST, names: dict[str, float]) -> float:
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body, names)
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        return _BIN_OPS[type(node.op)](_safe_eval(node.left, names), _safe_eval(node.right, names))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_safe_eval(node.operand, names))
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.Name):
        if node.id not in names:
            raise ValueError(f"ungrounded input value: '{node.id}'")
        return names[node.id]
    raise ValueError(f"disallowed expression node: {type(node).__name__}")


def _round(value: float, rounding: Optional[str]) -> float:
    if rounding and rounding.endswith("dp"):
        try:
            return round(value, int(rounding[:-2]))
        except ValueError:
            pass
    return value


def compute_calculation(
    expression: str,
    inputs: dict[str, FinancialInput | dict[str, Any]],
    rounding: Optional[str] = None,
) -> CalculationResult:
    """Deterministically evaluate a financial expression over grounded inputs.

    Pure (no trace emission) -- the `calculate` tool below wraps this with
    tool_call/tool_result events. Kept separate so evals/tests can exercise the
    arithmetic itself without needing a `TraceWriter`.

    Forbidden (spec section 13): eval, imports, arbitrary Python, mutation,
    hidden unit conversion, ungrounded input values. Every name in `expression`
    must be a key in `inputs`, and every input must carry a `citation_id`.
    """
    parsed = {k: (v if isinstance(v, FinancialInput) else FinancialInput(**v)) for k, v in inputs.items()}
    for name, fin in parsed.items():
        if not fin.citation_id:
            raise ValueError(f"input '{name}' is missing citation_id (ungrounded)")
    names = {k: float(v.value) for k, v in parsed.items()}
    tree = ast.parse(expression, mode="eval")
    value = _round(_safe_eval(tree, names), rounding)
    return CalculationResult(expression=expression, inputs=parsed, value=value, rounding=rounding)


def calculate(
    trace: TraceWriter,
    expression: str,
    inputs: dict[str, FinancialInput | dict[str, Any]],
    rounding: Optional[str] = None,
    item_id: Optional[str] = None,
) -> CalculationResult:
    """The LLM never performs arithmetic directly -- this is the only path to a
    derived number (spec section 13). Emits `tool_call` -> `tool_result`."""
    input_payload = {
        "expression": expression,
        "inputs": {k: (v if isinstance(v, dict) else v.model_dump()) for k, v in inputs.items()},
        "rounding": rounding,
    }
    trace.emit(
        type="tool_call",
        title="calculate",
        detail=f"Evaluate: {expression}",
        item_id=item_id,
        payload={"tool": "calculate", "input": input_payload},
    )
    try:
        result = compute_calculation(expression, inputs, rounding)
    except (ValueError, SyntaxError) as exc:
        message = f"calculate: {exc}"
        trace.emit(
            type="error",
            title="calculate failed",
            detail=message,
            item_id=item_id,
            payload={"message": message, "recoverable": True, "where": "tool"},
        )
        raise
    trace.emit(
        type="tool_result",
        title="calculate result",
        detail=f"{expression} = {result.value}",
        item_id=item_id,
        payload={"tool": "calculate", "output": result.model_dump()},
    )
    return result


# --- derivation self-check (IMP3-4) -----------------------------------------
# Terms the acid-test / quick-ratio numerator explicitly EXCLUDES. amd_01's
# repeatable defect was computing the quick ratio as (current_assets -
# inventory)/current_liabilities; the canonical numerator is cash + short-term
# investments + receivables and never subtracts inventory/prepaids inside it.
_ACID_TEST_EXCLUDED_TERMS = ("inventor", "prepaid")


def recompute_check(
    expression: str,
    input_values: dict[str, float],
    recorded_value: Optional[float],
    unit: Optional[str],
    *,
    relative_tol: float = 0.02,
) -> list[str]:
    """Re-derive a recorded ratio/percentage from the model's OWN calculate
    expression + SIGNED inputs and return human-readable warnings on
    disagreement (IMP3-4 change 5).

    Pure (no trace emission) so it is unit-testable standalone, matching this
    module's "each tool callable standalone" philosophy. The agent loop surfaces
    each warning as a soft trace note and STILL records the answer: the plan's
    risk guard mandates flag, not hard-reject, so a heuristic mismatch can never
    manufacture a false abstention. It targets the two confirmed-repeatable iter2
    derivation defects:

      * wrong quick/acid-test numerator (amd_01, verizon_02) -- the acid test is
        (cash + short-term investments + receivables)/current_liabilities and
        EXCLUDES inventory/prepaids, so an inventory/prepaid input SUBTRACTED
        inside a ratio numerator is the (current_assets - inventory)/CL form,
        which overstates the ratio (amd_01: 1.77 vs gold 1.57).
      * sign-flip on a signed rate (boeing_06) -- FY2021 effective tax rate is
        gold -14.76% because pretax income was negative; re-evaluating the
        division over the signed inputs yields a negative value whose sign
        disagrees with a recorded +14.7%.
    """
    warnings: list[str] = []
    if unit not in ("ratio", "percent"):
        return warnings

    # (a) canonical-numerator check -- a subtracted inventory/prepaid input inside
    # a ratio is the (current_assets - inventory)/CL anti-pattern, not the
    # acid-test numerator. Static on the expression string, so it fires even when
    # the arithmetic itself is internally consistent.
    for name in input_values:
        if any(term in name.lower() for term in _ACID_TEST_EXCLUDED_TERMS) and re.search(
            rf"-\s*{re.escape(name)}\b", expression
        ):
            warnings.append(
                f"input '{name}' is subtracted inside a ratio numerator; the quick/acid-test "
                "ratio is (cash + short-term investments + receivables)/current_liabilities and "
                "EXCLUDES inventory/prepaids -- it is NOT (current_assets - inventory)/CL. "
                "Confirm the numerator matches the canonical definition."
            )
            break

    # (b) sign / magnitude re-derivation over the SIGNED inputs.
    if recorded_value is None:
        return warnings
    try:
        recomputed = _safe_eval(
            ast.parse(expression, mode="eval"), {k: float(v) for k, v in input_values.items()}
        )
    except (ValueError, SyntaxError, TypeError):
        return warnings  # unparseable/ungrounded -> nothing to compare against
    if recomputed == 0 or recorded_value == 0:
        return warnings
    if (recomputed > 0) != (recorded_value > 0):
        warnings.append(
            f"recorded value {recorded_value} disagrees in SIGN with the value re-derived from the "
            f"calculate expression over the signed inputs ({recomputed:g}); when pretax income (or "
            "another denominator) is negative the ratio/rate must PRESERVE that sign -- do not flip "
            "it to positive."
        )
    else:
        # A percent answer may legitimately be recorded as a percentage (fraction
        # * 100) or as a fraction; accept either scale for the magnitude check.
        # The sign check above still applies -- ×100 preserves sign.
        scales = (recomputed, recomputed * 100.0, recomputed / 100.0)
        if not any(abs(recorded_value - s) / (abs(s) or 1.0) <= relative_tol for s in scales):
            warnings.append(
                f"recorded value {recorded_value} does not match the value re-derived from the "
                f"calculate expression over its inputs ({recomputed:g}); recompute or confirm the "
                "reported figure is drawn from that calculation."
            )
    return warnings


# --- record_answer -----------------------------------------------------------
def record_answer(trace: TraceWriter, item_answer: ItemAnswer | dict[str, Any]) -> dict[str, Any]:
    """Validate and record the final answer for a checklist item (spec section 13).

    Emits `tool_call` -> `item_answer` -> `tool_result`. Validates against the
    `ItemAnswer` schema; an invalid payload emits `error` (still satisfying the
    "tool_call must be followed by tool_result or error" ordering rule) and
    raises so the agent loop can force-abstain the item.
    """
    raw = item_answer if isinstance(item_answer, dict) else item_answer.model_dump()
    item_id = raw.get("item_id")
    trace.emit(
        type="tool_call",
        title="record_answer",
        detail=f"Record answer for {item_id}.",
        item_id=item_id,
        payload={"tool": "record_answer", "input": raw},
    )
    try:
        answer = item_answer if isinstance(item_answer, ItemAnswer) else ItemAnswer(**item_answer)
    except ValidationError as exc:
        message = f"record_answer: invalid item_answer: {exc}"
        trace.emit(
            type="error",
            title="record_answer failed",
            detail=message,
            item_id=item_id,
            payload={"message": message, "recoverable": True, "where": "tool"},
        )
        raise

    trace.emit(
        type="item_answer",
        title="Item answer",
        detail=answer.answer[:200],
        item_id=answer.item_id,
        payload=answer.model_dump(),
    )
    ack = {"ok": True}
    trace.emit(
        type="tool_result",
        title="record_answer ack",
        detail="Answer accepted.",
        item_id=answer.item_id,
        payload={"tool": "record_answer", "output": ack},
    )
    return ack


# --- flag_outstanding ---------------------------------------------------------
def flag_outstanding(
    trace: TraceWriter,
    item_id: str,
    reason: str,
    citations: Optional[list[Citation | dict[str, Any]]] = None,
    question: Optional[str] = None,
) -> dict[str, Any]:
    """Explicit abstention path (spec section 13). Used when required data is
    missing, evidence is ambiguous, period/unit is unclear, retrieval fails, the
    max tool-call cap is reached, or model output can't be parsed.

    Emits `decision` -> `tool_call` -> `item_answer` (status=abstained) ->
    `tool_result`.
    """
    normalized_citations = [c if isinstance(c, Citation) else Citation(**c) for c in (citations or [])]

    trace.emit(
        type="decision",
        title="Abstain",
        detail=reason,
        item_id=item_id,
        payload={"kind": "abstention", "text": reason},
    )
    trace.emit(
        type="tool_call",
        title="flag_outstanding",
        detail=f"Flag {item_id} as outstanding.",
        item_id=item_id,
        payload={
            "tool": "flag_outstanding",
            "input": {
                "item_id": item_id,
                "reason": reason,
                "citations": [c.model_dump() for c in normalized_citations] or None,
            },
        },
    )
    answer = ItemAnswer(
        item_id=item_id,
        question=question,
        answer=reason,
        value=None,
        unit="text",
        citations=normalized_citations,
        status="abstained",
        confidence=Confidence(grounded_inputs=0, assumed_inputs=0),
    )
    trace.emit(
        type="item_answer",
        title="Item answer (abstained)",
        detail=reason,
        item_id=item_id,
        payload=answer.model_dump(),
    )
    ack = {"ok": True}
    trace.emit(
        type="tool_result",
        title="flag_outstanding ack",
        detail="Abstention recorded.",
        item_id=item_id,
        payload={"tool": "flag_outstanding", "output": ack},
    )
    return ack

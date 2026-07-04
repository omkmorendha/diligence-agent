"""Agent tools (spec section 13).

Five tools: search_filing, get_pages, calculate, record_answer, flag_outstanding.
Every tool emits trace events. Only `calculate` is implemented here in v0 scaffold
form because it is on the never-cut list (spec section 26) and is fully
self-contained; the rest are stubbed until their build steps.

The LLM NEVER performs arithmetic — every derived number comes from `calculate`.
`calculate` uses a restricted AST evaluator (NO eval, NO imports, NO mutation).
"""

from __future__ import annotations

import ast
import operator
from typing import Any

from .schemas import CalculationResult, FinancialInput

# --- safe arithmetic (spec section 13: allowed ops only) ---
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


def _round(value: float, rounding: str | None) -> float:
    if rounding and rounding.endswith("dp"):
        try:
            return round(value, int(rounding[:-2]))
        except ValueError:
            pass
    return value


def calculate(
    expression: str,
    inputs: dict[str, FinancialInput | dict[str, Any]],
    rounding: str | None = None,
) -> CalculationResult:
    """Deterministically evaluate a financial expression over grounded inputs.

    Forbidden (spec section 13): eval, imports, arbitrary Python, mutation, hidden
    unit conversion, ungrounded input values. Every name in `expression` must be a
    key in `inputs`, and every input must carry a citation_id.
    """
    parsed = {k: (v if isinstance(v, FinancialInput) else FinancialInput(**v)) for k, v in inputs.items()}
    for name, fin in parsed.items():
        if not fin.citation_id:
            raise ValueError(f"input '{name}' is missing citation_id (ungrounded)")
    names = {k: float(v.value) for k, v in parsed.items()}
    tree = ast.parse(expression, mode="eval")
    value = _round(_safe_eval(tree, names), rounding)
    return CalculationResult(expression=expression, inputs=parsed, value=value, rounding=rounding)


# --- stubs (emit trace events when built; see spec section 13) ---
def search_filing(query: str, k: int = 6, doc_filter: list[str] | None = None):  # noqa: ARG001
    raise NotImplementedError("search_filing: implement in Step 11 (spec section 13).")


def get_pages(doc_id: str, pages: list[int]):  # noqa: ARG001
    raise NotImplementedError("get_pages: implement in Step 11 (spec section 13).")


def record_answer(item_answer: Any):  # noqa: ARG001
    raise NotImplementedError("record_answer: implement in Step 11 (spec section 13).")


def flag_outstanding(item_id: str, reason: str, citations: list[Any] | None = None):  # noqa: ARG001
    raise NotImplementedError("flag_outstanding: implement in Step 11 (spec section 13).")

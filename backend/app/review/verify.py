"""S4 — Verification fan-out (spec sections 1.5, 1.7, 8).

FROZEN CONTRACT — signature must not change.

Each surviving claim runs through the v0 agent loop (`_run_item`) with the claim's
derived question as an ad-hoc item for its company; the prompt includes the claim's
quoted value so the agent compares rather than merely answers. Verdict mapping is
deterministic (v0 ±1% tolerance rule). `NOT_IN_CORPUS` requires the exhausted-search
budget (>= NOT_IN_CORPUS_MIN_QUERIES). Fan-out uses a `ThreadPoolExecutor` with
`workers` threads, backoff on 429/5xx, and the run-scoped usage sink + trace emitter.
"""

from __future__ import annotations

import re
import time
from concurrent.futures import Future, ThreadPoolExecutor, wait
from typing import Any, Optional

from .. import agent, config
from ..schemas import (
    AgentVisibleItem,
    CalculationResult,
    Claim,
    ClaimValue,
    ItemAnswer,
    VerificationResult,
    Verdict,
)
from . import registry

_MAX_VERIFY_ATTEMPTS = 3
_BACKOFF_BASE_S = 0.25

_NUMBER_RE = re.compile(
    r"(?P<prefix>\$)?(?P<number>[-+]?\d[\d,]*(?:\.\d+)?)\s*"
    r"(?P<suffix>%|percent|percentage|billion|bn|b|million|mm|m)?",
    re.IGNORECASE,
)


def verify_claims(
    review_id: str,
    claims: list[Claim],
    trace: Any,
    workers: int,
) -> list[VerificationResult]:
    """Verify claims concurrently through the v0 agent; return one result per claim."""
    reg = registry.corpus_registry()
    timeout_s = max(0.0, float(config.REVIEW_TIMEOUT_S))
    deadline = time.monotonic() + timeout_s
    max_workers = max(1, int(workers or config.REVIEW_WORKERS))

    results_by_claim: dict[str, VerificationResult] = {}
    pending: list[Claim] = []

    for claim in claims:
        scoped_result = _scoped_result(claim, reg)
        if scoped_result is not None:
            claim.status = "VERIFIED"
            results_by_claim[claim.claim_id] = scoped_result
            _emit_claim_verdict(trace, claim, scoped_result)
            continue

        if claim.status == "SKIPPED":
            _emit_claim_verdict(
                trace,
                claim,
                None,
                detail="Claim skipped before verification.",
                payload={"status": "SKIPPED", "reason": "skipped"},
            )
            continue

        pending.append(claim)

    if not pending:
        return _ordered_results(claims, results_by_claim)

    executor = ThreadPoolExecutor(max_workers=min(max_workers, len(pending)))
    futures: dict[Future[VerificationResult], Claim] = {
        executor.submit(_verify_claim_with_retries, claim, trace, reg): claim
        for claim in pending
    }
    try:
        remaining_s = max(0.0, deadline - time.monotonic())
        done, not_done = wait(futures, timeout=remaining_s)

        for future in done:
            claim = futures[future]
            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001 -- one claim must not sink a review
                _mark_error(trace, claim, exc)
                continue
            claim.status = "VERIFIED"
            results_by_claim[claim.claim_id] = result
            _emit_claim_verdict(trace, claim, result)

        for future in not_done:
            claim = futures[future]
            future.cancel()
            _mark_error(trace, claim, TimeoutError(f"review {review_id} exceeded {timeout_s:g}s wall-clock cap"))
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    return _ordered_results(claims, results_by_claim)


def _ordered_results(
    claims: list[Claim],
    results_by_claim: dict[str, VerificationResult],
) -> list[VerificationResult]:
    return [results_by_claim[c.claim_id] for c in claims if c.claim_id in results_by_claim]


def _scoped_result(claim: Claim, reg: dict) -> Optional[VerificationResult]:
    verdict, explanation = registry.scope_verdict(claim, reg)
    if verdict not in ("OUT_OF_SCOPE", "UNVERIFIABLE"):
        return None
    return VerificationResult(
        claim_id=claim.claim_id,
        verdict=verdict,
        explanation=explanation,
        queries_tried=[],
        confidence="high" if verdict == "OUT_OF_SCOPE" else "medium",
    )


def _verify_claim_with_retries(claim: Claim, trace: Any, reg: dict) -> VerificationResult:
    last_error: Optional[BaseException] = None
    for attempt in range(_MAX_VERIFY_ATTEMPTS):
        try:
            return _verify_claim_once(claim, trace, reg)
        except Exception as exc:  # noqa: BLE001 -- retry classification is dynamic
            last_error = exc
            if attempt == _MAX_VERIFY_ATTEMPTS - 1 or not _is_transient(exc):
                raise
            time.sleep(_BACKOFF_BASE_S * (2**attempt))
    raise RuntimeError("verification failed") from last_error


def _verify_claim_once(claim: Claim, trace: Any, reg: dict) -> VerificationResult:
    company = registry.normalize_company(claim.company, reg) or claim.company
    visible = AgentVisibleItem(
        item_id=claim.claim_id,
        company=company,
        question=_comparison_question(claim),
    )
    item_answer = agent.run_agent_item(
        trace,
        company,
        visible,
        call_purpose="review_verification",
    )
    queries = _queries_tried(trace, claim.claim_id)
    calculation = _last_calculation(trace, claim.claim_id)
    return _map_item_answer(claim, item_answer, queries, calculation)


def _comparison_question(claim: Claim) -> str:
    doc_value = _extract_claim_value(claim.quote)
    value_line = ""
    if doc_value is not None and doc_value.value is not None:
        unit = f" {doc_value.unit}" if doc_value.unit else ""
        value_line = f"\nExtracted document value: {doc_value.value:g}{unit}."
    return (
        "Verify the uploaded-document claim below against the filing corpus. "
        "Compare the corpus evidence to the document claim; do not merely answer "
        "the question in isolation.\n\n"
        f"Document claim quote: {claim.quote!r}{value_line}\n"
        f"Derived verification question: {claim.question}"
    )


def _map_item_answer(
    claim: Claim,
    answer: ItemAnswer,
    queries_tried: list[str],
    calculation: Optional[CalculationResult],
) -> VerificationResult:
    doc_value = _extract_claim_value(claim.quote)
    corpus_value = (
        ClaimValue(value=float(answer.value), unit=answer.unit)
        if answer.value is not None
        else None
    )

    if answer.status == "abstained":
        distinct_query_count = len(_distinct(queries_tried))
        exhausted = distinct_query_count >= config.NOT_IN_CORPUS_MIN_QUERIES
        verdict: Verdict = "NOT_IN_CORPUS" if exhausted else "PARTIALLY_SUPPORTED"
        explanation = (
            "The agent abstained after exhausting the minimum distinct retrieval "
            f"budget ({distinct_query_count} queries)."
            if exhausted
            else "The agent abstained before the retrieval budget was exhausted; treating as inconclusive."
        )
        return VerificationResult(
            claim_id=claim.claim_id,
            verdict=verdict,
            doc_value=doc_value,
            corpus_value=None,
            explanation=explanation,
            citations=answer.citations,
            calculation=calculation,
            queries_tried=queries_tried,
            confidence="low",
        )

    if (
        claim.claim_type == "numeric"
        and doc_value is not None
        and doc_value.value is not None
        and corpus_value is not None
        and corpus_value.value is not None
    ):
        if _numeric_close(doc_value.value, corpus_value.value):
            verdict = "SUPPORTED"
            explanation = "The corpus value is within the configured relative tolerance of the document claim."
        else:
            verdict = "CONTRADICTED"
            explanation = "The corpus value differs from the numeric value stated in the document claim."
        return VerificationResult(
            claim_id=claim.claim_id,
            verdict=verdict,
            doc_value=doc_value,
            corpus_value=corpus_value,
            explanation=explanation,
            citations=answer.citations,
            calculation=calculation,
            queries_tried=queries_tried,
            confidence="high",
        )

    return VerificationResult(
        claim_id=claim.claim_id,
        verdict="PARTIALLY_SUPPORTED",
        doc_value=doc_value,
        corpus_value=corpus_value,
        explanation="The agent found relevant support, but no deterministic numeric comparison was computable.",
        citations=answer.citations,
        calculation=calculation,
        queries_tried=queries_tried,
        confidence="medium",
    )


def _extract_claim_value(text: str) -> Optional[ClaimValue]:
    for match in _NUMBER_RE.finditer(text or ""):
        raw_number = match.group("number")
        if not raw_number:
            continue
        try:
            value = float(raw_number.replace(",", ""))
        except ValueError:
            continue
        if _is_standalone_year(value, match.group(0)):
            continue

        suffix = (match.group("suffix") or "").lower()
        prefix = match.group("prefix")
        unit: Optional[str] = None
        if suffix in ("%", "percent", "percentage"):
            unit = "percent"
        elif suffix in ("billion", "bn", "b"):
            value *= 1000.0
            unit = "USD millions" if prefix or _has_currency_context(text) else "other"
        elif suffix in ("million", "mm", "m"):
            unit = "USD millions" if prefix or _has_currency_context(text) else "other"
        elif prefix or _has_currency_context(text):
            unit = "USD millions"

        return ClaimValue(value=value, unit=unit)
    return None


def _is_standalone_year(value: float, raw: str) -> bool:
    return (
        raw.strip().isdigit()
        and float(int(value)) == value
        and 1900 <= value <= 2100
    )


def _has_currency_context(text: str) -> bool:
    lowered = (text or "").lower()
    return "$" in lowered or "usd" in lowered or "dollar" in lowered


def _numeric_close(left: float, right: float) -> bool:
    denom = abs(right) if right != 0 else 1.0
    return abs(left - right) / denom <= config.DEFAULT_RELATIVE_TOLERANCE


def _queries_tried(trace: Any, claim_id: str) -> list[str]:
    queries: list[str] = []
    for event in _trace_events(trace):
        if getattr(event, "item_id", None) != claim_id:
            continue
        payload = getattr(event, "payload", {}) or {}
        query = None
        if getattr(event, "type", None) == "retrieval":
            query = payload.get("query")
        elif getattr(event, "type", None) == "tool_call" and payload.get("tool") == "search_filing":
            query = (payload.get("input") or {}).get("query")
        if query and not str(query).startswith("get_pages:"):
            queries.append(str(query))
    return _distinct(queries)


def _last_calculation(trace: Any, claim_id: str) -> Optional[CalculationResult]:
    for event in reversed(_trace_events(trace)):
        if getattr(event, "item_id", None) != claim_id:
            continue
        payload = getattr(event, "payload", {}) or {}
        if getattr(event, "type", None) == "tool_result" and payload.get("tool") == "calculate":
            output = payload.get("output")
            if isinstance(output, dict):
                try:
                    return CalculationResult(**output)
                except Exception:
                    return None
    return None


def _trace_events(trace: Any) -> list[Any]:
    events = getattr(trace, "events", [])
    lock = getattr(trace, "_lock", None)
    if lock is None:
        return list(events)
    with lock:
        return list(events)


def _distinct(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _is_transient(exc: BaseException) -> bool:
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    response = getattr(exc, "response", None)
    if status is None and response is not None:
        status = getattr(response, "status_code", None)
    try:
        code = int(status) if status is not None else None
    except (TypeError, ValueError):
        code = None
    if code == 429 or (code is not None and 500 <= code <= 599):
        return True
    text = str(exc).lower()
    return any(token in text for token in ("429", "rate limit", "500", "502", "503", "504", "temporarily"))


def _mark_error(trace: Any, claim: Claim, exc: BaseException) -> None:
    claim.status = "ERROR"
    _emit_claim_verdict(
        trace,
        claim,
        None,
        detail=str(exc),
        payload={"status": "ERROR", "error": f"{type(exc).__name__}: {exc}"},
    )


def _emit_claim_verdict(
    trace: Any,
    claim: Claim,
    result: Optional[VerificationResult],
    *,
    detail: Optional[str] = None,
    payload: Optional[dict[str, Any]] = None,
) -> None:
    if not hasattr(trace, "emit"):
        return
    event_payload: dict[str, Any] = {
        "claim_id": claim.claim_id,
        "status": claim.status,
    }
    if result is not None:
        event_payload.update(result.model_dump())
    if payload:
        event_payload.update(payload)
    title = "Claim verdict" if result is not None else "Claim verification status"
    event_detail = detail or (result.explanation if result is not None else claim.status)
    trace.emit(
        type="claim_verdict",
        title=title,
        detail=event_detail,
        item_id=claim.claim_id,
        payload=event_payload,
    )

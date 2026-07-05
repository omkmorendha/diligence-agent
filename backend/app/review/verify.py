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

from typing import Any

from ..schemas import Claim, VerificationResult


def verify_claims(
    review_id: str,
    claims: list[Claim],
    trace: Any,
    workers: int,
) -> list[VerificationResult]:
    """Verify claims concurrently through the v0 agent; return one result per claim."""
    raise NotImplementedError("verify_claims is a frozen stub (spec section 8)")

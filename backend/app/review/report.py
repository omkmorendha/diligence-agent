"""S5 — Report assembly (spec section 9).

FROZEN CONTRACT — signature must not change.

Deterministic assembly (no LLM call, same rule as v0 memo assembly): pair each
claim with its VerificationResult, roll up the by-verdict summary counts, and
emit a `ReviewReport`.
"""

from __future__ import annotations

from ..schemas import Claim, DocModel, ReviewReport, VerificationResult


def assemble_report(
    review_id: str,
    docmodel: DocModel,
    claims: list[Claim],
    results: list[VerificationResult],
) -> ReviewReport:
    """Combine claims + verification results into a ReviewReport with summary counts."""
    raise NotImplementedError("assemble_report is a frozen stub (spec section 9)")

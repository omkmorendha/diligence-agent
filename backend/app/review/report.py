"""S5 — Report assembly (spec section 9).

FROZEN CONTRACT — signature must not change.

Deterministic assembly (no LLM call, same rule as v0 memo assembly): pair each
claim with its VerificationResult, roll up the by-verdict summary counts, and
emit a `ReviewReport`.
"""

from __future__ import annotations

from ..schemas import (
    Claim,
    DocModel,
    ReviewReport,
    ReviewReportClaim,
    ReviewSummary,
    VerificationResult,
    Verdict,
)

# Verdict -> ReviewSummary counter field (spec section 1.6 taxonomy).
_VERDICT_FIELD: dict[Verdict, str] = {
    "SUPPORTED": "supported",
    "CONTRADICTED": "contradicted",
    "PARTIALLY_SUPPORTED": "partially_supported",
    "NOT_IN_CORPUS": "not_in_corpus",
    "OUT_OF_SCOPE": "out_of_scope",
    "UNVERIFIABLE": "unverifiable",
}


def assemble_report(
    review_id: str,
    docmodel: DocModel,
    claims: list[Claim],
    results: list[VerificationResult],
) -> ReviewReport:
    """Combine claims + verification results into a ReviewReport with summary counts."""
    result_by_id: dict[str, VerificationResult] = {r.claim_id: r for r in results}

    summary = ReviewSummary(total_claims=len(claims))
    report_claims: list[ReviewReportClaim] = []
    company_scope: list[str] = []
    seen_companies: set[str] = set()

    for claim in claims:
        if claim.company and claim.company not in seen_companies:
            seen_companies.add(claim.company)
            company_scope.append(claim.company)

        result = result_by_id.get(claim.claim_id)
        report_claims.append(ReviewReportClaim(claim=claim, result=result))

        # Claim-status buckets (SKIPPED by cap, ERROR after retries) are counted
        # off the claim, not a verdict — those claims carry no VerificationResult.
        if claim.status == "SKIPPED":
            summary.skipped += 1
            continue
        if claim.status == "ERROR":
            summary.error += 1
            continue
        if result is not None:
            field = _VERDICT_FIELD.get(result.verdict)
            if field is not None:
                setattr(summary, field, getattr(summary, field) + 1)

    return ReviewReport(
        review_id=review_id,
        filename=docmodel.filename,
        format=docmodel.format,
        company_scope=company_scope,
        summary=summary,
        claims=report_claims,
    )

"""Round-trip tests for the v1 review schemas (spec section 10)."""

from __future__ import annotations

from app.schemas import (
    Citation,
    Claim,
    ClaimAnchor,
    ClaimValue,
    CreateReviewResponse,
    DocBlock,
    DocModel,
    ReviewCard,
    ReviewReport,
    ReviewReportClaim,
    ReviewStatusResponse,
    ReviewSummary,
    TraceEvent,
    VerificationResult,
)


def test_docmodel_round_trip() -> None:
    dm = DocModel(
        doc_id="review_amd_memo_123",
        format="md",
        filename="amd_memo.md",
        canonical_text="AMD revenue was $6.6B in FY2015.",
        blocks=[
            DocBlock(text="AMD revenue was $6.6B in FY2015.", char_start=0, char_end=32, line_start=1),
        ],
    )
    assert DocModel.model_validate(dm.model_dump()) == dm


def test_claim_and_anchor_round_trip() -> None:
    claim = Claim(
        claim_id="c01",
        quote="AMD revenue was $6.6B in FY2015.",
        claim_type="numeric",
        company="AMD",
        period="FY2015",
        metric="revenue",
        question="What was AMD's revenue in FY2015?",
        priority=1,
        status="SKIPPED",
        anchor=ClaimAnchor(quote="AMD revenue was $6.6B in FY2015.", char_start=0, char_end=32, page=1),
    )
    assert Claim.model_validate(claim.model_dump()) == claim


def test_verification_result_reuses_citation() -> None:
    cite = Citation(
        citation_id="cit1",
        doc_id="amd_2015_10k",
        doc_name="AMD 2015 10-K",
        pdf_page=42,
        chunk_id="amd:amd_2015_10k:p42:c0",
        quote="Net revenue was $3,991 million.",
        char_start=0,
        char_end=31,
    )
    vr = VerificationResult(
        claim_id="c01",
        verdict="CONTRADICTED",
        doc_value=ClaimValue(value=6600.0, unit="USD millions"),
        corpus_value=ClaimValue(value=3991.0, unit="USD millions"),
        explanation="The 10-K reports $3,991M, not $6.6B.",
        citations=[cite],
        queries_tried=["AMD FY2015 net revenue"],
        confidence="high",
    )
    assert VerificationResult.model_validate(vr.model_dump()) == vr


def test_review_report_and_dtos_round_trip() -> None:
    report = ReviewReport(
        review_id="review_amd_memo_123",
        filename="amd_memo.md",
        format="md",
        company_scope=["AMD"],
        summary=ReviewSummary(total_claims=1, contradicted=1),
        claims=[
            ReviewReportClaim(
                claim=Claim(
                    claim_id="c01",
                    quote="AMD revenue was $6.6B in FY2015.",
                    claim_type="numeric",
                    company="AMD",
                    question="What was AMD's revenue in FY2015?",
                ),
                result=VerificationResult(claim_id="c01", verdict="CONTRADICTED"),
            )
        ],
    )
    assert ReviewReport.model_validate(report.model_dump()) == report

    card = ReviewCard(
        review_id="review_amd_memo_123",
        filename="amd_memo.md",
        format="md",
        status="completed",
        created_at="2026-07-05T00:00:00Z",
        summary=report.summary,
    )
    assert ReviewCard.model_validate(card.model_dump()) == card

    status = ReviewStatusResponse(
        review_id="review_amd_memo_123",
        filename="amd_memo.md",
        format="md",
        status="running",
        created_at="2026-07-05T00:00:00Z",
    )
    assert ReviewStatusResponse.model_validate(status.model_dump()) == status

    created = CreateReviewResponse(review_id="review_amd_memo_123")
    assert created.status == "queued"


def test_trace_event_accepts_new_types() -> None:
    for t in ("claim_extracted", "scope_check", "claim_verdict", "annotation"):
        ev = TraceEvent(run_id="review_x", seq=1, ts="2026-07-05T00:00:00Z", type=t, title=t, item_id="c01")
        assert TraceEvent.model_validate(ev.model_dump()).type == t

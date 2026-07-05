"""Tests for the deterministic review scorer (evals/review_scorer.py, spec section 14).

A synthetic pepsico report exercises every metric branch: extraction misses,
corrupted catches/misses, a false flag, fabrication detection (and its failure),
verdict (in)accuracy, PDF anchor overlap pass/fail against the real
pepsico_memo.pdf, and citation provenance via the reused v0 checkers.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "evals"))

from app.schemas import (  # noqa: E402
    Citation,
    Claim,
    ClaimAnchor,
    ReviewReport,
    ReviewReportClaim,
    SubsetItem,
    TraceEvent,
    VerificationResult,
)

import review_scorer as rs  # noqa: E402

MANIFEST = rs.load_manifest()
PEPSICO_DOC = next(d for d in MANIFEST["documents"] if d["doc_id"] == "pepsico_memo")
CLAIM_TEXT = {c["claim_id"]: c["claim_text"] for c in PEPSICO_DOC["claims"]}
PDF_PATH = rs.TESTDOCS_DIR / "pepsico_memo.pdf"


def _citation(doc_id: str, page: int) -> Citation:
    return Citation(
        citation_id="cit1",
        doc_id=doc_id,
        doc_name=doc_id,
        pdf_page=page,
        chunk_id=f"{doc_id}::p{page}",
        quote="evidence",
        char_start=0,
        char_end=8,
    )


def _report_claim(
    claim_id: str,
    *,
    verdict: str | None,
    quote: str | None = None,
    anchor_quote: str | None = None,
    citations: list[Citation] | None = None,
) -> ReviewReportClaim:
    quote = quote if quote is not None else CLAIM_TEXT[claim_id]
    anchor = (
        ClaimAnchor(quote=anchor_quote, char_start=0, char_end=len(anchor_quote))
        if anchor_quote is not None
        else None
    )
    claim = Claim(
        claim_id=claim_id,
        quote=quote,
        claim_type="numeric",
        company="PepsiCo",
        question="q",
        anchor=anchor,
    )
    result = (
        None
        if verdict is None
        else VerificationResult(claim_id=claim_id, verdict=verdict, citations=citations or [])
    )
    return ReviewReportClaim(claim=claim, result=result)


@pytest.fixture
def report() -> ReviewReport:
    """Synthetic pepsico report hitting every metric branch.

    pep_c06 (corrupted) is deliberately NOT extracted (extraction + corrupted miss).
    """
    claims = [
        # accurate -> SUPPORTED, anchored to real text (anchor pass), good citation.
        _report_claim(
            "pep_c01",
            verdict="SUPPORTED",
            anchor_quote=CLAIM_TEXT["pep_c01"],
            citations=[_citation("DOC_A", 2)],
        ),
        # corrupted -> CONTRADICTED (caught), anchor quote absent from PDF (anchor fail).
        _report_claim(
            "pep_c02",
            verdict="CONTRADICTED",
            anchor_quote="this exact sentence is nowhere in the pepsico pdf zzzqqq",
        ),
        # accurate -> CONTRADICTED == false flag (and verdict inaccurate).
        _report_claim("pep_c03", verdict="CONTRADICTED"),
        # corrupted -> PARTIALLY_SUPPORTED (caught) but verdict inaccurate vs CONTRADICTED.
        _report_claim("pep_c04", verdict="PARTIALLY_SUPPORTED"),
        # accurate -> SUPPORTED (correct), citation that misses gold page (provenance fail).
        _report_claim("pep_c05", verdict="SUPPORTED", citations=[_citation("DOC_B", 99)]),
        # pep_c06 corrupted: omitted entirely.
        # corrupted -> SUPPORTED == corrupted miss (false negative).
        _report_claim("pep_c08", verdict="SUPPORTED"),
        # accurate -> SUPPORTED (correct).
        _report_claim("pep_c07", verdict="SUPPORTED"),
        # fabricated -> NOT_IN_CORPUS (detected, correct).
        _report_claim("pep_c09", verdict="NOT_IN_CORPUS"),
        # fabricated -> CONTRADICTED (detection failure, verdict inaccurate).
        _report_claim("pep_c10", verdict="CONTRADICTED"),
    ]
    return ReviewReport(
        review_id="review_pepsico_memo_1",
        filename="pepsico_memo.pdf",
        format="pdf",
        company_scope=["PepsiCo"],
        claims=claims,
    )


@pytest.fixture
def subset_by_item() -> dict[str, SubsetItem]:
    def _item(item_id: str, doc_id: str, page: int) -> SubsetItem:
        return SubsetItem.model_validate(
            {
                "item_id": item_id,
                "question_id": "qid",
                "company": "PepsiCo",
                "question": "q",
                "gold_answer": "a",
                "gold_evidence": [
                    {
                        "doc_id": doc_id,
                        "doc_name": doc_id,
                        "doc_type": "8k",
                        "filing_period": "2023",
                        "pdf_page": page,
                        "page_label": str(page),
                        "evidence_text": "evidence",
                    }
                ],
            }
        )

    # pep_c01 -> pepsico_03 (citation DOC_A p2 matches); pep_c05 -> pepsico_07 (DOC_B p5, cited p99 misses).
    return {
        "pepsico_03": _item("pepsico_03", "DOC_A", 2),
        "pepsico_07": _item("pepsico_07", "DOC_B", 5),
    }


# --- fuzzy matching primitives ---------------------------------------------
def test_nfkc_ligature_fold_matches() -> None:
    assert rs.is_fuzzy_match("ﬁnancing activities", "financing activities")


def test_substring_is_full_match() -> None:
    assert rs.fuzzy_ratio("borrow a total of $8.4 billion", CLAIM_TEXT["pep_c03"]) == 1.0


def test_unrelated_text_does_not_match() -> None:
    assert not rs.is_fuzzy_match("the quick brown fox jumps", CLAIM_TEXT["pep_c01"])


def test_find_manifest_doc_by_filename(report: ReviewReport) -> None:
    assert rs.find_manifest_doc(MANIFEST, report)["doc_id"] == "pepsico_memo"


def test_find_manifest_doc_raises_when_absent() -> None:
    bogus = ReviewReport(review_id="r", filename="nope.pdf", format="pdf", company_scope=["Nobody"])
    with pytest.raises(ValueError):
        rs.find_manifest_doc(MANIFEST, bogus)


# --- individual metrics -----------------------------------------------------
def test_extraction_recall(report: ReviewReport) -> None:
    matches = rs.align_claims(PEPSICO_DOC, report)
    m = rs.extraction_recall(matches)
    assert (m["numerator"], m["denominator"]) == (9, 10)  # pep_c06 not extracted


def test_corrupted_recall_headline(report: ReviewReport) -> None:
    matches = rs.align_claims(PEPSICO_DOC, report)
    m = rs.corrupted_recall(matches)
    # c02 CONTRADICTED + c04 PARTIALLY caught; c06 unmatched, c08 SUPPORTED missed.
    assert (m["numerator"], m["denominator"]) == (2, 4)


def test_false_flag_rate(report: ReviewReport) -> None:
    matches = rs.align_claims(PEPSICO_DOC, report)
    m = rs.false_flag_rate(matches)
    # accurate = c01,c03,c05,c07; only c03 flagged CONTRADICTED.
    assert (m["numerator"], m["denominator"]) == (1, 4)


def test_fabrication_detection(report: ReviewReport) -> None:
    matches = rs.align_claims(PEPSICO_DOC, report)
    m = rs.fabrication_detection(matches)
    # c09 NOT_IN_CORPUS detected; c10 CONTRADICTED not detected.
    assert (m["numerator"], m["denominator"]) == (1, 2)


def test_verdict_accuracy(report: ReviewReport) -> None:
    matches = rs.align_claims(PEPSICO_DOC, report)
    m = rs.verdict_accuracy(matches)
    # correct: c01,c02,c05,c07,c09 = 5 of 9 matched-with-verdict.
    assert (m["numerator"], m["denominator"]) == (5, 9)


@pytest.mark.skipif(not PDF_PATH.exists(), reason="pepsico_memo.pdf fixture missing")
def test_anchor_precision_pdf_overlap(report: ReviewReport) -> None:
    pytest.importorskip("fitz")
    matches = rs.align_claims(PEPSICO_DOC, report)
    m = rs.anchor_precision(matches, PDF_PATH, "pdf")
    # anchored: c01 (real text -> overlap) and c02 (bogus text -> no rects).
    assert (m["numerator"], m["denominator"]) == (1, 2)


def test_anchor_precision_none_for_non_pdf(report: ReviewReport) -> None:
    matches = rs.align_claims(PEPSICO_DOC, report)
    assert rs.anchor_precision(matches, PDF_PATH, "md") is None
    assert rs.anchor_precision(matches, None, "pdf") is None


def test_citation_provenance_via_subset(report: ReviewReport, subset_by_item) -> None:
    matches = rs.align_claims(PEPSICO_DOC, report)
    m = rs.citation_provenance_metric(matches, subset_by_item, None)
    # c01 citation matches gold (pass); c05 citation page misses (fail).
    assert (m["numerator"], m["denominator"]) == (1, 2)


def test_citation_provenance_none_when_no_citations() -> None:
    report = ReviewReport(
        review_id="r",
        filename="pepsico_memo.pdf",
        format="pdf",
        company_scope=["PepsiCo"],
        claims=[_report_claim("pep_c01", verdict="SUPPORTED")],
    )
    matches = rs.align_claims(PEPSICO_DOC, report)
    assert rs.citation_provenance_metric(matches, None, None) is None


def test_citation_provenance_via_trace() -> None:
    """The trace branch reuses evals.scorers.citation_provenance (chunk_id seen)."""
    cited = _citation("DOC_A", 2)  # chunk_id == "DOC_A::p2"
    rc = _report_claim("pep_c01", verdict="SUPPORTED", citations=[cited])
    report = ReviewReport(
        review_id="r",
        filename="pepsico_memo.pdf",
        format="pdf",
        company_scope=["PepsiCo"],
        claims=[rc],
    )
    matches = rs.align_claims(PEPSICO_DOC, report)
    trace = [
        TraceEvent(
            run_id="r",
            seq=1,
            ts="2026-07-05T00:00:00Z",
            type="retrieval",
            title="retrieval",
            item_id="pepsico_03",
            payload={"chunks": [{"chunk_id": "DOC_A::p2"}]},
        )
    ]
    m = rs.citation_provenance_metric(matches, None, trace)
    assert (m["numerator"], m["denominator"]) == (1, 1)


# --- top-level scoring + table ---------------------------------------------
def test_score_report_targets(report: ReviewReport, subset_by_item) -> None:
    scored = rs.score_report(
        report, MANIFEST, source_path=PDF_PATH, subset_by_item=subset_by_item
    )
    targets = scored["targets"]
    # corrupted recall 2/4 = 0.5 < 0.8 -> missed.
    assert targets["corrupted_recall_ge_0.8"] is False
    # false-flag 1/4 = 0.25 > 1/11 -> missed.
    assert targets["false_flag_rate_le_0.0909"] is False
    # fabrication 1/2 = 0.5 < 0.75 -> missed.
    assert targets["fabrication_detection_ge_0.75"] is False
    assert scored["metrics"]["extraction_recall"]["numerator"] == 9


def test_perfect_report_meets_targets() -> None:
    """A report that catches every corrupted claim, flags no accurate one, and
    resolves every fabrication meets all three section-14 targets."""
    claims = []
    for c in PEPSICO_DOC["claims"]:
        cid = c["claim_id"]
        verdict = {
            "accurate": "SUPPORTED",
            "corrupted": "CONTRADICTED",
            "fabricated": "NOT_IN_CORPUS",
        }[c["seeded_status"]]
        claims.append(_report_claim(cid, verdict=verdict))
    report = ReviewReport(
        review_id="r",
        filename="pepsico_memo.pdf",
        format="pdf",
        company_scope=["PepsiCo"],
        claims=claims,
    )
    scored = rs.score_report(report, MANIFEST)
    assert all(v is True for v in scored["targets"].values())
    assert scored["metrics"]["verdict_accuracy"]["value"] == 1.0
    assert scored["metrics"]["corrupted_recall"]["value"] == 1.0


def test_format_table_is_string(report: ReviewReport, subset_by_item) -> None:
    scored = rs.score_report(report, MANIFEST, source_path=PDF_PATH, subset_by_item=subset_by_item)
    table = rs.format_table(scored)
    assert "corrupted recall" in table
    assert "target MISSED" in table
    assert isinstance(table, str)


def test_cli_json_output(report: ReviewReport, tmp_path: Path, capsys) -> None:
    report_path = tmp_path / "report.json"
    report_path.write_text(report.model_dump_json())
    rc = rs.main([str(report_path), "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["filename"] == "pepsico_memo.pdf"
    assert out["metrics"]["extraction_recall"]["numerator"] == 9

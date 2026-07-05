"""S5 + S6 tests (spec section 9): report assembly, PDF annotation, HTML report.

Deterministic — no LLM. Synthetic VerificationResult fixtures over the committed
`evals/testdocs/pepsico_memo.pdf`. The acceptance bars (task annotate-pdf):
  * annotation rects overlap the quote's search rects (anchor precision),
  * appendix pages are appended and list every claim (incl. SKIPPED),
  * original page content streams / text are never modified.
"""

from __future__ import annotations

import json
from pathlib import Path

import fitz  # pymupdf
import pytest

from app.review.annotate_pdf import _search_rects, annotate_pdf
from app.review.parse import anchor_quote, parse_document
from app.review.report import assemble_report
from app.review.report_html import render_report_html
from app.schemas import (
    Citation,
    Claim,
    ClaimValue,
    DocModel,
    VerificationResult,
)

TESTDOCS = Path(__file__).resolve().parents[2] / "evals" / "testdocs"
PEPSICO_PDF = TESTDOCS / "pepsico_memo.pdf"
MANIFEST = json.loads((TESTDOCS / "manifest.json").read_text())
PEP = next(d for d in MANIFEST["documents"] if d["doc_id"] == "pepsico_memo")


def _citation(page: int = 2) -> Citation:
    return Citation(
        citation_id="cit1",
        doc_id="0000077476-23-000082",
        doc_name="PepsiCo 8-K",
        pdf_page=page,
        chunk_id="pepsico:doc:p2:c0",
        quote="the agreement was increased by $400 million",
        char_start=0,
        char_end=10,
    )


def _verdict_for(seeded: str) -> str:
    return {
        "accurate": "SUPPORTED",
        "corrupted": "CONTRADICTED",
        "fabricated": "NOT_IN_CORPUS",
    }[seeded]


@pytest.fixture(scope="module")
def docmodel() -> DocModel:
    return parse_document(PEPSICO_PDF)


@pytest.fixture(scope="module")
def fixture_bundle(docmodel: DocModel):
    """Build claims (anchored) + synthetic results for the pepsico manifest, plus
    one SKIPPED claim (cut by cap, no result)."""
    claims: list[Claim] = []
    results: list[VerificationResult] = []
    for entry in PEP["claims"]:
        anchor = anchor_quote(docmodel, entry["claim_text"])
        assert anchor is not None, f"{entry['claim_id']} did not anchor"
        verdict = _verdict_for(entry["seeded_status"])
        claims.append(
            Claim(
                claim_id=entry["claim_id"],
                quote=anchor.quote,
                claim_type="numeric",
                company="PepsiCo",
                period="FY2022",
                question=f"Verify: {entry['claim_text'][:40]}",
                status="VERIFIED",
                anchor=anchor,
            )
        )
        results.append(
            VerificationResult(
                claim_id=entry["claim_id"],
                verdict=verdict,  # type: ignore[arg-type]
                doc_value=ClaimValue(value=600.0, unit="USD millions"),
                corpus_value=(
                    ClaimValue(value=400.0, unit="USD millions")
                    if verdict == "CONTRADICTED"
                    else None
                ),
                explanation=f"{verdict} per corpus for {entry['claim_id']}.",
                citations=[_citation()],
            )
        )
    # A SKIPPED claim (cut by MAX_CLAIMS cap) carries no result but must still
    # appear in the report and appendix.
    skipped = Claim(
        claim_id="pep_skip",
        quote="A skipped claim beyond the cap.",
        claim_type="factual",
        company="PepsiCo",
        question="skipped",
        status="SKIPPED",
        anchor=None,
    )
    claims.append(skipped)
    return docmodel, claims, results


def test_assemble_report_counts(fixture_bundle) -> None:
    docmodel, claims, results = fixture_bundle
    report = assemble_report("review_test", docmodel, claims, results)
    assert report.filename == docmodel.filename
    assert report.format == "pdf"
    assert report.company_scope == ["PepsiCo"]
    assert report.summary.total_claims == len(claims)
    # Manifest pepsico: 5 accurate/supported, 4 corrupted/contradicted, 2 fabricated/not_in_corpus.
    seeded = [c["seeded_status"] for c in PEP["claims"]]
    assert report.summary.supported == seeded.count("accurate")
    assert report.summary.contradicted == seeded.count("corrupted")
    assert report.summary.not_in_corpus == seeded.count("fabricated")
    assert report.summary.skipped == 1
    # Every claim is paired; SKIPPED has no result.
    by_id = {rc.claim.claim_id: rc for rc in report.claims}
    assert by_id["pep_skip"].result is None
    assert by_id["pep_c02"].result is not None


def test_anchor_precision(fixture_bundle, tmp_path) -> None:
    """Each highlight annotation's rect overlaps the quote's own search rects."""
    docmodel, claims, results = fixture_bundle
    report = assemble_report("review_test", docmodel, claims, results)
    out = tmp_path / "annotated.pdf"
    annotate_pdf(PEPSICO_PDF, report, out)

    src = fitz.open(PEPSICO_PDF)
    ann = fitz.open(out)
    # Map claim_id -> anchor page/quote for anchored claims.
    anchored = {
        rc.claim.claim_id: rc.claim.anchor
        for rc in report.claims
        if rc.claim.anchor is not None and rc.result is not None
    }
    # Collect annotation rects per page from the annotated pdf.
    annot_rects: dict[int, list[fitz.Rect]] = {}
    for pno in range(src.page_count):  # only original pages carry claim highlights
        annot_rects[pno] = [a.rect for a in ann[pno].annots()]

    checked = 0
    for claim_id, anchor in anchored.items():
        page_index = anchor.page - 1
        expected = _search_rects(src[page_index], anchor.quote)
        assert expected, f"{claim_id}: no expected rects for the quote"
        exp_union = fitz.Rect(expected[0])
        for r in expected[1:]:
            exp_union |= fitz.Rect(r)
        # At least one annotation rect on that page overlaps the quote's rects.
        overlaps = any(
            not (fitz.Rect(a) & exp_union).is_empty for a in annot_rects[page_index]
        )
        assert overlaps, f"{claim_id}: no annotation overlaps the quote rects"
        checked += 1
    assert checked >= 8  # anchored the whole pepsico set
    src.close()
    ann.close()


def test_ligature_claim_annotated(fixture_bundle, tmp_path) -> None:
    """pep_c01 contains `Pacific` where the PDF has the `Paciﬁc` ligature —
    search_for misses it, the NFKC word-fallback must still highlight it."""
    docmodel, claims, results = fixture_bundle
    report = assemble_report("review_test", docmodel, claims, results)
    anchor = next(rc.claim.anchor for rc in report.claims if rc.claim.claim_id == "pep_c01")
    src = fitz.open(PEPSICO_PDF)
    page = src[anchor.page - 1]
    assert not page.search_for(anchor.quote), "expected native search to miss the ligature"
    assert _search_rects(page, anchor.quote), "fallback should still locate the span"
    src.close()

    out = tmp_path / "annotated.pdf"
    annotate_pdf(PEPSICO_PDF, report, out)
    ann = fitz.open(out)
    titles = [a.info.get("title") for a in ann[anchor.page - 1].annots()]
    assert "SUPPORTED" in titles  # pep_c01 is accurate
    ann.close()


def test_appendix_and_original_unchanged(fixture_bundle, tmp_path) -> None:
    docmodel, claims, results = fixture_bundle
    report = assemble_report("review_test", docmodel, claims, results)
    out = tmp_path / "annotated.pdf"

    src = fitz.open(PEPSICO_PDF)
    orig_pages = src.page_count
    orig_text = [src[p].get_text("text") for p in range(orig_pages)]
    src.close()

    annotate_pdf(PEPSICO_PDF, report, out)
    ann = fitz.open(out)

    # Appendix pages were appended.
    assert ann.page_count > orig_pages
    appendix_text = "".join(ann[p].get_text("text") for p in range(orig_pages, ann.page_count))
    assert "Review Appendix" in appendix_text
    # Every claim, including the SKIPPED one, is listed.
    assert "SKIPPED" in appendix_text
    assert "skipped claim beyond the cap" in appendix_text.lower()

    # Original page text (content stream) is byte-for-byte unchanged.
    for p in range(orig_pages):
        assert ann[p].get_text("text") == orig_text[p]
    ann.close()


def test_render_report_html(fixture_bundle) -> None:
    docmodel, claims, results = fixture_bundle
    report = assemble_report("review_test", docmodel, claims, results)
    out = render_report_html(report, docmodel)

    assert "prefers-color-scheme" in out  # theme-aware
    assert "<style>" in out and "http" not in out.replace("http-equiv", "")  # self-contained-ish
    # A verdict card exists per claim, addressable by claim_id.
    assert 'id="card-pep_c02"' in out
    assert 'id="card-pep_skip"' in out
    # Highlighted spans link to their card; citation links target the corpus viewer.
    assert 'href="#card-pep_c02"' in out
    assert "/corpus/PepsiCo/" in out and "/page/2" in out
    # Contradicted claim shows both doc and corpus values.
    assert "Corpus:" in out and "Document:" in out

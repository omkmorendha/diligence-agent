"""Deterministic review scorer (v1 spec section 14).

Pure functions over a `report.json` (schemas.ReviewReport) + the seeded-error
ground truth in `evals/testdocs/manifest.json`. No LLM. Mirrors the v0 scorer
style in `evals/scorers.py` and reuses its citation checkers unchanged.

Metrics (spec section 14 table):
    * extraction recall     manifest claims matched (fuzzy, NFKC-folded) by an
                            extracted claim in the report.
    * corrupted recall      (HEADLINE) corrupted claims flagged CONTRADICTED or
                            PARTIALLY_SUPPORTED.
    * false-flag rate       accurate claims flagged CONTRADICTED (target ~0).
    * fabrication detection fabricated claims -> NOT_IN_CORPUS (NOT CONTRADICTED).
    * verdict accuracy      exact `expected_verdict` match over matched claims.
    * anchor precision       PDF only: the annotated span (anchor quote rects)
                            overlaps the manifest claim sentence rects. N/A when
                            the format is not PDF or the source PDF is absent.
    * citation provenance   verdict citations verify against corpus pages -- the
                            v0 checker (evals.scorers.citation_precision against
                            gold_evidence, or citation_provenance against a trace
                            when one is supplied) reused unchanged. N/A when there
                            is nothing to check against.

Count-based metrics report as {"numerator", "denominator", "value"}; `value` is
None when the denominator is 0 (nothing applicable). Targets (spec section 14):
corrupted recall >= 8/10, false-flag <= 1/11, fabrication detection >= 3/4.

CLI:
    uv run --project backend python evals/review_scorer.py <report.json>
"""

from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.schemas import (  # noqa: E402
    Citation,
    MemoItem,
    ReviewReport,
    ReviewReportClaim,
    SubsetItem,
    TraceEvent,
)

from scorers import citation_precision, citation_provenance  # noqa: E402

DEFAULT_MANIFEST = ROOT / "evals" / "testdocs" / "manifest.json"
DEFAULT_SUBSET = ROOT / "data" / "subset.json"
TESTDOCS_DIR = ROOT / "evals" / "testdocs"

# Fuzzy anchor-match threshold. The seeded docs place every claim_text verbatim,
# so an ideal extractor scores 1.0; the slack absorbs span-boundary drift (an
# extractor that quotes a sub-clause or a slightly wider sentence).
FUZZY_THRESHOLD = 0.82

# Corrupted claims are "caught" by either of these verdicts (spec section 14).
CORRUPTED_FLAG_VERDICTS = {"CONTRADICTED", "PARTIALLY_SUPPORTED"}


# --- normalization / fuzzy matching ----------------------------------------
def normalize_fuzzy(s: str) -> str:
    """NFKC-fold (collapses ligatures ﬁ/ﬂ per spec section 1.2), lowercase,
    collapse all whitespace runs to single spaces, strip."""
    s = unicodedata.normalize("NFKC", s).lower()
    return " ".join(s.split())


def fuzzy_ratio(a: str, b: str) -> float:
    """Best containment/similarity score between two normalized strings in [0,1].

    A quoted span that is a substring of the claim sentence (or vice versa) is a
    full match -- extractors legitimately quote a tighter or wider span than the
    manifest sentence. Otherwise fall back to SequenceMatcher similarity.
    """
    na, nb = normalize_fuzzy(a), normalize_fuzzy(b)
    if not na or not nb:
        return 0.0
    if na in nb or nb in na:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()


def is_fuzzy_match(a: str, b: str, threshold: float = FUZZY_THRESHOLD) -> bool:
    return fuzzy_ratio(a, b) >= threshold


# --- manifest <-> report alignment -----------------------------------------
def find_manifest_doc(manifest: dict, report: ReviewReport) -> dict:
    """Locate the manifest document entry matching this report (by filename)."""
    for doc in manifest.get("documents", []):
        if doc.get("filename") == report.filename:
            return doc
    # Fall back to matching on the review company scope, then bail loudly.
    for doc in manifest.get("documents", []):
        if doc.get("company") in report.company_scope:
            return doc
    raise ValueError(
        f"no manifest document matches report filename={report.filename!r} "
        f"scope={report.company_scope!r}"
    )


def _best_report_claim(
    claim_text: str, report_claims: list[ReviewReportClaim]
) -> Optional[ReviewReportClaim]:
    """The report claim whose quote best fuzzy-matches the manifest sentence, or
    None if nothing clears the threshold."""
    best: Optional[ReviewReportClaim] = None
    best_ratio = FUZZY_THRESHOLD
    for rc in report_claims:
        ratio = fuzzy_ratio(claim_text, rc.claim.quote)
        if ratio >= best_ratio:
            best, best_ratio = rc, ratio
    return best


class MatchedClaim:
    """One manifest claim aligned to its (optional) extracted report claim."""

    def __init__(self, manifest_claim: dict, report_claim: Optional[ReviewReportClaim]):
        self.manifest = manifest_claim
        self.report_claim = report_claim
        self.matched = report_claim is not None

    @property
    def verdict(self) -> Optional[str]:
        """The report's verdict for this claim, or None if unmatched / no result
        (e.g. a claim cut by the cap has status SKIPPED and no result)."""
        if self.report_claim is None or self.report_claim.result is None:
            return None
        return self.report_claim.result.verdict

    @property
    def expected_verdict(self) -> str:
        return self.manifest["expected_verdict"]

    @property
    def seeded_status(self) -> str:
        return self.manifest["seeded_status"]


def align_claims(manifest_doc: dict, report: ReviewReport) -> list[MatchedClaim]:
    """Align every manifest claim to its best-matching extracted report claim."""
    return [
        MatchedClaim(mc, _best_report_claim(mc["claim_text"], report.claims))
        for mc in manifest_doc["claims"]
    ]


# --- count metric helper ----------------------------------------------------
def _metric(numerator: int, denominator: int) -> dict:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": (numerator / denominator) if denominator else None,
    }


# --- individual metrics -----------------------------------------------------
def extraction_recall(matches: list[MatchedClaim]) -> dict:
    return _metric(sum(1 for m in matches if m.matched), len(matches))


def corrupted_recall(matches: list[MatchedClaim]) -> dict:
    """HEADLINE: corrupted claims flagged CONTRADICTED or PARTIALLY_SUPPORTED.

    Denominator is every seeded-corrupted claim -- a corrupted claim that was
    never extracted cannot be flagged, so it counts against recall.
    """
    corrupted = [m for m in matches if m.seeded_status == "corrupted"]
    caught = sum(1 for m in corrupted if m.verdict in CORRUPTED_FLAG_VERDICTS)
    return _metric(caught, len(corrupted))


def false_flag_rate(matches: list[MatchedClaim]) -> dict:
    """Accurate claims wrongly flagged CONTRADICTED (target ~0)."""
    accurate = [m for m in matches if m.seeded_status == "accurate"]
    flagged = sum(1 for m in accurate if m.verdict == "CONTRADICTED")
    return _metric(flagged, len(accurate))


def fabrication_detection(matches: list[MatchedClaim]) -> dict:
    """Fabricated claims correctly resolved to NOT_IN_CORPUS (not CONTRADICTED)."""
    fabricated = [m for m in matches if m.seeded_status == "fabricated"]
    detected = sum(1 for m in fabricated if m.verdict == "NOT_IN_CORPUS")
    return _metric(detected, len(fabricated))


def verdict_accuracy(matches: list[MatchedClaim]) -> dict:
    """Exact expected_verdict match over matched claims that carry a verdict."""
    verdicted = [m for m in matches if m.verdict is not None]
    correct = sum(1 for m in verdicted if m.verdict == m.expected_verdict)
    return _metric(correct, len(verdicted))


# --- anchor precision (PDF rect overlap) -----------------------------------
def _search_rects(page, text: str) -> list:
    """Rects for `text` on `page`, NFKC-tolerant with a shortened-prefix fallback
    for sentences that wrap awkwardly. Returns [] if nothing is found."""
    for candidate in (text, unicodedata.normalize("NFKC", text)):
        rects = page.search_for(candidate)
        if rects:
            return rects
    # Long sentences frequently wrap across columns/lines; a leading prefix still
    # locates the span for an overlap test.
    prefix = " ".join(text.split()[:8])
    if prefix and prefix != text:
        return page.search_for(prefix)
    return []


def _anchor_overlaps(source_path: Path, page_no: Optional[int], anchor_quote: str, claim_text: str) -> bool:
    """True iff the anchor quote's rects overlap the manifest sentence's rects on
    the anchored page (or any page when `page_no` is None)."""
    import fitz  # lazy: only the PDF path needs pymupdf

    doc = fitz.open(source_path)
    try:
        pages = [doc[page_no - 1]] if page_no else list(doc)
        for page in pages:
            ann = _search_rects(page, anchor_quote)
            sent = _search_rects(page, claim_text)
            if ann and sent and any(a.intersects(s) for a in ann for s in sent):
                return True
        return False
    finally:
        doc.close()


def anchor_precision(matches: list[MatchedClaim], source_path: Optional[Path], fmt: str) -> Optional[dict]:
    """PDF-only: fraction of matched-and-anchored claims whose highlighted span
    overlaps the manifest claim sentence. N/A (None) off the PDF path."""
    if fmt != "pdf" or source_path is None or not Path(source_path).exists():
        return None
    anchored = [
        m
        for m in matches
        if m.report_claim is not None and m.report_claim.claim.anchor is not None
    ]
    if not anchored:
        return None
    hits = 0
    for m in anchored:
        anchor = m.report_claim.claim.anchor
        if _anchor_overlaps(Path(source_path), anchor.page, anchor.quote, m.manifest["claim_text"]):
            hits += 1
    return _metric(hits, len(anchored))


# --- citation provenance (reuse v0 checker) --------------------------------
def _as_memo_item(item_id: str, citations: list[Citation]) -> MemoItem:
    """Adapt a VerificationResult's citations into a MemoItem so the v0 citation
    checkers apply unchanged (they read only .status and .citations here)."""
    return MemoItem(item_id=item_id, question="", answer="", citations=citations, status="answered")


def citation_provenance_metric(
    matches: list[MatchedClaim],
    subset_by_item: Optional[dict[str, SubsetItem]],
    trace_events: Optional[list[TraceEvent]],
) -> Optional[dict]:
    """Verdict citations verify against corpus pages, reusing the v0 checker.

    Only fact-asserting verdicts (those carrying citations) are checkable. With a
    trace, `evals.scorers.citation_provenance` (cited chunk_id seen in retrieval)
    is reused; otherwise `evals.scorers.citation_precision` against the subset's
    gold_evidence is reused. N/A (None) when neither reference is available.
    """
    checkable = [
        m
        for m in matches
        if m.report_claim is not None
        and m.report_claim.result is not None
        and m.report_claim.result.citations
    ]
    if not checkable:
        return None

    passed = 0
    checked = 0
    for m in checkable:
        item_id = m.manifest.get("item_id")
        result = m.report_claim.result
        memo_item = _as_memo_item(item_id or m.report_claim.claim.claim_id, result.citations)

        verdict: Optional[str] = None
        if trace_events is not None and item_id:
            verdict = citation_provenance(memo_item, item_id, trace_events)
        elif subset_by_item is not None and item_id and item_id in subset_by_item:
            verdict = citation_precision(memo_item, subset_by_item[item_id])
        if verdict is None:
            continue
        checked += 1
        if verdict == "pass":
            passed += 1

    if checked == 0:
        return None
    return _metric(passed, checked)


# --- top-level scoring ------------------------------------------------------
def score_report(
    report: ReviewReport,
    manifest: dict,
    *,
    source_path: Optional[Path] = None,
    subset_by_item: Optional[dict[str, SubsetItem]] = None,
    trace_events: Optional[list[TraceEvent]] = None,
) -> dict:
    """Score one report.json against its manifest document across every metric."""
    manifest_doc = find_manifest_doc(manifest, report)
    matches = align_claims(manifest_doc, report)

    metrics: dict[str, Optional[dict]] = {
        "extraction_recall": extraction_recall(matches),
        "corrupted_recall": corrupted_recall(matches),
        "false_flag_rate": false_flag_rate(matches),
        "fabrication_detection": fabrication_detection(matches),
        "verdict_accuracy": verdict_accuracy(matches),
        "anchor_precision": anchor_precision(matches, source_path, report.format),
        "citation_provenance": citation_provenance_metric(matches, subset_by_item, trace_events),
    }

    def frac(name: str) -> Optional[float]:
        m = metrics[name]
        return m["value"] if m else None

    cr = frac("corrupted_recall")
    ff = frac("false_flag_rate")
    fd = frac("fabrication_detection")
    targets = {
        # spec section 14: corrupted recall >= 8/10, false-flag <= 1/11, fab >= 3/4.
        "corrupted_recall_ge_0.8": None if cr is None else cr >= 0.8,
        "false_flag_rate_le_0.0909": None if ff is None else ff <= (1 / 11),
        "fabrication_detection_ge_0.75": None if fd is None else fd >= 0.75,
    }

    return {
        "review_id": report.review_id,
        "filename": report.filename,
        "format": report.format,
        "company": manifest_doc.get("company"),
        "metrics": metrics,
        "targets": targets,
    }


# --- human-readable table (mirrors scorers/run.py print style) -------------
_METRIC_ORDER = [
    ("extraction_recall", "extraction recall"),
    ("corrupted_recall", "corrupted recall  [HEADLINE]"),
    ("false_flag_rate", "false-flag rate"),
    ("fabrication_detection", "fabrication detection"),
    ("verdict_accuracy", "verdict accuracy"),
    ("anchor_precision", "anchor precision (PDF)"),
    ("citation_provenance", "citation provenance"),
]

_TARGET_FOR = {
    "corrupted_recall": "corrupted_recall_ge_0.8",
    "false_flag_rate": "false_flag_rate_le_0.0909",
    "fabrication_detection": "fabrication_detection_ge_0.75",
}


def _fmt_metric(m: Optional[dict]) -> str:
    if m is None:
        return "n/a"
    if m["value"] is None:
        return "n/a (0 applicable)"
    return f"{m['numerator']}/{m['denominator']} ({m['value']:.2f})"


def format_table(scored: dict) -> str:
    """Render a scored report as an aligned, human-readable table."""
    lines = [
        f"Review scorer -- {scored['filename']} "
        f"(review_id={scored['review_id']}, format={scored['format']})",
        "-" * 64,
    ]
    metrics = scored["metrics"]
    targets = scored["targets"]
    for key, label in _METRIC_ORDER:
        cell = _fmt_metric(metrics[key])
        target_key = _TARGET_FOR.get(key)
        marker = ""
        if target_key is not None:
            passed = targets.get(target_key)
            if passed is True:
                marker = "  [target MET]"
            elif passed is False:
                marker = "  [target MISSED]"
        lines.append(f"  {label:<30} {cell}{marker}")
    lines.append("-" * 64)
    met = [k for k, v in targets.items() if v is True]
    missed = [k for k, v in targets.items() if v is False]
    lines.append(f"  targets met: {len(met)}/{len(met) + len(missed)}")
    if missed:
        lines.append(f"  missed: {', '.join(missed)}")
    return "\n".join(lines)


# --- IO / CLI ---------------------------------------------------------------
def load_report(report: str | Path | dict) -> ReviewReport:
    if isinstance(report, dict):
        return ReviewReport.model_validate(report)
    return ReviewReport.model_validate_json(Path(report).read_text())


def load_manifest(path: str | Path = DEFAULT_MANIFEST) -> dict:
    return json.loads(Path(path).read_text())


def load_subset(path: str | Path = DEFAULT_SUBSET) -> Optional[dict[str, SubsetItem]]:
    p = Path(path)
    if not p.exists():
        return None
    raw = json.loads(p.read_text())
    items = raw if isinstance(raw, list) else raw.get("items", [])
    return {i["item_id"]: SubsetItem.model_validate(i) for i in items}


def load_trace(path: str | Path) -> list[TraceEvent]:
    return [
        TraceEvent.model_validate_json(line)
        for line in Path(path).read_text().splitlines()
        if line.strip()
    ]


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Deterministic review scorer (spec section 14).")
    ap.add_argument("report", help="Path to a report.json (schemas.ReviewReport).")
    ap.add_argument("--manifest", default=str(DEFAULT_MANIFEST), help="Ground-truth manifest.json.")
    ap.add_argument(
        "--source",
        default=None,
        help="Source document for anchor precision (PDF). Defaults to "
        "evals/testdocs/<filename> when present.",
    )
    ap.add_argument("--subset", default=str(DEFAULT_SUBSET), help="data/subset.json for citation provenance.")
    ap.add_argument("--trace", default=None, help="Optional trace.jsonl for chunk-level citation provenance.")
    ap.add_argument("--json", action="store_true", help="Emit the metrics dict as JSON instead of a table.")
    args = ap.parse_args(argv)

    report = load_report(args.report)
    manifest = load_manifest(args.manifest)

    source_path = Path(args.source) if args.source else TESTDOCS_DIR / report.filename
    if not Path(source_path).exists():
        source_path = None

    subset_by_item = load_subset(args.subset)
    trace_events = load_trace(args.trace) if args.trace else None

    scored = score_report(
        report,
        manifest,
        source_path=source_path,
        subset_by_item=subset_by_item,
        trace_events=trace_events,
    )

    if args.json:
        print(json.dumps(scored, indent=2))
    else:
        print(format_table(scored))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

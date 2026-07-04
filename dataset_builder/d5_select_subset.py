"""D5 — Stratified subset selection (spec section 6 D5, Step 8).

Target: ~4 companies x ~8 questions = ~32  (composition ~16 A / ~8 B / ~8 C).
Per company: >=2 predicted-baseline-failure questions; prefer recognizable
companies, clean evidence pages, strong parse quality, good live-trace potential.

Fallback policy (spec D5): 32/4 -> 24/3 -> 16/(2-3); prioritize A and C over B;
mark synthetic/unanswerable items and exclude from headline accuracy; disputed
human-reviewed items allowed only with {"human_reviewed": true}.

Emits the frozen subset.json schema (spec section 8), where each item carries gold
fields for the eval harness only. The agent-visible surface is item_id/company/question.

Output: data/subset.json

NOTE: characterize.py -> data/dataset_profile.json already confirmed the ideal
target is FEASIBLE (7 companies have >=8 usable questions). See AMBIGUITIES.md.

PAGE-NUMBER CONVENTION (decided; see AMBIGUITIES.md section 3):
    FinanceBench `evidence_page_num` is 0-indexed into the PDF. The spec's
    subset.json / citation schema uses a 1-indexed `pdf_page`. We adopt:

        pdf_page  = evidence_page_num + 1        # 1-indexed PDF page
        page_label = printed footer if D2 has it, else str(pdf_page)

    Use `to_pdf_page()` / `gold_evidence_from_raw()` below so the +1 mapping is
    applied in exactly one place. `get_pages`, the corpus endpoint, and citation
    scoring (+/-1 page slack) all consume the 1-indexed `pdf_page`.

TODO(Step 8): implement the stratified selection body; the page mapping and the
gold-evidence construction below are ready to use.
"""

from __future__ import annotations

from typing import Any


def to_pdf_page(evidence_page_num: int) -> int:
    """Map FinanceBench's 0-indexed page to the spec's 1-indexed `pdf_page`."""
    return int(evidence_page_num) + 1


def gold_evidence_from_raw(
    raw_evidence: dict[str, Any],
    doc_type: str | None,
    filing_period: str | None,
    page_label: str | None = None,
) -> dict[str, Any]:
    """Build one `gold_evidence` entry (spec section 8) from a raw FinanceBench
    evidence object, applying the pdf_page convention in a single place.

    `raw_evidence` fields used: doc_name, evidence_page_num, evidence_text.
    We keep the original 0-indexed value in `evidence_page_num_raw` for audit.
    """
    pdf_page = to_pdf_page(raw_evidence["evidence_page_num"])
    doc_name = raw_evidence["doc_name"]
    return {
        "doc_id": doc_name,            # doc_id == doc_name (AMBIGUITIES.md section 4)
        "doc_name": doc_name,
        "doc_type": (doc_type or "other"),
        "filing_period": filing_period or "",
        "pdf_page": pdf_page,
        "page_label": page_label if page_label is not None else str(pdf_page),
        "evidence_text": raw_evidence.get("evidence_text", ""),
        "evidence_page_num_raw": int(raw_evidence["evidence_page_num"]),
    }


def main() -> int:
    raise NotImplementedError("d5 subset selection: implement in Step 8 (spec section 6 D5).")


if __name__ == "__main__":
    raise SystemExit(main())

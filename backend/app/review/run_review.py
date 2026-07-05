"""Review orchestrator — drives S1..S6 (spec section 5).

FROZEN CONTRACT — signature must not change.

parse -> extract -> scope_check -> verify -> assemble_report -> annotate, writing
all artifacts under `runs/reviews/{review_id}/` (docmodel.json, claims.json,
report.json, annotated.<ext>, report.html, trace.jsonl, llm_calls.jsonl,
review.json) and returning the assembled `ReviewReport`. If >80% of claims are
out of scope, stops after scope check with status `out_of_scope` (spec section 8).
"""

from __future__ import annotations

from pathlib import Path

from ..schemas import ReviewReport


def run_review(review_id: str, upload_path: str | Path, pilot: bool) -> ReviewReport:
    """Run the full review pipeline for one upload and return the assembled report."""
    raise NotImplementedError("run_review is a frozen stub (spec section 5)")

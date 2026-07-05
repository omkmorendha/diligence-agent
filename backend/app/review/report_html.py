"""S6 — HTML review report (spec section 9).

FROZEN CONTRACT — signature must not change.

Render a self-contained (inline CSS), theme-aware HTML report: document text with
verdict-highlighted spans that open a verdict card with citations linking into the
existing `/corpus/{company}/{doc_id}/page/{n}` viewer. This is what the frontend
embeds for all three formats.
"""

from __future__ import annotations

from ..schemas import DocModel, ReviewReport


def render_report_html(report: ReviewReport, docmodel: DocModel) -> str:
    """Render the ReviewReport to a self-contained HTML string."""
    raise NotImplementedError("render_report_html is a frozen stub (spec section 9)")

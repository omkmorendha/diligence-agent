"""S6 — PDF annotation (spec sections 1.8, 9).

FROZEN CONTRACT — signature must not change.

The flagship, lossless path: for each claim `page.search_for(quote)` (NFKC-tolerant,
offset-map fallback) -> `add_highlight_annot(rects)` colored by verdict, with a popup
carrying verdict/corpus value/explanation/citation; a Review Appendix is appended via
`fitz.Story`. Original page content streams are never modified — annotations and
appended pages only. Writes the annotated copy to `out`.
"""

from __future__ import annotations

from pathlib import Path

from ..schemas import ReviewReport


def annotate_pdf(src: str | Path, report: ReviewReport, out: str | Path) -> None:
    """Write an annotated copy of the source PDF (highlights + appendix) to `out`."""
    raise NotImplementedError("annotate_pdf is a frozen stub (spec section 9)")

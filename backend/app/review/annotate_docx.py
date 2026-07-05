"""S6 — DOCX annotation (spec sections 1.8, 9).

FROZEN CONTRACT — signature must not change.

Split runs at anchor boundaries, apply a `WD_COLOR_INDEX` highlight per verdict,
insert `[R1]`-style markers, and append a Review Appendix section. Use native
`add_comment` when python-docx >= 1.2; otherwise markers + appendix are the
guaranteed path. Writes the annotated copy to `out`.
"""

from __future__ import annotations

from pathlib import Path

from ..schemas import ReviewReport


def annotate_docx(src: str | Path, report: ReviewReport, out: str | Path) -> None:
    """Write an annotated copy of the source DOCX (highlights + markers) to `out`."""
    raise NotImplementedError("annotate_docx is a frozen stub (spec section 9)")

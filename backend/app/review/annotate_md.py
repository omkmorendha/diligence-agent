"""S6 — Markdown annotation (spec sections 1.8, 9).

FROZEN CONTRACT — signature must not change.

Insert `<mark class="verdict-...">` spans around anchored quotes plus a footnote
appendix. Tags are inserted only — original bytes between them are never altered;
CI-style verification asserts that stripping the inserted tags reproduces the
source file byte-for-byte. Writes the annotated copy to `out`.
"""

from __future__ import annotations

from pathlib import Path

from ..schemas import ReviewReport


def annotate_md(src: str | Path, report: ReviewReport, out: str | Path) -> None:
    """Write an annotated copy of the source Markdown (<mark> spans + footnotes) to `out`."""
    raise NotImplementedError("annotate_md is a frozen stub (spec section 9)")

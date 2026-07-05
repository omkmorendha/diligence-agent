"""S1 — Document ingestion (spec section 6).

FROZEN CONTRACT — signature must not change.

Parse a `.pdf` / `.docx` / `.md` upload into a `DocModel`: NFKC-normalized,
whitespace-collapsed `canonical_text` plus per-format `blocks` carrying the
raw-position anchor fields (page for PDF, para_index for DOCX, line_start for MD)
and `char_start`/`char_end` into `canonical_text`.

Scanned PDFs (< 200 chars of extractable text) are rejected here (spec section 1.10).
"""

from __future__ import annotations

from pathlib import Path

from ..schemas import DocModel


def parse_document(path: str | Path) -> DocModel:
    """Parse an uploaded document into a DocModel. Raises on scanned/empty PDFs."""
    raise NotImplementedError("parse_document is a frozen stub (spec section 6)")

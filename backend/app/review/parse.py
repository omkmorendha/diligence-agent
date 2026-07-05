"""S1 — Document ingestion (spec section 6).

FROZEN CONTRACT — signature must not change.

Parse a `.pdf` / `.docx` / `.md` upload into a `DocModel`: NFKC-normalized,
whitespace-collapsed `canonical_text` plus per-format `blocks` carrying the
raw-position anchor fields (page for PDF, para_index for DOCX, line_start for MD)
and `char_start`/`char_end` into `canonical_text`.

Scanned PDFs (< 200 chars of extractable text) are rejected here (spec section 1.10).

`anchor_quote(docmodel, quote)` locates a verbatim span in `canonical_text`
tolerant to whitespace, unicode-punctuation and case differences, and — because
`canonical_text` is already NFKC — to the typographic ligatures (ﬁ/ﬂ) real PDF
extraction produces (spec sections 1.2 / 6). It is the shared anchoring helper
for extraction and the annotators.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path
from typing import Optional

from ..schemas import ClaimAnchor, DocBlock, DocModel

# Spec section 1.10: a PDF with no text layer (scanned) cannot be claim-extracted
# or annotated by span. `pymupdf` returns near-empty text for such files.
MIN_PDF_TEXT_CHARS = 200


# --- whitespace/unicode-tolerant verbatim matching --------------------------
# Mirrors backend/app/agent.py `_match_quote_offsets` (the citation-matching
# trick pointed at the uploaded document instead of the corpus), plus NFKC
# ligature folding: PDF extraction yields typographic ligatures (ﬁ ﬁ,
# ﬂ ﬂ) that a plain-ASCII claim quote never contains. `canonical_text` is
# NFKC-normalized at build time so the ligatures are already expanded there; the
# quote is NFKC-folded in `_normalize_match` so both sides agree.
_UNICODE_FOLD = {
    "–": "-",  # en dash
    "—": "-",  # em dash
    "−": "-",  # minus sign
    "‘": "'",  # left single quote
    "’": "'",  # right single quote
    "“": '"',  # left double quote
    "”": '"',  # right double quote
}


def _fold_char(ch: str) -> str:
    """Fold a single char for verbatim matching: map unicode dashes/quotes to
    ASCII, then lowercase. CASE-FOLD MUST STAY 1:1 — str.lower() can change
    length (e.g. 'İ' -> 'i̇') and would desync the offset map in
    `_normalized_with_offsets`, so a char that lowercases to more than one char
    is kept unfolded rather than break the map."""
    folded = _UNICODE_FOLD.get(ch, ch)
    lowered = folded.lower()
    return lowered if len(lowered) == 1 else folded


def _normalize_match(text: str) -> str:
    """Projection used on the query quote: NFKC-fold (expands ligatures), then
    collapse whitespace and fold unicode punctuation + case per-char. Match-only;
    offsets into the quote are never needed, so the length-changing NFKC pass is
    harmless here."""
    folded = "".join(_fold_char(ch) for ch in unicodedata.normalize("NFKC", text))
    return " ".join(folded.split())


def _normalized_with_offsets(raw: str) -> tuple[str, list[int]]:
    """Return (normalized_text, offset_map) where offset_map[i] is the index in
    `raw` that produced normalized char i. Applies the exact per-char projection
    of `_normalize_match` (fold + lowercase, whitespace runs collapsed to a
    single separator space) EXCEPT the length-changing NFKC pass — `raw` here is
    always the already-NFKC `canonical_text`, so re-normalizing would be a no-op
    that only risks desyncing the map. A separator space maps to the raw index of
    the following non-whitespace char, so a match landing on real chars maps back
    to a precise span."""
    norm: list[str] = []
    offset_map: list[int] = []
    started = False
    pending_ws = False
    for i, ch in enumerate(raw):
        if ch.isspace():
            if started:
                pending_ws = True
            continue
        if pending_ws:
            norm.append(" ")
            offset_map.append(i)
            pending_ws = False
        norm.append(_fold_char(ch))
        offset_map.append(i)
        started = True
    return "".join(norm), offset_map


def _match_quote_offsets(canonical_text: str, quote: str) -> Optional[tuple[int, int]]:
    """Find `quote` inside `canonical_text` tolerant to whitespace, unicode
    punctuation, ligatures and case. Returns (start, end) offsets into
    `canonical_text` (end exclusive) or None when the quote is genuinely absent."""
    norm_quote = _normalize_match(quote)
    if not norm_quote:
        return None
    norm_canon, offset_map = _normalized_with_offsets(canonical_text)
    pos = norm_canon.find(norm_quote)
    if pos == -1:
        return None
    start = offset_map[pos]
    end = offset_map[pos + len(norm_quote) - 1] + 1
    return start, end


def anchor_quote(docmodel: DocModel, quote: str) -> Optional[ClaimAnchor]:
    """Locate `quote` as a verbatim span in `docmodel.canonical_text` and return
    a `ClaimAnchor` carrying the canonical char offsets plus the per-format
    raw-position field (page / para_index / line_start) of the block the span
    starts in. Returns None when the quote cannot be anchored."""
    span = _match_quote_offsets(docmodel.canonical_text, quote)
    if span is None:
        return None
    start, end = span
    page: Optional[int] = None
    para_index: Optional[int] = None
    line_start: Optional[int] = None
    for block in docmodel.blocks:
        if block.char_start <= start < block.char_end:
            page = block.page
            para_index = block.para_index
            line_start = block.line_start
            break
    return ClaimAnchor(
        quote=docmodel.canonical_text[start:end],
        char_start=start,
        char_end=end,
        page=page,
        para_index=para_index,
        line_start=line_start,
    )


# --- canonical_text assembly -------------------------------------------------
def _canonicalize(raw: str) -> str:
    """NFKC-normalize and whitespace-collapse one raw block of text."""
    return " ".join(unicodedata.normalize("NFKC", raw).split())


def _assemble(raw_blocks: list[tuple[str, dict[str, Optional[int]]]]) -> tuple[str, list[DocBlock]]:
    """Join canonicalized non-empty blocks into `canonical_text` (single '\\n'
    separators) with a `DocBlock` per block carrying its canonical char span and
    per-format anchor field. Offsets index into the returned canonical_text."""
    parts: list[str] = []
    blocks: list[DocBlock] = []
    cursor = 0
    for raw, meta in raw_blocks:
        text = _canonicalize(raw)
        if not text:
            continue
        start = cursor
        end = start + len(text)
        blocks.append(
            DocBlock(
                text=text,
                char_start=start,
                char_end=end,
                page=meta.get("page"),
                para_index=meta.get("para_index"),
                line_start=meta.get("line_start"),
            )
        )
        parts.append(text)
        cursor = end + 1  # account for the joining '\n'
    return "\n".join(parts), blocks


# --- per-format extraction ---------------------------------------------------
def _parse_pdf(path: Path) -> list[tuple[str, dict[str, Optional[int]]]]:
    import fitz  # pymupdf

    raw_blocks: list[tuple[str, dict[str, Optional[int]]]] = []
    total_chars = 0
    with fitz.open(path) as doc:
        for page_index in range(doc.page_count):
            page_text = doc[page_index].get_text("text")
            total_chars += len(page_text.strip())
            # PDF anchor is 1-based page number to match the corpus page viewer.
            raw_blocks.append((page_text, {"page": page_index + 1}))
    if total_chars < MIN_PDF_TEXT_CHARS:
        raise ValueError(
            "no extractable text — scanned documents are not supported in v1 "
            f"(extracted {total_chars} chars, need >= {MIN_PDF_TEXT_CHARS})"
        )
    return raw_blocks


def _parse_docx(path: Path) -> list[tuple[str, dict[str, Optional[int]]]]:
    from docx import Document

    document = Document(str(path))
    raw_blocks: list[tuple[str, dict[str, Optional[int]]]] = []
    for para_index, para in enumerate(document.paragraphs):
        raw_blocks.append((para.text, {"para_index": para_index}))
    return raw_blocks


def _parse_md(path: Path) -> list[tuple[str, dict[str, Optional[int]]]]:
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")
    raw_blocks: list[tuple[str, dict[str, Optional[int]]]] = []
    block_lines: list[str] = []
    block_start_line = 1  # 1-based line number the current block opens on
    for idx, line in enumerate(lines):
        if line.strip() == "":
            if block_lines:
                raw_blocks.append(("\n".join(block_lines), {"line_start": block_start_line}))
                block_lines = []
            continue
        if not block_lines:
            block_start_line = idx + 1
        block_lines.append(line)
    if block_lines:
        raw_blocks.append(("\n".join(block_lines), {"line_start": block_start_line}))
    return raw_blocks


_PARSERS = {
    "pdf": _parse_pdf,
    "docx": _parse_docx,
    "md": _parse_md,
}


def _detect_format(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in (".md", ".markdown"):
        return "md"
    if suffix == ".pdf":
        return "pdf"
    if suffix == ".docx":
        return "docx"
    raise ValueError(f"unsupported document format: {path.suffix!r} (expected .pdf/.docx/.md)")


def parse_document(path: str | Path) -> DocModel:
    """Parse an uploaded document into a DocModel. Raises on scanned/empty PDFs
    and unsupported extensions."""
    path = Path(path)
    fmt = _detect_format(path)
    raw_blocks = _PARSERS[fmt](path)
    canonical_text, blocks = _assemble(raw_blocks)
    return DocModel(
        doc_id=path.stem,
        format=fmt,  # type: ignore[arg-type]
        filename=path.name,
        canonical_text=canonical_text,
        blocks=blocks,
    )

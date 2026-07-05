"""S1 ingestion tests (spec section 6): parse_document + anchor_quote against the
three committed fixtures. The acceptance bar (milestone 15.2) is that every
manifest claim_text anchors successfully in its document."""

from __future__ import annotations

import json
from pathlib import Path

import fitz  # pymupdf
import pytest

from app.review.parse import MIN_PDF_TEXT_CHARS, anchor_quote, parse_document

TESTDOCS = Path(__file__).resolve().parents[2] / "evals" / "testdocs"
MANIFEST = json.loads((TESTDOCS / "manifest.json").read_text())

# (filename, format, per-format anchor field that must be populated)
_DOC_META = {
    "pepsico_memo.pdf": ("pdf", "page"),
    "boeing_memo.docx": ("docx", "para_index"),
    "amd_memo.md": ("md", "line_start"),
}

_MANIFEST_CLAIMS = [
    (doc["filename"], claim["claim_id"], claim["claim_text"])
    for doc in MANIFEST["documents"]
    for claim in doc["claims"]
]


def _parse(filename: str):
    return parse_document(TESTDOCS / filename)


@pytest.mark.parametrize(
    "filename,claim_id,claim_text",
    _MANIFEST_CLAIMS,
    ids=[f"{fn}:{cid}" for fn, cid, _ in _MANIFEST_CLAIMS],
)
def test_every_manifest_claim_anchors(filename: str, claim_id: str, claim_text: str) -> None:
    """Acceptance bar: every manifest claim_text anchors in its document."""
    docmodel = _parse(filename)
    anchor = anchor_quote(docmodel, claim_text)
    assert anchor is not None, f"{claim_id} failed to anchor"
    # Offsets index a real span in canonical_text, and the stored quote is that span.
    assert 0 <= anchor.char_start < anchor.char_end <= len(docmodel.canonical_text)
    assert docmodel.canonical_text[anchor.char_start : anchor.char_end] == anchor.quote


@pytest.mark.parametrize("filename", list(_DOC_META))
def test_docmodel_shape(filename: str) -> None:
    fmt, _ = _DOC_META[filename]
    docmodel = _parse(filename)
    assert docmodel.format == fmt
    assert docmodel.filename == filename
    assert docmodel.doc_id == Path(filename).stem
    assert docmodel.canonical_text
    assert docmodel.blocks
    # Blocks are non-overlapping and each maps onto its canonical slice.
    prev_end = -1
    for block in docmodel.blocks:
        assert block.char_start > prev_end
        assert docmodel.canonical_text[block.char_start : block.char_end] == block.text
        prev_end = block.char_end


@pytest.mark.parametrize("filename", list(_DOC_META))
def test_per_format_anchor_field(filename: str) -> None:
    """The anchor carries exactly the raw-position field for its format."""
    fmt, field = _DOC_META[filename]
    docmodel = _parse(filename)
    claim_text = MANIFEST["documents"][list(_DOC_META).index(filename)]["claims"][0]["claim_text"]
    anchor = anchor_quote(docmodel, claim_text)
    assert anchor is not None
    populated = {
        "page": anchor.page,
        "para_index": anchor.para_index,
        "line_start": anchor.line_start,
    }
    assert populated[field] is not None
    for other, value in populated.items():
        if other != field:
            assert value is None


def test_canonical_text_is_whitespace_collapsed() -> None:
    for filename in _DOC_META:
        canonical = _parse(filename).canonical_text
        assert "  " not in canonical  # no runs of spaces
        assert "\t" not in canonical
        assert "\r" not in canonical


def test_pdf_ligatures_are_nfkc_folded() -> None:
    """Real PDF extraction yields ﬁ/ﬂ ligatures; NFKC folding into canonical_text
    is what lets plain-ASCII claim quotes anchor (spec sections 1.2 / 6)."""
    raw = "".join(page.get_text("text") for page in fitz.open(TESTDOCS / "pepsico_memo.pdf"))
    assert "ﬁ" in raw or "ﬂ" in raw, "fixture should contain ligatures"
    canonical = _parse("pepsico_memo.pdf").canonical_text
    assert "ﬁ" not in canonical
    assert "ﬂ" not in canonical


def test_anchor_is_case_and_whitespace_tolerant() -> None:
    docmodel = _parse("amd_memo.md")
    claim = MANIFEST["documents"][2]["claims"][0]["claim_text"]
    loud = "   " + claim.upper().replace(" ", "\n ") + "  "
    anchor = anchor_quote(docmodel, loud)
    assert anchor is not None
    # The stored quote is the true (original-case) span from canonical_text.
    assert anchor.quote in docmodel.canonical_text


def test_absent_quote_returns_none() -> None:
    docmodel = _parse("amd_memo.md")
    assert anchor_quote(docmodel, "This exact sentence appears in no fixture document.") is None
    assert anchor_quote(docmodel, "") is None


def test_scanned_pdf_is_rejected(tmp_path: Path) -> None:
    """A PDF with no text layer (< 200 chars) is rejected (spec section 1.10)."""
    doc = fitz.open()
    doc.new_page()  # blank page, no text
    scanned = tmp_path / "scanned.pdf"
    doc.save(scanned)
    doc.close()
    with pytest.raises(ValueError, match="no extractable text"):
        parse_document(scanned)
    # Sanity: the threshold constant is the documented one.
    assert MIN_PDF_TEXT_CHARS == 200


def test_unsupported_extension_is_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "notes.txt"
    bad.write_text("hello")
    with pytest.raises(ValueError, match="unsupported document format"):
        parse_document(bad)


def test_markdown_blocks_split_on_blank_lines() -> None:
    docmodel = _parse("amd_memo.md")
    # amd_memo.md interleaves headings and paragraphs separated by blank lines;
    # each non-empty block gets a distinct 1-based line_start.
    line_starts = [b.line_start for b in docmodel.blocks]
    assert all(ls is not None for ls in line_starts)
    assert line_starts == sorted(line_starts)
    assert len(set(line_starts)) == len(line_starts)

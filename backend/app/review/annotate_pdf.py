"""S6 — PDF annotation (spec sections 1.8, 9).

FROZEN CONTRACT — signature must not change.

The flagship, lossless path: for each claim `page.search_for(quote)` (NFKC-tolerant,
offset-map fallback) -> `add_highlight_annot(rects)` colored by verdict, with a popup
carrying verdict/corpus value/explanation/citation; a Review Appendix is appended via
`fitz.Story`. Original page content streams are never modified — annotations and
appended pages only. Writes the annotated copy to `out`.
"""

from __future__ import annotations

import html
import io
import unicodedata
from pathlib import Path
from typing import Optional

from ..schemas import Citation, ClaimValue, ReviewReport, ReviewReportClaim, Verdict

# Highlight stroke colors per the spec section 1.6 verdict table (RGB, 0..1).
# UNVERIFIABLE is "uncolored" — a faint neutral so the span is still anchored;
# OUT_OF_SCOPE is grey (dashed borders don't apply to text highlights).
_VERDICT_COLOR: dict[Verdict, tuple[float, float, float]] = {
    "SUPPORTED": (0.60, 0.85, 0.55),
    "CONTRADICTED": (0.95, 0.55, 0.55),
    "PARTIALLY_SUPPORTED": (0.98, 0.82, 0.40),
    "NOT_IN_CORPUS": (0.75, 0.75, 0.75),
    "OUT_OF_SCOPE": (0.75, 0.75, 0.75),
    "UNVERIFIABLE": (0.90, 0.90, 0.90),
}

# Appendix rendering colors (CSS) per verdict + claim status.
_APPENDIX_CSS_COLOR = {
    "SUPPORTED": "#2e7d32",
    "CONTRADICTED": "#c62828",
    "PARTIALLY_SUPPORTED": "#b26a00",
    "NOT_IN_CORPUS": "#616161",
    "OUT_OF_SCOPE": "#616161",
    "UNVERIFIABLE": "#455a64",
    "SKIPPED": "#616161",
    "ERROR": "#c62828",
}

_UNICODE_FOLD = {
    "–": "-", "—": "-", "−": "-",
    "‘": "'", "’": "'", "“": '"', "”": '"',
}


def _fold(text: str) -> str:
    """NFKC-fold (expands ligatures like ﬁ), map unicode dashes/quotes to ASCII,
    lowercase, and collapse whitespace to single spaces. Match-only projection
    shared by both sides of the word-fallback search (spec section 1.2)."""
    out = []
    for ch in unicodedata.normalize("NFKC", text):
        ch = _UNICODE_FOLD.get(ch, ch)
        lowered = ch.lower()
        out.append(lowered if len(lowered) == 1 else ch)
    return " ".join("".join(out).split())


def _search_rects(page, quote: str) -> list:
    """Find the quote on `page`, returning a list of fitz.Rect. Tries the native
    `page.search_for` first, then falls back to an NFKC-folded word-sequence match
    (search_for is not ligature-tolerant, so `Pacific` never matches a `Paciﬁc`
    ligature in the raw text — the whole reason for the fallback, spec section 1.2)."""
    rects = list(page.search_for(quote))
    if rects:
        return rects
    return _word_fallback_rects(page, quote)


def _word_fallback_rects(page, quote: str) -> list:
    """Offset-map fallback: fold every page word and the quote identically, locate
    the quote as a substring of the space-joined folded words, and union the rects
    of the covered words (grouped per text line for clean highlight bands)."""
    import fitz

    words = page.get_text("words")  # (x0,y0,x1,y1, word, block, line, word_no)
    if not words:
        return []
    folded_words = [_fold(w[4]) for w in words]

    # Space-joined folded stream with a char-offset -> word-index map.
    stream_parts: list[str] = []
    word_at_char: list[int] = []
    cursor = 0
    starts: list[int] = []
    for idx, fw in enumerate(folded_words):
        if idx > 0:
            stream_parts.append(" ")
            word_at_char.append(idx)  # the separator belongs to the following word
            cursor += 1
        starts.append(cursor)
        stream_parts.append(fw)
        word_at_char.extend([idx] * len(fw))
        cursor += len(fw)
    stream = "".join(stream_parts)

    needle = _fold(quote)
    if not needle:
        return []
    pos = stream.find(needle)
    if pos == -1:
        return []
    first = word_at_char[pos]
    last = word_at_char[min(pos + len(needle) - 1, len(word_at_char) - 1)]

    # Group matched words per (block, line) into merged rects.
    line_rects: dict[tuple[int, int], "fitz.Rect"] = {}
    for w in words[first : last + 1]:
        key = (w[5], w[6])
        r = fitz.Rect(w[:4])
        if key in line_rects:
            line_rects[key] |= r
        else:
            line_rects[key] = r
    return list(line_rects.values())


def _fmt_value(v: Optional[ClaimValue]) -> Optional[str]:
    if v is None or v.value is None:
        return None
    unit = f" {v.unit}" if v.unit else ""
    num = v.value
    text = str(int(num)) if float(num).is_integer() else str(num)
    return f"{text}{unit}"


def _fmt_citation(cit: Citation) -> str:
    return f"{cit.doc_name} p. {cit.pdf_page}"


def _popup_text(rc: ReviewReportClaim) -> str:
    """Popup body: verdict, corpus/doc values, explanation, and first citation."""
    result = rc.result
    lines: list[str] = []
    if result is None:
        lines.append(rc.claim.status)
        return "\n".join(lines)
    lines.append(result.verdict)
    corpus = _fmt_value(result.corpus_value)
    if corpus is not None:
        lines.append(f"Corpus value: {corpus}")
    doc = _fmt_value(result.doc_value)
    if doc is not None:
        lines.append(f"Document value: {doc}")
    if result.explanation:
        lines.append(result.explanation)
    if result.citations:
        lines.append(f"Citation: {_fmt_citation(result.citations[0])}")
    return "\n".join(lines)


def _highlight_claim(doc, rc: ReviewReportClaim) -> None:
    """Add one verdict-colored highlight for a claim that has a page anchor and a
    verdict. Claims with no result (SKIPPED) are appendix-only."""
    import fitz

    result = rc.result
    if result is None:
        return
    anchor = rc.claim.anchor
    if anchor is None or anchor.page is None:
        return
    page_index = anchor.page - 1
    if not (0 <= page_index < doc.page_count):
        return
    page = doc[page_index]
    quote = anchor.quote or rc.claim.quote
    rects = _search_rects(page, quote)
    if not rects:
        return
    quads = [fitz.Rect(r).quad for r in rects]
    annot = page.add_highlight_annot(quads)
    annot.set_colors(stroke=_VERDICT_COLOR.get(result.verdict, (0.90, 0.90, 0.90)))
    annot.set_info(title=result.verdict, content=_popup_text(rc))
    annot.update()


def _appendix_html(report: ReviewReport) -> str:
    """Self-contained HTML for the Review Appendix — lists EVERY claim including
    SKIPPED and OUT_OF_SCOPE (spec section 9)."""
    rows: list[str] = []
    for i, rc in enumerate(report.claims, start=1):
        claim = rc.claim
        result = rc.result
        verdict = result.verdict if result is not None else claim.status
        color = _APPENDIX_CSS_COLOR.get(verdict, "#616161")
        parts = [
            f'<p style="margin:0 0 2px 0"><b>{i}. [{html.escape(verdict)}]</b> '
            f'<span style="color:#555">{html.escape(claim.company or "")}'
            f'{(" · " + html.escape(claim.period)) if claim.period else ""}</span></p>',
            f'<p style="margin:0 0 2px 0">{html.escape(claim.quote)}</p>',
        ]
        if result is not None and result.explanation:
            parts.append(
                f'<p style="margin:0 0 2px 0;color:#444">{html.escape(result.explanation)}</p>'
            )
        if result is not None and result.citations:
            cites = "; ".join(_fmt_citation(c) for c in result.citations)
            parts.append(
                f'<p style="margin:0 0 2px 0;color:#666;font-size:9pt">Citations: {html.escape(cites)}</p>'
            )
        rows.append(
            f'<div style="border-left:4px solid {color};padding-left:8px;margin:0 0 10px 0">'
            + "".join(parts)
            + "</div>"
        )
    body = "".join(rows)
    return (
        '<div style="font-family:sans-serif;font-size:10pt;color:#111">'
        f'<h2 style="font-size:15pt">Review Appendix — {html.escape(report.filename)}</h2>'
        f'<p style="color:#555">{len(report.claims)} claims verified against the corpus.</p>'
        f"{body}</div>"
    )


def _append_appendix(doc, report: ReviewReport) -> None:
    """Build the appendix as a standalone PDF via fitz.Story + DocumentWriter and
    insert it after the original pages — original content streams stay untouched."""
    import fitz

    buf = io.BytesIO()
    writer = fitz.DocumentWriter(buf)
    story = fitz.Story(html=_appendix_html(report))
    mediabox = fitz.paper_rect("letter")
    where = mediabox + (54, 54, -54, -54)
    more = 1
    while more:
        dev = writer.begin_page(mediabox)
        more, _ = story.place(where)
        story.draw(dev)
        writer.end_page()
    writer.close()
    with fitz.open("pdf", buf.getvalue()) as apx:
        doc.insert_pdf(apx)


def annotate_pdf(src: str | Path, report: ReviewReport, out: str | Path) -> None:
    """Write an annotated copy of the source PDF (highlights + appendix) to `out`."""
    import fitz

    with fitz.open(src) as doc:
        for rc in report.claims:
            _highlight_claim(doc, rc)
        _append_appendix(doc, report)
        doc.save(str(out))

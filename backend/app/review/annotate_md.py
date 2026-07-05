"""S6 — Markdown annotation (spec sections 1.8, 9).

FROZEN CONTRACT — signature must not change.

Insert `<mark class="verdict-...">` spans around anchored quotes plus a footnote
appendix. Tags are inserted only — original bytes between them are never altered;
CI-style verification asserts that stripping the inserted tags reproduces the
source file byte-for-byte. Writes the annotated copy to `out`.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

from ..schemas import ClaimValue, Citation, ReviewReport, ReviewReportClaim
from .parse import _match_quote_offsets

# Sentinel opening the appended appendix. `annotate_md` writes exactly `"\n" +
# _APPENDIX_TOKEN + ...` after the marked source, so stripping from the single
# leading "\n" restores the original trailing bytes unambiguously (the source
# never contains the token). See `strip_annotations`.
_APPENDIX_TOKEN = "<!-- DILIAGENT REVIEW APPENDIX (auto-generated; strip to restore original) -->"

# Matches an inserted opening `<mark ...>` tag. Titles are HTML-escaped, so no
# unescaped `>` can appear inside the attribute list — `[^>]*` is safe.
_MARK_OPEN_RE = re.compile(r"<mark class=\"verdict-[a-z_]+\"[^>]*>")


def _verdict_class(verdict: str) -> str:
    return f"verdict-{verdict.lower()}"


def _fmt_value(v: ClaimValue | None) -> str:
    if v is None or v.value is None:
        return "n/a"
    num = f"{v.value:g}"
    return f"{num} {v.unit}" if v.unit else num


def _fmt_citation(citations: list[Citation]) -> str:
    if not citations:
        return ""
    c = citations[0]
    return f" (source: {c.doc_name} p.{c.pdf_page})"


def _title_text(rc: ReviewReportClaim, ref: str) -> str:
    """Short hover summary carried on the <mark> title attribute."""
    result = rc.result
    verdict = result.verdict if result else "SKIPPED"
    parts = [f"[{ref}] {verdict}"]
    if result and (result.doc_value or result.corpus_value):
        parts.append(f"doc: {_fmt_value(result.doc_value)}; corpus: {_fmt_value(result.corpus_value)}")
    if result and result.explanation:
        parts.append(result.explanation)
    return " — ".join(parts)


def _appendix(marked_refs: list[tuple[str, ReviewReportClaim]], report: ReviewReport) -> str:
    """Footnote-style appendix listing every claim (marked and unmarked)."""
    lines = [
        "\n" + _APPENDIX_TOKEN,
        "",
        "## Review Appendix",
        "",
        f"Automated review of `{report.filename}` — {report.summary.total_claims} claims.",
        "",
    ]
    ref_by_id = {rc.claim.claim_id: ref for ref, rc in marked_refs}
    for rc in report.claims:
        claim = rc.claim
        result = rc.result
        ref = ref_by_id.get(claim.claim_id)
        label = f"[{ref}]" if ref else f"[{claim.status}]"
        verdict = result.verdict if result else claim.status
        detail = ""
        if result and (result.doc_value or result.corpus_value):
            detail += f" doc: {_fmt_value(result.doc_value)}; corpus: {_fmt_value(result.corpus_value)}."
        if result and result.explanation:
            detail += f" {result.explanation}"
        if result:
            detail += _fmt_citation(result.citations)
        quote = claim.quote if len(claim.quote) <= 120 else claim.quote[:117] + "..."
        lines.append(f"- **{label} {verdict}** — “{quote}”{detail}")
    return "\n".join(lines) + "\n"


def strip_annotations(annotated: str) -> str:
    """Reverse `annotate_md`: drop the appended appendix and every inserted
    `<mark>`/`</mark>` tag, reproducing the original source byte-for-byte.

    The appendix is written as `"\\n" + _APPENDIX_TOKEN + ...`; cutting at that
    single leading newline removes exactly the bytes this module appended,
    regardless of the source's own trailing newlines."""
    token = "\n" + _APPENDIX_TOKEN
    idx = annotated.find(token)
    if idx != -1:
        annotated = annotated[:idx]
    annotated = _MARK_OPEN_RE.sub("", annotated)
    return annotated.replace("</mark>", "")


def annotate_md(src: str | Path, report: ReviewReport, out: str | Path) -> None:
    """Write an annotated copy of the source Markdown (<mark> spans + footnotes) to `out`."""
    raw = Path(src).read_bytes().decode("utf-8")

    # Resolve each highlightable claim to a verbatim span in the RAW source
    # (offsets index raw bytes, not the whitespace-collapsed canonical_text).
    spans: list[tuple[int, int, str, ReviewReportClaim]] = []
    for rc in report.claims:
        if rc.result is None or rc.claim.anchor is None:
            continue
        quote = rc.claim.anchor.quote or rc.claim.quote
        match = _match_quote_offsets(raw, quote)
        if match is None:
            continue
        start, end = match
        spans.append((start, end, rc.result.verdict, rc))

    # Non-overlapping, document order; number surviving marks R1..Rn.
    spans.sort(key=lambda s: s[0])
    chosen: list[tuple[int, int, str, ReviewReportClaim]] = []
    last_end = -1
    for start, end, verdict, rc in spans:
        if start < last_end:
            continue
        chosen.append((start, end, verdict, rc))
        last_end = end

    marked_refs = [(f"R{i + 1}", rc) for i, (_, _, _, rc) in enumerate(chosen)]

    # Insert tags back-to-front so earlier offsets stay valid.
    text = raw
    for (start, end, verdict, rc), (ref, _) in zip(reversed(chosen), reversed(marked_refs)):
        title = html.escape(_title_text(rc, ref), quote=True)
        open_tag = f'<mark class="{_verdict_class(verdict)}" title="{title}">'
        text = text[:end] + "</mark>" + text[end:]
        text = text[:start] + open_tag + text[start:]

    text += _appendix(marked_refs, report)
    Path(out).write_bytes(text.encode("utf-8"))

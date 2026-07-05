"""S6 — HTML review report (spec section 9).

FROZEN CONTRACT — signature must not change.

Render a self-contained (inline CSS), theme-aware HTML report: document text with
verdict-highlighted spans that open a verdict card with citations linking into the
existing `/corpus/{company}/{doc_id}/page/{n}` viewer. This is what the frontend
embeds for all three formats.
"""

from __future__ import annotations

import html
from urllib.parse import quote as urlquote

from ..schemas import (
    Citation,
    ClaimValue,
    DocModel,
    ReviewReport,
    ReviewReportClaim,
    ReviewSummary,
)

# Verdict -> (human label, CSS class). Colors are defined once in _CSS.
_VERDICT_LABEL: dict[str, str] = {
    "SUPPORTED": "Supported",
    "CONTRADICTED": "Contradicted",
    "PARTIALLY_SUPPORTED": "Partially supported",
    "NOT_IN_CORPUS": "Not in corpus",
    "OUT_OF_SCOPE": "Out of scope",
    "UNVERIFIABLE": "Unverifiable",
    "SKIPPED": "Skipped",
    "ERROR": "Error",
}

_SUMMARY_ORDER: list[tuple[str, str]] = [
    ("SUPPORTED", "supported"),
    ("CONTRADICTED", "contradicted"),
    ("PARTIALLY_SUPPORTED", "partially_supported"),
    ("NOT_IN_CORPUS", "not_in_corpus"),
    ("OUT_OF_SCOPE", "out_of_scope"),
    ("UNVERIFIABLE", "unverifiable"),
    ("SKIPPED", "skipped"),
    ("ERROR", "error"),
]

_CSS = """
:root{
  --bg:#ffffff;--fg:#1a1a1a;--muted:#666;--card:#f7f7f8;--border:#e2e2e6;
  --v-supported:#2e7d32;--v-supported-bg:#d7efdb;
  --v-contradicted:#c62828;--v-contradicted-bg:#f7d6d6;
  --v-partial:#b26a00;--v-partial-bg:#fbe7c4;
  --v-notincorpus:#616161;--v-notincorpus-bg:#e4e4e6;
  --v-outofscope:#616161;--v-outofscope-bg:#ececed;
  --v-unverifiable:#455a64;--v-unverifiable-bg:#eef1f2;
}
@media (prefers-color-scheme:dark){
  :root{
    --bg:#16171a;--fg:#e8e8ea;--muted:#9a9aa2;--card:#1f2024;--border:#33343a;
    --v-supported:#7bd88f;--v-supported-bg:#1f3a24;
    --v-contradicted:#f28b82;--v-contradicted-bg:#3a1f1f;
    --v-partial:#f0c265;--v-partial-bg:#3a2e14;
    --v-notincorpus:#b0b0b6;--v-notincorpus-bg:#2a2b2f;
    --v-outofscope:#b0b0b6;--v-outofscope-bg:#26272b;
    --v-unverifiable:#a7bcc6;--v-unverifiable-bg:#22282b;
  }
}
*{box-sizing:border-box}
.dg-report{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  background:var(--bg);color:var(--fg);line-height:1.55;padding:20px;max-width:900px;margin:0 auto}
.dg-report h1{font-size:20px;margin:0 0 4px}
.dg-report h2{font-size:15px;margin:24px 0 10px;border-bottom:1px solid var(--border);padding-bottom:4px}
.dg-sub{color:var(--muted);font-size:13px;margin:0 0 16px}
.dg-chips{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 8px}
.dg-chip{display:inline-flex;align-items:center;gap:6px;padding:3px 10px;border-radius:999px;
  font-size:12px;font-weight:600;border:1px solid var(--border)}
.dg-doc{white-space:pre-wrap;word-wrap:break-word;background:var(--card);border:1px solid var(--border);
  border-radius:8px;padding:16px;font-size:13px;overflow-x:auto}
mark.dg-mark{border-radius:3px;padding:0 1px;text-decoration:none;color:inherit;cursor:pointer}
.dg-cards{display:flex;flex-direction:column;gap:12px}
.dg-card{border:1px solid var(--border);border-left-width:5px;border-radius:8px;background:var(--card);padding:12px 14px}
.dg-card:target{outline:2px solid var(--fg);outline-offset:2px}
.dg-card .dg-q{font-size:13px;margin:6px 0}
.dg-card .dg-exp{font-size:13px;color:var(--fg);margin:6px 0}
.dg-vals{display:flex;flex-wrap:wrap;gap:16px;font-size:12px;color:var(--muted);margin:6px 0}
.dg-badge{display:inline-block;font-size:11px;font-weight:700;padding:2px 8px;border-radius:4px}
.dg-cites{margin:6px 0 0;font-size:12px}
.dg-cites a{color:var(--v-supported);text-decoration:underline}
.dg-meta{color:var(--muted);font-size:11px}
.v-supported{--vc:var(--v-supported);--vbg:var(--v-supported-bg)}
.v-contradicted{--vc:var(--v-contradicted);--vbg:var(--v-contradicted-bg)}
.v-partially_supported{--vc:var(--v-partial);--vbg:var(--v-partial-bg)}
.v-not_in_corpus{--vc:var(--v-notincorpus);--vbg:var(--v-notincorpus-bg)}
.v-out_of_scope{--vc:var(--v-outofscope);--vbg:var(--v-outofscope-bg);border-left-style:dashed}
.v-unverifiable{--vc:var(--v-unverifiable);--vbg:var(--v-unverifiable-bg)}
.v-skipped,.v-error{--vc:var(--v-notincorpus);--vbg:var(--v-notincorpus-bg)}
.dg-card{border-left-color:var(--vc)}
.dg-badge{color:#fff;background:var(--vc)}
mark.dg-mark{background:var(--vbg);box-shadow:inset 0 -2px 0 var(--vc)}
.dg-chip{color:var(--vc);background:var(--vbg);border-color:var(--vbg)}
""".strip()


def _verdict_class(verdict: str) -> str:
    return f"v-{verdict.lower()}"


def _fmt_value(v: ClaimValue | None) -> str | None:
    if v is None or v.value is None:
        return None
    num = v.value
    text = str(int(num)) if float(num).is_integer() else str(num)
    return f"{text}{(' ' + v.unit) if v.unit else ''}"


def _citation_href(company: str, cit: Citation) -> str:
    return f"/corpus/{urlquote(company, safe='')}/{urlquote(cit.doc_id, safe='')}/page/{cit.pdf_page}"


def _highlighted_doc(report: ReviewReport, docmodel: DocModel) -> str:
    """Render canonical_text with a verdict-colored <mark> around each anchored
    claim span; the mark links to its verdict card. Overlapping anchors keep the
    first (by start offset) and drop the rest so spans never nest."""
    text = docmodel.canonical_text
    spans: list[tuple[int, int, str, str]] = []
    for rc in report.claims:
        anchor = rc.claim.anchor
        if anchor is None:
            continue
        s, e = anchor.char_start, anchor.char_end
        if not (0 <= s < e <= len(text)):
            continue
        verdict = rc.result.verdict if rc.result is not None else rc.claim.status
        spans.append((s, e, rc.claim.claim_id, verdict))
    spans.sort(key=lambda x: (x[0], -x[1]))

    out: list[str] = []
    cursor = 0
    for s, e, claim_id, verdict in spans:
        if s < cursor:
            continue  # overlaps an already-emitted mark
        out.append(html.escape(text[cursor:s]))
        cls = _verdict_class(verdict)
        title = html.escape(_VERDICT_LABEL.get(verdict, verdict))
        out.append(
            f'<a href="#card-{html.escape(claim_id)}" style="text-decoration:none;color:inherit">'
            f'<mark class="dg-mark {cls}" title="{title}">{html.escape(text[s:e])}</mark></a>'
        )
        cursor = e
    out.append(html.escape(text[cursor:]))
    return "".join(out)


def _summary_chips(summary: ReviewSummary) -> str:
    chips: list[str] = []
    for verdict, field in _SUMMARY_ORDER:
        count = getattr(summary, field, 0)
        if not count:
            continue
        cls = _verdict_class(verdict)
        label = html.escape(_VERDICT_LABEL.get(verdict, verdict))
        chips.append(f'<span class="dg-chip {cls}">{label} · {count}</span>')
    if not chips:
        chips.append('<span class="dg-chip">No claims</span>')
    return '<div class="dg-chips">' + "".join(chips) + "</div>"


def _claim_card(rc: ReviewReportClaim, company_scope: list[str]) -> str:
    claim = rc.claim
    result = rc.result
    verdict = result.verdict if result is not None else claim.status
    cls = _verdict_class(verdict)
    label = html.escape(_VERDICT_LABEL.get(verdict, verdict))

    meta_bits = [claim.company or ""]
    if claim.period:
        meta_bits.append(claim.period)
    if claim.metric:
        meta_bits.append(claim.metric)
    meta = html.escape(" · ".join(b for b in meta_bits if b))

    parts = [
        f'<div id="card-{html.escape(claim.claim_id)}" class="dg-card {cls}">',
        f'<div><span class="dg-badge">{label}</span> '
        f'<span class="dg-meta">{meta}</span></div>',
        f'<div class="dg-q">“{html.escape(claim.quote)}”</div>',
    ]

    if result is not None:
        vals: list[str] = []
        doc_v = _fmt_value(result.doc_value)
        if doc_v is not None:
            vals.append(f"<span><b>Document:</b> {html.escape(doc_v)}</span>")
        corpus_v = _fmt_value(result.corpus_value)
        if corpus_v is not None:
            vals.append(f"<span><b>Corpus:</b> {html.escape(corpus_v)}</span>")
        if vals:
            parts.append('<div class="dg-vals">' + "".join(vals) + "</div>")
        if result.explanation:
            parts.append(f'<div class="dg-exp">{html.escape(result.explanation)}</div>')
        if result.citations:
            company = claim.company or (company_scope[0] if company_scope else "")
            links = []
            for cit in result.citations:
                href = _citation_href(company, cit)
                links.append(
                    f'<a href="{html.escape(href)}">{html.escape(_fmt_citation(cit))}</a>'
                )
            parts.append('<div class="dg-cites">Citations: ' + " · ".join(links) + "</div>")

    parts.append("</div>")
    return "".join(parts)


def _fmt_citation(cit: Citation) -> str:
    return f"{cit.doc_name} p. {cit.pdf_page}"


def render_report_html(report: ReviewReport, docmodel: DocModel) -> str:
    """Render the ReviewReport to a self-contained HTML string."""
    scope = ", ".join(html.escape(c) for c in report.company_scope) or "—"
    cards = "".join(_claim_card(rc, report.company_scope) for rc in report.claims)
    return (
        f"<style>{_CSS}</style>"
        '<div class="dg-report">'
        f"<h1>DiliAgent review — {html.escape(report.filename)}</h1>"
        f'<p class="dg-sub">Review {html.escape(report.review_id)} · '
        f"{report.summary.total_claims} claims · scope: {scope}</p>"
        f"{_summary_chips(report.summary)}"
        "<h2>Document</h2>"
        f'<div class="dg-doc">{_highlighted_doc(report, docmodel)}</div>'
        "<h2>Claims</h2>"
        f'<div class="dg-cards">{cards}</div>'
        "</div>"
    )

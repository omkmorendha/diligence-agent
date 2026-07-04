"""D2 — Parse-test filings (spec section 6 D2, Step 6).

For each filing PDF: extract text per page, measure chars/page, detect near-empty
pages, detect table-ish lines, flag suspicious extraction failures, preserve PDF
page numbers and document metadata. Veto companies/documents that fail parsing.

This is one of the largest schedule risks — front-load it.

Primary parser: pymupdf/fitz. Fallback: pdfplumber.

Outputs:
    data/parse_report.json
    data/pages/{company}/{doc_id}.json   (per-page text, for get_pages + citations)

Page-number convention (spec section 8, AMBIGUITIES.md section 3): pages are
recorded 1-indexed (`page` = pymupdf's 0-indexed `page.number` + 1), matching
the `pdf_page` convention used by d5_select_subset.py, get_pages, and the
corpus endpoint.

Usage:
    uv run dataset_builder/d2_parse_test.py
    uv run dataset_builder/d2_parse_test.py --limit 5   # debug
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import fitz  # pymupdf
import pdfplumber

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
FILINGS_DIR = ROOT / "data" / "filings"
PAGES_DIR = ROOT / "data" / "pages"
FINANCEBENCH_PATH = RAW_DIR / "financebench.jsonl"
REPORT_PATH = ROOT / "data" / "parse_report.json"

# --- quality thresholds (heuristic; tuned for dense financial-filing prose) ---
EMPTY_CHAR_THRESHOLD = 15       # fewer non-whitespace chars than this -> "empty"
LOW_TEXT_CHAR_THRESHOLD = 200   # fewer than this (but not empty) -> "low_text"
SUSPICIOUS_EMPTY_FRACTION = 0.35  # >this fraction of empty pages -> doc flagged suspicious

_TABLE_LINE_RE = re.compile(r"\S+")


def slugify(name: str) -> str:
    """Filesystem-safe company slug (lowercase, alnum + underscore)."""
    s = re.sub(r"[^a-z0-9]+", "_", name.strip().lower())
    return s.strip("_") or "unknown"


def _load_company_map() -> dict[str, str]:
    """doc_name -> company, sourced from D1's output (falls back to doc prefix)."""
    mapping: dict[str, str] = {}
    if FINANCEBENCH_PATH.exists():
        for line in FINANCEBENCH_PATH.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            mapping.setdefault(row["doc_name"], row["company"])
    return mapping


def _is_table_like_line(line: str) -> bool:
    """A line that looks like a table row: several whitespace-separated tokens,
    most of which contain a digit, '$', '%', or parens (financial-table shape)."""
    tokens = _TABLE_LINE_RE.findall(line)
    if len(tokens) < 3:
        return False
    numericish = sum(1 for t in tokens if re.search(r"[\d$%()]", t))
    return numericish >= max(2, len(tokens) // 2)


def _page_stats(text: str) -> dict[str, Any]:
    stripped = text.strip()
    char_count = len(stripped)
    lines = [ln for ln in text.splitlines() if ln.strip()]
    table_lines = sum(1 for ln in lines if _is_table_like_line(ln))
    table_like = bool(lines) and (table_lines / len(lines)) >= 0.2 and table_lines >= 3
    return {
        "char_count": char_count,
        "empty": char_count < EMPTY_CHAR_THRESHOLD,
        "low_text": EMPTY_CHAR_THRESHOLD <= char_count < LOW_TEXT_CHAR_THRESHOLD,
        "table_like": table_like,
    }


def _extract_with_fitz(pdf_path: Path) -> list[str]:
    doc = fitz.open(pdf_path)
    try:
        return [doc[i].get_text() for i in range(doc.page_count)]
    finally:
        doc.close()


def _extract_with_pdfplumber(pdf_path: Path) -> list[str]:
    with pdfplumber.open(pdf_path) as pdf:
        return [(page.extract_text() or "") for page in pdf.pages]


def _pages_payload(pages_text: list[str]) -> list[dict[str, Any]]:
    payload = []
    for i, text in enumerate(pages_text):
        stats = _page_stats(text)
        payload.append(
            {
                "page": i + 1,  # 1-indexed pdf page, matches pdf_page convention
                "text": text,
                **stats,
            }
        )
    return payload


def _parse_one(doc_name: str, company: str) -> dict[str, Any]:
    pdf_path = FILINGS_DIR / f"{doc_name}.pdf"
    entry: dict[str, Any] = {
        "doc_id": doc_name,
        "doc_name": doc_name,
        "company": company,
        "company_slug": slugify(company),
        "pdf_path": f"data/filings/{doc_name}.pdf",
        "status": "failed",
        "parser_used": None,
        "num_pages": 0,
        "empty_pages": [],
        "low_text_pages": [],
        "table_like_pages": [],
        "avg_chars_per_page": 0.0,
        "min_chars_per_page": 0,
        "suspicious_extraction": False,
        "error": None,
    }

    if not pdf_path.exists():
        entry["error"] = f"pdf not found: {pdf_path}"
        return entry

    pages_text: list[str] | None = None
    parser_used: str | None = None
    fitz_error: str | None = None

    try:
        pages_text = _extract_with_fitz(pdf_path)
        parser_used = "pymupdf"
    except Exception as exc:  # noqa: BLE001 - any pymupdf failure triggers fallback
        fitz_error = str(exc)

    if pages_text is not None:
        stats = [_page_stats(t) for t in pages_text]
        empty_fraction = (sum(1 for s in stats if s["empty"]) / len(stats)) if stats else 1.0
        suspicious = empty_fraction > SUSPICIOUS_EMPTY_FRACTION
        if suspicious or not stats:
            try:
                fallback_text = _extract_with_pdfplumber(pdf_path)
                fallback_stats = [_page_stats(t) for t in fallback_text]
                fallback_empty = (
                    sum(1 for s in fallback_stats if s["empty"]) / len(fallback_stats)
                    if fallback_stats
                    else 1.0
                )
                if fallback_empty < empty_fraction:
                    pages_text, parser_used = fallback_text, "pdfplumber_fallback"
                    suspicious = fallback_empty > SUSPICIOUS_EMPTY_FRACTION
            except Exception:  # noqa: BLE001 - keep the pymupdf result if fallback errors
                pass
        entry["suspicious_extraction"] = suspicious

    if pages_text is None:
        # pymupdf failed outright; try pdfplumber as the last resort.
        try:
            pages_text = _extract_with_pdfplumber(pdf_path)
            parser_used = "pdfplumber"
        except Exception as exc:  # noqa: BLE001
            entry["error"] = f"pymupdf: {fitz_error}; pdfplumber: {exc}"
            return entry

    pages_payload = _pages_payload(pages_text)
    char_counts = [p["char_count"] for p in pages_payload]

    entry.update(
        {
            "status": "ok",
            "parser_used": parser_used,
            "num_pages": len(pages_payload),
            "empty_pages": [p["page"] for p in pages_payload if p["empty"]],
            "low_text_pages": [p["page"] for p in pages_payload if p["low_text"]],
            "table_like_pages": [p["page"] for p in pages_payload if p["table_like"]],
            "avg_chars_per_page": round(sum(char_counts) / len(char_counts), 1) if char_counts else 0.0,
            "min_chars_per_page": min(char_counts) if char_counts else 0,
        }
    )

    out_dir = PAGES_DIR / entry["company_slug"]
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{doc_name}.json").write_text(
        json.dumps(
            {
                "doc_id": doc_name,
                "doc_name": doc_name,
                "company": company,
                "company_slug": entry["company_slug"],
                "parser_used": parser_used,
                "num_pages": len(pages_payload),
                "pages": pages_payload,
            },
            indent=2,
        )
    )
    return entry


def main() -> int:
    ap = argparse.ArgumentParser(description="Parse-test all filing PDFs (D2).")
    ap.add_argument("--limit", type=int, default=None, help="Limit number of docs (debug).")
    args = ap.parse_args()

    PAGES_DIR.mkdir(parents=True, exist_ok=True)
    company_map = _load_company_map()

    pdf_paths = sorted(FILINGS_DIR.glob("*.pdf"))
    if args.limit:
        pdf_paths = pdf_paths[: args.limit]

    if not pdf_paths:
        raise SystemExit(f"no PDFs found in {FILINGS_DIR} — run d1_pull_raw.py first")

    docs: list[dict[str, Any]] = []
    for i, pdf_path in enumerate(pdf_paths, 1):
        doc_name = pdf_path.stem
        company = company_map.get(doc_name, doc_name.split("_")[0])
        entry = _parse_one(doc_name, company)
        docs.append(entry)
        flag = ""
        if entry["status"] != "ok":
            flag = " FAILED"
        elif entry["suspicious_extraction"]:
            flag = " SUSPICIOUS"
        print(
            f"[d2] ({i}/{len(pdf_paths)}) {doc_name}: {entry['status']} "
            f"pages={entry['num_pages']} parser={entry['parser_used']}{flag}",
            file=sys.stderr,
        )

    ok_docs = [d for d in docs if d["status"] == "ok"]
    failed_docs = [d for d in docs if d["status"] != "ok"]
    suspicious_docs = [d for d in ok_docs if d["suspicious_extraction"]]

    report = {
        "parser_primary": "pymupdf",
        "parser_fallback": "pdfplumber",
        "thresholds": {
            "empty_char_threshold": EMPTY_CHAR_THRESHOLD,
            "low_text_char_threshold": LOW_TEXT_CHAR_THRESHOLD,
            "suspicious_empty_fraction": SUSPICIOUS_EMPTY_FRACTION,
        },
        "docs_total": len(docs),
        "docs_ok": len(ok_docs),
        "docs_failed": len(failed_docs),
        "docs_suspicious": len(suspicious_docs),
        "total_pages": sum(d["num_pages"] for d in ok_docs),
        "total_empty_pages": sum(len(d["empty_pages"]) for d in ok_docs),
        "total_low_text_pages": sum(len(d["low_text_pages"]) for d in ok_docs),
        "total_table_like_pages": sum(len(d["table_like_pages"]) for d in ok_docs),
        "vetoed_doc_ids": [d["doc_id"] for d in failed_docs],
        "suspicious_doc_ids": [d["doc_id"] for d in suspicious_docs],
        "docs": docs,
    }
    REPORT_PATH.write_text(json.dumps(report, indent=2))

    print(
        f"\n[d2] parsed {report['docs_ok']}/{report['docs_total']} docs ok "
        f"({report['docs_failed']} failed, {report['docs_suspicious']} suspicious).",
        file=sys.stderr,
    )
    print(f"[d2] wrote {REPORT_PATH}", file=sys.stderr)
    print(f"[d2] wrote per-doc pages under {PAGES_DIR}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

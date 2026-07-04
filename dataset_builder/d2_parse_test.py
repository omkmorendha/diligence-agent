"""D2 — Parse-test filings (spec section 6 D2, Step 6).

For each filing PDF: extract text per page, measure chars/page, detect near-empty
pages, detect table-ish lines, flag suspicious extraction failures, preserve PDF
page numbers and document metadata. Veto companies/documents that fail parsing.

This is one of the largest schedule risks — front-load it.

Primary parser: pymupdf/fitz. Fallback: pdfplumber.

Outputs:
    data/parse_report.json
    data/pages/{company}/{doc_id}.json   (per-page text, for get_pages + citations)

TODO(Step 6).
"""

from __future__ import annotations


def main() -> int:
    raise NotImplementedError("d2 parse-test: implement in Step 6 (spec section 6 D2).")


if __name__ == "__main__":
    raise SystemExit(main())

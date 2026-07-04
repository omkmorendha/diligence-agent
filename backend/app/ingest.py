"""Ingestion / chunking / embedding (spec section 25, Step 10).

Turns the subset's filing PDFs into a persisted, document-aware, page-aware
embedded index the retrieval layer can search.

Requirements (spec):
    * deterministic chunk_id: "company_slug:doc_id:p{page}:c{chunk_index}"
    * document-aware chunks (preserve doc identity)
    * page-aware chunks (preserve PDF page numbers)
    * local embeddings (sentence-transformers all-MiniLM-L6-v2)
    * persisted index on disk (NumPy; no vector DB for v0)

Primary parser pymupdf/fitz, fallback pdfplumber (spec section 3).

TODO(Step 10).
"""

from __future__ import annotations

from pathlib import Path


def ingest_company(company: str) -> Path:
    """Parse + chunk + embed a company's filings; persist the index. Returns index dir."""
    raise NotImplementedError("ingest: implement in Step 10 (spec section 25).")

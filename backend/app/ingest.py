"""Ingestion / chunking / embedding (spec section 25, Step 10).

Turns a company's parsed filing pages (`data/pages/{company_slug}/{doc_id}.json`,
written by D2 — `dataset_builder/d2_parse_test.py`) into a persisted,
document-aware, page-aware embedded index the retrieval layer can search.

Requirements (spec):
    * deterministic chunk_id: "company_slug:doc_id:p{page}:c{chunk_index}"
    * document-aware chunks (preserve doc identity)
    * page-aware chunks (preserve PDF page numbers; a chunk never spans pages)
    * local embeddings (sentence-transformers, EMBED_MODEL from .env)
    * persisted index on disk (NumPy; no vector DB for v0)

Index format (AMBIGUITIES.md section 9 — decided): per company, under
`data/index/{company_slug}/`:
    embeddings.npy   float32 matrix, shape (num_chunks, embed_dim), L2-normalized
                     rows in the same order as chunks.jsonl (so index i in
                     embeddings.npy <-> line i in chunks.jsonl).
    chunks.jsonl     one JSON object per chunk (chunk metadata, no `score` field
                     -- `score` is only meaningful at query time; see retrieval.py
                     which attaches it and returns `schemas.Chunk` instances).
    meta.json        {embed_model, embed_dim, num_chunks, chunk_chars,
                       chunk_overlap, doc_ids, built_at}

Chunking is a deterministic character-offset sliding window PER PAGE (never
crossing a page boundary, so `char_start`/`char_end` are offsets into that
page's own text and every chunk carries exact page provenance). Re-running
ingestion over the same source pages reproduces byte-identical chunk_ids,
offsets, and text -- embeddings are the only non-bit-exact output (float
round-off across runs/hardware), so identity is defined over chunk_id +
text + offsets, not the embedding vectors.

`doc_type` / `filing_period` are derived from `data/raw/financebench.jsonl`
metadata (AMBIGUITIES.md section 4): `doc_type` maps FinanceBench's native
label onto the spec's DocType enum (`Earnings -> other`), and `filing_period`
is parsed from `doc_name` when it encodes a quarter (e.g. `..._2023Q2_...` ->
`"2023Q2"`), else falls back to `FY{doc_period}`.

Usage:
    uv run --project backend backend/app/ingest.py --company amcor
    uv run --project backend backend/app/ingest.py --candidates   # 7 candidate companies
    uv run --project backend backend/app/ingest.py --all          # every company under data/pages
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from . import config

# --- chunking parameters (AMBIGUITIES.md section 9: no spec-mandated size) ---
CHUNK_CHARS = 1000
CHUNK_OVERLAP = 150
_EMBED_LOCK = threading.Lock()

# The 7 candidate companies with >=8 usable FinanceBench questions
# (data/dataset_profile.json -> candidate_companies), spec section 25 Step 10
# acceptance criteria minimum bar.
CANDIDATE_COMPANY_SLUGS = [
    "pepsico",
    "amcor",
    "johnson_johnson",
    "3m",
    "amd",
    "best_buy",
    "boeing",
]

_DOC_TYPE_MAP = {
    "10k": "10k",
    "10q": "10q",
    "8k": "8k",
    "earnings": "other",  # AMBIGUITIES.md section 4: Earnings not in spec enum
}
_QUARTER_RE = re.compile(r"_(\d{4})(Q[1-4])_", re.IGNORECASE)


def slugify(name: str) -> str:
    """Filesystem-safe company slug (lowercase, alnum + underscore). Matches
    `dataset_builder/d2_parse_test.py::slugify` so pages/index directories align."""
    s = re.sub(r"[^a-z0-9]+", "_", name.strip().lower())
    return s.strip("_") or "unknown"


@lru_cache(maxsize=1)
def _doc_meta_map() -> dict[str, tuple[str, str]]:
    """doc_name -> (doc_type, filing_period), sourced from data/raw/financebench.jsonl."""
    mapping: dict[str, tuple[str, str]] = {}
    if not config.RAW_DIR.joinpath("financebench.jsonl").exists():
        return mapping
    for line in (config.RAW_DIR / "financebench.jsonl").read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        doc_name = row.get("doc_name")
        if not doc_name or doc_name in mapping:
            continue
        raw_type = str(row.get("doc_type") or "").strip().lower()
        doc_type = _DOC_TYPE_MAP.get(raw_type, "other")
        period = _derive_filing_period(doc_name, row.get("doc_period"))
        mapping[doc_name] = (doc_type, period)
    return mapping


def _derive_filing_period(doc_name: str, doc_period: Any) -> str:
    """`..._2023Q2_..." -> "2023Q2"`; else `FY{doc_period}` (spec section 8,
    AMBIGUITIES.md section 4)."""
    m = _QUARTER_RE.search(f"_{doc_name}_")
    if m:
        return f"{m.group(1)}{m.group(2).upper()}"
    if doc_period is None:
        return ""
    return f"FY{doc_period}"


def _doc_type_and_period(doc_id: str) -> tuple[str, str]:
    doc_type, period = _doc_meta_map().get(doc_id, ("other", ""))
    if not period:
        # fall back to parsing doc_id directly (covers docs financebench.jsonl
        # doesn't carry rows for, e.g. filtered/dropped questions)
        m = re.search(r"_(\d{4})", doc_id)
        period = f"FY{m.group(1)}" if m else ""
    return doc_type, period


def _chunk_offsets(length: int, chunk_chars: int, overlap: int) -> list[tuple[int, int]]:
    """Deterministic fixed-size sliding-window char offsets over one page's text."""
    if length <= 0:
        return []
    step = max(1, chunk_chars - overlap)
    offsets: list[tuple[int, int]] = []
    start = 0
    while start < length:
        end = min(start + chunk_chars, length)
        offsets.append((start, end))
        if end >= length:
            break
        start += step
    return offsets


def _chunk_page(
    company_slug: str,
    doc_id: str,
    doc_name: str,
    doc_type: str,
    filing_period: str,
    page: dict[str, Any],
) -> list[dict[str, Any]]:
    """Chunk one page's text. Never crosses a page boundary. Skips pages with no
    usable text (parser-flagged `empty`)."""
    text = page.get("text") or ""
    if page.get("empty") or not text.strip():
        return []
    chunks = []
    for chunk_index, (start, end) in enumerate(_chunk_offsets(len(text), CHUNK_CHARS, CHUNK_OVERLAP)):
        chunk_text = text[start:end]
        if not chunk_text.strip():
            continue
        chunks.append(
            {
                "chunk_id": f"{company_slug}:{doc_id}:p{page['page']}:c{chunk_index}",
                "company": company_slug,
                "doc_id": doc_id,
                "doc_name": doc_name,
                "doc_type": doc_type,
                "filing_period": filing_period,
                "page": page["page"],
                "text": chunk_text,
                "char_start": start,
                "char_end": end,
            }
        )
    return chunks


@lru_cache(maxsize=1)
def _embedder():
    """Lazily construct (and cache) the sentence-transformers embedder."""
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(config.EMBED_MODEL)


def embed_texts(texts: list[str]) -> np.ndarray:
    """Embed a batch of texts. Returns an L2-normalized float32 matrix (cosine
    similarity == dot product on these rows)."""
    # SentenceTransformer/PyTorch lazy initialization is not fully thread-safe in
    # the review verifier fan-out path. Serialize access to the shared model; the
    # expensive filing search/rerank work around it still runs concurrently.
    with _EMBED_LOCK:
        embedder = _embedder()
        if not texts:
            dim = embedder.get_sentence_embedding_dimension()
            return np.zeros((0, dim), dtype=np.float32)
        vecs = embedder.encode(
            texts,
            batch_size=64,
            show_progress_bar=False,
            normalize_embeddings=True,
            convert_to_numpy=True,
        )
    return vecs.astype(np.float32)


def index_dir_for(company: str) -> Path:
    return config.INDEX_DIR / slugify(company)


def _pages_dir_for(company: str) -> Path:
    return config.PAGES_DIR / slugify(company)


def ingest_company(company: str) -> Path:
    """Parse (already-parsed pages) + chunk + embed a company's filings;
    persist the index. Returns the index directory."""
    slug = slugify(company)
    pages_dir = _pages_dir_for(slug)
    if not pages_dir.is_dir():
        raise FileNotFoundError(
            f"no parsed pages for company '{company}' (slug '{slug}') under {pages_dir}; "
            "run dataset_builder/d2_parse_test.py first"
        )

    doc_paths = sorted(pages_dir.glob("*.json"))
    if not doc_paths:
        raise FileNotFoundError(f"no doc page files under {pages_dir}")

    all_chunks: list[dict[str, Any]] = []
    doc_ids: list[str] = []
    for doc_path in doc_paths:
        doc = json.loads(doc_path.read_text())
        doc_id = doc["doc_id"]
        doc_name = doc.get("doc_name", doc_id)
        doc_type, filing_period = _doc_type_and_period(doc_id)
        doc_ids.append(doc_id)
        for page in doc.get("pages", []):
            all_chunks.extend(_chunk_page(slug, doc_id, doc_name, doc_type, filing_period, page))

    embeddings = embed_texts([c["text"] for c in all_chunks])

    out_dir = index_dir_for(slug)
    out_dir.mkdir(parents=True, exist_ok=True)

    np.save(out_dir / "embeddings.npy", embeddings)
    with (out_dir / "chunks.jsonl").open("w") as f:
        for c in all_chunks:
            f.write(json.dumps(c) + "\n")

    meta = {
        "company": slug,
        "embed_model": config.EMBED_MODEL,
        "embed_dim": int(embeddings.shape[1]) if embeddings.size else 0,
        "num_chunks": len(all_chunks),
        "chunk_chars": CHUNK_CHARS,
        "chunk_overlap": CHUNK_OVERLAP,
        "doc_ids": doc_ids,
        "built_at": datetime.now(timezone.utc).isoformat(),
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2))
    return out_dir


def _all_company_slugs() -> list[str]:
    if not config.PAGES_DIR.is_dir():
        return []
    return sorted(p.name for p in config.PAGES_DIR.iterdir() if p.is_dir())


def main() -> int:
    ap = argparse.ArgumentParser(description="Ingest (chunk + embed + persist) a company's filings (D10).")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--company", action="append", help="Company slug or name (repeatable).")
    group.add_argument(
        "--candidates", action="store_true",
        help="Ingest the 7 candidate companies with >=8 usable questions.",
    )
    group.add_argument("--all", action="store_true", help="Ingest every company under data/pages/.")
    args = ap.parse_args()

    if args.all:
        companies = _all_company_slugs()
    elif args.candidates:
        companies = CANDIDATE_COMPANY_SLUGS
    else:
        companies = args.company

    for i, company in enumerate(companies, 1):
        t0 = time.time()
        out_dir = ingest_company(company)
        meta = json.loads((out_dir / "meta.json").read_text())
        print(
            f"[ingest] ({i}/{len(companies)}) {company}: {meta['num_chunks']} chunks "
            f"from {len(meta['doc_ids'])} docs -> {out_dir} ({time.time() - t0:.1f}s)",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

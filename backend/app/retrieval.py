"""Retrieval (spec section 25, Step 10).

Cosine-similarity search over a company's persisted embedded index (built by
`ingest.py`). Backs the `search_filing` agent tool (spec section 13) and the
baseline's single retrieve step.

Index layout consumed here (see ingest.py docstring):
    data/index/{company_slug}/embeddings.npy   L2-normalized float32 matrix
    data/index/{company_slug}/chunks.jsonl     chunk metadata, row-aligned
    data/index/{company_slug}/meta.json        embed_model / build info
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np

from .ingest import embed_texts, index_dir_for, slugify
from .schemas import Chunk

# in-process cache: company slug -> (mtime, embeddings, chunk_rows)
_INDEX_CACHE: dict[str, tuple[float, np.ndarray, list[dict[str, Any]]]] = {}


class IndexNotFoundError(FileNotFoundError):
    """Raised when a company has no persisted index yet (run ingest.py first)."""


def _load_index(company_slug: str) -> tuple[np.ndarray, list[dict[str, Any]]]:
    out_dir = index_dir_for(company_slug)
    chunks_path = out_dir / "chunks.jsonl"
    embeddings_path = out_dir / "embeddings.npy"
    meta_path = out_dir / "meta.json"

    if not (chunks_path.exists() and embeddings_path.exists() and meta_path.exists()):
        raise IndexNotFoundError(
            f"no persisted index for company '{company_slug}' under {out_dir}; "
            "run `uv run --project backend backend/app/ingest.py --company "
            f"{company_slug}` first"
        )

    mtime = meta_path.stat().st_mtime
    cached = _INDEX_CACHE.get(company_slug)
    if cached is not None and cached[0] == mtime:
        return cached[1], cached[2]

    embeddings = np.load(embeddings_path)
    chunk_rows = [json.loads(line) for line in chunks_path.read_text().splitlines() if line.strip()]
    _INDEX_CACHE[company_slug] = (mtime, embeddings, chunk_rows)
    return embeddings, chunk_rows


def search(company: str, query: str, k: int = 6, doc_filter: list[str] | None = None) -> list[Chunk]:
    """Return top-k chunks for a query within a company's corpus, ranked by
    cosine similarity (embeddings are L2-normalized, so dot product == cosine)."""
    slug = slugify(company)
    embeddings, chunk_rows = _load_index(slug)

    if doc_filter:
        keep = [i for i, row in enumerate(chunk_rows) if row["doc_id"] in doc_filter]
    else:
        keep = list(range(len(chunk_rows)))

    if not keep:
        return []

    query_vec = embed_texts([query])[0]
    sub_embeddings = embeddings[keep]
    scores = sub_embeddings @ query_vec

    # Sort by score desc; break ties deterministically by chunk_id so repeated
    # queries against an unchanged index always return the same ordering.
    order = sorted(
        range(len(keep)),
        key=lambda j: (-float(scores[j]), chunk_rows[keep[j]]["chunk_id"]),
    )[:k]

    results: list[Chunk] = []
    for j in order:
        row = chunk_rows[keep[j]]
        results.append(Chunk(**row, score=float(scores[j])))
    return results

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
import re
from typing import Any

import numpy as np

from .ingest import embed_texts, index_dir_for, slugify
from .schemas import Chunk

# in-process cache: company slug -> (mtime, embeddings, chunk_rows)
_INDEX_CACHE: dict[str, tuple[float, np.ndarray, list[dict[str, Any]]]] = {}
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_SEMANTIC_WEIGHT = 0.78
_LEXICAL_WEIGHT = 0.17
_METADATA_WEIGHT = 0.05


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


def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN_RE.findall(text.lower()) if len(t) > 1}


def _lexical_score(query_tokens: set[str], row: dict[str, Any]) -> float:
    if not query_tokens:
        return 0.0
    text_tokens = _tokens(str(row.get("text") or ""))
    if not text_tokens:
        return 0.0
    overlap = len(query_tokens & text_tokens)
    return overlap / len(query_tokens)


def _metadata_score(query_tokens: set[str], row: dict[str, Any]) -> float:
    if not query_tokens:
        return 0.0
    metadata = " ".join(
        str(row.get(field) or "")
        for field in ("doc_id", "doc_name", "doc_type", "filing_period")
    )
    metadata_tokens = _tokens(metadata)
    if not metadata_tokens:
        return 0.0
    return len(query_tokens & metadata_tokens) / len(query_tokens)


def _combined_score(semantic: float, lexical: float, metadata: float) -> float:
    return (
        _SEMANTIC_WEIGHT * semantic
        + _LEXICAL_WEIGHT * lexical
        + _METADATA_WEIGHT * metadata
    )


def search(company: str, query: str, k: int = 6, doc_filter: list[str] | None = None) -> list[Chunk]:
    """Return top-k chunks for a query within a company's corpus.

    Dense cosine similarity is still the primary signal, but financial filing
    questions often contain exact labels, periods, document types, and line-item
    names that MiniLM can blur together. A small lexical/metadata rerank makes
    those exact anchors count without introducing a new index or vector store.
    """
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
    semantic_scores = sub_embeddings @ query_vec
    query_tokens = _tokens(query)

    # Sort by score desc; break ties deterministically by chunk_id so repeated
    # queries against an unchanged index always return the same ordering.
    scored: list[tuple[float, int]] = []
    for j, row_idx in enumerate(keep):
        row = chunk_rows[row_idx]
        semantic = float(semantic_scores[j])
        lexical = _lexical_score(query_tokens, row)
        metadata = _metadata_score(query_tokens, row)
        scored.append((_combined_score(semantic, lexical, metadata), j))
    order = sorted(
        scored,
        key=lambda pair: (-pair[0], chunk_rows[keep[pair[1]]]["chunk_id"]),
    )[:k]

    results: list[Chunk] = []
    for score, j in order:
        row = chunk_rows[keep[j]]
        results.append(Chunk(**row, score=float(score)))
    return results

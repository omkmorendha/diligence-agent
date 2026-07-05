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
import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

import numpy as np

from . import config
from .ingest import embed_texts, index_dir_for, slugify
from .schemas import Chunk

# in-process cache: company slug -> (mtime, embeddings, chunk_rows)
_INDEX_CACHE: dict[str, tuple[float, np.ndarray, list[dict[str, Any]]]] = {}
# parallel cache of BM25 corpus statistics (IMP3-5) keyed the same way; rebuilt
# only when the index mtime changes, so a hot company pays the tokenize/df pass once.
_BM25_CACHE: dict[str, tuple[float, "_Bm25Stats"]] = {}
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


def _token_list(text: str) -> list[str]:
    """Ordered/duplicated tokens (BM25 needs term FREQUENCIES, not just a set)."""
    return [t for t in _TOKEN_RE.findall(text.lower()) if len(t) > 1]


def _lexical_score(query_tokens: set[str], row: dict[str, Any]) -> float:
    if not query_tokens:
        return 0.0
    text_tokens = _tokens(str(row.get("text") or ""))
    if not text_tokens:
        return 0.0
    overlap = len(query_tokens & text_tokens)
    return overlap / len(query_tokens)


# --- BM25 lexical component (IMP3-5) -----------------------------------------
# WHY BM25 over the existing flat overlap ratio: `_lexical_score` gives every
# matched query term equal credit, so a rare, load-bearing term ("restructuring",
# "swaption") counts no more than a corpus-common one. BM25 folds in IDF (rare
# terms dominate) plus term-frequency saturation and document-length
# normalization, which is exactly what surfaces the one note/table that holds an
# answer MiniLM blurred out. Kept behind config.RETRIEVAL_HYBRID_BM25 so it can be
# A/B'd against dense-only recall (plan risk guard).
@dataclass
class _Bm25Stats:
    doc_tf: list[dict[str, int]]  # per-chunk term frequencies, row-aligned to chunk_rows
    doc_len: list[int]  # per-chunk token count (post-filter)
    avgdl: float
    idf: dict[str, float]


def _build_bm25_stats(chunk_rows: list[dict[str, Any]]) -> _Bm25Stats:
    """One tokenize + document-frequency pass over a company's corpus. Robertson/
    Sparck-Jones IDF with the standard +0.5 smoothing and a max(0, .) floor so a
    term present in >half the corpus can't contribute a negative lexical score."""
    doc_tf: list[dict[str, int]] = []
    doc_len: list[int] = []
    df: Counter[str] = Counter()
    for row in chunk_rows:
        tf = Counter(_token_list(str(row.get("text") or "")))
        doc_tf.append(dict(tf))
        doc_len.append(sum(tf.values()))
        df.update(tf.keys())
    n_docs = len(chunk_rows)
    avgdl = (sum(doc_len) / n_docs) if n_docs else 0.0
    idf = {
        term: max(0.0, math.log(1.0 + (n_docs - freq + 0.5) / (freq + 0.5)))
        for term, freq in df.items()
    }
    return _Bm25Stats(doc_tf=doc_tf, doc_len=doc_len, avgdl=avgdl, idf=idf)


def _bm25_stats(company_slug: str, chunk_rows: list[dict[str, Any]], mtime: float) -> _Bm25Stats:
    cached = _BM25_CACHE.get(company_slug)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    stats = _build_bm25_stats(chunk_rows)
    _BM25_CACHE[company_slug] = (mtime, stats)
    return stats


def _bm25_score(query_tokens: set[str], row_idx: int, stats: _Bm25Stats) -> float:
    """Raw (un-normalized) BM25 score of one corpus chunk against the query set."""
    if not query_tokens or stats.avgdl <= 0.0:
        return 0.0
    tf = stats.doc_tf[row_idx]
    dl = stats.doc_len[row_idx]
    k1 = config.RETRIEVAL_BM25_K1
    b = config.RETRIEVAL_BM25_B
    denom_len = k1 * (1.0 - b + b * dl / stats.avgdl)
    score = 0.0
    for term in query_tokens:
        freq = tf.get(term)
        if not freq:
            continue
        score += stats.idf.get(term, 0.0) * (freq * (k1 + 1.0)) / (freq + denom_len)
    return score


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
    mtime = _INDEX_CACHE[slug][0]

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

    # Hybrid lexical: when enabled (IMP3-5), replace the flat token-overlap ratio
    # with an IDF-weighted BM25 score min-max normalized to [0,1] ACROSS THE KEPT
    # candidate set, so it occupies the same bounded [0,1] slot as the old lexical
    # signal (same _LEXICAL_WEIGHT budget -- the dense cosine stays primary) while
    # letting rare exact terms dominate. Normalizing over `keep` (not the whole
    # corpus) keeps the blend calibrated under a doc_filter. Falls back to the
    # overlap ratio when the flag is off (A/B against dense-only recall).
    lexical_by_j: dict[int, float] = {}
    if config.RETRIEVAL_HYBRID_BM25:
        stats = _bm25_stats(slug, chunk_rows, mtime)
        raw_bm25 = [_bm25_score(query_tokens, row_idx, stats) for row_idx in keep]
        max_bm25 = max(raw_bm25) if raw_bm25 else 0.0
        if max_bm25 > 0.0:
            lexical_by_j = {j: raw_bm25[j] / max_bm25 for j in range(len(keep))}

    # Sort by score desc; break ties deterministically by chunk_id so repeated
    # queries against an unchanged index always return the same ordering.
    scored: list[tuple[float, int]] = []
    for j, row_idx in enumerate(keep):
        row = chunk_rows[row_idx]
        semantic = float(semantic_scores[j])
        lexical = lexical_by_j.get(j) if config.RETRIEVAL_HYBRID_BM25 else None
        if lexical is None:
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

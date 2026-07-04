"""Retrieval (spec section 25, Step 10).

Cosine-similarity search over a company's persisted embedded index. Backs the
`search_filing` agent tool and the baseline's single retrieve step.

TODO(Step 10).
"""

from __future__ import annotations

from .schemas import Chunk


def search(company: str, query: str, k: int = 6, doc_filter: list[str] | None = None) -> list[Chunk]:
    """Return top-k chunks for a query within a company's corpus, ranked by cosine score."""
    raise NotImplementedError("retrieval: implement in Step 10 (spec section 13 / 25).")

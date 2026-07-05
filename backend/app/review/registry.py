"""S3 — Corpus scope pre-check (spec sections 1.4, 8).

FROZEN CONTRACT — signatures must not change.

`corpus_registry()` builds `{company: {doc_ids, periods}}` once from
`data/index/*/meta.json` + `data/subset.json`. `scope_check` tags each claim
against that registry (with a small company-alias table) and stamps an immediate
`OUT_OF_SCOPE` verdict (via claim status/downstream result) for claims whose
company or period the corpus does not cover — no agent run for those.
"""

from __future__ import annotations

from ..schemas import Claim


def corpus_registry() -> dict:
    """Return {company: {"doc_ids": [...], "periods": [...]}} for the RAG corpus."""
    raise NotImplementedError("corpus_registry is a frozen stub (spec section 8)")


def scope_check(claims: list[Claim]) -> list[Claim]:
    """Tag claims in/out of corpus scope; out-of-scope claims skip the agent run."""
    raise NotImplementedError("scope_check is a frozen stub (spec section 8)")

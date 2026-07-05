"""S2 — Claim extraction (spec section 7).

FROZEN CONTRACT — signature must not change.

One `llm.chat(json_mode=True)` pass over `DocModel.canonical_text` (chunked with
overlap if long, deduped across chunks) yields verifiable claims. Deterministic
post-processing anchors each `quote` in the DocModel (unanchorable claims dropped),
dedupes, sorts by (type priority, document order), and caps at
`MAX_CLAIMS_PER_REVIEW`; cap overflow is kept with `status="SKIPPED"`. When
`pilot` is True, only the first `PILOT_CLAIMS` by priority are returned active.
"""

from __future__ import annotations

from ..schemas import Claim, DocModel


def extract_claims(docmodel: DocModel, pilot: bool) -> list[Claim]:
    """Extract, anchor, dedupe, prioritize and cap claims from a parsed document."""
    raise NotImplementedError("extract_claims is a frozen stub (spec section 7)")

"""Deterministic scorers (spec section 20, Step 3).

Pure functions over memo.json + trace.jsonl + subset.json gold fields. No LLM.
These are the TDD foundation — every scorer is tested against evals/fixtures/.

Metrics (spec section 20):
    * answer accuracy       numeric: default +/-1% relative tolerance (overridable);
                            string: normalized exact match (lowercase, strip punct,
                            collapse whitespace, basic unit normalization).
    * abstention scoring    correct only when the item is truly unanswerable /
                            evidence-insufficient; otherwise incorrect-but-calibrated.
    * citation precision    doc_id match + cited page within +/-1 page slack.
    * citation provenance   every cited chunk_id appeared in a prior retrieval event.
    * arithmetic integrity  every material financial number traces to a calculate
                            result or a cited quote span (ignore page numbers, years,
                            item ids, confidence counts, dates, run summary counts).
    * trace shape           A_multi_input: >=2 retrievals, >=1 calculate, >=2 grounded
                            inputs; C_lookup: short path (<=2 retrievals, soft).

TODO(Step 3).
"""

from __future__ import annotations

import re

_PUNCT = re.compile(r"[^\w\s.%-]")


def normalize_string(s: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace (spec section 20)."""
    s = s.lower().strip()
    s = _PUNCT.sub("", s)
    return re.sub(r"\s+", " ", s)


def numeric_within_tolerance(
    predicted: float, gold: float, relative: float | None = 0.01, absolute: float | None = None
) -> bool:
    """True if predicted matches gold within relative and/or absolute tolerance."""
    if absolute is not None and abs(predicted - gold) <= absolute:
        return True
    if relative is not None:
        denom = abs(gold) if gold != 0 else 1.0
        return abs(predicted - gold) / denom <= relative
    return predicted == gold


# TODO(Step 3): answer_accuracy(), citation_precision(), citation_provenance(),
# arithmetic_integrity(), trace_shape(), score_run(), score_fixtures().

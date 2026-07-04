"""LLM judges — Tier 2 eval (spec section 21).

Build ONLY after deterministic eval works. Same LLM endpoint as the agent for v0
(disclose if asked). Mitigations: narrow rubrics, one criterion per call, structured
output, and a corrupted-memo calibration gate.

Criteria:
    groundedness   1=unsupported/hallucinated .. 5=fully grounded
    actionability  1=not actionable .. 5=clear and actionable

Calibration gate (spec section 21): corrupt one memo (swap a citation, inject a
wrong number), run judges, assert they score it low; persist
results/corrupted_memo_judge.json. If calibration fails, do NOT show judge scores
as headline metrics.

TODO(after Step 3).
"""

from __future__ import annotations


def judge_groundedness(memo_item: dict, cited_passages: list[str]) -> dict:
    raise NotImplementedError("judges: build after deterministic eval (spec section 21).")


def judge_actionability(memo_item: dict) -> dict:
    raise NotImplementedError("judges: build after deterministic eval (spec section 21).")

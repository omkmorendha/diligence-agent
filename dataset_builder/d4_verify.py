"""D4 — Verifier agent (spec section 6 D4, Step 7).

Independent LLM pass (separate from D3). Given question, gold answer, gold evidence
text, and the D3 classification, checks:
    1. Does the gold answer follow from the evidence?
    2. Is the bucket label reasonable?
    3. Are expected inputs correct?
    4. Is there unit or period ambiguity?
    5. Should this question be included?

Disagreements between D3 and D4 go to human spot-check.

Outputs:
    data/verified.jsonl
    data/disputes.jsonl

TODO(Step 7).
"""

from __future__ import annotations


def main() -> int:
    raise NotImplementedError("d4 verifier: implement in Step 7 (spec section 6 D4).")


if __name__ == "__main__":
    raise SystemExit(main())

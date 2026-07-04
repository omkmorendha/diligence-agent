"""D3 — Classifier agent (spec section 6 D3, Step 7).

One LLM call per question (via backend.app.llm.chat, json mode). Input: question,
gold answer, gold evidence text, document metadata. Output strict JSON with:
bucket (A_multi_input | B_judgment | C_lookup), expected_formula, expected_inputs,
inputs_span_multiple_statements, predicted_baseline_failure,
answer_verifiable_from_evidence, unit_or_period_ambiguity, notes.

Rules:
    * A_multi_input requires >=2 distinct inputs and a calculation.
    * B_judgment requires interpretation/comparison/qualitative reasoning.
    * C_lookup is a direct lookup.
    * Exclude if answer_verifiable_from_evidence is false.
    * Exclude if unit_or_period_ambiguity is true (unless human-reviewed).

NOTE: characterize.py already produces a HEURISTIC bucket preview from FinanceBench's
native question_reasoning labels; D3 is the authoritative LLM pass.

Output: data/classified.jsonl

TODO(Step 7).
"""

from __future__ import annotations


def main() -> int:
    raise NotImplementedError("d3 classifier: implement in Step 7 (spec section 6 D3).")


if __name__ == "__main__":
    raise SystemExit(main())

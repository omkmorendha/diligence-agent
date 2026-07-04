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
native question_reasoning labels; D3 is the authoritative LLM pass. Exclusion (per
the rules above) is applied downstream in D5 subset selection, not here — this
script classifies ALL 150 rows so D4/D5 have the full picture to work from.

Output: data/classified.jsonl

Usage:
    uv run --project backend dataset_builder/d3_classify.py
    uv run --project backend dataset_builder/d3_classify.py --limit 5 --workers 1
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from characterize import guess_bucket  # noqa: E402
from llm_json import call_json_with_retry  # noqa: E402

RAW_PATH = ROOT / "data" / "raw" / "financebench.jsonl"
OUT_PATH = ROOT / "data" / "classified.jsonl"

BUCKETS = ("A_multi_input", "B_judgment", "C_lookup")

REQUIRED_KEYS = (
    "bucket",
    "expected_formula",
    "expected_inputs",
    "inputs_span_multiple_statements",
    "predicted_baseline_failure",
    "answer_verifiable_from_evidence",
    "unit_or_period_ambiguity",
    "notes",
)

SYSTEM_PROMPT = """You are a strict financial-diligence question classifier.

Classify one FinanceBench question into exactly one bucket:
- A_multi_input: requires >=2 distinct inputs AND a calculation (e.g. a ratio,
  a delta, a multi-line-item sum).
- B_judgment: requires interpretation, comparison, or qualitative reasoning
  (not answerable by a single direct number lookup or arithmetic formula alone).
- C_lookup: answerable with a single direct lookup of one reported figure.

You must also assess whether the gold answer is actually verifiable from the
provided evidence text, and whether the question/answer has any unresolved unit
or reporting-period ambiguity (e.g. millions vs thousands, FY vs calendar year,
which period the question intends when evidence shows several).

Respond with EXACTLY one JSON object, no prose, no markdown code fence, matching
this shape:

{
  "question_id": "<echo the given question_id>",
  "bucket": "A_multi_input | B_judgment | C_lookup",
  "expected_formula": "<short formula/derivation string, or empty string for C_lookup>",
  "expected_inputs": ["<input 1>", "<input 2>", "..."],
  "inputs_span_multiple_statements": true | false,
  "predicted_baseline_failure": true | false,
  "answer_verifiable_from_evidence": true | false,
  "unit_or_period_ambiguity": true | false,
  "notes": "<one or two sentence rationale>"
}

predicted_baseline_failure = true means you predict a naive single-shot
retrieve-then-answer RAG baseline (no calculator, no multi-hop retrieval) would
likely get this wrong (this is normally true for A_multi_input, sometimes true
for B_judgment, and rarely true for C_lookup)."""


def _build_user_prompt(row: dict[str, Any]) -> str:
    evidence_blocks = []
    for i, ev in enumerate(row.get("evidence", []), 1):
        evidence_blocks.append(
            f"[Evidence {i} | doc={ev.get('doc_name')} page_idx={ev.get('evidence_page_num')}]\n"
            f"{ev.get('evidence_text', '')}"
        )
    evidence_text = "\n\n".join(evidence_blocks) or "(no evidence provided)"

    return f"""question_id: {row['question_id']}
company: {row['company']}
doc_name: {row['doc_name']}
doc_type: {row.get('doc_type')}
doc_period: {row.get('doc_period')}
gics_sector: {row.get('gics_sector')}

question: {row['question']}

gold_answer: {row['gold_answer']}

gold_evidence:
{evidence_text}

Classify this question now. Respond with the JSON object only."""


def _validate_classification(obj: dict[str, Any]) -> dict[str, Any]:
    missing = [k for k in REQUIRED_KEYS if k not in obj]
    if missing:
        raise ValueError(f"missing keys: {missing}")
    if obj["bucket"] not in BUCKETS:
        raise ValueError(f"invalid bucket: {obj['bucket']!r}")
    if not isinstance(obj.get("expected_inputs"), list):
        raise ValueError("expected_inputs must be a list")
    for key in (
        "inputs_span_multiple_statements",
        "predicted_baseline_failure",
        "answer_verifiable_from_evidence",
        "unit_or_period_ambiguity",
    ):
        if not isinstance(obj[key], bool):
            raise ValueError(f"{key} must be a bool")
    return obj


def _heuristic_fallback(row: dict[str, Any], error: str) -> dict[str, Any]:
    """Guarantee every row gets classified, even if the LLM call/parse fails twice."""
    bucket = guess_bucket(row.get("question_type"), row.get("question_reasoning"))
    return {
        "bucket": bucket,
        "expected_formula": "",
        "expected_inputs": [],
        "inputs_span_multiple_statements": False,
        "predicted_baseline_failure": bucket == "A_multi_input",
        "answer_verifiable_from_evidence": True,
        "unit_or_period_ambiguity": False,
        "notes": f"HEURISTIC FALLBACK (LLM classification failed): {error}",
        "classifier_error": error,
    }


def _classify_one(row: dict[str, Any]) -> dict[str, Any]:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _build_user_prompt(row)},
    ]
    obj, error = call_json_with_retry(messages, _validate_classification, max_tokens=1200)
    if obj is not None:
        classification = {k: obj[k] for k in REQUIRED_KEYS}
        classification["classifier_error"] = None
    else:
        classification = _heuristic_fallback(row, error or "unknown error")

    return {
        "question_id": row["question_id"],
        "company": row["company"],
        "doc_name": row["doc_name"],
        "question": row["question"],
        "gold_answer": row["gold_answer"],
        **classification,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="D3 — LLM-classify all questions into A/B/C buckets.")
    ap.add_argument("--limit", type=int, default=None, help="Limit number of questions (debug).")
    ap.add_argument(
        "--workers", type=int, default=3,
        help="Concurrent LLM calls (kept low; the NVIDIA endpoint rate-limits hard "
             "under concurrency — see llm_json.call_json_with_retry backoff).",
    )
    args = ap.parse_args()

    if not RAW_PATH.exists():
        raise SystemExit(f"missing {RAW_PATH} — run d1_pull_raw.py first")

    rows = [json.loads(line) for line in RAW_PATH.read_text().splitlines() if line.strip()]
    if args.limit:
        rows = rows[: args.limit]

    print(f"[d3] classifying {len(rows)} questions with {args.workers} worker(s)...", file=sys.stderr)
    t0 = time.time()
    results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_classify_one, row): row["question_id"] for row in rows}
        done = 0
        for fut in as_completed(futures):
            qid = futures[fut]
            try:
                results[qid] = fut.result()
            except Exception as exc:  # noqa: BLE001 - should not happen (fallback catches it)
                row = next(r for r in rows if r["question_id"] == qid)
                results[qid] = {
                    "question_id": qid,
                    "company": row["company"],
                    "doc_name": row["doc_name"],
                    "question": row["question"],
                    "gold_answer": row["gold_answer"],
                    **_heuristic_fallback(row, f"{type(exc).__name__}: {exc}"),
                }
            done += 1
            if done % 10 == 0 or done == len(rows):
                print(f"[d3] {done}/{len(rows)} classified", file=sys.stderr)

    # preserve input order
    ordered = [results[r["question_id"]] for r in rows]

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w") as f:
        for rec in ordered:
            f.write(json.dumps(rec) + "\n")

    bucket_counts: dict[str, int] = {}
    failures = 0
    for rec in ordered:
        bucket_counts[rec["bucket"]] = bucket_counts.get(rec["bucket"], 0) + 1
        if rec.get("classifier_error"):
            failures += 1

    dt = time.time() - t0
    print(f"\n[d3] classified {len(ordered)} questions in {dt:.1f}s. buckets={bucket_counts} "
          f"fallbacks={failures}", file=sys.stderr)
    print(f"[d3] wrote {OUT_PATH}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

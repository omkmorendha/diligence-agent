"""D4 — Verifier agent (spec section 6 D4, Step 7).

Independent LLM pass (separate from D3). Given question, gold answer, gold evidence
text, and the D3 classification, checks:
    1. Does the gold answer follow from the evidence?
    2. Is the bucket label reasonable?
    3. Are expected inputs correct?
    4. Is there unit or period ambiguity?
    5. Should this question be included?

In addition (this is what makes the verifier independent rather than a rubber
stamp of D3), a DETERMINISTIC check confirms the gold evidence_text is actually
findable in the parsed page text produced by D2 (data/pages/{company_slug}/{doc}.json),
using the pdf_page convention (evidence_page_num + 1, see AMBIGUITIES.md section 3).
This catches page-mapping bugs and evidence/PDF mismatches no LLM pass would notice.

Disagreements between D3 and D4 (bucket mismatch, verifiability mismatch, or
evidence not findable in the parsed page) go to human spot-check.

Outputs:
    data/verified.jsonl   one row per question (all 150), merged D3 + D4 + evidence check
    data/disputes.jsonl   subset of verified.jsonl where D3 and D4 disagree

Usage:
    uv run --project backend dataset_builder/d4_verify.py
    uv run --project backend dataset_builder/d4_verify.py --limit 5 --workers 1
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from d2_parse_test import slugify  # noqa: E402
from llm_json import call_json_with_retry  # noqa: E402

RAW_PATH = ROOT / "data" / "raw" / "financebench.jsonl"
CLASSIFIED_PATH = ROOT / "data" / "classified.jsonl"
PAGES_DIR = ROOT / "data" / "pages"
VERIFIED_PATH = ROOT / "data" / "verified.jsonl"
DISPUTES_PATH = ROOT / "data" / "disputes.jsonl"

BUCKETS = ("A_multi_input", "B_judgment", "C_lookup")

# Evidence is "findable" if this fraction of its (normalized) characters are
# matched, in order, somewhere in the parsed page text. Empirically, correctly
# paired evidence/page scores >=0.98 on this corpus (whitespace/dash noise only);
# 0.85 leaves generous slack for extraction differences without masking a real
# page-mismatch bug (which scores <0.3, see dev notes / AMBIGUITIES.md).
EVIDENCE_FINDABLE_THRESHOLD = 0.85

REQUIRED_KEYS = (
    "gold_answer_follows_from_evidence",
    "bucket_reasonable",
    "suggested_bucket",
    "expected_inputs_correct",
    "unit_or_period_ambiguity",
    "should_include",
    "notes",
)

SYSTEM_PROMPT = """You are an independent verifier auditing another model's (D3) \
classification of a FinanceBench diligence question. You do NOT trust D3 — \
re-derive your own judgment from the question, gold answer, and evidence, then \
compare against D3's classification.

Check:
1. Does the gold answer actually follow from the provided evidence text?
2. Is D3's bucket label (A_multi_input | B_judgment | C_lookup) reasonable? If not,
   what bucket would you assign instead?
3. Are D3's expected_inputs correct and sufficient to derive the gold answer?
4. Is there any unresolved unit or reporting-period ambiguity?
5. Should this question be included in the final eval subset?

Respond with EXACTLY one JSON object, no prose, no markdown code fence:

{
  "gold_answer_follows_from_evidence": true | false,
  "bucket_reasonable": true | false,
  "suggested_bucket": "A_multi_input | B_judgment | C_lookup",
  "expected_inputs_correct": true | false,
  "unit_or_period_ambiguity": true | false,
  "should_include": true | false,
  "notes": "<one or two sentence rationale, including why should_include if false>"
}"""


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _containment_ratio(evidence_text: str, page_text: str) -> float:
    """Fraction of evidence_text's characters matched (in order) inside page_text.

    Uses difflib's matching blocks rather than a plain substring check because
    the D2 (pymupdf) extraction and FinanceBench's own evidence_text differ in
    whitespace/dash characters even for a correct page match. Ratio is relative
    to len(evidence_text) so a short quote inside a long page still scores ~1.0
    (unlike SequenceMatcher.ratio(), which penalizes length mismatch).
    """
    a, b = _norm(evidence_text), _norm(page_text)
    if not a:
        return 0.0
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    matched = sum(block.size for block in sm.get_matching_blocks())
    return matched / len(a)


_PAGES_CACHE: dict[str, dict[str, Any] | None] = {}


def _load_pages(company: str, doc_name: str) -> dict[str, Any] | None:
    key = f"{slugify(company)}/{doc_name}"
    if key in _PAGES_CACHE:
        return _PAGES_CACHE[key]
    path = PAGES_DIR / slugify(company) / f"{doc_name}.json"
    payload = json.loads(path.read_text()) if path.exists() else None
    _PAGES_CACHE[key] = payload
    return payload


def _check_evidence_findable(row: dict[str, Any]) -> list[dict[str, Any]]:
    checks = []
    for ev in row.get("evidence", []):
        doc_name = ev["doc_name"]
        page_idx = ev["evidence_page_num"]
        pdf_page = page_idx + 1  # spec's 1-indexed convention (AMBIGUITIES.md section 3)
        pages = _load_pages(row["company"], doc_name)
        if pages is None:
            checks.append(
                {
                    "doc_name": doc_name,
                    "evidence_page_num": page_idx,
                    "pdf_page": pdf_page,
                    "ratio": 0.0,
                    "findable": False,
                    "error": "parsed pages not found (run d2_parse_test.py)",
                }
            )
            continue
        pages_list = pages.get("pages", [])
        if not (0 <= page_idx < len(pages_list)):
            checks.append(
                {
                    "doc_name": doc_name,
                    "evidence_page_num": page_idx,
                    "pdf_page": pdf_page,
                    "ratio": 0.0,
                    "findable": False,
                    "error": f"page index {page_idx} out of range (doc has {len(pages_list)} pages)",
                }
            )
            continue
        page_text = pages_list[page_idx].get("text", "")
        ratio = _containment_ratio(ev.get("evidence_text", ""), page_text)
        checks.append(
            {
                "doc_name": doc_name,
                "evidence_page_num": page_idx,
                "pdf_page": pdf_page,
                "ratio": round(ratio, 3),
                "findable": ratio >= EVIDENCE_FINDABLE_THRESHOLD,
                "error": None,
            }
        )
    return checks


def _validate_verification(obj: dict[str, Any]) -> dict[str, Any]:
    missing = [k for k in REQUIRED_KEYS if k not in obj]
    if missing:
        raise ValueError(f"missing keys: {missing}")
    if obj["suggested_bucket"] not in BUCKETS:
        raise ValueError(f"invalid suggested_bucket: {obj['suggested_bucket']!r}")
    for key in (
        "gold_answer_follows_from_evidence",
        "bucket_reasonable",
        "expected_inputs_correct",
        "unit_or_period_ambiguity",
        "should_include",
    ):
        if not isinstance(obj[key], bool):
            raise ValueError(f"{key} must be a bool")
    return obj


def _build_user_prompt(row: dict[str, Any], classification: dict[str, Any]) -> str:
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

question: {row['question']}
gold_answer: {row['gold_answer']}

gold_evidence:
{evidence_text}

D3's classification (do not assume this is correct):
  bucket: {classification['bucket']}
  expected_formula: {classification['expected_formula']!r}
  expected_inputs: {classification['expected_inputs']}
  answer_verifiable_from_evidence: {classification['answer_verifiable_from_evidence']}
  unit_or_period_ambiguity: {classification['unit_or_period_ambiguity']}
  notes: {classification['notes']!r}

Verify now. Respond with the JSON object only."""


def _heuristic_fallback(error: str) -> dict[str, Any]:
    """Conservative fallback if the LLM verifier call/parse fails twice: don't
    silently pass a row — flag it for human review instead of guessing include."""
    return {
        "gold_answer_follows_from_evidence": False,
        "bucket_reasonable": False,
        "suggested_bucket": "B_judgment",
        "expected_inputs_correct": False,
        "unit_or_period_ambiguity": False,
        "should_include": False,
        "notes": f"HEURISTIC FALLBACK (LLM verification failed, flagged for human review): {error}",
        "verifier_error": error,
    }


def _verify_one(row: dict[str, Any], classification: dict[str, Any]) -> dict[str, Any]:
    evidence_checks = _check_evidence_findable(row)
    evidence_all_findable = bool(evidence_checks) and all(c["findable"] for c in evidence_checks)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _build_user_prompt(row, classification)},
    ]
    # See the matching comment in d3_classify.py: the reasoning model spends
    # completion tokens on its `reasoning` field before `content`, so this needs
    # real headroom, not just enough for the JSON payload.
    obj, error = call_json_with_retry(messages, _validate_verification, max_tokens=8000)
    if obj is not None:
        verification = {k: obj[k] for k in REQUIRED_KEYS}
        verification["verifier_error"] = None
    else:
        verification = _heuristic_fallback(error or "unknown error")

    disagreement_reasons: list[str] = []
    if verification["suggested_bucket"] != classification["bucket"]:
        disagreement_reasons.append(
            f"bucket mismatch: d3={classification['bucket']} vs d4={verification['suggested_bucket']}"
        )
    if not verification["bucket_reasonable"]:
        disagreement_reasons.append("d4 flagged d3 bucket as unreasonable")
    if verification["gold_answer_follows_from_evidence"] != classification["answer_verifiable_from_evidence"]:
        disagreement_reasons.append(
            "answer_verifiable_from_evidence mismatch: "
            f"d3={classification['answer_verifiable_from_evidence']} "
            f"vs d4={verification['gold_answer_follows_from_evidence']}"
        )
    if verification["unit_or_period_ambiguity"] != classification["unit_or_period_ambiguity"]:
        disagreement_reasons.append(
            "unit_or_period_ambiguity mismatch: "
            f"d3={classification['unit_or_period_ambiguity']} vs d4={verification['unit_or_period_ambiguity']}"
        )
    if not verification["expected_inputs_correct"]:
        disagreement_reasons.append("d4 flagged d3 expected_inputs as incorrect")
    if not evidence_all_findable:
        disagreement_reasons.append("gold evidence_text not findable in parsed page text (D2 output)")
    if not verification["should_include"]:
        disagreement_reasons.append("d4 recommends exclusion")

    # final include decision: both passes must agree the row is verifiable and
    # includable, evidence must actually be findable in the parsed pages, and no
    # unresolved ambiguity per the D3 exclusion rules (spec section 6 D3).
    include = (
        classification["answer_verifiable_from_evidence"]
        and verification["gold_answer_follows_from_evidence"]
        and not classification["unit_or_period_ambiguity"]
        and not verification["unit_or_period_ambiguity"]
        and verification["should_include"]
        and evidence_all_findable
    )

    return {
        "question_id": row["question_id"],
        "company": row["company"],
        "doc_name": row["doc_name"],
        "question": row["question"],
        "gold_answer": row["gold_answer"],
        "bucket_d3": classification["bucket"],
        "expected_formula": classification["expected_formula"],
        "expected_inputs": classification["expected_inputs"],
        "inputs_span_multiple_statements": classification["inputs_span_multiple_statements"],
        "predicted_baseline_failure": classification["predicted_baseline_failure"],
        "answer_verifiable_from_evidence_d3": classification["answer_verifiable_from_evidence"],
        "unit_or_period_ambiguity_d3": classification["unit_or_period_ambiguity"],
        "notes_d3": classification["notes"],
        "classifier_error": classification.get("classifier_error"),
        "verifier_suggested_bucket": verification["suggested_bucket"],
        "verifier_bucket_reasonable": verification["bucket_reasonable"],
        "verifier_gold_answer_follows_from_evidence": verification["gold_answer_follows_from_evidence"],
        "verifier_expected_inputs_correct": verification["expected_inputs_correct"],
        "verifier_unit_or_period_ambiguity": verification["unit_or_period_ambiguity"],
        "verifier_should_include": verification["should_include"],
        "verifier_notes": verification["notes"],
        "verifier_error": verification.get("verifier_error"),
        "evidence_checks": evidence_checks,
        "evidence_all_findable": evidence_all_findable,
        "disagreement": bool(disagreement_reasons),
        "disagreement_reasons": disagreement_reasons,
        "human_reviewed": False,
        "include": include,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="D4 — verify D3 classifications against parsed pages.")
    ap.add_argument("--limit", type=int, default=None, help="Limit number of questions (debug).")
    ap.add_argument(
        "--workers", type=int, default=3,
        help="Concurrent LLM calls (kept low; the NVIDIA endpoint rate-limits hard "
             "under concurrency — see llm_json.call_json_with_retry backoff).",
    )
    args = ap.parse_args()

    if not RAW_PATH.exists():
        raise SystemExit(f"missing {RAW_PATH} — run d1_pull_raw.py first")
    if not CLASSIFIED_PATH.exists():
        raise SystemExit(f"missing {CLASSIFIED_PATH} — run d3_classify.py first")

    raw_rows = [json.loads(line) for line in RAW_PATH.read_text().splitlines() if line.strip()]
    classified_by_id = {
        json.loads(line)["question_id"]: json.loads(line)
        for line in CLASSIFIED_PATH.read_text().splitlines()
        if line.strip()
    }

    pairs = []
    for row in raw_rows:
        classification = classified_by_id.get(row["question_id"])
        if classification is None:
            print(f"[d4] WARNING: no classification for {row['question_id']}, skipping", file=sys.stderr)
            continue
        pairs.append((row, classification))

    if args.limit:
        pairs = pairs[: args.limit]

    print(f"[d4] verifying {len(pairs)} questions with {args.workers} worker(s)...", file=sys.stderr)
    t0 = time.time()
    results: dict[str, dict[str, Any]] = {}
    pairs_by_qid = {row["question_id"]: (row, cls) for row, cls in pairs}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_verify_one, row, cls): row["question_id"] for row, cls in pairs}
        done = 0
        for fut in as_completed(futures):
            qid = futures[fut]
            try:
                results[qid] = fut.result()
            except Exception as exc:  # noqa: BLE001 - should not happen (fallback catches it)
                row, classification = pairs_by_qid[qid]
                verification = _heuristic_fallback(f"{type(exc).__name__}: {exc}")
                results[qid] = {
                    "question_id": row["question_id"],
                    "company": row["company"],
                    "doc_name": row["doc_name"],
                    "question": row["question"],
                    "gold_answer": row["gold_answer"],
                    "bucket_d3": classification["bucket"],
                    "expected_formula": classification["expected_formula"],
                    "expected_inputs": classification["expected_inputs"],
                    "inputs_span_multiple_statements": classification["inputs_span_multiple_statements"],
                    "predicted_baseline_failure": classification["predicted_baseline_failure"],
                    "answer_verifiable_from_evidence_d3": classification["answer_verifiable_from_evidence"],
                    "unit_or_period_ambiguity_d3": classification["unit_or_period_ambiguity"],
                    "notes_d3": classification["notes"],
                    "classifier_error": classification.get("classifier_error"),
                    "verifier_suggested_bucket": verification["suggested_bucket"],
                    "verifier_bucket_reasonable": verification["bucket_reasonable"],
                    "verifier_gold_answer_follows_from_evidence": verification["gold_answer_follows_from_evidence"],
                    "verifier_expected_inputs_correct": verification["expected_inputs_correct"],
                    "verifier_unit_or_period_ambiguity": verification["unit_or_period_ambiguity"],
                    "verifier_should_include": verification["should_include"],
                    "verifier_notes": verification["notes"],
                    "verifier_error": verification.get("verifier_error"),
                    "evidence_checks": [],
                    "evidence_all_findable": False,
                    "disagreement": True,
                    "disagreement_reasons": [f"unhandled exception in _verify_one: {type(exc).__name__}: {exc}"],
                    "human_reviewed": False,
                    "include": False,
                }
            done += 1
            if done % 10 == 0 or done == len(pairs):
                print(f"[d4] {done}/{len(pairs)} verified", file=sys.stderr)

    ordered = [results[row["question_id"]] for row, _ in pairs]

    VERIFIED_PATH.parent.mkdir(parents=True, exist_ok=True)
    with VERIFIED_PATH.open("w") as f:
        for rec in ordered:
            f.write(json.dumps(rec) + "\n")

    disputes = [rec for rec in ordered if rec["disagreement"]]
    with DISPUTES_PATH.open("w") as f:
        for rec in disputes:
            f.write(json.dumps(rec) + "\n")

    dt = time.time() - t0
    included = sum(1 for rec in ordered if rec["include"])
    print(
        f"\n[d4] verified {len(ordered)} questions in {dt:.1f}s. "
        f"disputes={len(disputes)} included={included}/{len(ordered)}",
        file=sys.stderr,
    )
    print(f"[d4] wrote {VERIFIED_PATH}", file=sys.stderr)
    print(f"[d4] wrote {DISPUTES_PATH}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

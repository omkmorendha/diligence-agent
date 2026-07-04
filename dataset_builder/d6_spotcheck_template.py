"""D6 — Human spot-check template (spec section 6 D6, Step 8).

Two modes, run in sequence:

1. `--mode template` (default): read `data/subset.json`, deterministically sample
   ~8 records stratified across the selected companies (and, within a company,
   across buckets, preferring `demo_candidate` items first), and write a
   pre-filled worksheet to `data/spotcheck_template.json` for a human to
   complete. Each record carries the five manual checks from the spec:
       - gold answer follows from evidence
       - evidence page is valid
       - bucket label is reasonable
       - expected inputs are reasonable
       - unit/period is clear
   plus an overall `pass` (true/false/null) and free-text `reviewer_notes`, all
   initialized to `null`/`""` for a human to fill in.

2. `--mode score`: read back the (human-completed) `data/spotcheck_template.json`
   and write the spec's exact summary schema to `data/spotcheck.json`:
       {"sample_size": 8, "passed": 7, "failed": 1, "pass_rate": 0.875,
        "notes": "..."}
   A record counts as failed if any sub-check is `false` or the overall `pass`
   is explicitly `false`. Records left entirely unfilled (`pass` still `null`)
   are reported separately and excluded from `passed`/`failed` counts (they are
   not yet reviewed) -- `--mode score` errors out if none have been reviewed at
   all, so this is never silently run on an empty template.

Usage:
    uv run --project backend dataset_builder/d6_spotcheck_template.py
    uv run --project backend dataset_builder/d6_spotcheck_template.py --mode score
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SUBSET_PATH = ROOT / "data" / "subset.json"
TEMPLATE_PATH = ROOT / "data" / "spotcheck_template.json"
SPOTCHECK_PATH = ROOT / "data" / "spotcheck.json"

SAMPLE_SIZE = 8
CHECK_KEYS = (
    "gold_answer_follows_from_evidence",
    "evidence_page_valid",
    "bucket_label_reasonable",
    "expected_inputs_reasonable",
    "unit_or_period_clear",
)


def _sample_items(items: list[dict[str, Any]], sample_size: int) -> list[dict[str, Any]]:
    """Deterministically sample ~sample_size items stratified across companies
    (round-robin quota) and, within a company, across buckets, preferring
    demo_candidate items first. Order of `items` (as written by D5) breaks ties.
    """
    companies = list(dict.fromkeys(it["company"] for it in items))  # first-seen order = D5's rank order
    by_company: dict[str, list[dict[str, Any]]] = {c: [] for c in companies}
    for it in items:
        by_company[it["company"]].append(it)

    # Round-robin quota across companies so the sample stays stratified even
    # when sample_size doesn't divide evenly.
    quota = {c: 0 for c in companies}
    for i in range(sample_size):
        quota[companies[i % len(companies)]] += 1

    sample: list[dict[str, Any]] = []
    for c in companies:
        pool = sorted(by_company[c], key=lambda it: (not it["demo_candidate"], it["item_id"]))
        picked: list[dict[str, Any]] = []
        seen_buckets: set[str] = set()
        # First pass: greedily maximize bucket diversity among demo-preferred order.
        for it in pool:
            if len(picked) >= quota[c]:
                break
            if it["bucket"] not in seen_buckets:
                picked.append(it)
                seen_buckets.add(it["bucket"])
        # Second pass: fill any remaining quota from what's left, same priority order.
        for it in pool:
            if len(picked) >= quota[c]:
                break
            if it not in picked:
                picked.append(it)
        sample.extend(picked)

    sample.sort(key=lambda it: it["item_id"])
    return sample[:sample_size]


def _worksheet_record(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "item_id": item["item_id"],
        "company": item["company"],
        "question": item["question"],
        "gold_answer": item["gold_answer"],
        "bucket": item["bucket"],
        "expected_inputs": item["expected_inputs"],
        "gold_evidence": [
            {
                "doc_name": ev["doc_name"],
                "pdf_page": ev["pdf_page"],
                "evidence_text": ev["evidence_text"],
            }
            for ev in item["gold_evidence"]
        ],
        "checks": {k: None for k in CHECK_KEYS},
        "pass": None,
        "reviewer_notes": "",
    }


def build_template() -> dict[str, Any]:
    if not SUBSET_PATH.exists():
        raise SystemExit(f"missing {SUBSET_PATH} -- run d5_select_subset.py first")
    items = json.loads(SUBSET_PATH.read_text())
    if not items:
        raise SystemExit(f"{SUBSET_PATH} is empty -- nothing to spot-check")

    sample_size = min(SAMPLE_SIZE, len(items))
    sample = _sample_items(items, sample_size)
    return {
        "sample_size": len(sample),
        "instructions": (
            "For each record, check: (1) gold answer follows from evidence, "
            "(2) evidence page is valid, (3) bucket label is reasonable, "
            "(4) expected inputs are reasonable, (5) unit/period is clear. "
            "Fill each `checks.*` with true/false, set the record's `pass` to "
            "true only if all five checks pass, and add `reviewer_notes` for "
            "any failure. Then run: "
            "uv run --project backend dataset_builder/d6_spotcheck_template.py --mode score"
        ),
        "records": [_worksheet_record(it) for it in sample],
    }


def score_template() -> dict[str, Any]:
    if not TEMPLATE_PATH.exists():
        raise SystemExit(f"missing {TEMPLATE_PATH} -- run --mode template first")
    template = json.loads(TEMPLATE_PATH.read_text())
    records = template["records"]

    reviewed = [r for r in records if r["pass"] is not None]
    unreviewed = [r for r in records if r["pass"] is None]
    if not reviewed:
        raise SystemExit(
            f"no records in {TEMPLATE_PATH} have been reviewed yet (all `pass` are null) -- "
            "complete the human audit before scoring"
        )

    passed = sum(1 for r in reviewed if r["pass"] is True)
    failed = sum(1 for r in reviewed if r["pass"] is False)
    pass_rate = passed / len(reviewed) if reviewed else 0.0

    notes_parts = []
    for r in reviewed:
        if r["pass"] is False and r.get("reviewer_notes"):
            notes_parts.append(f"{r['item_id']}: {r['reviewer_notes']}")
    if unreviewed:
        notes_parts.append(f"{len(unreviewed)} sampled record(s) left unreviewed: " + ", ".join(r["item_id"] for r in unreviewed))
    notes = " | ".join(notes_parts) if notes_parts else "All sampled records passed."

    return {
        "sample_size": len(reviewed),
        "passed": passed,
        "failed": failed,
        "pass_rate": round(pass_rate, 4),
        "notes": notes,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="D6 -- human spot-check worksheet + scoring.")
    ap.add_argument("--mode", choices=("template", "score"), default="template")
    args = ap.parse_args()

    if args.mode == "template":
        template = build_template()
        TEMPLATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        TEMPLATE_PATH.write_text(json.dumps(template, indent=2) + "\n")
        print(
            f"[d6] wrote {template['sample_size']}-record spot-check worksheet to {TEMPLATE_PATH}\n"
            f"[d6] fill it in by hand, then run with --mode score",
            file=sys.stderr,
        )
    else:
        summary = score_template()
        SPOTCHECK_PATH.write_text(json.dumps(summary, indent=2) + "\n")
        print(f"[d6] wrote spot-check summary to {SPOTCHECK_PATH}: {summary}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

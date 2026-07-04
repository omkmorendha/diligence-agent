"""Deterministic eval harness entrypoint (spec section 18, Step 3).

    uv run --project backend evals/run.py --fixtures
    uv run --project backend evals/run.py --system baseline
    uv run --project backend evals/run.py --system agent

Inputs:  data/subset.json, runs/{run_id}/trace.jsonl, runs/{run_id}/memo.json
Outputs: results/baseline.json, results/agent.json, results/comparison.json

Built BEFORE the agent (eval-first). Scores: answer accuracy, citation precision,
citation provenance, arithmetic integrity, trace shape, abstention. See scorers.py.

`--fixtures` scores evals/fixtures/* against their expected.json and exits non-zero
on any mismatch -- this is the TDD gate for Step 3 and needs no real run data.
`--system {agent,baseline}` scores real runs/ data against data/subset.json (only
meaningful once the baseline/agent exist -- Steps 9/12).

`--judges` (spec section 21, Tier 2 eval) is an optional add-on flag, off by
default -- the deterministic scorer path above runs exactly the same with or
without it. When set:
  * with `--fixtures`: also runs the LLM-judge corrupted-memo calibration gate
    (evals/judges.py) and prints PASS/FAIL; does not change the fixture exit code.
  * with `--system`: runs the calibration gate first. If it passes, also scores
    every memo item's groundedness/actionability via the LLM judges and folds the
    averages into results/{system}.json as groundedness_judge/actionability_judge
    (matching backend/app/schemas.py SystemMetrics). If calibration fails, judge
    scores are omitted entirely rather than shown as headline metrics (spec
    section 21).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.schemas import Memo, SubsetItem, TraceEvent  # noqa: E402

from scorers import FIXTURES_DIR, score_fixtures, score_run  # noqa: E402

import judges  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = ROOT / "results"


def run_fixtures(fixtures_dir: Path = FIXTURES_DIR) -> int:
    """Score every fixture and report pass/fail against its expected.json."""
    results = score_fixtures(fixtures_dir)
    if not results:
        print(f"[eval] no fixtures found under {fixtures_dir}", file=sys.stderr)
        return 1

    all_ok = True
    for r in results:
        status = "OK  " if r["ok"] else "FAIL"
        print(f"[{status}] {r['fixture']} (scorer_under_test={r['scorer_under_test']})")
        if not r["ok"]:
            all_ok = False
            for mismatch in r["mismatches"]:
                print(f"        {mismatch}")

    passed = sum(1 for r in results if r["ok"])
    print(f"\n{passed}/{len(results)} fixtures scored as expected.")
    return 0 if all_ok else 1


def _load_subset(subset_path: Path) -> dict[str, SubsetItem]:
    raw = json.loads(subset_path.read_text())
    items = raw if isinstance(raw, list) else raw.get("items", [])
    return {i["item_id"]: SubsetItem.model_validate(i) for i in items}


def _discover_runs(runs_dir: Path) -> list[Path]:
    if not runs_dir.is_dir():
        return []
    return sorted(
        d for d in runs_dir.iterdir() if d.is_dir() and (d / "trace.jsonl").exists() and (d / "memo.json").exists()
    )


def _fraction(scores: list[dict[str, Optional[str]]], metric: str, positive: str = "pass") -> Optional[float]:
    values = [s[metric] for s in scores if s.get(metric) is not None]
    if not values:
        return None
    return sum(1 for v in values if v == positive) / len(values)


def _aggregate(scores: list[dict[str, Optional[str]]]) -> dict:
    def fraction(metric: str, positive: str = "pass") -> Optional[float]:
        return _fraction(scores, metric, positive)

    # spec section 22: per-bucket (A_multi_input/B_judgment/C_lookup) answer_accuracy.
    # `bucket` rides along on each per-item score dict (see run_system) purely for
    # this grouping -- it is not itself a pass/fail metric. A bucket is omitted
    # entirely (rather than emitting a null) when every item in it was abstained,
    # since backend/app/schemas.py's BucketAccuracy.answer_accuracy is a plain
    # float, not Optional -- matching the spec section 22 example, which never
    # shows a null bucket value.
    by_bucket: dict[str, dict[str, float]] = {}
    for bucket in sorted({s["bucket"] for s in scores if s.get("bucket")}):
        bucket_scores = [s for s in scores if s.get("bucket") == bucket]
        acc = _fraction(bucket_scores, "answer_accuracy")
        if acc is not None:
            by_bucket[bucket] = {"answer_accuracy": acc}

    return {
        "num_items_scored": len(scores),
        "answer_accuracy": fraction("answer_accuracy"),
        "citation_precision": fraction("citation_precision"),
        "citation_provenance": fraction("citation_provenance"),
        "arithmetic_integrity": fraction("arithmetic_integrity"),
        "trace_shape": fraction("trace_shape"),
        # abstention() returns "correct" / "incorrect_but_calibrated", not "pass"/"fail".
        "abstention_correct_rate": fraction("abstention", positive="correct"),
        # spec section 22: per-bucket (A_multi_input/B_judgment/C_lookup) answer_accuracy.
        "by_bucket": by_bucket,
    }


def _run_judges_over(memo_trace_pairs: list[tuple[Memo, list[TraceEvent]]]) -> Optional[dict[str, float]]:
    """Run the LLM judges (spec section 21) over every memo item in the given runs.

    Runs the corrupted-memo calibration gate first; returns None (and logs why) if
    it fails, so callers never surface uncalibrated judge scores as headline
    metrics. Otherwise returns the mean groundedness/actionability across all
    non-null per-item scores.
    """
    calibration = judges.run_calibration_gate()
    if not calibration["passed"]:
        print(
            "[eval] LLM-judge calibration gate FAILED (see results/corrupted_memo_judge.json) -- "
            "omitting groundedness_judge/actionability_judge (spec section 21).",
            file=sys.stderr,
        )
        return None

    groundedness_scores: list[int] = []
    actionability_scores: list[int] = []
    for memo, trace_events in memo_trace_pairs:
        for memo_item in memo.items:
            result = judges.judge_memo_item(memo_item, memo_item.item_id, trace_events)
            g, a = result.get("groundedness"), result["actionability"]
            if g and g.get("score") is not None:
                groundedness_scores.append(g["score"])
            if a and a.get("score") is not None:
                actionability_scores.append(a["score"])

    return {
        "groundedness_judge": (sum(groundedness_scores) / len(groundedness_scores)) if groundedness_scores else None,
        "actionability_judge": (sum(actionability_scores) / len(actionability_scores)) if actionability_scores else None,
    }


def run_system(system: str, runs_dir: Path, subset_path: Path, run_judges: bool = False) -> int:
    """Score real runs/{run_id}/{trace.jsonl,memo.json} against data/subset.json."""
    if not subset_path.exists():
        print(
            f"[eval] {subset_path} does not exist yet (built in Step 8) -- nothing to score for "
            f"system={system}.",
            file=sys.stderr,
        )
        return 1

    subset_by_item = _load_subset(subset_path)
    run_dirs = _discover_runs(runs_dir)
    if not run_dirs:
        print(f"[eval] no runs found under {runs_dir} -- nothing to score for system={system}.", file=sys.stderr)
        return 1

    per_item_scores: list[dict[str, Optional[str]]] = []
    memo_trace_pairs: list[tuple[Memo, list[TraceEvent]]] = []
    for run_dir in run_dirs:
        trace_events = [
            TraceEvent.model_validate_json(line)
            for line in (run_dir / "trace.jsonl").read_text().splitlines()
            if line.strip()
        ]
        memo = Memo.model_validate_json((run_dir / "memo.json").read_text())
        memo_trace_pairs.append((memo, trace_events))
        for memo_item in memo.items:
            subset_item = subset_by_item.get(memo_item.item_id)
            if subset_item is None:
                print(
                    f"[eval] skipping {memo_item.item_id} in {run_dir.name}: not present in {subset_path}",
                    file=sys.stderr,
                )
                continue
            item_score = score_run(subset_item, trace_events, memo_item)
            item_score["bucket"] = subset_item.bucket
            per_item_scores.append(item_score)

    result = {"system": system, "runs_scored": [d.name for d in run_dirs], **_aggregate(per_item_scores)}

    if run_judges:
        judge_scores = _run_judges_over(memo_trace_pairs)
        if judge_scores is not None:
            result.update(judge_scores)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / f"{system}.json"
    out_path.write_text(json.dumps(result, indent=2) + "\n")
    print(f"[eval] wrote {out_path}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Run deterministic evals (spec section 18).")
    ap.add_argument("--system", choices=["agent", "baseline"], help="Score real runs/ data for this system.")
    ap.add_argument("--fixtures", action="store_true", help="Score bundled fixtures instead of real runs.")
    ap.add_argument("--runs-dir", default=str(ROOT / "runs"), help="Override runs/ directory (real mode).")
    ap.add_argument("--subset", default=str(ROOT / "data" / "subset.json"), help="Override data/subset.json path.")
    ap.add_argument(
        "--judges", action="store_true",
        help="Also run LLM judges (spec section 21, evals/judges.py) behind this flag. "
             "Deterministic scoring above is unaffected either way.",
    )
    args = ap.parse_args()

    if not args.fixtures and not args.system:
        ap.error("pass --fixtures or --system {agent,baseline}")

    print(f"[eval] system={args.system} fixtures={args.fixtures} judges={args.judges}", file=sys.stderr)

    if args.fixtures:
        rc = run_fixtures()
        if args.judges:
            calibration = judges.run_calibration_gate()
            status = "PASS" if calibration["passed"] else "FAIL"
            print(f"[eval] judge calibration gate: {status} (results/corrupted_memo_judge.json)", file=sys.stderr)
        return rc
    return run_system(args.system, Path(args.runs_dir), Path(args.subset), run_judges=args.judges)


if __name__ == "__main__":
    raise SystemExit(main())

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


def _aggregate(scores: list[dict[str, Optional[str]]]) -> dict:
    def fraction(metric: str) -> Optional[float]:
        values = [s[metric] for s in scores if s.get(metric) is not None]
        if not values:
            return None
        return sum(1 for v in values if v == "pass") / len(values)

    return {
        "num_items_scored": len(scores),
        "answer_accuracy": fraction("answer_accuracy"),
        "citation_precision": fraction("citation_precision"),
        "citation_provenance": fraction("citation_provenance"),
        "arithmetic_integrity": fraction("arithmetic_integrity"),
        "trace_shape": fraction("trace_shape"),
        "abstention_correct_rate": fraction("abstention"),
    }


def run_system(system: str, runs_dir: Path, subset_path: Path) -> int:
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
    for run_dir in run_dirs:
        trace_events = [
            TraceEvent.model_validate_json(line)
            for line in (run_dir / "trace.jsonl").read_text().splitlines()
            if line.strip()
        ]
        memo = Memo.model_validate_json((run_dir / "memo.json").read_text())
        for memo_item in memo.items:
            subset_item = subset_by_item.get(memo_item.item_id)
            if subset_item is None:
                print(
                    f"[eval] skipping {memo_item.item_id} in {run_dir.name}: not present in {subset_path}",
                    file=sys.stderr,
                )
                continue
            per_item_scores.append(score_run(subset_item, trace_events, memo_item))

    result = {"system": system, "runs_scored": [d.name for d in run_dirs], **_aggregate(per_item_scores)}

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
    args = ap.parse_args()

    if not args.fixtures and not args.system:
        ap.error("pass --fixtures or --system {agent,baseline}")

    print(f"[eval] system={args.system} fixtures={args.fixtures}", file=sys.stderr)

    if args.fixtures:
        return run_fixtures()
    return run_system(args.system, Path(args.runs_dir), Path(args.subset))


if __name__ == "__main__":
    raise SystemExit(main())

"""Deterministic eval harness entrypoint (spec section 18, Step 3).

    uv run --project backend evals/run.py --system baseline
    uv run --project backend evals/run.py --system agent

Inputs:  data/subset.json, runs/{run_id}/trace.jsonl, runs/{run_id}/memo.json
Outputs: results/baseline.json, results/agent.json, results/comparison.json

Built BEFORE the agent (eval-first). Scores: answer accuracy, citation precision,
citation provenance, arithmetic integrity, trace shape, abstention. See scorers.py.

TODO(Step 3).
"""

from __future__ import annotations

import argparse
import sys


def main() -> int:
    ap = argparse.ArgumentParser(description="Run deterministic evals (spec section 18).")
    ap.add_argument("--system", choices=["agent", "baseline"], required=True)
    ap.add_argument("--fixtures", action="store_true", help="Score bundled fixtures instead of real runs.")
    args = ap.parse_args()
    print(f"[eval] system={args.system} fixtures={args.fixtures}", file=sys.stderr)
    raise NotImplementedError("eval harness: implement in Step 3 (spec sections 18-20).")


if __name__ == "__main__":
    raise SystemExit(main())

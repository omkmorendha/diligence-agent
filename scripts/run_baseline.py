"""Baseline run driver (spec sections 16/25 Step 9).

Runs the naive-RAG baseline (`backend/app/baseline.py`) once per company found in
`data/subset.json`, producing `runs/{run_id}/{trace.jsonl,memo.json,memo.md}` for
each -- the artifacts `evals/run.py --system baseline` scores.

Usage:
    uv run --project backend scripts/run_baseline.py                # every company
    uv run --project backend scripts/run_baseline.py --company AMD  # one company
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import config  # noqa: E402
from app.baseline import run_baseline  # noqa: E402
from app.ingest import slugify  # noqa: E402
from app.trace import TraceWriter  # noqa: E402


def _companies_in_subset() -> list[str]:
    raw = json.loads(config.SUBSET_PATH.read_text())
    rows = raw if isinstance(raw, list) else raw.get("items", [])
    seen: list[str] = []
    for row in rows:
        company = row.get("company")
        if company and company not in seen:
            seen.append(company)
    return seen


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the naive-RAG baseline over data/subset.json.")
    ap.add_argument("--company", action="append", help="Restrict to this company (repeatable). Default: all.")
    ap.add_argument(
        "--run-id-prefix",
        help="Deterministic run ids: <prefix>-<company-slug> instead of baseline_<slug>_<epoch>.",
    )
    args = ap.parse_args()

    companies = args.company or _companies_in_subset()
    if not companies:
        print(f"[baseline] no companies found in {config.SUBSET_PATH}", file=sys.stderr)
        return 1

    for i, company in enumerate(companies, 1):
        if args.run_id_prefix:
            run_id = f"{args.run_id_prefix}-{slugify(company)}"
        else:
            run_id = f"baseline_{slugify(company)}_{int(time.time())}"
        trace = TraceWriter(run_id)
        t0 = time.time()
        print(f"[baseline] ({i}/{len(companies)}) {company} -> run_id={run_id}", file=sys.stderr)
        run_baseline(run_id, company, None, trace)
        print(f"[baseline] {company} done in {time.time() - t0:.1f}s -> {trace.run_dir}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

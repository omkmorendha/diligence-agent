"""One-command driver for a full improve-eval loop iteration.

Runs the agent (or baseline) over every company in data/subset.json with
bounded parallelism (one OS process per company -- the endpoint was verified
clean at 32 concurrent requests), then stages the run artifacts, scores them in
isolation, and analyzes them:

    results/iterations/iter<N>/
        runs/<run_id>/...          staged copies (trace.jsonl, memo.json, llm_calls.jsonl)
        logs/<slug>.log            per-company process output
        per_item_scores.json       evals/run.py --dump-items
        agent.json | baseline.json aggregate scores snapshot
        metrics.json               scripts/analyze_iteration.py output
        code_state.diff / code_state.txt   git state for traceability
    results/iterations/loop_state.json     cross-iteration index

Usage:
    uv run --project backend scripts/run_iteration.py --iteration 1 --label "baseline measurement"
    uv run --project backend scripts/run_iteration.py --iteration 0 --system baseline --no-judges
    ... --skip-runs        # rescore/reanalyze already-staged runs
    ... --company AMD      # restrict (pilot)
    ... --item-ids id1,id2 # restrict further (pilot; agent only)
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.ingest import slugify  # noqa: E402

ITER_ROOT = ROOT / "results" / "iterations"


def _companies() -> list[str]:
    rows = json.loads((ROOT / "data" / "subset.json").read_text())
    seen: list[str] = []
    for row in rows:
        if row["company"] not in seen:
            seen.append(row["company"])
    return seen


def _launch_company(system: str, company: str, run_id: str, log_path: Path, item_ids: str | None) -> int:
    """Run one company's agent/baseline process; returns the exit code."""
    run_dir = ROOT / "runs" / run_id
    if run_dir.exists():
        shutil.rmtree(run_dir)  # a stale dir would make TraceWriter append duplicate events

    if system == "agent":
        cmd = ["uv", "run", "--project", ".", "python", "-m", "app.agent", "--company", company, "--run-id", run_id]
        if item_ids:
            cmd += ["--item-ids", item_ids]
        cwd = ROOT / "backend"
    else:
        cmd = [
            "uv", "run", "--project", "backend", "python", str(ROOT / "scripts" / "run_baseline.py"),
            "--company", company, "--run-id-prefix", run_id.rsplit("-", 1)[0],
        ]
        cwd = ROOT

    with log_path.open("a") as log:
        log.write(f"\n=== {datetime.now(timezone.utc).isoformat()} {' '.join(cmd)}\n")
        log.flush()
        proc = subprocess.run(cmd, cwd=cwd, stdout=log, stderr=subprocess.STDOUT)
    return proc.returncode


def main() -> int:
    ap = argparse.ArgumentParser(description="Run + score + analyze one improve-eval loop iteration.")
    ap.add_argument("--iteration", type=int, required=True)
    ap.add_argument("--system", choices=["agent", "baseline"], default="agent")
    ap.add_argument("--label", default="")
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--company", action="append", help="Restrict to these companies (pilot mode).")
    ap.add_argument("--item-ids", help="Comma-separated item ids (agent pilot mode only).")
    ap.add_argument("--skip-runs", action="store_true", help="Skip the runs; rescore/reanalyze staged artifacts.")
    ap.add_argument("--no-judges", action="store_true", help="Skip Tier-2 LLM judges during scoring.")
    ap.add_argument("--iter-dir", help="Override the iteration directory (default results/iterations/iter<N>).")
    args = ap.parse_args()

    system = args.system
    iter_dir = (Path(args.iter_dir) if args.iter_dir else ITER_ROOT / f"iter{args.iteration}").resolve()
    staging = iter_dir / "runs"
    logs_dir = iter_dir / "logs"
    staging.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    companies = args.company or _companies()
    run_ids = {c: f"iter{args.iteration}-{system}-{slugify(c)}" for c in companies}

    t_start = time.time()
    if not args.skip_runs:
        print(f"[iter{args.iteration}] launching {len(companies)} {system} run(s), concurrency={args.concurrency}")
        failures: list[str] = []
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            futures = {
                pool.submit(
                    _launch_company, system, c, run_ids[c], logs_dir / f"{slugify(c)}.log", args.item_ids
                ): c
                for c in companies
            }
            for fut in as_completed(futures):
                company = futures[fut]
                rc = fut.result()
                if rc != 0:
                    print(f"[iter{args.iteration}] {company} FAILED (rc={rc}), retrying once", file=sys.stderr)
                    rc = _launch_company(system, company, run_ids[company], logs_dir / f"{slugify(company)}.log", args.item_ids)
                if rc != 0:
                    failures.append(company)
                    print(f"[iter{args.iteration}] {company} failed twice (rc={rc})", file=sys.stderr)
                else:
                    print(f"[iter{args.iteration}] {company} done ({time.time() - t_start:.0f}s elapsed)")

        if failures:
            print(f"[iter{args.iteration}] ABORTING: failed companies: {failures}", file=sys.stderr)
            return 1

        # Stage: copy each run dir into the iteration folder so scoring is isolated
        # from whatever else lives under runs/.
        for c in companies:
            src = ROOT / "runs" / run_ids[c]
            dst = staging / run_ids[c]
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
        print(f"[iter{args.iteration}] staged {len(companies)} run(s) -> {staging}")

    # Score in isolation (+ judges unless disabled), dumping per-item rows.
    score_cmd = [
        "uv", "run", "--project", "backend", "python", str(ROOT / "evals" / "run.py"),
        "--system", system,
        "--runs-dir", str(staging),
        "--dump-items", str(iter_dir / "per_item_scores.json"),
    ]
    if not args.no_judges:
        score_cmd.append("--judges")
    print(f"[iter{args.iteration}] scoring: {' '.join(score_cmd)}")
    rc = subprocess.run(score_cmd, cwd=ROOT).returncode
    if rc != 0:
        print(f"[iter{args.iteration}] scoring failed (rc={rc})", file=sys.stderr)
        return rc
    shutil.copy(ROOT / "results" / f"{system}.json", iter_dir / f"{system}.json")

    # Analyze (stdlib-only script).
    analyze_cmd = [
        sys.executable, str(ROOT / "scripts" / "analyze_iteration.py"),
        "--iteration", str(args.iteration),
        "--runs-dir", str(staging),
        "--scores", str(iter_dir / "per_item_scores.json"),
        "--label", args.label,
        "--out", str(iter_dir / "metrics.json"),
    ]
    rc = subprocess.run(analyze_cmd, cwd=ROOT).returncode
    if rc != 0:
        return rc

    # Snapshot code state for traceability across improvement rounds.
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    diff = subprocess.run(["git", "diff", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout
    (iter_dir / "code_state.txt").write_text(f"HEAD: {head}\nlabel: {args.label}\n")
    (iter_dir / "code_state.diff").write_text(diff)

    # Update the cross-iteration index.
    state_path = ITER_ROOT / "loop_state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {"iterations": {}}
    metrics = json.loads((iter_dir / "metrics.json").read_text())
    state["iterations"][str(args.iteration)] = {
        "system": system,
        "label": args.label,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "wall_s": round(time.time() - t_start, 1),
        "aggregate": metrics["aggregate"],
        "dir": str(iter_dir.relative_to(ROOT)),
    }
    state_path.write_text(json.dumps(state, indent=2) + "\n")

    print(f"[iter{args.iteration}] complete in {time.time() - t_start:.0f}s -> {iter_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Run repeated full-agent eval experiments and publish iteration analytics.

Usage examples:
    uv run --project backend scripts/run_iterative_eval.py --iterations 5
    uv run --project backend scripts/run_iterative_eval.py --iterations 1 --companies AMD

The script is host-side by design: it can take a long time and writes artifacts
under results/iterations/{experiment_id}/ for the API/frontend to serve.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "evals"))

from app import config  # noqa: E402
from app.agent import run_agent  # noqa: E402
from app.ingest import slugify  # noqa: E402
from app.trace import TraceWriter  # noqa: E402
from iteration_analytics import build_iteration_report, now_iso  # noqa: E402
from run import score_run_dirs  # noqa: E402

ITERATIONS_DIR = config.RESULTS_DIR / "iterations"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def _experiment_id() -> str:
    return datetime.now(timezone.utc).strftime("iter-%Y%m%dT%H%M%SZ")


def _load_subset_items() -> list[dict[str, Any]]:
    raw = _read_json(config.SUBSET_PATH)
    return raw if isinstance(raw, list) else raw.get("items", [])


def _subset_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    bucket_counts: dict[str, int] = {}
    companies = set()
    for item in items:
        companies.add(item["company"])
        bucket = item.get("bucket", "C_lookup")
        bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1
    return {
        "num_questions": len(items),
        "num_companies": len(companies),
        "bucket_counts": bucket_counts,
    }


def _selected_companies(items: list[dict[str, Any]], requested: Optional[list[str]]) -> list[str]:
    available = sorted({item["company"] for item in items})
    if not requested:
        return available
    normalized = {company.lower(): company for company in available}
    selected: list[str] = []
    for company in requested:
        match = normalized.get(company.lower())
        if match is None:
            raise SystemExit(f"[iter-eval] unknown company {company!r}; available={available}")
        selected.append(match)
    return selected


def _select_eval_items(
    items: list[dict[str, Any]],
    companies: list[str],
    requested_item_ids: Optional[list[str]],
    target_items: int,
) -> list[dict[str, Any]]:
    company_set = set(companies)
    requested_set = set(requested_item_ids) if requested_item_ids else None
    selected = [
        item
        for item in items
        if item["company"] in company_set and (requested_set is None or item["item_id"] in requested_set)
    ]
    if target_items > 0:
        if len(selected) < target_items:
            raise SystemExit(
                f"[iter-eval] selected eval source has {len(selected)} item(s), "
                f"but --target-items requires {target_items}. Regenerate data/subset.json with at least "
                f"{target_items} items or pass --target-items 0 for an explicit smaller run."
            )
        selected = selected[:target_items]
    return selected


def _item_ids_for_company(
    items: list[dict[str, Any]],
    company: str,
) -> list[str]:
    return [item["item_id"] for item in items if item["company"] == company]


def _completed(run_id: str) -> bool:
    run_dir = config.RUNS_DIR / run_id
    return (run_dir / "trace.jsonl").exists() and (run_dir / "memo.json").exists()


def _load_or_create_manifest(
    path: Path,
    experiment_id: str,
    args: argparse.Namespace,
    companies: list[str],
    eval_items: list[dict[str, Any]],
) -> dict[str, Any]:
    if path.exists():
        return _read_json(path)
    return {
        "schema_version": "0.1",
        "experiment_id": experiment_id,
        "created_at": now_iso(),
        "completed_at": None,
        "status": "running",
        "system": "agent",
        "model": config.LLM_MODEL,
        "tool_protocol": config.selected_tool_protocol(),
        "run_selection": {
            "iterations": args.iterations,
            "companies": companies,
            "item_ids": [item["item_id"] for item in eval_items],
            "target_items": args.target_items,
        },
        "iterations": [],
    }


def _ensure_iteration(manifest: dict[str, Any], iteration_num: int) -> dict[str, Any]:
    for iteration in manifest["iterations"]:
        if iteration["iteration"] == iteration_num:
            return iteration
    iteration = {
        "iteration": iteration_num,
        "status": "pending",
        "started_at": None,
        "completed_at": None,
        "runs": [],
    }
    manifest["iterations"].append(iteration)
    manifest["iterations"].sort(key=lambda row: row["iteration"])
    return iteration


def _ensure_run_record(iteration: dict[str, Any], run_id: str, company: str, item_ids: Optional[list[str]]) -> dict[str, Any]:
    for run in iteration["runs"]:
        if run["run_id"] == run_id:
            return run
    run = {
        "run_id": run_id,
        "company": company,
        "item_ids": item_ids,
        "status": "pending",
        "started_at": None,
        "completed_at": None,
        "error": None,
    }
    iteration["runs"].append(run)
    return run


def _run_one(run_record: dict[str, Any], force: bool) -> None:
    run_id = run_record["run_id"]
    run_dir = config.RUNS_DIR / run_id
    if _completed(run_id) and not force:
        run_record["status"] = "completed"
        return
    if run_dir.exists() and force:
        shutil.rmtree(run_dir)

    run_record["status"] = "running"
    run_record["started_at"] = now_iso()
    trace = TraceWriter(run_id=run_id)
    try:
        run_agent(
            run_id=run_id,
            company=run_record["company"],
            item_ids=run_record["item_ids"],
            trace=trace,
        )
        run_record["status"] = "completed"
        run_record["error"] = None
    except Exception as exc:  # noqa: BLE001 - keep long experiments resumable
        run_record["status"] = "failed"
        run_record["error"] = str(exc)
    finally:
        run_record["completed_at"] = now_iso()
        trace.close()


def _publish_report(experiment_dir: Path, manifest: dict[str, Any], subset_summary: dict[str, Any]) -> None:
    completed_run_dirs = [
        config.RUNS_DIR / run["run_id"]
        for iteration in manifest["iterations"]
        for run in iteration["runs"]
        if run["status"] == "completed" and _completed(run["run_id"])
    ]
    if not completed_run_dirs:
        print("[iter-eval] no completed runs to score yet", file=sys.stderr)
        return

    scored = score_run_dirs("agent", completed_run_dirs, config.SUBSET_PATH, run_judges=False)
    report = build_iteration_report(
        experiment_id=manifest["experiment_id"],
        manifest=manifest,
        scored=scored,
        run_dirs=completed_run_dirs,
        subset_summary=subset_summary,
    )

    _write_json(experiment_dir / "per_item_scores.json", report["per_item_scores"])
    _write_json(experiment_dir / "iterations.json", report["iterations"])
    _write_json(experiment_dir / "cumulative.json", report["cumulative"])
    _write_json(experiment_dir / "regressions.json", report["regressions"])
    _write_json(experiment_dir / "latest.json", report)
    _write_json(ITERATIONS_DIR / "latest.json", report)
    print(f"[iter-eval] wrote {experiment_dir / 'latest.json'}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run repeated agent evals and publish iteration analytics.")
    parser.add_argument("--iterations", type=int, default=5, help="Number of full eval iterations to run.")
    parser.add_argument("--companies", help="Comma-separated company names. Default: all companies in data/subset.json.")
    parser.add_argument("--item-ids", help="Comma-separated item IDs to run where they belong. Default: full company checklist.")
    parser.add_argument(
        "--target-items",
        type=int,
        default=40,
        help="Number of questions to run per iteration. Default: 40. Use 0 to run all explicitly selected items.",
    )
    parser.add_argument("--experiment-id", help="Stable experiment ID. Default: timestamped iter-YYYYmmddTHHMMSSZ.")
    parser.add_argument("--force", action="store_true", help="Delete and rerun existing run IDs for this experiment.")
    args = parser.parse_args()

    if args.iterations < 1:
        raise SystemExit("[iter-eval] --iterations must be >= 1")
    if not config.SUBSET_PATH.exists():
        raise SystemExit(f"[iter-eval] missing subset file: {config.SUBSET_PATH}")

    items = _load_subset_items()
    requested_companies = [s.strip() for s in args.companies.split(",") if s.strip()] if args.companies else None
    requested_item_ids = [s.strip() for s in args.item_ids.split(",") if s.strip()] if args.item_ids else None
    companies = _selected_companies(items, requested_companies)
    eval_items = _select_eval_items(items, companies, requested_item_ids, args.target_items)
    companies = sorted({item["company"] for item in eval_items}, key=lambda c: companies.index(c))
    experiment_id = args.experiment_id or _experiment_id()
    experiment_dir = ITERATIONS_DIR / experiment_id
    manifest_path = experiment_dir / "manifest.json"
    manifest = _load_or_create_manifest(manifest_path, experiment_id, args, companies, eval_items)

    _write_json(manifest_path, manifest)
    print(
        f"[iter-eval] experiment_id={experiment_id} iterations={args.iterations} "
        f"questions={len(eval_items)} companies={','.join(companies)} model={config.LLM_MODEL}"
    )

    for iteration_num in range(1, args.iterations + 1):
        iteration = _ensure_iteration(manifest, iteration_num)
        iteration["status"] = "running"
        iteration["started_at"] = iteration["started_at"] or now_iso()
        _write_json(manifest_path, manifest)

        for company in companies:
            item_ids = _item_ids_for_company(eval_items, company)
            if not item_ids:
                continue
            run_id = f"iter-{experiment_id}-i{iteration_num}-{slugify(company)}"
            run_record = _ensure_run_record(iteration, run_id, company, item_ids)
            print(f"[iter-eval] iteration={iteration_num} run_id={run_id} company={company}")
            _run_one(run_record, force=args.force)
            _write_json(manifest_path, manifest)

        statuses = {run["status"] for run in iteration["runs"]}
        iteration["status"] = "completed" if statuses == {"completed"} else "partial" if "completed" in statuses else "failed"
        iteration["completed_at"] = now_iso()
        _write_json(manifest_path, manifest)
        _publish_report(experiment_dir, manifest, _subset_summary(items))

    manifest["status"] = "completed" if all(i["status"] == "completed" for i in manifest["iterations"]) else "partial"
    manifest["completed_at"] = now_iso()
    _write_json(manifest_path, manifest)
    _publish_report(experiment_dir, manifest, _subset_summary(items))
    return 0 if manifest["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

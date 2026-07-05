from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "evals"))
sys.path.insert(0, str(ROOT / "backend"))

from iteration_analytics import build_iteration_report, derive_trace_timing  # noqa: E402
from run import _load_trace, score_run_dirs  # noqa: E402


def _copy_fixture(tmp_path: Path, fixture_name: str, run_id: str) -> Path:
    src = ROOT / "evals" / "fixtures" / fixture_name
    dst = tmp_path / run_id
    shutil.copytree(src, dst)
    return dst


def _subset_from_fixture(tmp_path: Path, fixture_name: str) -> Path:
    fixture = ROOT / "evals" / "fixtures" / fixture_name
    subset_path = tmp_path / "subset.json"
    subset_path.write_text(json.dumps([json.loads((fixture / "subset_item.json").read_text())]))
    return subset_path


def test_score_run_dirs_scopes_to_explicit_runs_and_keeps_details(tmp_path):
    run_dir = _copy_fixture(tmp_path, "correct_lookup", "iter-test-i1-company")
    subset_path = _subset_from_fixture(tmp_path, "correct_lookup")

    scored = score_run_dirs("agent", [run_dir], subset_path)

    assert scored["runs_scored"] == ["iter-test-i1-company"]
    assert scored["num_items_scored"] == 1
    assert scored["answer_accuracy"] == 1.0
    assert scored["per_run"][0]["run_id"] == "iter-test-i1-company"
    assert scored["per_item_scores"][0]["scores"]["answer_accuracy"] == "pass"


def test_trace_timing_derives_stage_buckets_from_event_timestamps(tmp_path):
    run_dir = _copy_fixture(tmp_path, "correct_calculation", "iter-test-i1-company")
    trace_events = _load_trace(run_dir / "trace.jsonl")

    timing = derive_trace_timing(trace_events)

    assert timing["total_seconds"] >= 0
    assert set(timing["stage_seconds"]) & {"reasoning", "retrieval", "calculation", "answering"}
    assert timing["started_at"]
    assert timing["completed_at"]


def test_build_iteration_report_includes_cumulative_bottlenecks_and_missing_metrics(tmp_path):
    run_dir = _copy_fixture(tmp_path, "incorrect_calculation", "iter-test-i1-company")
    subset_path = _subset_from_fixture(tmp_path, "incorrect_calculation")
    scored = score_run_dirs("agent", [run_dir], subset_path)
    manifest = {
        "experiment_id": "iter-test",
        "model": "test-model",
        "tool_protocol": "native",
        "run_selection": {"iterations": 1, "companies": ["TestCo"], "item_ids": None},
        "iterations": [
            {
                "iteration": 1,
                "status": "completed",
                "started_at": "2026-01-01T00:00:00Z",
                "completed_at": "2026-01-01T00:01:00Z",
                "runs": [{"run_id": run_dir.name, "status": "completed"}],
            }
        ],
    }
    subset_summary = {"num_questions": 1, "num_companies": 1, "bucket_counts": {"A_multi_input": 1}}

    report = build_iteration_report("iter-test", manifest, scored, [run_dir], subset_summary)

    assert report["experiment_id"] == "iter-test"
    assert report["iterations"][0]["metrics"]["num_items_scored"] == 1
    assert report["cumulative"][0]["through_iteration"] == 1
    assert report["bottlenecks"]["repeated_failure_items"]
    assert any(metric["metric"] == "token_usage" for metric in report["missing_metrics"])

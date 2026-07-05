"""Analytics helpers for repeated agent eval experiments.

This module stays deterministic: it only reads trace/memo artifacts and the
existing per-item scorer output. It does not call the model or judges.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.schemas import Memo, TraceEvent  # noqa: E402

METRIC_KEYS = [
    "answer_accuracy",
    "citation_precision",
    "citation_provenance",
    "arithmetic_integrity",
    "trace_shape",
    "abstention",
]

HEADLINE_METRICS = [
    "answer_accuracy",
    "citation_precision",
    "citation_provenance",
    "arithmetic_integrity",
    "trace_shape",
    "abstention_correct_rate",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _seconds_between(start: Optional[datetime], end: Optional[datetime]) -> float:
    if start is None or end is None:
        return 0.0
    return max(0.0, (end - start).total_seconds())


def _fraction(scores: list[dict[str, Any]], metric: str, positive: str = "pass") -> Optional[float]:
    values = [s.get(metric) for s in scores if s.get(metric) is not None]
    if not values:
        return None
    return sum(1 for v in values if v == positive) / len(values)


def aggregate_scores(scores: list[dict[str, Any]]) -> dict[str, Any]:
    by_bucket: dict[str, dict[str, float]] = {}
    for bucket in sorted({s["bucket"] for s in scores if s.get("bucket")}):
        bucket_scores = [s for s in scores if s.get("bucket") == bucket]
        acc = _fraction(bucket_scores, "answer_accuracy")
        if acc is not None:
            by_bucket[bucket] = {"answer_accuracy": acc}

    return {
        "num_items_scored": len(scores),
        "answer_accuracy": _fraction(scores, "answer_accuracy"),
        "citation_precision": _fraction(scores, "citation_precision"),
        "citation_provenance": _fraction(scores, "citation_provenance"),
        "arithmetic_integrity": _fraction(scores, "arithmetic_integrity"),
        "trace_shape": _fraction(scores, "trace_shape"),
        "abstention_correct_rate": _fraction(scores, "abstention", positive="correct"),
        "by_bucket": by_bucket,
    }


def _event_stage(event: TraceEvent) -> str:
    if event.type == "retrieval":
        return "retrieval"
    if event.type in ("plan", "scratchpad", "decision"):
        return "reasoning"
    if event.type in ("citation", "item_answer"):
        return "answering"
    if event.type == "verdict":
        return "memo"
    if event.type == "error":
        return "error"
    if event.type in ("tool_call", "tool_result"):
        tool = event.payload.get("tool")
        if tool == "calculate":
            return "calculation"
        if tool in ("search_filing", "get_pages"):
            return "retrieval"
        return "tool_use"
    return "other"


def derive_trace_timing(trace_events: list[TraceEvent]) -> dict[str, Any]:
    stage_seconds: dict[str, float] = defaultdict(float)
    if not trace_events:
        return {"total_seconds": 0.0, "stage_seconds": {}, "started_at": None, "completed_at": None}

    ordered = sorted(trace_events, key=lambda e: e.seq)
    timestamps = [_parse_ts(e.ts) for e in ordered]
    for idx, event in enumerate(ordered[:-1]):
        stage_seconds[_event_stage(event)] += _seconds_between(timestamps[idx], timestamps[idx + 1])

    started_at = ordered[0].ts
    completed_at = ordered[-1].ts
    total_seconds = _seconds_between(timestamps[0], timestamps[-1])
    return {
        "total_seconds": total_seconds,
        "stage_seconds": {stage: round(seconds, 3) for stage, seconds in sorted(stage_seconds.items())},
        "started_at": started_at,
        "completed_at": completed_at,
    }


def derive_item_timing(trace_events: list[TraceEvent]) -> dict[str, dict[str, Any]]:
    by_item: dict[str, list[TraceEvent]] = defaultdict(list)
    for event in trace_events:
        if event.item_id:
            by_item[event.item_id].append(event)

    result: dict[str, dict[str, Any]] = {}
    for item_id, events in by_item.items():
        ordered = sorted(events, key=lambda e: e.seq)
        first = _parse_ts(ordered[0].ts)
        last = _parse_ts(ordered[-1].ts)
        retrievals = [e for e in ordered if e.type == "retrieval"]
        first_retrieval = _parse_ts(retrievals[0].ts) if retrievals else None
        result[item_id] = {
            "item_id": item_id,
            "duration_seconds": round(_seconds_between(first, last), 3),
            "time_to_first_retrieval_seconds": round(_seconds_between(first, first_retrieval), 3)
            if first_retrieval
            else None,
            "retrieval_events": len(retrievals),
            "tool_calls": sum(1 for e in ordered if e.type == "tool_call"),
            "calculate_calls": sum(
                1 for e in ordered if e.type == "tool_call" and e.payload.get("tool") == "calculate"
            ),
            "error_events": sum(1 for e in ordered if e.type == "error"),
        }
    return result


def derive_behavior(trace_events: list[TraceEvent], memo: Memo, per_item_scores: list[dict[str, Any]]) -> dict[str, Any]:
    item_timing = derive_item_timing(trace_events)
    c_lookup_over_retrieval = [
        s["item_id"]
        for s in per_item_scores
        if s.get("bucket") == "C_lookup" and item_timing.get(s["item_id"], {}).get("retrieval_events", 0) > 2
    ]
    answered = sum(1 for item in memo.items if item.status == "answered")
    abstained = sum(1 for item in memo.items if item.status == "abstained")
    citations = sum(len(item.citations) for item in memo.items)
    return {
        "answer_coverage": answered / len(memo.items) if memo.items else None,
        "abstention_rate": abstained / len(memo.items) if memo.items else None,
        "citations_per_answered_item": citations / answered if answered else None,
        "retrieval_events": sum(1 for e in trace_events if e.type == "retrieval"),
        "tool_calls": sum(1 for e in trace_events if e.type == "tool_call"),
        "calculate_calls": sum(
            1 for e in trace_events if e.type == "tool_call" and e.payload.get("tool") == "calculate"
        ),
        "error_events": sum(1 for e in trace_events if e.type == "error"),
        "c_lookup_over_retrieval_items": c_lookup_over_retrieval,
    }


def _load_trace(run_dir: Path) -> list[TraceEvent]:
    return [
        TraceEvent.model_validate_json(line)
        for line in (run_dir / "trace.jsonl").read_text().splitlines()
        if line.strip()
    ]


def _load_memo(run_dir: Path) -> Memo:
    return Memo.model_validate_json((run_dir / "memo.json").read_text())


def _missing_metrics() -> list[dict[str, str]]:
    return [
        {
            "metric": "token_usage",
            "status": "missing",
            "reason": "LLM responses are not currently persisted with prompt/completion token counts.",
            "needed_instrumentation": "Record usage fields from app.llm.chat responses in trace events.",
        },
        {
            "metric": "model_cost",
            "status": "missing",
            "reason": "Token usage and model price metadata are unavailable in current artifacts.",
            "needed_instrumentation": "Persist token usage and a configured per-model pricing table.",
        },
        {
            "metric": "exact_llm_call_latency",
            "status": "partial",
            "reason": "Trace timestamps approximate stage duration but do not isolate each LLM request.",
            "needed_instrumentation": "Emit explicit llm_call_started/llm_call_completed trace events or response metadata.",
        },
        {
            "metric": "retry_count",
            "status": "missing",
            "reason": "Tool protocol and JSON-repair retries are not represented as structured trace fields.",
            "needed_instrumentation": "Emit retry counters from tool_protocol.py and any JSON repair path.",
        },
    ]


def _iteration_summary(
    iteration: dict[str, Any],
    per_item_scores: list[dict[str, Any]],
    per_run: list[dict[str, Any]],
) -> dict[str, Any]:
    run_ids = [r["run_id"] for r in iteration.get("runs", [])]
    item_scores = [s for s in per_item_scores if s.get("run_id") in run_ids]
    run_summaries = [r for r in per_run if r.get("run_id") in run_ids]
    metrics = aggregate_scores(item_scores)
    stage_totals: dict[str, float] = defaultdict(float)
    total_seconds = 0.0
    behavior_totals = Counter()
    for run in run_summaries:
        timing = run.get("timing", {})
        total_seconds += timing.get("total_seconds", 0.0) or 0.0
        for stage, seconds in timing.get("stage_seconds", {}).items():
            stage_totals[stage] += seconds
        behavior = run.get("behavior", {})
        for key in ("retrieval_events", "tool_calls", "calculate_calls", "error_events"):
            behavior_totals[key] += behavior.get(key, 0) or 0

    return {
        "iteration": iteration.get("iteration"),
        "run_ids": run_ids,
        "status": iteration.get("status", "unknown"),
        "started_at": iteration.get("started_at"),
        "completed_at": iteration.get("completed_at"),
        "metrics": metrics,
        "duration_seconds": round(total_seconds, 3),
        "stage_seconds": {stage: round(seconds, 3) for stage, seconds in sorted(stage_totals.items())},
        "behavior": dict(behavior_totals),
    }


def _regressions(iterations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for prev, curr in zip(iterations, iterations[1:]):
        prev_metrics = prev.get("metrics", {})
        curr_metrics = curr.get("metrics", {})
        deltas = []
        for metric in HEADLINE_METRICS:
            before = prev_metrics.get(metric)
            after = curr_metrics.get(metric)
            if before is None or after is None:
                continue
            delta = after - before
            deltas.append(
                {
                    "metric": metric,
                    "previous": before,
                    "current": after,
                    "delta": delta,
                    "direction": "improvement" if delta > 0 else "regression" if delta < 0 else "flat",
                }
            )
        result.append(
            {
                "from_iteration": prev.get("iteration"),
                "to_iteration": curr.get("iteration"),
                "deltas": deltas,
            }
        )
    return result


def _bottlenecks(per_item_scores: list[dict[str, Any]], per_run: list[dict[str, Any]]) -> dict[str, Any]:
    failure_counts: Counter[tuple[str, str, str]] = Counter()
    company_failures: Counter[str] = Counter()
    bucket_failures: Counter[str] = Counter()
    for score in per_item_scores:
        for metric in ("answer_accuracy", "citation_precision", "arithmetic_integrity", "trace_shape"):
            if score.get(metric) == "fail":
                key = (score["item_id"], score.get("question", ""), metric)
                failure_counts[key] += 1
                company_failures[score.get("company", "unknown")] += 1
                bucket_failures[score.get("bucket", "unknown")] += 1

    slow_runs = sorted(
        [
            {
                "run_id": run.get("run_id"),
                "company": run.get("company"),
                "duration_seconds": run.get("timing", {}).get("total_seconds", 0.0),
            }
            for run in per_run
        ],
        key=lambda row: row["duration_seconds"],
        reverse=True,
    )[:5]

    return {
        "repeated_failure_items": [
            {"item_id": item_id, "question": question, "metric": metric, "failures": count}
            for (item_id, question, metric), count in failure_counts.most_common(10)
        ],
        "failures_by_company": dict(company_failures.most_common()),
        "failures_by_bucket": dict(bucket_failures.most_common()),
        "slowest_runs": slow_runs,
    }


def build_iteration_report(
    experiment_id: str,
    manifest: dict[str, Any],
    scored: dict[str, Any],
    run_dirs: list[Path],
    subset_summary: dict[str, Any],
) -> dict[str, Any]:
    per_item_scores = scored.get("per_item_scores", [])
    per_run_by_id = {run["run_id"]: dict(run) for run in scored.get("per_run", [])}

    for run_dir in run_dirs:
        if run_dir.name not in per_run_by_id:
            continue
        trace_events = _load_trace(run_dir)
        memo = _load_memo(run_dir)
        run_item_scores = [s for s in per_item_scores if s.get("run_id") == run_dir.name]
        per_run_by_id[run_dir.name]["timing"] = derive_trace_timing(trace_events)
        per_run_by_id[run_dir.name]["item_timing"] = derive_item_timing(trace_events)
        per_run_by_id[run_dir.name]["behavior"] = derive_behavior(trace_events, memo, run_item_scores)

    per_run = list(per_run_by_id.values())
    iteration_summaries = [
        _iteration_summary(iteration, per_item_scores, per_run)
        for iteration in manifest.get("iterations", [])
    ]
    cumulative = []
    for summary in iteration_summaries:
        iteration_num = summary.get("iteration")
        run_ids = [
            run_id
            for iteration in manifest.get("iterations", [])
            if iteration.get("iteration", 0) <= iteration_num
            for run_id in [run.get("run_id") for run in iteration.get("runs", [])]
        ]
        cumulative_scores = [s for s in per_item_scores if s.get("run_id") in run_ids]
        cumulative.append({"through_iteration": iteration_num, "metrics": aggregate_scores(cumulative_scores)})

    return {
        "schema_version": "0.1",
        "experiment_id": experiment_id,
        "created_at": now_iso(),
        "system": scored.get("system", "agent"),
        "model": manifest.get("model"),
        "tool_protocol": manifest.get("tool_protocol"),
        "run_selection": manifest.get("run_selection", {}),
        "subset": subset_summary,
        "iterations": iteration_summaries,
        "cumulative": cumulative,
        "overall": aggregate_scores(per_item_scores),
        "regressions": _regressions(iteration_summaries),
        "bottlenecks": _bottlenecks(per_item_scores, per_run),
        "missing_metrics": _missing_metrics(),
        "per_run": per_run,
        "per_item_scores": per_item_scores,
    }

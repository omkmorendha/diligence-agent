"""Cross-run iteration analyzer for the improve-eval loop.

Consumes one iteration's staged run artifacts:

    <runs-dir>/<run_id>/trace.jsonl      (always)
    <runs-dir>/<run_id>/memo.json        (always)
    <runs-dir>/<run_id>/llm_calls.jsonl  (written by backend/app/llm.py's usage sink)

plus the per-item deterministic scores dumped by `evals/run.py --dump-items` and
`data/subset.json` (buckets / gold units / answerability), and produces one
`metrics.json` with everything the loop's analysis + reporting stages need:

  * per-item: wall time, LLM time, token usage, tool-call mix, rejection counts,
    failure classification, waterfall segments (llm vs tool, absolute offsets)
  * per-run: wall time, plan time, token totals
  * aggregates: metric fractions overall + per bucket, latency percentiles,
    token totals by purpose, failure taxonomy with item ids, behavior stats

Pure stdlib on purpose -- it must stay runnable even if backend deps change.

Usage:
    python3 scripts/analyze_iteration.py --iteration 1 \
        --runs-dir results/iterations/iter1/runs \
        --scores results/iterations/iter1/per_item_scores.json \
        --out results/iterations/iter1/metrics.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]

METRICS = (
    "answer_accuracy",
    "citation_precision",
    "citation_provenance",
    "arithmetic_integrity",
    "trace_shape",
)

REJECTION_CLASSES = [
    ("citation references chunk_id", "citation_unretrieved_chunk"),
    ("verbatim substring", "citation_quote_not_verbatim"),
    ("must match a calculate result", "numeric_value_ungrounded"),
    ("requires a derived/computed number", "calculate_required"),
    ("assumed inputs", "assumed_inputs"),
    ("at least one verified citation", "answer_without_citation"),
    ("must be a JSON", "malformed_tool_args"),
]

ABSTAIN_CAUSES = [
    ("maximum tool-call budget", "budget_exhausted"),
    ("could not be parsed", "parse_failure"),
]


def _ts(value: str) -> float:
    return datetime.fromisoformat(value).timestamp()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _fraction(rows: list[dict[str, Any]], metric: str, positive: str = "pass") -> Optional[float]:
    values = [r.get(metric) for r in rows if r.get(metric) is not None]
    if not values:
        return None
    return round(sum(1 for v in values if v == positive) / len(values), 4)


def _classify_rejection(detail: str) -> str:
    for needle, label in REJECTION_CLASSES:
        if needle in detail:
            return label
    return "other_error"


def _abstain_cause(reason: str) -> str:
    for needle, label in ABSTAIN_CAUSES:
        if needle in reason:
            return label
    return "model_choice"


def _percentile(values: list[float], pct: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round(pct / 100 * (len(ordered) - 1)))))
    return round(ordered[idx], 2)


def analyze(iteration: int, runs_dir: Path, scores_path: Path, subset_path: Path, label: str) -> dict[str, Any]:
    subset = {row["item_id"]: row for row in json.loads(subset_path.read_text())}
    score_rows = json.loads(scores_path.read_text()) if scores_path.exists() else []
    scores_by_item: dict[str, dict[str, Any]] = {r["item_id"]: r for r in score_rows if r.get("item_id")}

    run_dirs = sorted(d for d in runs_dir.iterdir() if d.is_dir() and (d / "trace.jsonl").exists())
    per_item: list[dict[str, Any]] = []
    per_run: list[dict[str, Any]] = []

    for run_dir in run_dirs:
        events = _read_jsonl(run_dir / "trace.jsonl")
        llm_calls = _read_jsonl(run_dir / "llm_calls.jsonl")
        memo = json.loads((run_dir / "memo.json").read_text()) if (run_dir / "memo.json").exists() else {"items": []}

        run_t0 = _ts(events[0]["ts"]) if events else None
        run_t1 = _ts(events[-1]["ts"]) if events else None
        plan_s = round(sum(c.get("duration_s") or 0 for c in llm_calls if c.get("purpose") == "plan"), 2)

        llm_by_item: dict[Optional[str], list[dict[str, Any]]] = {}
        for c in llm_calls:
            llm_by_item.setdefault(c.get("item_id"), []).append(c)

        for memo_item in memo.get("items", []):
            item_id = memo_item["item_id"]
            item_events = [e for e in events if e.get("item_id") == item_id]
            calls = llm_by_item.get(item_id, [])

            starts: list[float] = [_ts(e["ts"]) for e in item_events]
            starts += [_ts(c["ts"]) - (c.get("duration_s") or 0) for c in calls if c.get("ts")]
            ends: list[float] = [_ts(e["ts"]) for e in item_events] + [_ts(c["ts"]) for c in calls if c.get("ts")]
            wall_s = round(max(ends) - min(starts), 2) if starts and ends else None

            tool_counts: dict[str, int] = {}
            for e in item_events:
                if e["type"] == "tool_call":
                    tool = e.get("payload", {}).get("tool", "unknown")
                    tool_counts[tool] = tool_counts.get(tool, 0) + 1

            rejections: dict[str, int] = {}
            for e in item_events:
                if e["type"] == "error":
                    label_ = _classify_rejection(e.get("detail") or "")
                    rejections[label_] = rejections.get(label_, 0) + 1

            # Waterfall segments: llm spans from llm_calls, tool spans from
            # tool_call -> next tool_result/error pairing, offsets relative to run start.
            segments: list[dict[str, Any]] = []
            if run_t0 is not None:
                for c in calls:
                    if c.get("ts") and c.get("duration_s") is not None:
                        end = _ts(c["ts"])
                        segments.append(
                            {"kind": "llm", "t0": round(end - c["duration_s"] - run_t0, 2), "t1": round(end - run_t0, 2)}
                        )
                pending: Optional[dict[str, Any]] = None
                for e in item_events:
                    if e["type"] == "tool_call":
                        pending = e
                    elif e["type"] in ("tool_result", "error") and pending is not None:
                        segments.append(
                            {
                                "kind": f"tool:{pending.get('payload', {}).get('tool', 'unknown')}",
                                "t0": round(_ts(pending["ts"]) - run_t0, 2),
                                "t1": round(_ts(e["ts"]) - run_t0, 2),
                            }
                        )
                        pending = None
                segments.sort(key=lambda s: s["t0"])

            sub = subset.get(item_id, {})
            score = scores_by_item.get(item_id, {})
            status = memo_item.get("status")
            abstain_cause = _abstain_cause(memo_item.get("answer") or "") if status == "abstained" else None

            if status == "answered":
                acc = score.get("answer_accuracy")
                if acc == "pass":
                    failure_class = "correct"
                elif acc == "fail":
                    failure_class = "wrong_value" if sub.get("gold_value") is not None else "wrong_text"
                else:
                    failure_class = "unscored"
            else:
                failure_class = (
                    "abstained_answerable" if sub.get("answer_verifiable_from_evidence") else "abstained_correct"
                )

            prompt_tokens = sum(c.get("prompt_tokens") or 0 for c in calls)
            completion_tokens = sum(c.get("completion_tokens") or 0 for c in calls)

            per_item.append(
                {
                    "item_id": item_id,
                    "run_id": run_dir.name,
                    "company": memo.get("company"),
                    "bucket": sub.get("bucket"),
                    "status": status,
                    "failure_class": failure_class,
                    "abstain_cause": abstain_cause,
                    "scores": {m: score.get(m) for m in METRICS} | {"abstention": score.get("abstention")},
                    "wall_s": wall_s,
                    "llm_s": round(sum(c.get("duration_s") or 0 for c in calls), 2),
                    "llm_calls": len(calls),
                    "llm_errors": sum(1 for c in calls if c.get("error")),
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "tool_counts": tool_counts,
                    "rejections": rejections,
                    "segments": segments,
                }
            )

        per_run.append(
            {
                "run_id": run_dir.name,
                "company": memo.get("company"),
                "wall_s": round(run_t1 - run_t0, 2) if run_t0 is not None and run_t1 is not None else None,
                "plan_s": plan_s,
                "n_items": len(memo.get("items", [])),
                "llm_calls": len(llm_calls),
                "llm_errors": sum(1 for c in llm_calls if c.get("error")),
                "prompt_tokens": sum(c.get("prompt_tokens") or 0 for c in llm_calls),
                "completion_tokens": sum(c.get("completion_tokens") or 0 for c in llm_calls),
            }
        )

    # --- aggregates ---------------------------------------------------------
    buckets = sorted({i["bucket"] for i in per_item if i["bucket"]})
    by_bucket = {}
    for b in buckets:
        rows = [scores_by_item[i["item_id"]] for i in per_item if i["bucket"] == b and i["item_id"] in scores_by_item]
        by_bucket[b] = {m: _fraction(rows, m) for m in METRICS}
        by_bucket[b]["n_items"] = len(rows)
        by_bucket[b]["answered"] = sum(1 for i in per_item if i["bucket"] == b and i["status"] == "answered")

    walls = [i["wall_s"] for i in per_item if i["wall_s"] is not None]
    all_call_durs = [
        c
        for i in per_item
        for c in [round(i["llm_s"] / i["llm_calls"], 2)] if i["llm_calls"]
    ]

    taxonomy: dict[str, list[str]] = {}
    for i in per_item:
        key = i["failure_class"]
        if i["abstain_cause"] and key == "abstained_answerable":
            key = f"abstained_answerable/{i['abstain_cause']}"
        taxonomy.setdefault(key, []).append(i["item_id"])

    rejection_totals: dict[str, int] = {}
    for i in per_item:
        for k, v in i["rejections"].items():
            rejection_totals[k] = rejection_totals.get(k, 0) + v

    tool_totals: dict[str, int] = {}
    for i in per_item:
        for k, v in i["tool_counts"].items():
            tool_totals[k] = tool_totals.get(k, 0) + v

    tokens_by_purpose: dict[str, dict[str, int]] = {}
    for run_dir in run_dirs:
        for c in _read_jsonl(run_dir / "llm_calls.jsonl"):
            purpose = c.get("purpose") or "other"
            slot = tokens_by_purpose.setdefault(purpose, {"prompt": 0, "completion": 0, "calls": 0, "seconds": 0})
            slot["prompt"] += c.get("prompt_tokens") or 0
            slot["completion"] += c.get("completion_tokens") or 0
            slot["calls"] += 1
            slot["seconds"] = round(slot["seconds"] + (c.get("duration_s") or 0), 1)

    aggregate = {m: _fraction(score_rows, m) for m in METRICS}
    aggregate["abstention_correct_rate"] = _fraction(score_rows, "abstention", positive="correct")
    aggregate["n_items"] = len(per_item)
    aggregate["answered"] = sum(1 for i in per_item if i["status"] == "answered")
    aggregate["abstained"] = sum(1 for i in per_item if i["status"] == "abstained")

    return {
        "iteration": iteration,
        "label": label,
        "generated_at": datetime.now().astimezone().isoformat(),
        "aggregate": aggregate,
        "by_bucket": by_bucket,
        "timing": {
            "item_wall_p50_s": _percentile(walls, 50),
            "item_wall_p95_s": _percentile(walls, 95),
            "item_wall_mean_s": round(statistics.mean(walls), 2) if walls else None,
            "mean_llm_call_s": round(statistics.mean(all_call_durs), 2) if all_call_durs else None,
            "total_llm_s": round(sum(i["llm_s"] for i in per_item), 1),
            "run_wall_max_s": max((r["wall_s"] for r in per_run if r["wall_s"]), default=None),
            "slowest_items": [
                {"item_id": i["item_id"], "wall_s": i["wall_s"], "llm_calls": i["llm_calls"]}
                for i in sorted(per_item, key=lambda x: -(x["wall_s"] or 0))[:8]
            ],
        },
        "tokens": {
            "prompt_total": sum(i["prompt_tokens"] for i in per_item),
            "completion_total": sum(i["completion_tokens"] for i in per_item),
            "by_purpose": tokens_by_purpose,
            "mean_prompt_per_item": round(statistics.mean([i["prompt_tokens"] for i in per_item]), 0) if per_item else None,
        },
        "behavior": {
            "tool_totals": tool_totals,
            "rejection_totals": rejection_totals,
            "mean_tool_calls_per_item": round(
                statistics.mean([sum(i["tool_counts"].values()) for i in per_item]), 2
            ) if per_item else None,
            "budget_exhausted_items": [i["item_id"] for i in per_item if i["abstain_cause"] == "budget_exhausted"],
            "llm_error_total": sum(i["llm_errors"] for i in per_item),
        },
        "failure_taxonomy": {k: sorted(v) for k, v in sorted(taxonomy.items())},
        "per_run": per_run,
        "per_item": per_item,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Analyze one improve-eval loop iteration's staged runs.")
    ap.add_argument("--iteration", type=int, required=True)
    ap.add_argument("--runs-dir", required=True)
    ap.add_argument("--scores", required=True, help="per-item scores JSON from evals/run.py --dump-items")
    ap.add_argument("--subset", default=str(ROOT / "data" / "subset.json"))
    ap.add_argument("--label", default="")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    result = analyze(args.iteration, Path(args.runs_dir), Path(args.scores), Path(args.subset), args.label)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2) + "\n")
    agg = result["aggregate"]
    print(
        f"[analyze] iter{args.iteration}: accuracy={agg['answer_accuracy']} "
        f"citation_precision={agg['citation_precision']} answered={agg['answered']}/{agg['n_items']} -> {out}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

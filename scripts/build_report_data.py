"""Assemble the cross-iteration report dataset for the improvement-loop report.

Reads every iteration's metrics.json (+ scoring-version snapshots + agent.json
judge fields + improvement plans) and emits one JSON the report renders from:
trends, per-bucket evolution, timing waterfalls, token/cost series, failure-
taxonomy evolution, churn matrix (per-item pass/fail across iterations),
judge series, and the scoring-version history.

Usage: python3 scripts/build_report_data.py --out results/iterations/report_data.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
R = ROOT / "results" / "iterations"

ITERS = [
    ("baseline61", "Baseline (naive RAG)"),
    ("iter1", "Iter 1: unmodified agent"),
    ("iter2", "Iter 2: citation-spiral fix"),
    ("iter3", "Iter 3: precision hardening"),
    ("iter4", "Iter 4: coverage recovery"),
    ("iter5", "Iter 5: stability (final)"),
]

SCORE_VERSIONS = [
    ("pre_gold_fix", "v0: string-match only"),
    ("pre_canonical_fix", "v1: + parsed numeric golds"),
    ("pre_iter4_annots", "v2: + polarity/canonical branches"),
    ("pre_final_scorer", "v3: + iter4 annotations"),
    (None, "v4: final (+ geography, signed-off golds)"),
]


def load(path: Path):
    return json.loads(path.read_text()) if path.exists() else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(R / "report_data.json"))
    args = ap.parse_args()

    out: dict = {"iterations": [], "score_versions": {}, "churn": {}, "plans": {}}

    all_items: set[str] = set()
    for key, label in ITERS:
        d = R / key
        m = load(d / "metrics.json")
        if m is None:
            continue
        sysfile = load(d / "agent.json") or load(d / "baseline.json") or {}
        entry = {
            "key": key,
            "label": label,
            "aggregate": m["aggregate"],
            "correct_of_61": len(m["failure_taxonomy"].get("correct", [])),
            "by_bucket": m["by_bucket"],
            "taxonomy": {k: len(v) for k, v in m["failure_taxonomy"].items()},
            "taxonomy_items": m["failure_taxonomy"],
            "timing": {k: v for k, v in m["timing"].items() if k != "slowest_items"},
            "slowest_items": m["timing"].get("slowest_items", [])[:5],
            "tokens": {
                "prompt_total": m["tokens"]["prompt_total"],
                "completion_total": m["tokens"]["completion_total"],
                "by_purpose": m["tokens"].get("by_purpose", {}),
            },
            "behavior": m["behavior"],
            "judges": {k: v for k, v in sysfile.items() if "judge" in k or "agreement" in k},
            "per_item": [
                {
                    "item_id": i["item_id"],
                    "bucket": i["bucket"],
                    "status": i["status"],
                    "failure_class": i["failure_class"],
                    "wall_s": i["wall_s"],
                    "llm_s": i["llm_s"],
                    "llm_calls": i["llm_calls"],
                    "prompt_tokens": i["prompt_tokens"],
                    "completion_tokens": i["completion_tokens"],
                    "tool_counts": i["tool_counts"],
                    "rejections": i["rejections"],
                    "scores": i["scores"],
                }
                for i in m["per_item"]
            ],
            # Median-length item's waterfall segments as the representative example
            "waterfall_example": None,
        }
        answered = [i for i in m["per_item"] if i["status"] == "answered" and i["segments"]]
        if answered:
            answered.sort(key=lambda i: i["wall_s"] or 0)
            mid = answered[len(answered) // 2]
            entry["waterfall_example"] = {"item_id": mid["item_id"], "wall_s": mid["wall_s"], "segments": mid["segments"]}
        all_items.update(i["item_id"] for i in m["per_item"])
        out["iterations"].append(entry)

        # scoring-version history per iteration (accuracy under each scorer version)
        versions = {}
        for vdir, vlabel in SCORE_VERSIONS:
            vm = m if vdir is None else load(d / vdir / "metrics.json")
            if vm:
                versions[vlabel] = {
                    "answer_accuracy": vm["aggregate"]["answer_accuracy"],
                    "correct": len(vm["failure_taxonomy"].get("correct", [])),
                }
        out["score_versions"][key] = versions

        plan = load(d / "improvement_plan.json")
        if plan and plan.get("plan"):
            out["plans"][key] = {
                "summary": plan["plan"]["analysis_summary"],
                "improvements": [
                    {"id": i["id"], "title": i["title"], "category": i["category"], "priority": i["priority"]}
                    for i in plan["plan"]["improvements"]
                ],
            }

    # churn matrix: item -> per-iteration outcome (correct / wrong / abstained / n.a.)
    for item in sorted(all_items):
        row = {}
        for entry in out["iterations"]:
            hit = next((i for i in entry["per_item"] if i["item_id"] == item), None)
            row[entry["key"]] = hit["failure_class"] if hit else None
        out["churn"][item] = row

    Path(args.out).write_text(json.dumps(out, indent=2) + "\n")
    n = len(out["iterations"])
    print(f"[report-data] {n} iterations, {len(all_items)} items -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

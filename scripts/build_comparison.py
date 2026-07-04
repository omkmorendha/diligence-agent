"""Build results/comparison.json from results/{baseline,agent}.json (spec section 22, Step 17).

Usage:
    uv run --project backend evals/run.py --system baseline
    uv run --project backend evals/run.py --system agent
    uv run --project backend scripts/build_comparison.py

Pure aggregation -- no LLM calls, no new scoring. Reads the two per-system result
files `evals/run.py` already wrote (each now carrying a `by_bucket` breakdown,
see evals/run.py's `_aggregate`) plus `data/subset.json` for the subset summary,
and assembles the single `results/comparison.json` the Evals tab
(`GET /evals/results`) renders, matching `backend/app/schemas.py`'s `Comparison`
schema.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import config  # noqa: E402

RESULTS_DIR = ROOT / "results"

SYSTEM_LABELS = {
    "baseline": "Naive-RAG baseline",
    "agent": "Agent",
}


def _subset_summary() -> dict:
    raw = json.loads(config.SUBSET_PATH.read_text())
    items = raw if isinstance(raw, list) else raw.get("items", [])
    companies = {i["company"] for i in items}
    bucket_counts: dict[str, int] = {}
    for i in items:
        bucket = i.get("bucket", "C_lookup")
        bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1
    return {
        "num_questions": len(items),
        "num_companies": len(companies),
        "bucket_counts": bucket_counts,
    }


def _system_entry(system: str) -> dict:
    path = RESULTS_DIR / f"{system}.json"
    if not path.exists():
        raise SystemExit(
            f"[comparison] {path} does not exist -- run `evals/run.py --system {system}` first."
        )
    data = json.loads(path.read_text())
    by_bucket = data.get("by_bucket") or {}
    return {
        "answer_accuracy": data.get("answer_accuracy"),
        "citation_precision": data.get("citation_precision"),
        "citation_provenance": data.get("citation_provenance"),
        "arithmetic_integrity": data.get("arithmetic_integrity"),
        "trace_shape": data.get("trace_shape"),
        "abstention_correct_rate": data.get("abstention_correct_rate"),
        "groundedness_judge": data.get("groundedness_judge"),
        "actionability_judge": data.get("actionability_judge"),
        "by_bucket": by_bucket,
        "label": SYSTEM_LABELS.get(system, system),
        "notes": (
            f"Real deterministic scores from results/{system}.json: "
            f"{len(data.get('runs_scored', []))} run(s), "
            f"{data.get('num_items_scored', 0)}/{_subset_summary()['num_questions']} subset items scored."
        ),
    }


def main() -> int:
    comparison = {
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "subset": _subset_summary(),
        "systems": {
            "published_reference": {
                "label": "Published FinanceBench reference",
                "notes": "Context only, not apples-to-apples with this exact subset.",
            },
            "baseline": _system_entry("baseline"),
            "agent": _system_entry("agent"),
        },
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / "comparison.json"
    out_path.write_text(json.dumps(comparison, indent=2) + "\n")
    print(f"[comparison] wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

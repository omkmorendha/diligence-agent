"""Dataset characterization (spec section 25, Step 5).

Profiles the pulled FinanceBench raw data so we can decide whether the ideal
subset composition (4 companies x ~8 questions, ~16 A / ~8 B / ~8 C) is
feasible, or whether the fallback policy (spec section D5) must kick in.

This is the tool that turns the spec's "open risks" into concrete numbers.

Input:
    data/raw/financebench.jsonl   (produced by d1_pull_raw.py)

Output (spec: data/dataset_profile.json):
    total rows, companies, document types, reasoning types, question types,
    evidence-count distribution, PDF availability, and candidate companies
    ranked by number of usable questions.

We also apply a HEURISTIC bucket pre-mapping from FinanceBench's native
`question_type` / `question_reasoning` fields onto the spec's A/B/C buckets.
This is only a preview — the authoritative bucketing happens in D3/D4.

Usage:
    uv run dataset_builder/characterize.py
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "financebench.jsonl"
OUT = ROOT / "data" / "dataset_profile.json"


def guess_bucket(question_type: str | None, reasoning: str | None) -> str:
    """Heuristic A/B/C preview from FinanceBench native labels.

    FinanceBench question_type values include e.g.:
        metrics-generated, novel-generated, domain-relevant
    question_reasoning values include e.g.:
        Information extraction, Numerical reasoning,
        Logical reasoning (based on numerical reasoning), etc.

    Mapping (preview only; D3 classifier is authoritative):
        Numerical / multi-step reasoning  -> A_multi_input
        Logical / interpretation          -> B_judgment
        Information extraction / lookup    -> C_lookup
    """
    r = (reasoning or "").lower()
    if "numerical" in r:
        # "logical reasoning (based on numerical reasoning)" -> still a computation
        return "A_multi_input"
    if "logical" in r or "interpret" in r or "comparison" in r:
        return "B_judgment"
    if "information extraction" in r or "extraction" in r or "lookup" in r:
        return "C_lookup"
    # fall back on question_type
    qt = (question_type or "").lower()
    if "metrics" in qt:
        return "C_lookup"
    return "B_judgment"


def main() -> int:
    if not RAW.exists():
        raise SystemExit(f"missing {RAW} — run d1_pull_raw.py first")

    rows = [json.loads(line) for line in RAW.read_text().splitlines() if line.strip()]

    companies = Counter(r["company"] for r in rows)
    doc_types = Counter(r.get("doc_type") for r in rows)
    doc_periods = Counter(str(r.get("doc_period")) for r in rows)
    sectors = Counter(r.get("gics_sector") for r in rows)
    q_types = Counter(r.get("question_type") for r in rows)
    reasonings = Counter(r.get("question_reasoning") for r in rows)
    evidence_counts = Counter(len(r.get("evidence", [])) for r in rows)
    buckets = Counter(guess_bucket(r.get("question_type"), r.get("question_reasoning")) for r in rows)

    # per-company breakdown for subset selection
    q_by_company: Counter = Counter(r["company"] for r in rows)
    docs_by_company: dict[str, set] = defaultdict(set)
    buckets_by_company: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        docs_by_company[r["company"]].add(r["doc_name"])
        buckets_by_company[r["company"]][
            guess_bucket(r.get("question_type"), r.get("question_reasoning"))
        ] += 1

    candidate_companies: list[dict] = sorted(
        (
            {
                "company": name,
                "usable_questions": count,
                "num_docs": len(docs_by_company[name]),
                "bucket_preview": dict(buckets_by_company[name]),
            }
            for name, count in q_by_company.items()
        ),
        key=lambda x: x["usable_questions"],  # type: ignore[return-value,index]
        reverse=True,
    )

    # feasibility check against the spec's ideal target
    companies_with_8plus = [c for c in candidate_companies if c["usable_questions"] >= 8]
    profile = {
        "total_rows": len(rows),
        "num_companies": len(companies),
        "num_docs": len({r["doc_name"] for r in rows}),
        "document_types": dict(doc_types),
        "document_periods": dict(sorted(doc_periods.items())),
        "gics_sectors": dict(sectors),
        "question_types": dict(q_types),
        "question_reasonings": dict(reasonings),
        "evidence_count_distribution": dict(sorted(evidence_counts.items())),
        "bucket_preview_totals": dict(buckets),
        "candidate_companies": candidate_companies,
        "feasibility": {
            "ideal_target": "4 companies x ~8 questions = ~32 (16 A / 8 B / 8 C)",
            "companies_with_8plus_questions": [c["company"] for c in companies_with_8plus],
            "num_companies_with_8plus": len(companies_with_8plus),
            "ideal_feasible": len(companies_with_8plus) >= 4,
            "note": "bucket_preview is heuristic; D3/D4 classifier is authoritative.",
        },
    }

    OUT.write_text(json.dumps(profile, indent=2, default=str))
    print(f"[characterize] {len(rows)} questions, {len(companies)} companies, "
          f"{profile['num_docs']} docs")
    print(f"[characterize] bucket preview: {dict(buckets)}")
    print(f"[characterize] companies with >=8 questions: {len(companies_with_8plus)} "
          f"(ideal_feasible={profile['feasibility']['ideal_feasible']})")
    print(f"[characterize] top companies: "
          f"{[(c['company'], c['usable_questions']) for c in candidate_companies[:6]]}")
    print(f"[characterize] wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

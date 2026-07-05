"""D5 — Stratified subset selection (spec section 6 D5, Step 8).

Target: 40 questions across ranked companies  (composition target ~20 A / ~10 B / ~10 C).
Per company: >=2 predicted-baseline-failure questions; prefer recognizable
companies, clean evidence pages, strong parse quality, good live-trace potential.

Fallback policy (spec D5): 32/4 -> 24/3 -> 16/(2-3); prioritize A and C over B;
mark synthetic/unanswerable items and exclude from headline accuracy; disputed
human-reviewed items allowed only with {"human_reviewed": true}.

Emits the frozen subset.json schema (spec section 8), where each item carries gold
fields for the eval harness only. The agent-visible surface is item_id/company/question.

Output:
    data/subset.json         list[SubsetItem] (spec section 8)
    data/subset_report.md    stratification writeup (companies/tiers/buckets chosen and why)

NOTE: characterize.py -> data/dataset_profile.json already confirmed the ideal
target is FEASIBLE (7 companies have >=8 usable questions) using D3's *heuristic
preview*. This script is deterministic over data/verified.jsonl (D3+D4, authoritative)
instead, which is stricter. The iterative eval workflow uses a larger 40-question
subset so repeated runs have enough signal to expose regressions and variance.

PAGE-NUMBER CONVENTION (decided; see AMBIGUITIES.md section 3):
    FinanceBench `evidence_page_num` is 0-indexed into the PDF. The spec's
    subset.json / citation schema uses a 1-indexed `pdf_page`. We adopt:

        pdf_page  = evidence_page_num + 1        # 1-indexed PDF page
        page_label = printed footer if D2 has it, else str(pdf_page)

    Use `to_pdf_page()` / `gold_evidence_from_raw()` below so the +1 mapping is
    applied in exactly one place. `get_pages`, the corpus endpoint, and citation
    scoring (+/-1 page slack) all consume the 1-indexed `pdf_page`.

SELECTION POLICY (deterministic given data/verified.jsonl):
    1. Eligible pool = rows where `include=true` AND `disagreement=false`.
       Disagreement rows (D3/D4 disagree on bucket/verifiability/evidence) are
       exactly `data/disputes.jsonl` and, per the fallback policy, may only be
       added back in if a human marks `human_reviewed: true` -- no human review
       has happened yet (that is D6's job, run *after* this script), so none of
       data/disputes.jsonl is auto-included here.
    2. Rank companies by (# eligible questions desc, # predicted-baseline-failure
       questions desc, company name asc) -- a recognizable-brand + demo-value
       proxy: FinanceBench's companies are all large, well-known public issuers,
       so "prefer recognizable companies" is satisfied by construction, and more
       eligible/failure-prone questions means a richer, more demonstrable checklist.
    3. Walk ranked companies until exactly 40 selected questions are available.
       Each fully included company must clear >=2 baseline-failure eligible
       questions and >=4 eligible questions total; the final company may be
       partially included to hit the 40-question target exactly.
    4. Per selected company, cap at 8 questions. If a company has more than 8
       eligible, drop the excess starting from B_judgment (least prioritized per
       the fallback policy's "prioritize A_multi_input and C_lookup over
       B_judgment"), then C_lookup, then A_multi_input; within a bucket, keep
       predicted-baseline-failure items and clean-evidence items (no evidence
       page on D2's empty/low-text page list) preferentially, breaking remaining
       ties by question_id for determinism.
    5. `gold_value`/`gold_unit` are parsed from `gold_answer` only for clean
       numeric strings ("$1577.00", "4.2%"); everything else is left as
       `gold_unit="text"`, `gold_value=null` and scored by string match (spec
       section 20; see AMBIGUITIES.md section 5).
    6. `demo_candidate=true` for predicted-baseline-failure A_multi_input items
       (best fits the demo script's multi-retrieval + calculator + citation
       flow, spec section 27); one is guaranteed per company where available.

No natively unanswerable question exists in FinanceBench (AMBIGUITIES.md
section 7), so the fallback policy's unanswerable requirement is omitted per
its own escape hatch ("If no true unanswerable question exists, omit the
unanswerable requirement.").
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from d2_parse_test import slugify  # noqa: E402
from app.schemas import SubsetItem  # noqa: E402

RAW_PATH = ROOT / "data" / "raw" / "financebench.jsonl"
VERIFIED_PATH = ROOT / "data" / "verified.jsonl"
PARSE_REPORT_PATH = ROOT / "data" / "parse_report.json"
SUBSET_PATH = ROOT / "data" / "subset.json"
REPORT_PATH = ROOT / "data" / "subset_report.md"

BUCKETS = ("A_multi_input", "B_judgment", "C_lookup")
# Drop order when a company has more than the per-company cap: least-prioritized
# bucket first (fallback policy point 4: "prioritize A_multi_input and C_lookup
# over B_judgment").
DROP_PRIORITY = ("B_judgment", "C_lookup", "A_multi_input")

TARGET_QUESTIONS = 40
MIN_BASELINE_FAILURE_PER_COMPANY = 2
MIN_ELIGIBLE_PER_COMPANY = 4
PER_COMPANY_CAP = 8

# Per-8-slot target composition, proportional to the original 4:2:2 ideal.
TARGET_COMPOSITION = {"A_multi_input": 4, "C_lookup": 2, "B_judgment": 2}
FILL_PRIORITY = ("A_multi_input", "C_lookup", "B_judgment")  # for redistributing shortfall

DOC_TYPE_MAP = {"10k": "10k", "10q": "10q", "8k": "8k"}  # else "other" (e.g. FinanceBench "Earnings")


def to_pdf_page(evidence_page_num: int) -> int:
    """Map FinanceBench's 0-indexed page to the spec's 1-indexed `pdf_page`."""
    return int(evidence_page_num) + 1


def gold_evidence_from_raw(
    raw_evidence: dict[str, Any],
    doc_type: str | None,
    filing_period: str | None,
    page_label: str | None = None,
) -> dict[str, Any]:
    """Build one `gold_evidence` entry (spec section 8) from a raw FinanceBench
    evidence object, applying the pdf_page convention in a single place.

    `raw_evidence` fields used: doc_name, evidence_page_num, evidence_text.
    We keep the original 0-indexed value in `evidence_page_num_raw` for audit.
    """
    pdf_page = to_pdf_page(raw_evidence["evidence_page_num"])
    doc_name = raw_evidence["doc_name"]
    return {
        "doc_id": doc_name,            # doc_id == doc_name (AMBIGUITIES.md section 4)
        "doc_name": doc_name,
        "doc_type": (doc_type or "other"),
        "filing_period": filing_period or "",
        "pdf_page": pdf_page,
        "page_label": page_label if page_label is not None else str(pdf_page),
        "evidence_text": raw_evidence.get("evidence_text", ""),
        "evidence_page_num_raw": int(raw_evidence["evidence_page_num"]),
    }


_NUMERIC_RE = re.compile(r"^\$?-?[\d,]+(\.\d+)?%?$")


def parse_gold_value(gold_answer: str) -> tuple[float | None, str]:
    """Parse `gold_value`/`gold_unit` from a clean numeric answer string only
    (AMBIGUITIES.md section 5). Prose / mixed-text answers stay text/null and
    are scored by normalized string match (spec section 20).
    """
    s = gold_answer.strip()
    if not _NUMERIC_RE.match(s):
        return None, "text"
    is_pct = s.endswith("%")
    is_usd = s.startswith("$")
    core = s[:-1] if is_pct else s
    core = core[1:] if is_usd else core
    core = core.replace(",", "")
    try:
        value = float(core)
    except ValueError:
        return None, "text"
    if is_pct:
        return value, "percent"
    if is_usd:
        return value, "USD millions"
    return value, "ratio"


_QUARTER_RE = re.compile(r"Q([1-4])")
_DATED_RE = re.compile(r"dated-(\d{4}-\d{2}-\d{2})")


def derive_filing_period(doc_name: str, doc_type_raw: str, doc_period: int) -> str:
    """Best-effort filing_period string from doc_name/doc_type/doc_period
    (AMBIGUITIES.md section 4). Quarter markers and 8-K filing dates in the
    doc_name win over the plain fiscal year when present.
    """
    m = _QUARTER_RE.search(doc_name)
    if m:
        return f"{doc_period}Q{m.group(1)}"
    m = _DATED_RE.search(doc_name)
    if m:
        return m.group(1)
    if doc_type_raw == "10k":
        return f"FY{doc_period}"
    return str(doc_period)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _evidence_clean(evidence: list[dict[str, Any]], doc_by_id: dict[str, dict[str, Any]]) -> bool:
    """True if none of the item's evidence pages are on D2's empty/low-text list."""
    for ev in evidence:
        doc = doc_by_id.get(ev["doc_name"])
        if doc is None:
            continue
        pdf_page = to_pdf_page(ev["evidence_page_num"])
        if pdf_page in doc.get("empty_pages", []) or pdf_page in doc.get("low_text_pages", []):
            return False
    return True


def _rank_companies(eligible: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group eligible rows by company and rank per the SELECTION POLICY docstring."""
    by_company: dict[str, list[dict[str, Any]]] = {}
    for row in eligible:
        by_company.setdefault(row["company"], []).append(row)

    stats = []
    for company, rows in by_company.items():
        baseline_failures = sum(1 for r in rows if r["predicted_baseline_failure"])
        stats.append({"company": company, "rows": rows, "n_eligible": len(rows), "baseline_failures": baseline_failures})

    stats.sort(key=lambda s: (-s["n_eligible"], -s["baseline_failures"], s["company"]))
    return stats


def _select_company_rows(rows: list[dict[str, Any]], doc_by_id_getter) -> list[dict[str, Any]]:
    """Pick <=PER_COMPANY_CAP rows for one company, targeting TARGET_COMPOSITION
    and preferring baseline-failure + clean-evidence items within a bucket.
    """
    by_bucket: dict[str, list[dict[str, Any]]] = {b: [] for b in BUCKETS}
    for r in rows:
        by_bucket[r["bucket_d3"]].append(r)

    def sort_key(r: dict[str, Any]):
        # baseline-failure first (demo value), then clean evidence, then stable question_id order.
        return (not r["predicted_baseline_failure"], not r["_clean_evidence"], r["question_id"])

    for b in BUCKETS:
        by_bucket[b].sort(key=sort_key)

    if sum(len(v) for v in by_bucket.values()) <= PER_COMPANY_CAP:
        return list(rows)

    # Start from the target composition (capped by availability), then fill any
    # remaining capacity in FILL_PRIORITY order (A, C over B).
    alloc = {b: min(TARGET_COMPOSITION[b], len(by_bucket[b])) for b in BUCKETS}
    remaining = PER_COMPANY_CAP - sum(alloc.values())
    for b in FILL_PRIORITY:
        if remaining <= 0:
            break
        extra = min(remaining, len(by_bucket[b]) - alloc[b])
        if extra > 0:
            alloc[b] += extra
            remaining -= extra

    selected = []
    for b in BUCKETS:
        selected.extend(by_bucket[b][: alloc[b]])
    return selected


def _eligible_company_stats(ranked: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        stat
        for stat in ranked
        if stat["baseline_failures"] >= MIN_BASELINE_FAILURE_PER_COMPANY
        and stat["n_eligible"] >= MIN_ELIGIBLE_PER_COMPANY
    ]


def build_subset() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not VERIFIED_PATH.exists():
        raise SystemExit(f"missing {VERIFIED_PATH} -- run d4_verify.py first")
    if not RAW_PATH.exists():
        raise SystemExit(f"missing {RAW_PATH} -- run d1_pull_raw.py first")
    if not PARSE_REPORT_PATH.exists():
        raise SystemExit(f"missing {PARSE_REPORT_PATH} -- run d2_parse_test.py first")

    verified = _load_jsonl(VERIFIED_PATH)
    raw_by_qid = {r["question_id"]: r for r in _load_jsonl(RAW_PATH)}
    parse_report = json.loads(PARSE_REPORT_PATH.read_text())
    doc_by_id = {d["doc_id"]: d for d in parse_report["docs"]}

    eligible = [r for r in verified if r["include"] and not r["disagreement"]]
    for r in eligible:
        raw = raw_by_qid[r["question_id"]]
        r["_clean_evidence"] = _evidence_clean(raw["evidence"], doc_by_id)

    ranked = _eligible_company_stats(_rank_companies(eligible))

    items: list[dict[str, Any]] = []
    company_report = []
    for company_stat in ranked:
        if len(items) >= TARGET_QUESTIONS:
            break
        company = company_stat["company"]
        selected_rows = _select_company_rows(company_stat["rows"], doc_by_id)
        # Deterministic within-company order: bucket priority (A, C, B), then question_id.
        order = {"A_multi_input": 0, "C_lookup": 1, "B_judgment": 2}
        selected_rows.sort(key=lambda r: (order[r["bucket_d3"]], r["question_id"]))
        remaining = TARGET_QUESTIONS - len(items)
        selected_rows = selected_rows[:remaining]

        slug = slugify(company)
        bucket_counts = {"A_multi_input": 0, "B_judgment": 0, "C_lookup": 0}
        baseline_failures = 0
        for i, row in enumerate(selected_rows, start=1):
            raw = raw_by_qid[row["question_id"]]
            doc_type_raw = raw["doc_type"]
            doc_type = DOC_TYPE_MAP.get(doc_type_raw, "other")
            filing_period = derive_filing_period(raw["doc_name"], doc_type_raw, raw["doc_period"])

            gold_value, gold_unit = parse_gold_value(row["gold_answer"])
            gold_evidence = [
                gold_evidence_from_raw(ev, doc_type, filing_period) for ev in raw["evidence"]
            ]
            for ge in gold_evidence:
                ge.pop("evidence_page_num_raw", None)  # audit-only, not part of the frozen schema

            bucket = row["bucket_d3"]
            bucket_counts[bucket] += 1
            if row["predicted_baseline_failure"]:
                baseline_failures += 1

            demo_candidate = bool(row["predicted_baseline_failure"] and bucket == "A_multi_input")

            expected_formula = row["expected_formula"] or None

            item = {
                "item_id": f"{slug}_{i:02d}",
                "question_id": row["question_id"],
                "company": company,
                "question": row["question"],
                "gold_answer": row["gold_answer"],
                "gold_value": gold_value,
                "gold_unit": gold_unit,
                "gold_evidence": gold_evidence,
                "bucket": bucket,
                "expected_formula": expected_formula,
                "expected_inputs": row["expected_inputs"],
                "predicted_baseline_failure": row["predicted_baseline_failure"],
                "answer_verifiable_from_evidence": True,   # guaranteed by the `include` filter
                "unit_or_period_ambiguity": False,          # guaranteed by the `include` filter
                "demo_candidate": demo_candidate,
                "human_reviewed": False,
                "tolerance": {"relative": 0.01, "absolute": None},
            }
            # Validate against the frozen schema before it ever hits disk.
            SubsetItem.model_validate(item)
            items.append(item)

        # Ensure at least one demo_candidate per company if any baseline-failure item exists.
        if baseline_failures > 0 and not any(
            it["demo_candidate"] for it in items if it["company"] == company
        ):
            for it in items:
                if it["company"] == company and it["predicted_baseline_failure"]:
                    it["demo_candidate"] = True
                    break

        company_report.append(
            {
                "company": company,
                "n_selected": len(selected_rows),
                "n_eligible": company_stat["n_eligible"],
                "bucket_counts": bucket_counts,
                "baseline_failures": baseline_failures,
            }
        )

    if len(items) != TARGET_QUESTIONS:
        raise SystemExit(
            f"strict verified pool produced {len(items)} selected items, expected {TARGET_QUESTIONS}"
        )

    meta = {
        "target_questions": TARGET_QUESTIONS,
        "total_items": len(items),
        "companies": company_report,
        "bucket_totals": {
            b: sum(c["bucket_counts"][b] for c in company_report) for b in BUCKETS
        },
    }
    return items, meta


def _write_report(meta: dict[str, Any]) -> None:
    lines = [
        "# D5 subset selection report",
        "",
        f"Selected **{meta['total_items']}** questions across "
        f"**{len(meta['companies'])}** companies (target: "
        f"{meta['target_questions']} questions, <=8 per company).",
        "",
        "## Bucket composition",
        "",
        f"- A_multi_input: {meta['bucket_totals']['A_multi_input']} (ideal ~20)",
        f"- B_judgment: {meta['bucket_totals']['B_judgment']} (ideal ~10)",
        f"- C_lookup: {meta['bucket_totals']['C_lookup']} (ideal ~10)",
        "",
        "C_lookup questions are scarce in the D3/D4-verified pool for the "
        "highest-eligible-count companies on this corpus, so the achieved "
        "composition may undershoot the ideal 20/10/10 split on scarce buckets in favor of "
        "B; this is the actual FinanceBench distribution, not a selection bug "
        "(see AMBIGUITIES.md section 6 and data/verified.jsonl bucket_d3 counts).",
        "",
        "## Per-company breakdown",
        "",
        "| company | selected | eligible pool | A | B | C | baseline-failures |",
        "|---|---|---|---|---|---|---|",
    ]
    for c in meta["companies"]:
        bc = c["bucket_counts"]
        lines.append(
            f"| {c['company']} | {c['n_selected']} | {c['n_eligible']} | "
            f"{bc['A_multi_input']} | {bc['B_judgment']} | {bc['C_lookup']} | {c['baseline_failures']} |"
        )
    lines += [
        "",
        "## Selection policy",
        "",
        "Eligible pool = `data/verified.jsonl` rows with `include=true` and "
        "`disagreement=false` (i.e. excludes everything in `data/disputes.jsonl` "
        "-- those require `human_reviewed: true` per the fallback policy, which "
        "is D6's job and has not happened yet). Companies ranked by "
        "(eligible count desc, baseline-failure count desc, name asc), then "
        f"included until the global target of {TARGET_QUESTIONS} questions is "
        f"reached. Fully included companies clear >= {MIN_BASELINE_FAILURE_PER_COMPANY} "
        f"baseline-failure questions and >= {MIN_ELIGIBLE_PER_COMPANY} eligible "
        "questions; the final company may be partially included to hit exactly "
        "40. Per-company selection targets a 4:2:2 (A:C:B) split of the 8-slot "
        "cap, falls back to filling remaining slots in A, then C, then B order "
        "when a bucket is short, and drops down from B, then C, then A when a "
        "company has more than 8 eligible questions.",
        "",
    ]
    REPORT_PATH.write_text("\n".join(lines) + "\n")


def main() -> int:
    items, meta = build_subset()

    SUBSET_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUBSET_PATH.write_text(json.dumps(items, indent=2) + "\n")
    _write_report(meta)

    print(
        f"[d5] wrote {len(items)} items across {len(meta['companies'])} companies to {SUBSET_PATH}",
        file=sys.stderr,
    )
    for c in meta["companies"]:
        bc = c["bucket_counts"]
        print(
            f"[d5]   {c['company']}: {c['n_selected']}/{c['n_eligible']} "
            f"(A={bc['A_multi_input']} B={bc['B_judgment']} C={bc['C_lookup']} "
            f"bf={c['baseline_failures']})",
            file=sys.stderr,
        )
    print(f"[d5] report written to {REPORT_PATH}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""D5 — Stratified subset selection (spec section 6 D5, Step 8).

Target: ~4 companies x ~8 questions = ~32  (composition ~16 A / ~8 B / ~8 C).
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
instead, which is stricter — see SELECTION POLICY below for why the final subset is
~28-32 rather than exactly 32.

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
    3. Walk the fallback tiers (4 companies/8 each -> 3/8 -> 2/8) in order; a tier
       is accepted once every candidate company in it has >=2 baseline-failure
       eligible questions (the spec's explicit per-company floor) and >=4 eligible
       questions total (a sanity floor so no company contributes a token question).
       On this corpus tier 1 (4 companies) is accepted: PepsiCo, Boeing,
       Johnson & Johnson, AMD all clear both floors.
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
# IMP3-1: sidecar of human-reviewable canonical gold annotations (gold_polarity /
# gold_canonical), keyed by question_id. Merged in additively below; gold_answer and
# gold_evidence are never touched. See data/gold_annotations.json's _README.
GOLD_ANNOTATIONS_PATH = ROOT / "data" / "gold_annotations.json"

BUCKETS = ("A_multi_input", "B_judgment", "C_lookup")
# Drop order when a company has more than the per-company cap: least-prioritized
# bucket first (fallback policy point 4: "prioritize A_multi_input and C_lookup
# over B_judgment").
DROP_PRIORITY = ("B_judgment", "C_lookup", "A_multi_input")

MIN_BASELINE_FAILURE_PER_COMPANY = 2
MIN_ELIGIBLE_PER_COMPANY = 4
PER_COMPANY_CAP = 8
FALLBACK_TIERS = [4, 3, 2]  # number of companies to try, in order

# Per-8-slot target composition, proportional to the spec's ~16A/~8B/~8C ideal.
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

# --- IMP-2 (iter1 improvement_plan.json): numeric-text gold parsing -----------
# Iter1's headline accuracy was depressed by a measurement artifact: 37 golds with
# gold_value=null contained digits, so numeric answers were graded by normalized
# STRING match (scorers.answer_accuracy falls back to string match when gold_value
# is None) instead of the numeric +/-1% scorer. We conservatively lift the PRIMARY
# magnitude out of prose golds that reduce to a single number, leaving genuinely
# qualitative / comparative / multi-number / entity answers as text so the string /
# judge scorer still applies (task step 2: "when in doubt, leave it text").
#
# CANONICAL SCALE: scorers.answer_accuracy compares gold_value vs the memo's numeric
# `value` numerically and UNIT-AGNOSTICALLY, so the parsed value must match the scale
# the agent reports, which is USD millions (agent memos state e.g. "$13.2 billion
# (13,200 USD millions)"). Hence: absolute "$X,XXX,XXX" -> /1e6; "$X billion" -> x1000;
# "$X million" -> as-is; all consistent with the existing 6 "$NNNN.00" USD-millions golds.

# A magnitude token: currency/percent/decimal literal, possibly comma-grouped.
_NUM_TOKEN_RE = re.compile(r"[-+]?\$?\d[\d,]*(?:\.\d+)?%?")

# Cue words that demote a lone number to a supporting detail behind a categorical /
# yes-no / comparison / ranking answer (the number is NOT what the question wants, so
# +/-1% grading would be meaningless). Examples this guards: mgm_resorts_06 "...revenue
# declined 44%..." (answer is a region), verizon_04 "Cross currency swaps. Its notional
# value was $32,502 million." (answer is the instrument), best_buy_05 "...the most cash
# flow from operating activities ($1.8 bn)" (answer is the activity), verizon_03 "...debt
# decreased by $229 million." (a signed change behind a yes/no). Kept deliberately
# specific ("increased by", not bare "increase") so genuine magnitude answers survive
# (pepsico_01 "$400,000,000 increase.").
_QUALITATIVE_CUES = (
    "decreased by", "increased by", "increase of", "decrease of",
    "decline of", "declined", "improved", "grew by",
    "most", "least", "highest", "largest", "biggest", "lowest", "worst",
    "notional value", "per share", "compared", "consistent", "high growth",
    "cash flow from",
)

_PCT_POINT_RE = re.compile(r"([-+]?\d+(?:\.\d+)?)\s*percentage points?", re.IGNORECASE)
_DOLLAR_BILLION_RE = re.compile(r"\$\s?([\d,]+(?:\.\d+)?)\s*(?:billion|bn)\b", re.IGNORECASE)
_DOLLAR_MILLION_RE = re.compile(r"\$\s?([\d,]+(?:\.\d+)?)\s*(?:million|mn)\b", re.IGNORECASE)
_DOLLAR_ABS_RE = re.compile(r"\$\s?([\d,]+(?:\.\d+)?)")
_DECIMAL_TOKEN_RE = re.compile(r"^[-+]?\d+\.\d+$")


def _is_year_like(raw: str) -> bool:
    """A bare 4-digit integer in [1900, 2100] is a fiscal-year/date marker (FY2022,
    "in 2023"), not a financial magnitude -- mirrors scorers._extract_numbers so the
    magnitude count here matches how the scorer reads numbers.
    """
    if any(c in raw for c in "$%,."):
        return False
    try:
        val = float(raw)
    except ValueError:
        return False
    return 1900.0 <= val <= 2100.0


def _magnitude_tokens(s: str) -> list[str]:
    """Financial-magnitude tokens in `s`, excluding fiscal-year/date markers."""
    return [m.group() for m in _NUM_TOKEN_RE.finditer(s) if not _is_year_like(m.group())]


def _parse_prose_gold(gold_answer: str) -> tuple[float | None, str]:
    """Lift the primary magnitude+unit out of a prose gold, but ONLY when the answer
    reduces to exactly ONE magnitude and no cue marks that number as a supporting
    detail. Multiple magnitudes (ranges/comparisons), zero magnitudes (pure prose),
    or a cue-guarded lone number all stay text/null (task step 2, conservative).
    """
    s = gold_answer.strip()
    lower = s.lower()

    # Multiple / no magnitudes -> not a single-number answer.
    tokens = _magnitude_tokens(s)
    if len(tokens) != 1:
        return None, "text"

    # The lone number is a supporting stat behind a categorical/comparison answer.
    if any(cue in lower for cue in _QUALITATIVE_CUES):
        return None, "text"

    # "X percentage point(s)" -> percent (e.g. pepsico_04 guidance raise).
    m = _PCT_POINT_RE.search(s)
    if m:
        return float(m.group(1)), "percent"

    # Bare embedded "X%" (e.g. "accounted for 16%") is left as text: among the iter1
    # golds every such answer is a yes-no/entity judgment, not a "the value is X%" answer.
    if tokens[0].endswith("%"):
        return None, "text"

    # Dollar magnitudes, normalized to USD millions to match the agent's report scale.
    m = _DOLLAR_BILLION_RE.search(s)
    if m:
        return float(m.group(1).replace(",", "")) * 1000.0, "USD millions"
    m = _DOLLAR_MILLION_RE.search(s)
    if m:
        return float(m.group(1).replace(",", "")), "USD millions"
    m = _DOLLAR_ABS_RE.search(s)
    if m:
        absolute = float(m.group(1).replace(",", ""))
        # Only treat a scale-word-less "$X" as absolute dollars when it is clearly a
        # raw figure (>= $1,000,000); smaller bare "$X" would collide with the existing
        # "$NNNN.00 == NNNN millions" convention, so leave those alone (none in subset).
        if absolute >= 1_000_000:
            return absolute / 1_000_000.0, "USD millions"
        return None, "text"

    # Plain decimal ("quick ratio is 1.57", "sold inventory 2.7 times") -> ratio.
    if _DECIMAL_TOKEN_RE.match(tokens[0]):
        return float(tokens[0]), "ratio"

    return None, "text"


def parse_gold_value(gold_answer: str) -> tuple[float | None, str]:
    """Parse `gold_value`/`gold_unit` from a gold answer (AMBIGUITIES.md section 5).

    Fast path: a whole-string clean numeric literal ("$1577.00", "4.2%", "0.83") --
    the original convention, kept byte-identical so the already-parsed golds are
    unchanged. Otherwise fall through to conservative prose extraction (IMP-2): a
    single primary magnitude with prose around it is lifted to gold_value/gold_unit;
    everything else stays text/null and is scored by string match (spec section 20).
    """
    s = gold_answer.strip()
    if _NUMERIC_RE.match(s):
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

    return _parse_prose_gold(gold_answer)


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


def _load_gold_annotations() -> dict[str, dict[str, Any]]:
    """IMP3-1: load the question_id -> {gold_polarity|gold_canonical, evidence} sidecar.

    Optional: if the file is absent the merge is a no-op (all items default to None),
    so the builder still runs standalone. The `evidence` field is documentation-only
    and is NOT emitted into subset.json -- only gold_polarity / gold_canonical are.
    """
    if not GOLD_ANNOTATIONS_PATH.exists():
        return {}
    doc = json.loads(GOLD_ANNOTATIONS_PATH.read_text())
    return doc.get("annotations", {})


def _annotation_is_confirmed(annotation: dict[str, Any]) -> bool:
    """Whether an optional sidecar annotation is safe to merge.

    Most sidecar entries are curated annotations. Entries explicitly flagged for
    human review are pending until the sidecar carries human_reviewed=true.
    """
    if not annotation:
        return False
    if annotation.get("flagged_for_human_review"):
        return bool(annotation.get("human_reviewed"))
    return True


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


def _choose_top_n_qualifying(ranked: list[dict[str, Any]], n: int) -> tuple[list[dict[str, Any]], int]:
    """Expanded-subset mode (--n-companies): take the top `n` ranked companies
    that individually clear both floors, skipping non-qualifying companies
    instead of rejecting the whole tier. Same ranking, same floors, same
    per-company selection as the default path -- so the default 4-company
    subset is always a prefix (the original 28 items keep their item_ids)."""
    qualifying = [
        c
        for c in ranked
        if c["baseline_failures"] >= MIN_BASELINE_FAILURE_PER_COMPANY and c["n_eligible"] >= MIN_ELIGIBLE_PER_COMPANY
    ]
    chosen = qualifying[:n]
    return chosen, len(chosen)


def _choose_tier(ranked: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Walk FALLBACK_TIERS, accept the first tier whose candidate companies all
    clear the baseline-failure and eligible-count floors. Returns (companies, tier_n).
    """
    for n in FALLBACK_TIERS:
        candidates = ranked[:n]
        if len(candidates) < n:
            continue
        if all(
            c["baseline_failures"] >= MIN_BASELINE_FAILURE_PER_COMPANY and c["n_eligible"] >= MIN_ELIGIBLE_PER_COMPANY
            for c in candidates
        ):
            return candidates, n
    # Nothing clears the floors at any tier -- take whatever is available (best effort).
    return ranked[: FALLBACK_TIERS[-1]], FALLBACK_TIERS[-1]


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


def build_subset(n_companies: int | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
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
    gold_annotations = _load_gold_annotations()  # IMP3-1 sidecar, by question_id

    eligible = [r for r in verified if r["include"] and not r["disagreement"]]
    for r in eligible:
        raw = raw_by_qid[r["question_id"]]
        r["_clean_evidence"] = _evidence_clean(raw["evidence"], doc_by_id)

    ranked = _rank_companies(eligible)
    if n_companies is not None:
        chosen_companies, tier_n = _choose_top_n_qualifying(ranked, n_companies)
        selection_mode = "expanded"
    else:
        chosen_companies, tier_n = _choose_tier(ranked)
        selection_mode = "fallback"

    items: list[dict[str, Any]] = []
    company_report = []
    for company_stat in chosen_companies:
        company = company_stat["company"]
        selected_rows = _select_company_rows(company_stat["rows"], doc_by_id)
        # Deterministic within-company order: bucket priority (A, C, B), then question_id.
        order = {"A_multi_input": 0, "C_lookup": 1, "B_judgment": 2}
        selected_rows.sort(key=lambda r: (order[r["bucket_d3"]], r["question_id"]))

        slug = slugify(company)
        bucket_counts = {"A_multi_input": 0, "B_judgment": 0, "C_lookup": 0}
        baseline_failures = 0
        for i, row in enumerate(selected_rows, start=1):
            raw = raw_by_qid[row["question_id"]]
            doc_type_raw = raw["doc_type"]
            doc_type = DOC_TYPE_MAP.get(doc_type_raw, "other")
            filing_period = derive_filing_period(raw["doc_name"], doc_type_raw, raw["doc_period"])

            gold_value, gold_unit = parse_gold_value(row["gold_answer"])
            # IMP3-1: pull the canonical annotation for this question (if any). Only the
            # gold_polarity / gold_canonical fields flow into the item; `evidence` is
            # documentation kept in the sidecar. Unannotated items stay None (schema default).
            annotation = gold_annotations.get(row["question_id"], {})
            annotation_confirmed = _annotation_is_confirmed(annotation)
            applied_annotation = annotation if annotation_confirmed else {}
            gold_polarity = applied_annotation.get("gold_polarity")
            gold_canonical = applied_annotation.get("gold_canonical")
            # IMP4-1: a few golds carry two magnitudes in prose (e.g. verizon_05's
            # "$1097 million ... $862 million"), so parse_gold_value leaves them
            # text/null even though the gold's own expected_formula intends the sum.
            # The sidecar may supply an explicit human-reviewed gold_value/gold_unit;
            # apply it additively, defaulting to the parsed value so an annotation
            # WITHOUT these keys never clobbers an already-parsed number (e.g. amd_01
            # keeps gold_value=1.57 while gaining only gold_polarity).
            gold_value = applied_annotation.get("gold_value", gold_value)
            gold_unit = applied_annotation.get("gold_unit", gold_unit)
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
                "gold_polarity": gold_polarity,      # IMP3-1 (None unless annotated)
                "gold_canonical": gold_canonical,    # IMP3-1 (None unless annotated)
                "gold_evidence": gold_evidence,
                "bucket": bucket,
                "expected_formula": expected_formula,
                "expected_inputs": row["expected_inputs"],
                "predicted_baseline_failure": row["predicted_baseline_failure"],
                "answer_verifiable_from_evidence": True,   # guaranteed by the `include` filter
                "unit_or_period_ambiguity": False,          # guaranteed by the `include` filter
                "demo_candidate": demo_candidate,
                "human_reviewed": annotation_confirmed,
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

    meta = {
        "tier_n_companies": tier_n,
        "total_items": len(items),
        "companies": company_report,
        "bucket_totals": {
            b: sum(c["bucket_counts"][b] for c in company_report) for b in BUCKETS
        },
        "selection_mode": selection_mode,
    }
    return items, meta


def _bucket_target_labels(meta: dict[str, Any]) -> dict[str, str]:
    if meta.get("selection_mode") == "expanded":
        return {
            bucket: f"expanded target ~{meta['tier_n_companies'] * TARGET_COMPOSITION[bucket]}"
            for bucket in BUCKETS
        }
    return {
        "A_multi_input": "fallback target ~16",
        "B_judgment": "fallback target ~8",
        "C_lookup": "fallback target ~8",
    }


def _write_report(meta: dict[str, Any]) -> None:
    if meta.get("selection_mode") == "expanded":
        summary_suffix = f"expanded top-company mode: {meta['tier_n_companies']} qualifying companies x <=8 each"
        target_context = (
            f"{meta['tier_n_companies']} companies x per-company 4:2:2 target "
            f"({meta['tier_n_companies'] * TARGET_COMPOSITION['A_multi_input']}/"
            f"{meta['tier_n_companies'] * TARGET_COMPOSITION['B_judgment']}/"
            f"{meta['tier_n_companies'] * TARGET_COMPOSITION['C_lookup']})"
        )
        policy_text = (
            f"Expanded mode selected the top {meta['tier_n_companies']} qualifying companies "
            f"(each with >= {MIN_BASELINE_FAILURE_PER_COMPANY} baseline-failure questions and "
            f">= {MIN_ELIGIBLE_PER_COMPANY} eligible questions) using the same company ranking "
            "and per-company 4:2:2 (A:C:B) allocation as fallback mode."
        )
    else:
        summary_suffix = f"fallback tier: {meta['tier_n_companies']} companies x <=8 each"
        target_context = "fallback 4-company target (16/8/8)"
        policy_text = (
            "The 4-companies-x-8 tier is accepted once every candidate clears "
            f">= {MIN_BASELINE_FAILURE_PER_COMPANY} baseline-failure questions and "
            f">= {MIN_ELIGIBLE_PER_COMPANY} eligible questions."
        )
    bucket_labels = _bucket_target_labels(meta)
    lines = [
        "# D5 subset selection report",
        "",
        f"Selected **{meta['total_items']}** questions across "
        f"**{meta['tier_n_companies']}** companies ({summary_suffix}).",
        "",
        "## Bucket composition",
        "",
        f"- A_multi_input: {meta['bucket_totals']['A_multi_input']} ({bucket_labels['A_multi_input']})",
        f"- B_judgment: {meta['bucket_totals']['B_judgment']} ({bucket_labels['B_judgment']})",
        f"- C_lookup: {meta['bucket_totals']['C_lookup']} ({bucket_labels['C_lookup']})",
        "",
        "C_lookup questions are scarce in the D3/D4-verified pool for the "
        "highest-eligible-count companies on this corpus, so the achieved "
        f"composition undershoots the {target_context} split on A and C in favor of "
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
        f"(eligible count desc, baseline-failure count desc, name asc). {policy_text} "
        "Per-company selection targets a 4:2:2 (A:C:B) split of the 8-slot cap, falls back to filling "
        "remaining slots in A, then C, then B order when a bucket is short, and "
        "drops down from B, then C, then A when a company has more than 8 "
        "eligible questions.",
        "",
    ]
    REPORT_PATH.write_text("\n".join(lines) + "\n")


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="D5 stratified subset selection (spec section 6).")
    ap.add_argument(
        "--n-companies",
        type=int,
        default=None,
        help="Expanded mode: select the top N qualifying companies (>=2 baseline-failure, "
        ">=4 eligible questions each) instead of walking the spec's 4/3/2 fallback tiers. "
        "Default: original tier walk (4 companies).",
    )
    args = ap.parse_args()

    items, meta = build_subset(n_companies=args.n_companies)

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

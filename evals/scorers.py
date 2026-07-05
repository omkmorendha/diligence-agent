"""Deterministic scorers (spec section 20, Step 3).

Pure functions over memo.json + trace.jsonl + subset.json gold fields. No LLM.
These are the TDD foundation -- every scorer is tested against evals/fixtures/.

Metrics (spec section 20):
    * answer accuracy       numeric: default +/-1% relative tolerance (overridable);
                            string: normalized exact match (lowercase, strip punct,
                            collapse whitespace, basic unit normalization).
    * abstention scoring    correct only when the item is truly unanswerable /
                            evidence-insufficient; otherwise incorrect-but-calibrated.
    * citation precision    doc_id match + cited page within +/-1 page slack.
    * citation provenance   every cited chunk_id appeared in a prior retrieval event.
    * arithmetic integrity  every material financial number traces to a calculate
                            result or a cited quote span (ignore page numbers, years,
                            item ids, confidence counts, dates, run summary counts).
    * trace shape           A_multi_input: >=2 retrievals, >=1 calculate, >=2 grounded
                            inputs; C_lookup: short path (<=2 retrievals, soft).

All scorers return one of "pass" / "fail" / None. None means "not applicable" --
e.g. citation metrics on an abstained item, or citation_provenance/precision when
there is nothing to check against gold.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.schemas import MemoItem, SubsetItem, TraceEvent  # noqa: E402

_PUNCT = re.compile(r"[^\w\s.%-]")
_NUM_RE = re.compile(r"[-+]?\$?\d[\d,]*(?:\.\d+)?%?")

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def normalize_string(s: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace (spec section 20)."""
    s = s.lower().strip()
    s = _PUNCT.sub("", s)
    return re.sub(r"\s+", " ", s)


def numeric_within_tolerance(
    predicted: float, gold: float, relative: float | None = 0.01, absolute: float | None = None
) -> bool:
    """True if predicted matches gold within relative and/or absolute tolerance."""
    if absolute is not None and abs(predicted - gold) <= absolute:
        return True
    if relative is not None:
        denom = abs(gold) if gold != 0 else 1.0
        return abs(predicted - gold) / denom <= relative
    return predicted == gold


def _extract_numbers(text: str) -> list[float]:
    """Pull candidate numeric literals out of a quote span.

    Ignores bare 4-digit integers that look like fiscal years/dates (spec section
    20: "ignore ... fiscal years ... dates") unless they carry a currency/percent/
    decimal marker that makes them look like an actual financial figure.
    """
    values: list[float] = []
    for m in _NUM_RE.finditer(text):
        raw = m.group()
        has_dollar = "$" in raw
        has_percent = "%" in raw
        has_comma = "," in raw
        cleaned = raw.replace("$", "").replace(",", "").replace("%", "")
        if not cleaned:
            continue
        try:
            val = float(cleaned)
        except ValueError:
            continue
        is_year_like = (
            not has_dollar
            and not has_percent
            and not has_comma
            and "." not in cleaned
            and 1900 <= val <= 2100
        )
        if is_year_like:
            continue
        values.append(val)
    return values


def _retrieval_chunk_ids(item_id: str, trace_events: list[TraceEvent]) -> set[str]:
    """Every chunk_id surfaced by a `retrieval` event for this item, anywhere in the trace.

    Used by citation_provenance and (transitively) arithmetic_integrity: a citation
    only "counts" as grounding a number if the exact chunk it points at was actually
    returned by search, as opposed to invented from memory or gold leakage.
    """
    chunk_ids: set[str] = set()
    for e in trace_events:
        if e.type == "retrieval" and e.item_id == item_id:
            for c in e.payload.get("chunks", []):
                chunk_id = c.get("chunk_id")
                if chunk_id:
                    chunk_ids.add(chunk_id)
    return chunk_ids


def _calculate_values(item_id: str, trace_events: list[TraceEvent]) -> list[float]:
    """Every numeric result produced by a `calculate` tool_result for this item."""
    values: list[float] = []
    for e in trace_events:
        if e.type == "tool_result" and e.item_id == item_id:
            payload = e.payload
            if payload.get("tool") == "calculate":
                out = payload.get("output", {})
                if "value" in out and out["value"] is not None:
                    values.append(float(out["value"]))
    return values


# --- IMP3-1 (results/iterations/iter2/improvement_plan.json): canonical matching ----
# The exact-string fallback (normalize_string(answer)==normalize_string(gold_answer))
# below can only pass when the memo answer is a byte-for-byte-normalized copy of the
# prose gold, which no free-text answer ever is -- so 27/34 iter2 "failures" were
# actually CORRECT yes/no and entity answers blocked purely by string form. These two
# helpers back the polarity and canonical branches that fire ONLY when the item carries
# a human-reviewed gold_polarity / gold_canonical annotation (data/gold_annotations.json).
# They ADD pass opportunities gated on those fields; the numeric and exact-string
# branches are unchanged, so no existing pass ever becomes a fail.

# Tiny, GENERIC financial-domain synonym map (phrase -> canonical token). Deliberately
# NOT item-specific: it only collapses the standard cash-flow-statement section names
# to their {operating,investing,financing} choice tokens so "operations" / "operating
# activities" all read as the same canonical answer. Never add answer-specific phrases.
_CANON_SYNONYMS = {
    "operating activities": "operating",
    "operations": "operating",
    "investing activities": "investing",
    "financing activities": "financing",
}
# Generic English stopwords so "Developed Rest of the World" == "Developed Rest of
# World" -- articles/conjunctions carry no entity signal.
_CANON_STOPWORDS = {"the", "a", "an", "of", "and"}


def _canonical_token_set(s: str) -> set[str]:
    """Normalize `s` and reduce it to a set of content tokens for canonical matching.

    Applies normalize_string (lowercase/strip-punct/collapse-ws), rewrites the generic
    cash-flow synonyms as whole phrases, then drops stopwords. Set membership (not
    ordered substring) is what the canonical branch tests against, so token order and
    filler words don't matter but every content token must be present.
    """
    norm = normalize_string(s)
    for phrase, repl in _CANON_SYNONYMS.items():
        norm = re.sub(rf"\b{re.escape(phrase)}\b", repl, norm)
    # normalize_string deliberately PRESERVES '.'/'%' (they carry numeric meaning for
    # the numeric scorer), so an entity ending a sentence tokenizes as "therachon." /
    # "bonds." and would never match the punctuation-free gold token. Strip edge
    # '.'/'%' here so canonical matching is genuinely punct-insensitive as documented.
    return {tok for t in norm.split() if (tok := t.strip(".%")) and tok not in _CANON_STOPWORDS}


def _leading_polarity(answer: str) -> Optional[str]:
    """The agent answer's leading polarity token: the FIRST 'yes'/'no' in reading order.

    Case/punct-insensitive. normalize_string preserves '.'/'%' (they matter to the
    numeric scorer), so a leading "No." / "Yes." tokenizes with a trailing period;
    strip edge '.'/'%' per token so those honest polarity leads still match.
    """
    for tok in normalize_string(answer).split():
        stripped = tok.strip(".%")
        if stripped in ("yes", "no"):
            return stripped
    return None


def _canonical_match(answer: str, gold_canonical) -> bool:
    """True iff every gold canonical entity's content tokens are a subset of the answer's.

    A str gold is one entity (all its tokens must appear); a list gold is an entity set
    (EVERY listed entity must appear, e.g. "three companies acquired"). Token-set
    subset, not loose substring: "corporate bonds" needs both tokens present, so a
    stray "bonds" alone will not false-pass.
    """
    ans_tokens = _canonical_token_set(answer)
    golds = gold_canonical if isinstance(gold_canonical, list) else [gold_canonical]
    for g in golds:
        g_tokens = _canonical_token_set(g)
        if not g_tokens or not g_tokens.issubset(ans_tokens):
            return False
    return True


def answer_accuracy(memo_item: MemoItem, subset_item: SubsetItem) -> Optional[str]:
    """Numeric: +/-tolerance relative/absolute match. String: normalized exact match.

    IMP3-1 adds two branches ABOVE the exact-string fallback, each gated on a
    human-reviewed annotation being present: a polarity branch (leading yes/no token)
    and a canonical entity/choice branch. The numeric branch and the exact-string
    fallback are byte-identical to before.
    """
    if memo_item.status == "abstained":
        return None

    if subset_item.gold_value is not None and memo_item.value is not None:
        tol = subset_item.tolerance
        ok = numeric_within_tolerance(memo_item.value, subset_item.gold_value, tol.relative, tol.absolute)
        return "pass" if ok else "fail"

    # IMP3-1 polarity branch: for annotated yes/no items, polarity IS the answer.
    if subset_item.gold_polarity is not None:
        return "pass" if _leading_polarity(memo_item.answer) == subset_item.gold_polarity else "fail"

    # IMP3-1 canonical branch: for annotated entity/choice items, match the canonical
    # entity (or the full entity set) at the token level with the tiny synonym map.
    if subset_item.gold_canonical is not None:
        return "pass" if _canonical_match(memo_item.answer, subset_item.gold_canonical) else "fail"

    ok = normalize_string(memo_item.answer) == normalize_string(subset_item.gold_answer)
    return "pass" if ok else "fail"


def citation_precision(memo_item: MemoItem, subset_item: SubsetItem, page_slack: int = 1) -> Optional[str]:
    """doc_id match + cited page within +/-page_slack of a gold evidence document.

    A material claim (any answered item) with zero citations automatically fails --
    there is nothing to check the claim against.
    """
    if memo_item.status == "abstained":
        return None

    if not memo_item.citations:
        return "fail"
    if not subset_item.gold_evidence:
        return "fail"

    for citation in memo_item.citations:
        matched = any(
            citation.doc_id == gold.doc_id and abs(citation.pdf_page - gold.pdf_page) <= page_slack
            for gold in subset_item.gold_evidence
        )
        if not matched:
            return "fail"
    return "pass"


def citation_provenance(memo_item: MemoItem, item_id: str, trace_events: list[TraceEvent]) -> Optional[str]:
    """Every cited chunk_id must appear in a prior `retrieval` event in the same trace."""
    if memo_item.status == "abstained":
        return None
    if not memo_item.citations:
        return None

    retrieved = _retrieval_chunk_ids(item_id, trace_events)
    for citation in memo_item.citations:
        if citation.chunk_id not in retrieved:
            return "fail"
    return "pass"


def arithmetic_integrity(
    memo_item: MemoItem, item_id: str, trace_events: list[TraceEvent], subset_item: SubsetItem
) -> Optional[str]:
    """The claimed numeric value must trace to a `calculate` result or a cited quote span.

    A cited quote only counts as grounding if its chunk_id was itself actually
    retrieved (i.e. citation_provenance would pass for it) -- a citation invented
    from memory does not get to double as arithmetic support.
    """
    if memo_item.status == "abstained":
        return None
    if memo_item.value is None:
        return "pass"

    grounded_values = list(_calculate_values(item_id, trace_events))

    retrieved = _retrieval_chunk_ids(item_id, trace_events)
    for citation in memo_item.citations:
        if citation.chunk_id in retrieved:
            grounded_values.extend(_extract_numbers(citation.quote))

    tol = subset_item.tolerance
    for gv in grounded_values:
        if numeric_within_tolerance(memo_item.value, gv, tol.relative, tol.absolute):
            return "pass"
    return "fail"


def trace_shape(memo_item: MemoItem, subset_item: SubsetItem, item_id: str, trace_events: list[TraceEvent]) -> str:
    """Structural checks (spec section 20): plan before retrieval, exactly one final
    answer/abstention, and bucket-specific thresholds for A_multi_input.

    C_lookup over-retrieval is explicitly a soft/inefficiency signal, not a hard
    failure, so it is not checked here.
    """
    item_events = [e for e in trace_events if e.item_id == item_id]
    plans = [e for e in trace_events if e.type == "plan"]
    retrievals = [e for e in item_events if e.type == "retrieval"]
    calculates = [
        e for e in item_events if e.type == "tool_call" and e.payload.get("tool") == "calculate"
    ]
    answers = [e for e in item_events if e.type == "item_answer"]

    if len(answers) != 1:
        return "fail"
    answer_event = answers[0]
    if answer_event.payload.get("status") not in ("answered", "abstained"):
        return "fail"

    if not plans:
        return "fail"
    first_plan_seq = min(e.seq for e in plans)
    if retrievals:
        first_retrieval_seq = min(e.seq for e in retrievals)
        if first_plan_seq >= first_retrieval_seq:
            return "fail"

    if subset_item.bucket == "A_multi_input":
        if len(retrievals) < 2:
            return "fail"
        if len(calculates) < 1:
            return "fail"
        grounded_inputs = answer_event.payload.get("confidence", {}).get("grounded_inputs", 0)
        if grounded_inputs < 2:
            return "fail"

    return "pass"


def abstention(memo_item: MemoItem, subset_item: SubsetItem) -> Optional[str]:
    """Correct only when the item is truly unanswerable / evidence-insufficient.

    Returns None for any item that was actually answered -- abstention scoring only
    applies to items the agent chose to abstain on.
    """
    if memo_item.status != "abstained":
        return None
    return "correct" if not subset_item.answer_verifiable_from_evidence else "incorrect_but_calibrated"


def score_run(subset_item: SubsetItem, trace_events: list[TraceEvent], memo_item: MemoItem) -> dict[str, Optional[str]]:
    """Score a single (subset_item, trace, memo_item) triple across all six metrics."""
    item_id = subset_item.item_id
    return {
        "answer_accuracy": answer_accuracy(memo_item, subset_item),
        "citation_precision": citation_precision(memo_item, subset_item),
        "citation_provenance": citation_provenance(memo_item, item_id, trace_events),
        "arithmetic_integrity": arithmetic_integrity(memo_item, item_id, trace_events, subset_item),
        "abstention": abstention(memo_item, subset_item),
        "trace_shape": trace_shape(memo_item, subset_item, item_id, trace_events),
    }


def _load_trace(path: Path) -> list[TraceEvent]:
    events: list[TraceEvent] = []
    for line in path.read_text().splitlines():
        if line.strip():
            events.append(TraceEvent.model_validate_json(line))
    return events


def score_fixture(fixture_dir: Path) -> dict:
    """Score one fixture directory and diff the result against its expected.json."""
    name = fixture_dir.name
    subset_item = SubsetItem.model_validate_json((fixture_dir / "subset_item.json").read_text())
    trace_events = _load_trace(fixture_dir / "trace.jsonl")
    memo_item_raw = json.loads((fixture_dir / "memo.json").read_text())["items"][0]
    memo_item = MemoItem.model_validate(memo_item_raw)
    expected = json.loads((fixture_dir / "expected.json").read_text())

    actual = score_run(subset_item, trace_events, memo_item)
    expected_scores: dict[str, Optional[str]] = expected["expected_scores"]

    mismatches = [
        f"{metric}: expected {expected_scores.get(metric)!r}, got {actual.get(metric)!r}"
        for metric in expected_scores
        if actual.get(metric) != expected_scores.get(metric)
    ]

    return {
        "fixture": name,
        "scorer_under_test": expected.get("scorer_under_test"),
        "actual": actual,
        "expected": expected_scores,
        "ok": not mismatches,
        "mismatches": mismatches,
    }


def score_fixtures(fixtures_dir: Path = FIXTURES_DIR) -> list[dict]:
    """Score every fixture directory under fixtures_dir. Deterministic, no LLM."""
    results = []
    for d in sorted(fixtures_dir.iterdir()):
        if not d.is_dir():
            continue
        if not (d / "expected.json").exists():
            continue
        results.append(score_fixture(d))
    return results

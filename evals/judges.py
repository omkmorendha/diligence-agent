"""LLM judges — Tier 2 eval (spec section 21).

Built ONLY after the deterministic eval works (evals/scorers.py, Step 3). Same LLM
endpoint as the agent for v0 (disclose if asked -- see backend/app/llm.py, the
single adapter). Mitigations: narrow rubrics, one criterion per call, structured
output, and a corrupted-memo calibration gate.

Criteria (5-point rubrics with concrete anchors + contrastive exemplars, IMP-5):
    groundedness   1=unsupported/hallucinated .. 3=number present but WRONG period/
                   entity/unit .. 5=every claim supported AND period/entity matches the
                   question. The judge must first enumerate each material claim ->
                   supporting quote -> match/mismatch, then score the lowest claim.
    actionability  1=not actionable .. 2=fluent-but-hollow (incl. budget-exhaustion
                   abstentions with no missing-evidence/next-step) .. 5=self-contained
                   with its numeric basis (or an abstention naming both).

Judge sees: the memo item (question/answer/value), its cited passages (each tagged with
its filing period so period-mismatch is detectable), and tool outputs (calculate
results). Judge does NOT see: the agent scratchpad, hidden reasoning, or the gold answer.

Why the redesign (iter1 analyst finding): groundedness_judge and actionability_judge
were both a flat 5.0 with ZERO variance across all 61 items in iter1 AND baseline. The
verbatim-citation gate guarantees a claimed number appears in its citation, so the old
"is it supported?" axis could never discriminate; and actionability graded only fluency.
The redesign restores signal by scoring period/entity relevance (not just presence) and
by failing hollow abstentions.

Calibration gate (spec section 21), three fixture classes with distinct bounds:

    evals/fixtures/corrupted_swapped_citation/       (citation swapped to an unrelated
                                                       passage) -> assert groundedness<=2
    evals/fixtures/corrupted_wrong_number/           (value post-hoc injected) -> <=2
    evals/judge_fixtures/period_mismatch_wrong_year/ (well-cited number but WRONG fiscal
                                                       year -- the operating-region
                                                       hard-negative) -> assert <=3
    evals/judge_fixtures/long_multiclaim_grounded/   (long, correct, multi-claim answer
                                                       used as a truncation guard)
                                                     -> assert groundedness>=3

Run judges against all fixture groups, assert they satisfy their ceiling or floor, and
persist results/corrupted_memo_judge.json. If calibration fails, judge scores must not
be shown as headline metrics (see evals/run.py's --judges integration). The judge-only
fixtures live under evals/judge_fixtures/ (NOT evals/fixtures/) so the deterministic
score_fixtures() gate cannot discover them and the 8/8 count is untouched.

Usage:
    uv run --project backend evals/judges.py --calibrate
    uv run --project backend evals/judges.py --fixtures
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "dataset_builder"))

from app import config  # noqa: E402
from app.schemas import MemoItem, TraceEvent  # noqa: E402

from llm_json import call_json_with_retry  # noqa: E402

from scorers import FIXTURES_DIR  # noqa: E402

# Three classes of calibration fixture, each with its own groundedness bound:
#
#   (1) Egregious corruption (swapped/injected) -- the citation is topically unrelated
#       to the claim it backs, so a working judge must floor it. Threshold <= 2. These
#       live in evals/fixtures/ but carry no expected.json, so the deterministic
#       score_fixtures() gate skips them (see scorers.py:312) -- they never touch 8/8.
#
#   (2) Hard-negative "operating region" fixture (IMP-5): a well-cited answer whose
#       number genuinely appears in the cited passage but for the WRONG fiscal year.
#       This is the class the old 1/3/5 rubric scored 5 and the analyst flagged as the
#       real blind spot. A working judge must score it <= 3 (period-mismatch anchor).
#       It lives in evals/judge_fixtures/ (NOT evals/fixtures/) so it can never be
#       discovered by score_fixtures() and can never perturb the deterministic count.
#   (3) Long, correct multi-claim fixture (IMP3-3): the judge must return a valid
#       groundedness score at or above LONG_CALIBRATION_GROUNDEDNESS_MIN. This is a
#       floor-based truncation guard, not a corrupted-answer ceiling.
CALIBRATION_FIXTURES = ("corrupted_swapped_citation", "corrupted_wrong_number")
CALIBRATION_GROUNDEDNESS_MAX = 2  # spec: "assert judges score it low" (scale 1-5, 3=partial)

JUDGE_FIXTURES_DIR = ROOT / "evals" / "judge_fixtures"
HARD_NEGATIVE_FIXTURES = ("period_mismatch_wrong_year",)
HARD_NEGATIVE_GROUNDEDNESS_MAX = 3  # period/entity mismatch anchor: number present, wrong period

# (3) Truncation-regression guard (IMP3-3): a LONG, multi-claim, correct-and-grounded memo
#     item. Its gate assertion is a FLOOR, not a ceiling -- the only requirement is that the
#     judge RETURNS a valid score (>= 3) instead of erroring. At the old 4000 max_tokens the
#     hidden reasoning channel exhausted the budget on this exact shape and returned
#     content='' (a judge_error / None score), so this fixture would have FAILED the floor
#     and caught the truncation regression. It lives under evals/judge_fixtures/ so the
#     deterministic score_fixtures() gate never discovers it (8/8 count untouched).
LONG_CALIBRATION_FIXTURES = ("long_multiclaim_grounded",)
LONG_CALIBRATION_GROUNDEDNESS_MIN = 3

RESULTS_PATH = config.RESULTS_DIR / "corrupted_memo_judge.json"

GROUNDEDNESS_SYSTEM_PROMPT = """You are a strict, narrow-scope grader for a single \
financial-diligence memo answer. Judge ONLY groundedness: is every material claim in \
the answer supported by a cited passage or tool output, AND does that evidence match \
the fiscal period, entity, segment, and line-item that the QUESTION asks about?

WHY period/entity matching matters: the answer already passed a verbatim-citation gate, \
so a number will always appear somewhere in its citation. That is NOT enough. The real \
failure mode is citing a REAL number from the WRONG place -- FY2022 revenue to answer an \
FY2023 question, or a segment total to answer a consolidated question. You must catch \
that. Two independent failure modes each lower the score:
  (a) support failure  -- a claim has no matching quote/tool output, or contradicts it.
  (b) relevance failure -- the number/fact IS present in a cited passage, but the \
      passage is for a DIFFERENT fiscal period, entity, segment, unit, or line-item \
      than the question asks about.

Do NOT use outside knowledge of the true answer, do NOT judge writing style, and do NOT \
judge completeness -- only support and period/entity relevance of what IS claimed.

PROCEDURE -- do this BEFORE choosing a score:
1. List each MATERIAL claim in the answer (every number, entity, yes/no assertion).
2. For each claim, quote the specific supporting text from the CITED PASSAGES or TOOL \
   OUTPUTS (each cited passage is tagged with its filing period), and mark it \
   match / period_mismatch / unsupported. A "period_mismatch" means the value is found \
   but its period/entity/segment/unit does not match the question.
3. The overall score is driven by the LOWEST-scoring material claim.

Scale (concrete anchors):
5 = every material claim is supported by a cited quote or tool output AND the period, \
    entity, segment, unit, and line-item in the evidence all match what the question asks.
4 = every claim is supported and relevant, but one secondary figure is only indirectly \
    supported (e.g. requires an obvious one-step read of a cited table).
3 = the claimed number/fact appears in a cited passage, BUT its period, entity, segment, \
    unit, or line-item does NOT match the question (a period/entity/unit mismatch), OR \
    exactly one material claim is unsupported while the rest are grounded.
2 = most material claims lack matching support, or a citation is topically unrelated to \
    the claim it is supposed to back.
1 = the key numeric or factual claim has NO support in the cited passages/tool outputs \
    or contradicts them; or the item abstains ("cannot answer") even though the cited \
    evidence plainly contains what was asked (abstention without cause).

CONTRASTIVE EXEMPLARS:
Example A -> score 5 (clean match):
  QUESTION: What was Acme's FY2023 total revenue (USD millions)?
  ANSWER: FY2023 total revenue was $1,250 million.
  CITED [FY2023 | Acme FY2023 10-K]: "Total net revenue for fiscal 2023 was $1,250 million."
  Reasoning: claim "$1,250M FY2023 revenue" -> quote states $1,250M for fiscal 2023 -> \
  value AND period match -> match. Lowest claim = match. SCORE 5.
Example B -> score 3 (well-cited but WRONG fiscal year):
  QUESTION: What was Acme's FY2023 total revenue (USD millions)?
  ANSWER: FY2023 total revenue was $1,100 million.
  CITED [FY2022 | Acme FY2022 10-K]: "Total net revenue for fiscal 2022 was $1,100 million."
  Reasoning: claim "$1,100M FY2023 revenue" -> the value $1,100M is genuinely in the quote, \
  BUT the quote is for fiscal 2022 and the question asks FY2023 -> period_mismatch. \
  Lowest claim = period_mismatch. SCORE 3 (do NOT reward the verbatim match with a 5).

Respond with EXACTLY one JSON object, no prose, no markdown code fence:
{
  "claims": [
    {"claim": "<material claim>", "support": "<quoted supporting text, or 'none'>", \
"match": "match" | "period_mismatch" | "unsupported"}
  ],
  "score": 1 | 2 | 3 | 4 | 5,
  "justification": "<one or two sentence rationale naming the lowest-scoring claim and why>"
}"""

ACTIONABILITY_SYSTEM_PROMPT = """You are a strict, narrow-scope grader for a single \
financial-diligence memo answer. Judge ONLY actionability: could a human analyst act on \
this answer AS WRITTEN -- does it state its own numeric basis, and if it abstains, does \
it name what is missing AND the next step to resolve it?

Do not judge whether the number is correct or whether citations are verbatim. Judge \
whether the answer TEXT is self-contained and usable. Fluent prose is NOT the same as \
actionable content -- this model always writes fluently, so do not reward polish.

HARD RULE for abstentions / non-answers: an abstention is actionable ONLY if it names \
(a) the SPECIFIC missing evidence (which document / note / line-item is absent) AND \
(b) the SPECIFIC next retrieval or computation that would resolve it. A generic \
"reached the tool-call budget" or "could not find a grounded answer" abstention that \
names neither MUST score 2 -- it is a recoverable failure dressed as a conclusion. An \
abstention that names the missing evidence but no next step is a 3, not a 5.

Also penalize ANSWERED items that assert a conclusion (a ratio, a yes/no, a "largest \
segment") without stating the numeric basis for it in the answer text itself.

Scale (concrete anchors):
5 = clear, self-contained, states its numeric basis; an analyst acts immediately. An \
    abstention scores 5 ONLY if it names the exact missing evidence AND the exact next step.
4 = actionable, but one caveat or supporting figure is left implicit.
3 = understandable but thin -- a conclusion with weak/partial numeric basis, or an \
    abstention that names the missing evidence but not the next step.
2 = fluent but hollow: an answer or abstention that reads fine yet names no basis, no \
    specific missing evidence, and no next step (the budget-exhaustion template lands here).
1 = vague, confusing, or gives no usable information at all.

CONTRASTIVE EXEMPLARS:
Example A -> score 5 (actionable abstention):
  ANSWER: Cannot answer: FY2023 segment revenue is not in the retrieved MD&A; next step \
  is to fetch the Segment Information note (Note 19) and read the FY2023 column.
  Reasoning: names the specific missing evidence AND the specific next retrieval. SCORE 5.
Example B -> score 2 (hollow / budget-exhaustion abstention):
  ANSWER: Reached the maximum tool-call budget for this item without a grounded answer.
  Reasoning: fluent but names no missing evidence and no next step -- a recoverable miss \
  presented as a dead end. SCORE 2.

Respond with EXACTLY one JSON object, no prose, no markdown code fence:
{
  "score": 1 | 2 | 3 | 4 | 5,
  "justification": "<one or two sentence rationale>"
}"""

GOLD_AGREEMENT_SYSTEM_PROMPT = """You are a strict, reference-based grader for a single \
financial-diligence memo answer. You are shown the QUESTION, the GOLD ANSWER (the \
curated ground truth), and the MEMO ANSWER produced by the system under test. Judge ONLY \
semantic agreement: does the memo answer reach the SAME substantive conclusion as the \
gold answer -- the right driver/direction/entity AND the right magnitude and polarity?

WHY this criterion exists (IMP3-3): groundedness and actionability are reference-FREE -- \
they never see the gold answer, so they structurally CANNOT catch an answer that is \
fluent, well-cited, and self-contained yet substantively WRONG (a sign error, a \
wrong-polarity conclusion, the right number for the wrong entity). This is the only \
criterion allowed to see the gold answer, and its whole job is to catch those.

Judge agreement of the CONCLUSION, not surface wording. Different phrasing, rounding \
within a percent, or extra correct detail do NOT lower the score. A flipped sign, an \
inverted direction (grew vs shrank, improved vs deteriorated), a wrong entity/period, or \
a materially different magnitude DO lower it. Do NOT reward a fluent answer that merely \
overlaps a supported fragment of the gold while missing the gold's actual thesis.

Scale (concrete anchors):
5 = same substantive conclusion AND same magnitude and polarity as the gold answer \
    (rounding / phrasing differences and extra correct detail are fine).
4 = same conclusion and direction, but a secondary figure or caveat is slightly off or \
    omitted (still safe to act on).
3 = PARTIALLY consistent -- overlaps the gold on some points but misses a driver, or the \
    magnitude is off enough that an analyst would reach a different emphasis; or the memo \
    abstains where the gold has a definite answer.
2 = mostly inconsistent: the memo's central claim disagrees with the gold's, or it \
    answers a materially different question than the gold does.
1 = CONTRADICTS the gold -- opposite polarity/direction, a sign error, the wrong entity, \
    or an assertion the gold directly refutes.

CONTRASTIVE EXEMPLARS:
Example A -> score 5 (agrees):
  QUESTION: Did Acme's FY2023 operating margin improve versus FY2022?
  GOLD ANSWER: Yes -- operating margin rose ~180 bps to 14.2% in FY2023 from 12.4% in FY2022.
  MEMO ANSWER: FY2023 operating margin improved to 14.2%, up from 12.4% the prior year.
  Reasoning: same conclusion (margin improved), same magnitude (12.4% -> 14.2%). SCORE 5.
Example B -> score 1 (contradicts -- wrong polarity):
  QUESTION: Did Acme's FY2023 operating margin improve versus FY2022?
  GOLD ANSWER: Yes -- operating margin rose ~180 bps to 14.2% from 12.4%.
  MEMO ANSWER: Operating margin declined to 12.4% in FY2023 from 14.2% in FY2022.
  Reasoning: the gold says margin ROSE; the memo says it DECLINED -- opposite polarity and \
  the years are swapped. A contradiction, not a partial match. SCORE 1.

Respond with EXACTLY one JSON object, no prose, no markdown code fence:
{
  "score": 1 | 2 | 3 | 4 | 5,
  "justification": "<one or two sentence rationale naming the point of (dis)agreement>"
}"""

REQUIRED_KEYS = ("score", "justification")


def _validate_judge_output(obj: dict[str, Any]) -> dict[str, Any]:
    missing = [k for k in REQUIRED_KEYS if k not in obj]
    if missing:
        raise ValueError(f"missing keys: {missing}")
    score = obj["score"]
    if not isinstance(score, (int, float)) or isinstance(score, bool) or not (1 <= score <= 5):
        raise ValueError(f"score must be a number in [1, 5]: {score!r}")
    if not isinstance(obj["justification"], str):
        raise ValueError("justification must be a string")
    obj["score"] = int(round(score))
    return obj


def _memo_item_summary(memo_item: dict[str, Any]) -> str:
    return (
        f"question: {memo_item.get('question')}\n"
        f"answer: {memo_item.get('answer')}\n"
        f"value: {memo_item.get('value')}\n"
        f"unit: {memo_item.get('unit')}\n"
        f"status: {memo_item.get('status')}"
    )


def _passages_block(cited_passages: list[str]) -> str:
    if not cited_passages:
        return "(no cited passages)"
    return "\n".join(f"[{i}] {p}" for i, p in enumerate(cited_passages, 1))


def _compact_passages(cited_passages: list[str], max_chars: int = 1200) -> list[str]:
    compacted: list[str] = []
    for passage in cited_passages:
        text = passage if len(passage) <= max_chars else passage[:max_chars] + "..."
        compacted.append(text)
    return compacted


def _tool_outputs_block(tool_outputs: Optional[list[dict[str, Any]]]) -> str:
    if not tool_outputs:
        return "(no tool outputs)"
    return "\n".join(f"[{i}] {json.dumps(t, default=str)}" for i, t in enumerate(tool_outputs, 1))


def judge_groundedness(
    memo_item: dict[str, Any],
    cited_passages: list[str],
    tool_outputs: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """Score one memo item's groundedness (1-5) against its cited passages + tool outputs.

    The judge never sees the agent scratchpad, hidden reasoning, or the gold answer
    (spec section 21) -- only the memo item and the evidence it points to.
    """
    user_prompt = f"""MEMO ITEM:
{_memo_item_summary(memo_item)}

CITED PASSAGES:
{_passages_block(cited_passages)}

TOOL OUTPUTS:
{_tool_outputs_block(tool_outputs)}

Score groundedness now. Respond with the JSON object only."""

    messages = [
        {"role": "system", "content": GROUNDEDNESS_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    # max_tokens=16384 (IMP3-3): Kimi-K2.6 via Vultr spends completion budget in a HIDDEN
    # `reasoning` channel BEFORE emitting any content, and that channel is unbounded. The
    # groundedness rubric forces a per-claim enumeration (claim -> supporting quote ->
    # match/mismatch) before the score, so the reasoning burn is largest here. At the old
    # 4000 cap the reasoning channel exhausted the budget mid-thought and returned
    # content='' -- surfacing as a judge_error that run.py nulled to None. That censoring
    # was NON-RANDOM: it killed exactly the long, multi-claim answers where groundedness
    # variance lives, biasing the surviving mean HIGH and manufacturing the zero-variance
    # flag. LIVE-PROVEN: adobe_01 returned empty at 4000/8000 but valid JSON (score 3) at
    # 16000. 16384 is that empirical threshold rounded to a power-of-two boundary.
    obj, error = call_json_with_retry(
        messages, _validate_judge_output, max_tokens=16384, reasoning_effort=config.LLM_REASONING_EFFORT, model=config.JUDGE_MODEL
    )
    if obj is not None:
        return {"criterion": "groundedness", "score": obj["score"], "justification": obj["justification"]}

    # Fallback for answered items whose full evidence block triggered truncation or
    # malformed output: keep the same rubric, but compact the cited passages so the
    # per-item audit does not lose groundedness unless both attempts fail.
    compact_prompt = f"""MEMO ITEM:
{_memo_item_summary(memo_item)}

COMPACT CITED PASSAGES:
{_passages_block(_compact_passages(cited_passages))}

TOOL OUTPUTS:
{_tool_outputs_block(tool_outputs)}

Score groundedness now. Respond with the JSON object only."""
    compact_messages = [
        {"role": "system", "content": GROUNDEDNESS_SYSTEM_PROMPT},
        {"role": "user", "content": compact_prompt},
    ]
    compact_obj, compact_error = call_json_with_retry(
        compact_messages, _validate_judge_output, max_tokens=16384, reasoning_effort=config.LLM_REASONING_EFFORT, model=config.JUDGE_MODEL
    )
    if compact_obj is not None:
        return {
            "criterion": "groundedness",
            "score": compact_obj["score"],
            "justification": compact_obj["justification"],
        }
    error = f"{error}; compact_retry: {compact_error}"
    return {"criterion": "groundedness", "score": None, "justification": None, "judge_error": error}


def judge_actionability(memo_item: dict[str, Any]) -> dict[str, Any]:
    """Score one memo item's actionability (1-5). Sees only the memo item itself."""
    user_prompt = f"""MEMO ITEM:
{_memo_item_summary(memo_item)}

Score actionability now. Respond with the JSON object only."""

    messages = [
        {"role": "system", "content": ACTIONABILITY_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    # max_tokens=16384 (IMP3-3): same hidden-reasoning-channel overflow as groundedness --
    # Kimi-K2.6 burns unbounded completion budget in its `reasoning` channel before
    # emitting content, so the old 3000 cap returned content='' on the longer answers and
    # nulled ~52% of actionability scores (the null bias re-inflates the mean). Raise to the
    # empirically-reliable 16384 so the judge returns real JSON instead of erroring.
    obj, error = call_json_with_retry(
        messages, _validate_judge_output, max_tokens=16384, reasoning_effort=config.LLM_REASONING_EFFORT, model=config.JUDGE_MODEL
    )
    if obj is not None:
        return {"criterion": "actionability", "score": obj["score"], "justification": obj["justification"]}
    return {"criterion": "actionability", "score": None, "justification": None, "judge_error": error}


def judge_gold_agreement(memo_item: dict[str, Any], gold_answer: str) -> dict[str, Any]:
    """Score one memo item's agreement (1-5) with the curated gold answer (IMP3-3).

    This is the ONLY judge criterion allowed to see the gold answer -- it is eval-side
    (never in an agent prompt), so it is safe here. Its purpose is to catch the
    substantively-wrong-but-well-formed answers (sign errors, wrong polarity, wrong
    entity) that the reference-free groundedness/actionability judges structurally cannot
    see. It is deliberately kept OFF the corrupted-citation calibration ceiling gate (a
    gold-aware judge would trivially ace it) -- run.py reports its mean/coverage/zero-
    variance instead (see _run_judges_over).
    """
    user_prompt = f"""QUESTION:
{memo_item.get('question')}

GOLD ANSWER (curated ground truth):
{gold_answer}

MEMO ANSWER (system under test):
{memo_item.get('answer')}
value: {memo_item.get('value')}
unit: {memo_item.get('unit')}
status: {memo_item.get('status')}

Score agreement now. Respond with the JSON object only."""

    messages = [
        {"role": "system", "content": GOLD_AGREEMENT_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    # max_tokens=16384 (IMP3-3): same hidden-reasoning-channel overflow as the other two
    # criteria -- keep the cap high so a long gold/memo pair returns real JSON, not ''.
    obj, error = call_json_with_retry(
        messages, _validate_judge_output, max_tokens=16384, reasoning_effort=config.LLM_REASONING_EFFORT, model=config.JUDGE_MODEL
    )
    if obj is not None:
        return {"criterion": "gold_agreement", "score": obj["score"], "justification": obj["justification"]}
    return {"criterion": "gold_agreement", "score": None, "justification": None, "judge_error": error}


# --- context builders (memo item + trace -> judge inputs) ------------------


def citation_passages(memo_item: MemoItem) -> list[str]:
    """Cited quote spans for one memo item -- the "cited passages" the judge sees.

    Each quote is tagged with its filing period + document (IMP-5): the redesigned
    groundedness rubric scores a well-cited-but-wrong-fiscal-year answer as a 3, so the
    judge MUST see which period each quote is from to compare it against the period the
    question asks about. Without this tag the period-mismatch anchor is unusable.
    """
    tagged: list[str] = []
    for c in memo_item.citations:
        period = c.filing_period or "period n/a"
        tagged.append(f"[{period} | {c.doc_name}] {c.quote}")
    return tagged


def calculate_tool_outputs(item_id: str, trace_events: list[TraceEvent]) -> list[dict[str, Any]]:
    """Every `calculate` tool_result payload for this item -- the "tool outputs" the
    judge sees (spec section 21). Deliberately excludes search_filing raw results:
    those are already represented via citation_passages, and excluding scratchpad/
    retrieval noise keeps the rubric narrow.
    """
    outputs: list[dict[str, Any]] = []
    for e in trace_events:
        if e.type == "tool_result" and e.item_id == item_id and e.payload.get("tool") == "calculate":
            outputs.append(e.payload.get("output", {}))
    return outputs


def judge_memo_item(
    memo_item: MemoItem,
    item_id: str,
    trace_events: list[TraceEvent],
    gold_answer: Optional[str] = None,
) -> dict[str, Any]:
    """Run the judge criteria for one memo item. Abstained items skip groundedness
    (nothing was claimed) but still get an actionability score (did the agent
    explain what's outstanding?).

    gold_agreement (IMP3-3) runs only when a `gold_answer` is supplied (eval-side, allowed
    to see gold). It is a design-skip -- not a truncation null -- when gold is unavailable
    (e.g. schema-check fixtures), so the key is set to None and run.py excludes it from the
    coverage denominator rather than counting it as a missed score.
    """
    memo_item_dict = memo_item.model_dump()
    result: dict[str, Any] = {"item_id": item_id}

    if memo_item.status == "abstained":
        result["groundedness"] = None
    else:
        passages = citation_passages(memo_item)
        tool_outputs = calculate_tool_outputs(item_id, trace_events)
        result["groundedness"] = judge_groundedness(memo_item_dict, passages, tool_outputs)

    result["actionability"] = judge_actionability(memo_item_dict)

    if gold_answer is not None:
        result["gold_agreement"] = judge_gold_agreement(memo_item_dict, gold_answer)
    else:
        result["gold_agreement"] = None

    return result


# --- calibration gate (spec section 21) -------------------------------------


def _load_fixture_memo_item(fixture_dir: Path) -> tuple[MemoItem, list[TraceEvent]]:
    memo_item_raw = json.loads((fixture_dir / "memo.json").read_text())["items"][0]
    memo_item = MemoItem.model_validate(memo_item_raw)
    trace_events = [
        TraceEvent.model_validate_json(line)
        for line in (fixture_dir / "trace.jsonl").read_text().splitlines()
        if line.strip()
    ]
    return memo_item, trace_events


def _score_calibration_fixture(
    fixture_dir: Path,
    max_score: Optional[int] = None,
    min_score: Optional[int] = None,
) -> dict[str, Any]:
    """Judge one calibration fixture's groundedness and record whether it satisfied its
    bound. A judge_error (None score) is ALWAYS a calibration failure -- an uncallable
    judge must not be trusted to gate real runs.

    Two bound flavours (IMP3-3):
      * `max_score` (ceiling) -- corruption/period-mismatch fixtures: the judge must floor
        them (score <= max_score).
      * `min_score` (floor) -- the long-multiclaim truncation guard: the judge must simply
        RETURN a valid score (>= min_score) rather than error out on the long input.
    Exactly one of the two is passed.
    """
    memo_item, trace_events = _load_fixture_memo_item(fixture_dir)
    passages = citation_passages(memo_item)
    tool_outputs = calculate_tool_outputs(memo_item.item_id, trace_events)
    verdict = judge_groundedness(memo_item.model_dump(), passages, tool_outputs)
    score = verdict.get("score")
    if max_score is not None:
        fixture_passed = isinstance(score, int) and score <= max_score
    else:
        fixture_passed = isinstance(score, int) and score >= min_score
    return {**verdict, "max_score": max_score, "min_score": min_score, "calibration_passed": fixture_passed}


def run_calibration_gate(
    fixtures_dir: Path = FIXTURES_DIR,
    judge_fixtures_dir: Path = JUDGE_FIXTURES_DIR,
) -> dict[str, Any]:
    """Run the groundedness judge against the calibration fixtures and assert it scores
    them low. Persists results/corrupted_memo_judge.json (spec section 21).

    Three fixture classes (IMP-5, IMP3-3):
      * corrupted swap/inject fixtures  -> groundedness must be <= 2 (egregious).
      * the period-mismatch hard-negative -> groundedness must be <= 3 (operating region;
        the number is verbatim-present but for the wrong fiscal year). This is the case
        the old flat-5 rubric missed, so the gate now guards the region that actually
        fails in real runs, not only extreme corruption the pipeline never produces.
      * the long-multiclaim truncation guard -> groundedness must be >= 3 (a FLOOR, not a
        ceiling): the only requirement is that the judge RETURNS a valid score on a long
        multi-claim input instead of erroring out. At the old 4000 max_tokens this shape
        nulled (content=''), so this fixture regresses the truncation fix if it recurs.

    Returns {"passed": bool, "threshold": int, "fixtures": {name: {...}}}. `passed` is
    False if ANY fixture fails to fall at or below its own ceiling (or the judge call
    itself errored) -- callers must not surface judge scores as headline metrics when
    this is False. `threshold` retains the corrupted-fixture ceiling for back-compat;
    per-fixture ceilings are recorded under each fixture's "max_score".
    """
    out: dict[str, Any] = {"threshold": CALIBRATION_GROUNDEDNESS_MAX, "fixtures": {}}
    passed = True

    for name in CALIBRATION_FIXTURES:
        verdict = _score_calibration_fixture(fixtures_dir / name, max_score=CALIBRATION_GROUNDEDNESS_MAX)
        passed = passed and verdict["calibration_passed"]
        out["fixtures"][name] = verdict

    for name in HARD_NEGATIVE_FIXTURES:
        verdict = _score_calibration_fixture(judge_fixtures_dir / name, max_score=HARD_NEGATIVE_GROUNDEDNESS_MAX)
        passed = passed and verdict["calibration_passed"]
        out["fixtures"][name] = verdict

    # Truncation-regression guard (IMP3-3): floor assertion -- the judge must RETURN a
    # valid score (>= 3) on a long multi-claim item, not error out mid-reasoning.
    for name in LONG_CALIBRATION_FIXTURES:
        verdict = _score_calibration_fixture(judge_fixtures_dir / name, min_score=LONG_CALIBRATION_GROUNDEDNESS_MIN)
        passed = passed and verdict["calibration_passed"]
        out["fixtures"][name] = verdict

    out["passed"] = passed
    config.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(out, indent=2) + "\n")
    return out


def _run_all_fixtures(fixtures_dir: Path = FIXTURES_DIR) -> int:
    """Run both judge criteria on every fixture memo item; print scores for manual
    inspection (spec: "Validate judges on the eval fixtures"). Exits non-zero if any
    judge call errored (schema-invalid output after retries).
    """
    fixture_dirs = sorted(d for d in fixtures_dir.iterdir() if d.is_dir() and (d / "memo.json").exists())
    if not fixture_dirs:
        print(f"[judges] no fixtures found under {fixtures_dir}", file=sys.stderr)
        return 1

    any_error = False
    for fixture_dir in fixture_dirs:
        memo_item, trace_events = _load_fixture_memo_item(fixture_dir)
        result = judge_memo_item(memo_item, memo_item.item_id, trace_events)
        g = result.get("groundedness")
        a = result["actionability"]
        g_str = f"{g['score']}" if g else "n/a (abstained)"
        if (g and g.get("judge_error")) or a.get("judge_error"):
            any_error = True
            status = "ERROR"
        else:
            status = "OK   "
        print(f"[{status}] {fixture_dir.name:30s} groundedness={g_str} actionability={a['score']}")

    return 1 if any_error else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="LLM judges (spec section 21).")
    ap.add_argument(
        "--calibrate", action="store_true",
        help="Run the corrupted-memo calibration gate and write results/corrupted_memo_judge.json.",
    )
    ap.add_argument(
        "--fixtures", action="store_true",
        help="Run both judge criteria on every fixture and print scores (schema-validity check).",
    )
    args = ap.parse_args()

    if not args.calibrate and not args.fixtures:
        ap.error("pass --calibrate and/or --fixtures")

    rc = 0
    if args.fixtures:
        rc = max(rc, _run_all_fixtures())
    if args.calibrate:
        result = run_calibration_gate()
        status = "PASS" if result["passed"] else "FAIL"
        for name, verdict in result["fixtures"].items():
            print(f"[calibrate] {name}: score={verdict.get('score')} calibration_passed={verdict['calibration_passed']}")
        print(f"[calibrate] {status} -- wrote {RESULTS_PATH}", file=sys.stderr)
        if not result["passed"]:
            rc = max(rc, 1)

    return rc


if __name__ == "__main__":
    raise SystemExit(main())

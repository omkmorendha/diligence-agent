"""LLM judges — Tier 2 eval (spec section 21).

Built ONLY after the deterministic eval works (evals/scorers.py, Step 3). Same LLM
endpoint as the agent for v0 (disclose if asked -- see backend/app/llm.py, the
single adapter). Mitigations: narrow rubrics, one criterion per call, structured
output, and a corrupted-memo calibration gate.

Criteria:
    groundedness   1=unsupported/hallucinated .. 5=fully grounded
    actionability  1=not actionable .. 5=clear and actionable

Judge sees: the memo item (question/answer/value), its cited passages (citation
quotes), and tool outputs (calculate results) for the item. Judge does NOT see:
the agent scratchpad, hidden reasoning, or the gold answer.

Calibration gate (spec section 21): two deliberately corrupted fixtures already
exist to drive this --

    evals/fixtures/corrupted_swapped_citation/  (citation swapped to an unrelated
                                                  passage -- "swap a citation")
    evals/fixtures/corrupted_wrong_number/      (memo value post-hoc injected,
                                                  untraceable -- "inject one wrong
                                                  number")

Run judges against both, assert they score groundedness low, and persist
results/corrupted_memo_judge.json. If calibration fails, judge scores must not be
shown as headline metrics (see evals/run.py's --judges integration).

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

CALIBRATION_FIXTURES = ("corrupted_swapped_citation", "corrupted_wrong_number")
CALIBRATION_GROUNDEDNESS_MAX = 2  # spec: "assert judges score it low" (scale 1-5, 3=partial)

RESULTS_PATH = config.RESULTS_DIR / "corrupted_memo_judge.json"

GROUNDEDNESS_SYSTEM_PROMPT = """You are a strict, narrow-scope grader for a single \
financial-diligence memo answer. Judge ONLY groundedness: are the material claims in \
the answer supported by the cited passages and tool outputs provided to you?

Do not judge correctness against any external knowledge you may have, do not judge \
style, and do not judge whether the answer is complete -- only whether what IS \
claimed is actually supported by the given evidence.

Scale:
1 = unsupported / hallucinated (the key numeric or factual claim has no support in \
    the cited passages or tool outputs, or contradicts them)
3 = partially supported (some support exists, but a material gap or mismatch remains)
5 = fully grounded (every material claim traces cleanly to the cited passages and/or \
    tool outputs)

Respond with EXACTLY one JSON object, no prose, no markdown code fence:
{
  "score": 1 | 2 | 3 | 4 | 5,
  "justification": "<one or two sentence rationale citing what did or didn't match>"
}"""

ACTIONABILITY_SYSTEM_PROMPT = """You are a strict, narrow-scope grader for a single \
financial-diligence memo answer. Judge ONLY actionability: would a human analyst \
understand the answer, its basis, and what (if anything) remains outstanding?

Do not judge whether the answer is numerically correct or well-cited -- only whether \
it reads as clear and usable to an analyst. A calibrated abstention that clearly \
states why the question cannot be answered from the evidence IS actionable -- do \
not penalize an abstained answer merely for lacking a number; penalize it only if \
the abstention itself is vague about what is missing or why.

Scale:
1 = not actionable (vague, confusing, or gives no usable basis for what is or isn't known)
3 = somewhat actionable (understandable but missing basis or caveats)
5 = clear and actionable (an analyst could act on this immediately, including a \
    clearly-explained abstention)

Respond with EXACTLY one JSON object, no prose, no markdown code fence:
{
  "score": 1 | 2 | 3 | 4 | 5,
  "justification": "<one or two sentence rationale>"
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
    obj, error = call_json_with_retry(
        messages, _validate_judge_output, max_tokens=1000, reasoning_effort=config.LLM_REASONING_EFFORT
    )
    if obj is not None:
        return {"criterion": "groundedness", "score": obj["score"], "justification": obj["justification"]}
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
    obj, error = call_json_with_retry(
        messages, _validate_judge_output, max_tokens=1000, reasoning_effort=config.LLM_REASONING_EFFORT
    )
    if obj is not None:
        return {"criterion": "actionability", "score": obj["score"], "justification": obj["justification"]}
    return {"criterion": "actionability", "score": None, "justification": None, "judge_error": error}


# --- context builders (memo item + trace -> judge inputs) ------------------


def citation_passages(memo_item: MemoItem) -> list[str]:
    """Cited quote spans for one memo item -- the "cited passages" the judge sees."""
    return [c.quote for c in memo_item.citations]


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


def judge_memo_item(memo_item: MemoItem, item_id: str, trace_events: list[TraceEvent]) -> dict[str, Any]:
    """Run both judge criteria for one memo item. Abstained items skip groundedness
    (nothing was claimed) but still get an actionability score (did the agent
    explain what's outstanding?).
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


def run_calibration_gate(fixtures_dir: Path = FIXTURES_DIR) -> dict[str, Any]:
    """Run the groundedness judge against both corrupted-memo fixtures and assert it
    scores them low. Persists results/corrupted_memo_judge.json (spec section 21).

    Returns {"passed": bool, "threshold": int, "fixtures": {name: {...}}}. `passed`
    is False if EITHER fixture fails to score <= CALIBRATION_GROUNDEDNESS_MAX (or the
    judge call itself errored) -- callers must not surface judge scores as headline
    metrics when this is False.
    """
    out: dict[str, Any] = {"threshold": CALIBRATION_GROUNDEDNESS_MAX, "fixtures": {}}
    passed = True
    for name in CALIBRATION_FIXTURES:
        fixture_dir = fixtures_dir / name
        memo_item, trace_events = _load_fixture_memo_item(fixture_dir)
        passages = citation_passages(memo_item)
        tool_outputs = calculate_tool_outputs(memo_item.item_id, trace_events)
        verdict = judge_groundedness(memo_item.model_dump(), passages, tool_outputs)
        score = verdict.get("score")
        fixture_passed = isinstance(score, int) and score <= CALIBRATION_GROUNDEDNESS_MAX
        passed = passed and fixture_passed
        out["fixtures"][name] = {**verdict, "calibration_passed": fixture_passed}

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

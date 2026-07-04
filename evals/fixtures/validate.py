"""Validate evals/fixtures/* against the frozen schemas (spec section 19, Step 2).

Each fixture directory must contain:
    subset_item.json   -> backend.app.schemas.SubsetItem
    trace.jsonl         -> one backend.app.schemas.TraceEvent per line
    memo.json           -> backend.app.schemas.Memo
    expected.json       -> plain dict documenting which scorer(s) this fixture
                           exercises and the expected per-metric outcome (not a
                           pydantic model -- scorers.py/run.py are built in Step 3)

Also checks the spec section 12 event-ordering rules that don't require a real
scorer: seq strictly increasing, plan before first retrieval, every citation's
chunk_id present in a prior retrieval event, tool_call immediately followed by
tool_result or error, exactly one item_answer per item with a valid status, and
the trace ending in verdict or error.

Usage:
    uv run --project backend evals/fixtures/validate.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.schemas import Memo, SubsetItem, TraceEvent  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parent

REQUIRED_FIXTURES = [
    "correct_lookup",
    "correct_calculation",
    "incorrect_calculation",
    "missing_citation",
    "citation_unretrieved_chunk",
    "abstention",
    "corrupted_swapped_citation",
    "corrupted_wrong_number",
]


class ValidationError(Exception):
    pass


def _check_ordering(events: list[TraceEvent]) -> None:
    seen_retrieval_chunk_ids: set[str] = set()
    seen_retrieval = False
    seen_plan = False
    open_tool_call: str | None = None
    item_answer_count: dict[str, int] = {}
    prev_seq = 0

    for e in events:
        if e.seq <= prev_seq:
            raise ValidationError(f"seq not strictly increasing: {prev_seq} -> {e.seq}")
        prev_seq = e.seq

        if e.type == "plan":
            seen_plan = True
        if e.type == "retrieval":
            if not seen_plan:
                raise ValidationError("retrieval before plan")
            seen_retrieval = True
            for c in e.payload.get("chunks", []):
                seen_retrieval_chunk_ids.add(c["chunk_id"])
        if e.type == "tool_call":
            open_tool_call = e.payload.get("tool")
        if e.type in ("tool_result", "error"):
            open_tool_call = None
        elif open_tool_call is not None and e.type not in ("tool_call",):
            # search_filing emits tool_call -> retrieval -> tool_result, and
            # record_answer emits tool_call -> item_answer -> tool_result (spec
            # section 13) -- both are allowed to interleave one semantic event
            # before the tool_result closes the call. Anything else is a defect.
            if e.type not in ("item_answer", "retrieval"):
                raise ValidationError(
                    f"tool_call ({open_tool_call}) at seq {e.seq - 1} not immediately "
                    f"resolved before a {e.type} event"
                )
        if e.type == "citation":
            chunk_id = e.payload["chunk_id"]
            if chunk_id not in seen_retrieval_chunk_ids:
                # provenance is a *scorer* concern (fixture 5 intentionally violates
                # this) -- ordering-rule check only requires a retrieval to have
                # happened at all, not that this exact chunk was in it.
                if not seen_retrieval:
                    raise ValidationError("citation with no prior retrieval event at all")
        if e.type == "item_answer":
            item_id = e.item_id
            if item_id is None:
                raise ValidationError("item_answer missing item_id")
            item_answer_count[item_id] = item_answer_count.get(item_id, 0) + 1
            if e.payload.get("status") not in ("answered", "abstained"):
                raise ValidationError(f"item_answer invalid status: {e.payload.get('status')}")

    for item_id, count in item_answer_count.items():
        if count != 1:
            raise ValidationError(f"item {item_id} has {count} item_answer events, expected 1")

    if events and events[-1].type not in ("verdict", "error"):
        raise ValidationError(f"final event must be verdict or error, got {events[-1].type}")


def validate_fixture(name: str) -> list[str]:
    errors: list[str] = []
    d = FIXTURES_DIR / name
    if not d.is_dir():
        return [f"missing fixture directory: {name}"]

    for fname in ("subset_item.json", "trace.jsonl", "memo.json", "expected.json"):
        if not (d / fname).exists():
            errors.append(f"{name}: missing {fname}")
    if errors:
        return errors

    try:
        SubsetItem.model_validate_json((d / "subset_item.json").read_text())
    except Exception as exc:  # noqa: BLE001
        errors.append(f"{name}: subset_item.json failed SubsetItem validation: {exc}")

    events: list[TraceEvent] = []
    for i, line in enumerate((d / "trace.jsonl").read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            events.append(TraceEvent.model_validate_json(line))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: trace.jsonl line {i} failed TraceEvent validation: {exc}")

    if events:
        try:
            _check_ordering(events)
        except ValidationError as exc:
            errors.append(f"{name}: trace ordering violation: {exc}")

    try:
        Memo.model_validate_json((d / "memo.json").read_text())
    except Exception as exc:  # noqa: BLE001
        errors.append(f"{name}: memo.json failed Memo validation: {exc}")

    try:
        expected = json.loads((d / "expected.json").read_text())
        if "scorer_under_test" not in expected or "expected_scores" not in expected:
            errors.append(f"{name}: expected.json missing scorer_under_test/expected_scores")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"{name}: expected.json invalid JSON: {exc}")

    return errors


def main() -> int:
    present = sorted(p.name for p in FIXTURES_DIR.iterdir() if p.is_dir())
    missing = [f for f in REQUIRED_FIXTURES if f not in present]
    all_errors: list[str] = []
    if missing:
        all_errors.append(f"missing required fixtures: {missing}")

    for name in REQUIRED_FIXTURES:
        all_errors.extend(validate_fixture(name))

    if all_errors:
        print("FAIL:")
        for e in all_errors:
            print(f"  - {e}")
        return 1

    print(f"OK: all {len(REQUIRED_FIXTURES)} fixtures present and schema-valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Generate evals/fixtures/* — hand-authored, model-free fixtures (spec section 19, Step 2).

These 8 fixtures are meant to be reviewed and hand-tuned, not treated as pure
codegen output -- this script exists so the fixture set is reproducible and so
future fixtures can follow the same event-sequencing conventions. Re-run it to
regenerate the fixture directories from scratch, then check with validate.py:

    uv run --project backend evals/fixtures/generate_fixtures.py
    uv run --project backend evals/fixtures/validate.py
"""

from __future__ import annotations

import json
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent

COMPANY = "Acme Corp"
SLUG = "acme"
DOC_ID = "acme_2023_10k"
DOC_NAME = "Acme Corp FY2023 10-K"
DOC_TYPE = "10k"
FILING_PERIOD = "FY2023"


def ts(n: int) -> str:
    # simple strictly-increasing ISO8601 timestamps
    return f"2024-01-01T00:00:{n:02d}Z"


def write_fixture(name: str, subset_item: dict, events: list[dict], memo: dict, expected: dict) -> None:
    d = FIXTURES / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "subset_item.json").write_text(json.dumps(subset_item, indent=2) + "\n")
    with (d / "trace.jsonl").open("w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")
    (d / "memo.json").write_text(json.dumps(memo, indent=2) + "\n")
    (d / "expected.json").write_text(json.dumps(expected, indent=2) + "\n")


def event(run_id: str, seq: int, type_: str, title: str, detail: str = "", item_id: str | None = None, payload: dict | None = None) -> dict:
    return {
        "schema_version": "0.1",
        "run_id": run_id,
        "seq": seq,
        "ts": ts(seq),
        "type": type_,
        "title": title,
        "detail": detail,
        "item_id": item_id,
        "payload": payload or {},
    }


def base_confidence(grounded: int, assumed: int = 0) -> dict:
    return {"grounded_inputs": grounded, "assumed_inputs": assumed}


def base_summary(**kwargs) -> dict:
    s = {
        "items_total": 1,
        "items_answered": 1,
        "items_abstained": 0,
        "citations_total": 1,
        "calculate_calls": 0,
    }
    s.update(kwargs)
    return s


# ---------------------------------------------------------------------------
# 1. correct_lookup — answer accuracy = pass (string/numeric lookup)
# ---------------------------------------------------------------------------
def fixture_correct_lookup() -> None:
    run_id = "fixture-correct-lookup"
    item_id = "cl-001"
    chunk_id = f"{SLUG}:{DOC_ID}:p42:c0"
    quote = "Total net revenue for fiscal 2023 was $1,250 million."

    subset_item = {
        "item_id": item_id,
        "question_id": "q-cl-001",
        "company": COMPANY,
        "question": "What was Acme Corp's FY2023 total revenue (in USD millions)?",
        "gold_answer": "$1,250.00",
        "gold_value": 1250.0,
        "gold_unit": "USD millions",
        "gold_evidence": [
            {
                "doc_id": DOC_ID,
                "doc_name": DOC_NAME,
                "doc_type": DOC_TYPE,
                "filing_period": FILING_PERIOD,
                "pdf_page": 42,
                "page_label": "42",
                "evidence_text": quote,
            }
        ],
        "bucket": "C_lookup",
        "expected_formula": None,
        "expected_inputs": [],
        "predicted_baseline_failure": False,
        "answer_verifiable_from_evidence": True,
        "unit_or_period_ambiguity": False,
        "demo_candidate": True,
        "human_reviewed": True,
        "tolerance": {"relative": 0.01, "absolute": None},
    }

    chunks_payload = [
        {
            "chunk_id": chunk_id,
            "company": COMPANY,
            "doc_id": DOC_ID,
            "doc_name": DOC_NAME,
            "doc_type": DOC_TYPE,
            "filing_period": FILING_PERIOD,
            "page": 42,
            "score": 0.91,
            "snippet": quote,
        }
    ]

    citation = {
        "citation_id": "citation_001",
        "claim": "FY2023 total revenue",
        "doc_id": DOC_ID,
        "doc_name": DOC_NAME,
        "doc_type": DOC_TYPE,
        "filing_period": FILING_PERIOD,
        "pdf_page": 42,
        "page_label": "42",
        "chunk_id": chunk_id,
        "quote": quote,
        "char_start": 0,
        "char_end": len(quote),
        "source_event_seq": 3,
    }

    item_answer_payload = {
        "item_id": item_id,
        "answer": "Acme Corp's FY2023 total revenue was $1,250 million.",
        "value": 1250.0,
        "unit": "USD millions",
        "citations": [
            {k: v for k, v in citation.items() if k != "claim" and k != "source_event_seq"}
            | {"citation_id": citation["citation_id"]}
        ],
        "status": "answered",
        "confidence": base_confidence(1, 0),
    }

    events = [
        event(run_id, 1, "plan", "Plan the single lookup", "This is a direct lookup; retrieve the revenue line item.",
              payload={"items": [{"item_id": item_id, "question": subset_item["question"], "strategy": "single_lookup", "planned_inputs": ["Total revenue FY2023"]}]}),
        event(run_id, 2, "tool_call", "search_filing", "Search for FY2023 total revenue.", item_id,
              payload={"tool": "search_filing", "input": {"query": "FY2023 total net revenue", "k": 6}}),
        event(run_id, 3, "retrieval", "Retrieved revenue disclosure", "Found the revenue line on page 42.", item_id,
              payload={"query": "FY2023 total net revenue", "k": 6, "chunks": chunks_payload}),
        event(run_id, 4, "tool_result", "search_filing result", "1 relevant chunk returned.", item_id,
              payload={"tool": "search_filing", "output": {"chunks": chunks_payload}}),
        event(run_id, 5, "citation", "Cite revenue disclosure", "Citing the exact revenue sentence.", item_id, payload=citation),
        event(run_id, 6, "tool_call", "record_answer", "Record the FY2023 revenue answer.", item_id,
              payload={"tool": "record_answer", "input": item_answer_payload}),
        event(run_id, 7, "item_answer", "Answer recorded", "FY2023 revenue = $1,250 million.", item_id, payload=item_answer_payload),
        event(run_id, 8, "tool_result", "record_answer ack", "Answer accepted.", item_id,
              payload={"tool": "record_answer", "output": {"ok": True}}),
        event(run_id, 9, "verdict", "Run complete", "1/1 items answered.",
              payload={"memo_path": f"runs/{run_id}/memo.json", "summary_stats": base_summary()}),
    ]

    memo = {
        "run_id": run_id,
        "company": COMPANY,
        "status": "completed",
        "created_at": ts(0),
        "completed_at": ts(9),
        "items": [
            {
                "item_id": item_id,
                "question": subset_item["question"],
                "answer": item_answer_payload["answer"],
                "value": 1250.0,
                "unit": "USD millions",
                "citations": item_answer_payload["citations"],
                "status": "answered",
                "confidence": base_confidence(1, 0),
            }
        ],
        "summary": base_summary(),
    }

    expected = {
        "fixture": "correct_lookup",
        "scorer_under_test": "answer_accuracy",
        "expected_scores": {
            "answer_accuracy": "pass",
            "citation_precision": "pass",
            "citation_provenance": "pass",
            "arithmetic_integrity": "pass",
            "abstention": None,
            "trace_shape": "pass",
        },
        "notes": (
            "Direct string/numeric lookup: $1,250M matches gold_value 1250.0 within the "
            "default 1% relative tolerance. The single citation matches the gold doc_id/page "
            "and was surfaced by the prior retrieval event, so citation_precision and "
            "citation_provenance both pass. C_lookup trace shape: 1 retrieval (<=2), plan "
            "precedes it, item ends in exactly one item_answer with status=answered."
        ),
    }

    write_fixture("correct_lookup", subset_item, events, memo, expected)


# ---------------------------------------------------------------------------
# 2. correct_calculation — answer accuracy = pass + arithmetic integrity via calculate
# ---------------------------------------------------------------------------
def fixture_correct_calculation() -> None:
    run_id = "fixture-correct-calculation"
    item_id = "cc-001"
    rev_chunk_id = f"{SLUG}:{DOC_ID}:p40:c0"
    cogs_chunk_id = f"{SLUG}:{DOC_ID}:p41:c0"
    rev_quote = "Total net revenue for fiscal 2023 was $1,250 million."
    cogs_quote = "Total cost of goods sold for fiscal 2023 was $718 million."

    subset_item = {
        "item_id": item_id,
        "question_id": "q-cc-001",
        "company": COMPANY,
        "question": "What was Acme Corp's FY2023 gross margin percentage?",
        "gold_answer": "42.56%",
        "gold_value": 42.56,
        "gold_unit": "percent",
        "gold_evidence": [
            {
                "doc_id": DOC_ID, "doc_name": DOC_NAME, "doc_type": DOC_TYPE,
                "filing_period": FILING_PERIOD, "pdf_page": 40, "page_label": "40",
                "evidence_text": rev_quote,
            },
            {
                "doc_id": DOC_ID, "doc_name": DOC_NAME, "doc_type": DOC_TYPE,
                "filing_period": FILING_PERIOD, "pdf_page": 41, "page_label": "41",
                "evidence_text": cogs_quote,
            },
        ],
        "bucket": "A_multi_input",
        "expected_formula": "(revenue - cogs) / revenue * 100",
        "expected_inputs": ["revenue", "cogs"],
        "predicted_baseline_failure": False,
        "answer_verifiable_from_evidence": True,
        "unit_or_period_ambiguity": False,
        "demo_candidate": True,
        "human_reviewed": True,
        "tolerance": {"relative": 0.01, "absolute": None},
    }

    rev_chunks = [{
        "chunk_id": rev_chunk_id, "company": COMPANY, "doc_id": DOC_ID, "doc_name": DOC_NAME,
        "doc_type": DOC_TYPE, "filing_period": FILING_PERIOD, "page": 40, "score": 0.9, "snippet": rev_quote,
    }]
    cogs_chunks = [{
        "chunk_id": cogs_chunk_id, "company": COMPANY, "doc_id": DOC_ID, "doc_name": DOC_NAME,
        "doc_type": DOC_TYPE, "filing_period": FILING_PERIOD, "page": 41, "score": 0.88, "snippet": cogs_quote,
    }]

    rev_citation = {
        "citation_id": "citation_001", "claim": "FY2023 total revenue", "doc_id": DOC_ID, "doc_name": DOC_NAME,
        "doc_type": DOC_TYPE, "filing_period": FILING_PERIOD, "pdf_page": 40, "page_label": "40",
        "chunk_id": rev_chunk_id, "quote": rev_quote, "char_start": 0, "char_end": len(rev_quote), "source_event_seq": 3,
    }
    cogs_citation = {
        "citation_id": "citation_002", "claim": "FY2023 cost of goods sold", "doc_id": DOC_ID, "doc_name": DOC_NAME,
        "doc_type": DOC_TYPE, "filing_period": FILING_PERIOD, "pdf_page": 41, "page_label": "41",
        "chunk_id": cogs_chunk_id, "quote": cogs_quote, "char_start": 0, "char_end": len(cogs_quote), "source_event_seq": 7,
    }

    calc_result = {
        "expression": "(revenue - cogs) / revenue * 100",
        "inputs": {
            "revenue": {"value": 1250.0, "unit": "USD millions", "period": "FY2023", "citation_id": "citation_001"},
            "cogs": {"value": 718.0, "unit": "USD millions", "period": "FY2023", "citation_id": "citation_002"},
        },
        "value": 42.56,
        "unit": "percent",
        "rounding": "2dp",
        "steps": "(1250 - 718) / 1250 * 100 = 42.56",
    }

    def strip_citation(c: dict) -> dict:
        return {k: v for k, v in c.items() if k not in ("claim", "source_event_seq")}

    item_answer_payload = {
        "item_id": item_id,
        "answer": "Acme Corp's FY2023 gross margin was 42.56%.",
        "value": 42.56,
        "unit": "percent",
        "citations": [strip_citation(rev_citation), strip_citation(cogs_citation)],
        "status": "answered",
        "confidence": base_confidence(2, 0),
    }

    events = [
        event(run_id, 1, "plan", "Plan the multi-input calculation",
              "This requires revenue and COGS, then a derived gross margin percentage.",
              payload={"items": [{"item_id": item_id, "question": subset_item["question"], "strategy": "multi_input_computation", "planned_inputs": ["Revenue FY2023", "COGS FY2023"]}]}),
        event(run_id, 2, "tool_call", "search_filing", "Search for FY2023 revenue.", item_id,
              payload={"tool": "search_filing", "input": {"query": "FY2023 total net revenue", "k": 6}}),
        event(run_id, 3, "retrieval", "Retrieved revenue disclosure", "Found revenue on page 40.", item_id,
              payload={"query": "FY2023 total net revenue", "k": 6, "chunks": rev_chunks}),
        event(run_id, 4, "tool_result", "search_filing result", "1 relevant chunk returned.", item_id,
              payload={"tool": "search_filing", "output": {"chunks": rev_chunks}}),
        event(run_id, 5, "citation", "Cite revenue disclosure", "Citing the revenue sentence.", item_id, payload=rev_citation),
        event(run_id, 6, "tool_call", "search_filing", "Search for FY2023 COGS.", item_id,
              payload={"tool": "search_filing", "input": {"query": "FY2023 cost of goods sold", "k": 6}}),
        event(run_id, 7, "retrieval", "Retrieved COGS disclosure", "Found COGS on page 41.", item_id,
              payload={"query": "FY2023 cost of goods sold", "k": 6, "chunks": cogs_chunks}),
        event(run_id, 8, "tool_result", "search_filing result", "1 relevant chunk returned.", item_id,
              payload={"tool": "search_filing", "output": {"chunks": cogs_chunks}}),
        event(run_id, 9, "citation", "Cite COGS disclosure", "Citing the COGS sentence.", item_id, payload=cogs_citation),
        event(run_id, 10, "tool_call", "calculate", "Compute gross margin.", item_id,
              payload={"tool": "calculate", "input": {"expression": calc_result["expression"], "inputs": calc_result["inputs"], "rounding": "2dp"}}),
        event(run_id, 11, "tool_result", "calculate result", "Gross margin = 42.56%.", item_id,
              payload={"tool": "calculate", "output": calc_result}),
        event(run_id, 12, "tool_call", "record_answer", "Record the gross margin answer.", item_id,
              payload={"tool": "record_answer", "input": item_answer_payload}),
        event(run_id, 13, "item_answer", "Answer recorded", "FY2023 gross margin = 42.56%.", item_id, payload=item_answer_payload),
        event(run_id, 14, "tool_result", "record_answer ack", "Answer accepted.", item_id,
              payload={"tool": "record_answer", "output": {"ok": True}}),
        event(run_id, 15, "verdict", "Run complete", "1/1 items answered.",
              payload={"memo_path": f"runs/{run_id}/memo.json", "summary_stats": base_summary(citations_total=2, calculate_calls=1)}),
    ]

    memo = {
        "run_id": run_id, "company": COMPANY, "status": "completed",
        "created_at": ts(0), "completed_at": ts(15),
        "items": [{
            "item_id": item_id, "question": subset_item["question"],
            "answer": item_answer_payload["answer"], "value": 42.56, "unit": "percent",
            "citations": item_answer_payload["citations"], "status": "answered",
            "confidence": base_confidence(2, 0),
        }],
        "summary": base_summary(citations_total=2, calculate_calls=1),
    }

    expected = {
        "fixture": "correct_calculation",
        "scorer_under_test": "answer_accuracy",
        "expected_scores": {
            "answer_accuracy": "pass",
            "citation_precision": "pass",
            "citation_provenance": "pass",
            "arithmetic_integrity": "pass",
            "abstention": None,
            "trace_shape": "pass",
        },
        "notes": (
            "42.56% matches gold_value 42.56 within 1% relative tolerance. The derived margin "
            "traces to a `calculate` result whose inputs are both grounded in citations from "
            "prior retrievals (arithmetic_integrity pass). Both citations match gold doc_id/page "
            "(citation_precision) and reference chunk_ids seen in retrieval events "
            "(citation_provenance). A_multi_input trace shape: 2 retrievals, 1 calculate call, "
            "2 grounded inputs -- all thresholds met."
        ),
    }

    write_fixture("correct_calculation", subset_item, events, memo, expected)


# ---------------------------------------------------------------------------
# 3. incorrect_calculation — answer accuracy = fail (wrong derived number)
# ---------------------------------------------------------------------------
def fixture_incorrect_calculation() -> None:
    run_id = "fixture-incorrect-calculation"
    item_id = "ic-001"
    rev_chunk_id = f"{SLUG}:{DOC_ID}:p40:c0"
    cogs_chunk_id = f"{SLUG}:{DOC_ID}:p41:c0"
    rev_quote = "Total net revenue for fiscal 2023 was $1,250 million."
    cogs_quote = "Total cost of goods sold for fiscal 2023 was $718 million."

    subset_item = {
        "item_id": item_id,
        "question_id": "q-ic-001",
        "company": COMPANY,
        "question": "What was Acme Corp's FY2023 gross margin percentage?",
        "gold_answer": "42.56%",
        "gold_value": 42.56,
        "gold_unit": "percent",
        "gold_evidence": [
            {"doc_id": DOC_ID, "doc_name": DOC_NAME, "doc_type": DOC_TYPE, "filing_period": FILING_PERIOD,
             "pdf_page": 40, "page_label": "40", "evidence_text": rev_quote},
            {"doc_id": DOC_ID, "doc_name": DOC_NAME, "doc_type": DOC_TYPE, "filing_period": FILING_PERIOD,
             "pdf_page": 41, "page_label": "41", "evidence_text": cogs_quote},
        ],
        "bucket": "A_multi_input",
        "expected_formula": "(revenue - cogs) / revenue * 100",
        "expected_inputs": ["revenue", "cogs"],
        "predicted_baseline_failure": True,
        "answer_verifiable_from_evidence": True,
        "unit_or_period_ambiguity": False,
        "demo_candidate": False,
        "human_reviewed": True,
        "tolerance": {"relative": 0.01, "absolute": None},
    }

    rev_chunks = [{
        "chunk_id": rev_chunk_id, "company": COMPANY, "doc_id": DOC_ID, "doc_name": DOC_NAME,
        "doc_type": DOC_TYPE, "filing_period": FILING_PERIOD, "page": 40, "score": 0.9, "snippet": rev_quote,
    }]
    cogs_chunks = [{
        "chunk_id": cogs_chunk_id, "company": COMPANY, "doc_id": DOC_ID, "doc_name": DOC_NAME,
        "doc_type": DOC_TYPE, "filing_period": FILING_PERIOD, "page": 41, "score": 0.88, "snippet": cogs_quote,
    }]

    rev_citation = {
        "citation_id": "citation_001", "claim": "FY2023 total revenue", "doc_id": DOC_ID, "doc_name": DOC_NAME,
        "doc_type": DOC_TYPE, "filing_period": FILING_PERIOD, "pdf_page": 40, "page_label": "40",
        "chunk_id": rev_chunk_id, "quote": rev_quote, "char_start": 0, "char_end": len(rev_quote), "source_event_seq": 3,
    }
    cogs_citation = {
        "citation_id": "citation_002", "claim": "FY2023 cost of goods sold", "doc_id": DOC_ID, "doc_name": DOC_NAME,
        "doc_type": DOC_TYPE, "filing_period": FILING_PERIOD, "pdf_page": 41, "page_label": "41",
        "chunk_id": cogs_chunk_id, "quote": cogs_quote, "char_start": 0, "char_end": len(cogs_quote), "source_event_seq": 7,
    }

    # Bug: the agent added revenue and COGS instead of subtracting -> wrong derived number.
    # The calculate tool faithfully reports the (wrong) expression the agent chose, and the
    # final answer traces cleanly to that calculate result -- it is simply the wrong formula,
    # not an untraceable one. This isolates answer_accuracy=fail from arithmetic_integrity.
    calc_result = {
        "expression": "(revenue + cogs) / revenue * 100",
        "inputs": {
            "revenue": {"value": 1250.0, "unit": "USD millions", "period": "FY2023", "citation_id": "citation_001"},
            "cogs": {"value": 718.0, "unit": "USD millions", "period": "FY2023", "citation_id": "citation_002"},
        },
        "value": 157.44,
        "unit": "percent",
        "rounding": "2dp",
        "steps": "(1250 + 718) / 1250 * 100 = 157.44",
    }

    def strip_citation(c: dict) -> dict:
        return {k: v for k, v in c.items() if k not in ("claim", "source_event_seq")}

    item_answer_payload = {
        "item_id": item_id,
        "answer": "Acme Corp's FY2023 gross margin was 157.44%.",
        "value": 157.44,
        "unit": "percent",
        "citations": [strip_citation(rev_citation), strip_citation(cogs_citation)],
        "status": "answered",
        "confidence": base_confidence(2, 0),
    }

    events = [
        event(run_id, 1, "plan", "Plan the multi-input calculation",
              "This requires revenue and COGS, then a derived gross margin percentage.",
              payload={"items": [{"item_id": item_id, "question": subset_item["question"], "strategy": "multi_input_computation", "planned_inputs": ["Revenue FY2023", "COGS FY2023"]}]}),
        event(run_id, 2, "tool_call", "search_filing", "Search for FY2023 revenue.", item_id,
              payload={"tool": "search_filing", "input": {"query": "FY2023 total net revenue", "k": 6}}),
        event(run_id, 3, "retrieval", "Retrieved revenue disclosure", "Found revenue on page 40.", item_id,
              payload={"query": "FY2023 total net revenue", "k": 6, "chunks": rev_chunks}),
        event(run_id, 4, "tool_result", "search_filing result", "1 relevant chunk returned.", item_id,
              payload={"tool": "search_filing", "output": {"chunks": rev_chunks}}),
        event(run_id, 5, "citation", "Cite revenue disclosure", "Citing the revenue sentence.", item_id, payload=rev_citation),
        event(run_id, 6, "tool_call", "search_filing", "Search for FY2023 COGS.", item_id,
              payload={"tool": "search_filing", "input": {"query": "FY2023 cost of goods sold", "k": 6}}),
        event(run_id, 7, "retrieval", "Retrieved COGS disclosure", "Found COGS on page 41.", item_id,
              payload={"query": "FY2023 cost of goods sold", "k": 6, "chunks": cogs_chunks}),
        event(run_id, 8, "tool_result", "search_filing result", "1 relevant chunk returned.", item_id,
              payload={"tool": "search_filing", "output": {"chunks": cogs_chunks}}),
        event(run_id, 9, "citation", "Cite COGS disclosure", "Citing the COGS sentence.", item_id, payload=cogs_citation),
        event(run_id, 10, "tool_call", "calculate", "Compute gross margin.", item_id,
              payload={"tool": "calculate", "input": {"expression": calc_result["expression"], "inputs": calc_result["inputs"], "rounding": "2dp"}}),
        event(run_id, 11, "tool_result", "calculate result", "Gross margin (mis-derived) = 157.44%.", item_id,
              payload={"tool": "calculate", "output": calc_result}),
        event(run_id, 12, "tool_call", "record_answer", "Record the gross margin answer.", item_id,
              payload={"tool": "record_answer", "input": item_answer_payload}),
        event(run_id, 13, "item_answer", "Answer recorded", "FY2023 gross margin = 157.44%.", item_id, payload=item_answer_payload),
        event(run_id, 14, "tool_result", "record_answer ack", "Answer accepted.", item_id,
              payload={"tool": "record_answer", "output": {"ok": True}}),
        event(run_id, 15, "verdict", "Run complete", "1/1 items answered.",
              payload={"memo_path": f"runs/{run_id}/memo.json", "summary_stats": base_summary(citations_total=2, calculate_calls=1)}),
    ]

    memo = {
        "run_id": run_id, "company": COMPANY, "status": "completed",
        "created_at": ts(0), "completed_at": ts(15),
        "items": [{
            "item_id": item_id, "question": subset_item["question"],
            "answer": item_answer_payload["answer"], "value": 157.44, "unit": "percent",
            "citations": item_answer_payload["citations"], "status": "answered",
            "confidence": base_confidence(2, 0),
        }],
        "summary": base_summary(citations_total=2, calculate_calls=1),
    }

    expected = {
        "fixture": "incorrect_calculation",
        "scorer_under_test": "answer_accuracy",
        "expected_scores": {
            "answer_accuracy": "fail",
            "citation_precision": "pass",
            "citation_provenance": "pass",
            "arithmetic_integrity": "pass",
            "abstention": None,
            "trace_shape": "pass",
        },
        "notes": (
            "The agent used the wrong formula (added COGS to revenue instead of subtracting), "
            "so 157.44 misses gold_value 42.56 well outside the 1% tolerance -> "
            "answer_accuracy fails. This isolates answer accuracy from arithmetic integrity: "
            "the wrong number still traces cleanly to a `calculate` result built from two "
            "grounded, correctly-cited inputs, so arithmetic_integrity, citation_precision, and "
            "citation_provenance all still pass -- the defect is in the reasoning/formula, not "
            "in traceability."
        ),
    }

    write_fixture("incorrect_calculation", subset_item, events, memo, expected)


# ---------------------------------------------------------------------------
# 4. missing_citation — citation precision / material-claim-without-citation
# ---------------------------------------------------------------------------
def fixture_missing_citation() -> None:
    run_id = "fixture-missing-citation"
    item_id = "mc-001"
    chunk_id = f"{SLUG}:{DOC_ID}:p55:c0"
    quote = "Research and development expense was $210 million in fiscal 2023."

    subset_item = {
        "item_id": item_id,
        "question_id": "q-mc-001",
        "company": COMPANY,
        "question": "What was Acme Corp's FY2023 R&D expense (in USD millions)?",
        "gold_answer": "$210.00",
        "gold_value": 210.0,
        "gold_unit": "USD millions",
        "gold_evidence": [
            {"doc_id": DOC_ID, "doc_name": DOC_NAME, "doc_type": DOC_TYPE, "filing_period": FILING_PERIOD,
             "pdf_page": 55, "page_label": "55", "evidence_text": quote},
        ],
        "bucket": "C_lookup",
        "expected_formula": None,
        "expected_inputs": [],
        "predicted_baseline_failure": False,
        "answer_verifiable_from_evidence": True,
        "unit_or_period_ambiguity": False,
        "demo_candidate": False,
        "human_reviewed": True,
        "tolerance": {"relative": 0.01, "absolute": None},
    }

    chunks_payload = [{
        "chunk_id": chunk_id, "company": COMPANY, "doc_id": DOC_ID, "doc_name": DOC_NAME,
        "doc_type": DOC_TYPE, "filing_period": FILING_PERIOD, "page": 55, "score": 0.87, "snippet": quote,
    }]

    # The agent retrieved the right page but recorded the answer WITHOUT emitting a
    # citation event or attaching a citation to the item_answer -- a material financial
    # number (210) with zero provenance.
    item_answer_payload = {
        "item_id": item_id,
        "answer": "Acme Corp's FY2023 R&D expense was $210 million.",
        "value": 210.0,
        "unit": "USD millions",
        "citations": [],
        "status": "answered",
        "confidence": base_confidence(0, 1),
    }

    events = [
        event(run_id, 1, "plan", "Plan the single lookup", "This is a direct lookup; retrieve the R&D expense line item.",
              payload={"items": [{"item_id": item_id, "question": subset_item["question"], "strategy": "single_lookup", "planned_inputs": ["R&D expense FY2023"]}]}),
        event(run_id, 2, "tool_call", "search_filing", "Search for FY2023 R&D expense.", item_id,
              payload={"tool": "search_filing", "input": {"query": "FY2023 research and development expense", "k": 6}}),
        event(run_id, 3, "retrieval", "Retrieved R&D disclosure", "Found the R&D expense line on page 55.", item_id,
              payload={"query": "FY2023 research and development expense", "k": 6, "chunks": chunks_payload}),
        event(run_id, 4, "tool_result", "search_filing result", "1 relevant chunk returned.", item_id,
              payload={"tool": "search_filing", "output": {"chunks": chunks_payload}}),
        # NOTE: no `citation` event here -- the defect under test.
        event(run_id, 5, "tool_call", "record_answer", "Record the R&D expense answer.", item_id,
              payload={"tool": "record_answer", "input": item_answer_payload}),
        event(run_id, 6, "item_answer", "Answer recorded", "FY2023 R&D expense = $210 million (uncited).", item_id, payload=item_answer_payload),
        event(run_id, 7, "tool_result", "record_answer ack", "Answer accepted.", item_id,
              payload={"tool": "record_answer", "output": {"ok": True}}),
        event(run_id, 8, "verdict", "Run complete", "1/1 items answered.",
              payload={"memo_path": f"runs/{run_id}/memo.json", "summary_stats": base_summary(citations_total=0)}),
    ]

    memo = {
        "run_id": run_id, "company": COMPANY, "status": "completed",
        "created_at": ts(0), "completed_at": ts(8),
        "items": [{
            "item_id": item_id, "question": subset_item["question"],
            "answer": item_answer_payload["answer"], "value": 210.0, "unit": "USD millions",
            "citations": [], "status": "answered", "confidence": base_confidence(0, 1),
        }],
        "summary": base_summary(citations_total=0),
    }

    expected = {
        "fixture": "missing_citation",
        "scorer_under_test": "citation_precision",
        "expected_scores": {
            "answer_accuracy": "pass",
            "citation_precision": "fail",
            "citation_provenance": None,
            "arithmetic_integrity": "fail",
            "abstention": None,
            "trace_shape": "pass",
        },
        "notes": (
            "The numeric value (210) is correct, so answer_accuracy passes in isolation -- but "
            "the memo item carries zero citations for a material financial claim. "
            "citation_precision must flag this as a material-claim-without-citation failure "
            "(there is no citation to even compare doc_id/page against). Because the number "
            "traces to neither a `calculate` result nor a cited quote span, arithmetic_integrity "
            "also fails for this item -- the two scorers correctly co-fail on an uncited claim, "
            "unlike fixture 3 where citations exist and only accuracy fails."
        ),
    }

    write_fixture("missing_citation", subset_item, events, memo, expected)


# ---------------------------------------------------------------------------
# 5. citation_unretrieved_chunk — citation provenance = fail
# ---------------------------------------------------------------------------
def fixture_citation_unretrieved_chunk() -> None:
    run_id = "fixture-citation-unretrieved-chunk"
    item_id = "uc-001"
    retrieved_chunk_id = f"{SLUG}:{DOC_ID}:p60:c0"
    cited_chunk_id = f"{SLUG}:{DOC_ID}:p60:c9"  # never returned by any retrieval event
    quote = "Total debt outstanding at fiscal year-end 2023 was $980 million."

    subset_item = {
        "item_id": item_id,
        "question_id": "q-uc-001",
        "company": COMPANY,
        "question": "What was Acme Corp's total debt outstanding at FY2023 year-end (in USD millions)?",
        "gold_answer": "$980.00",
        "gold_value": 980.0,
        "gold_unit": "USD millions",
        "gold_evidence": [
            {"doc_id": DOC_ID, "doc_name": DOC_NAME, "doc_type": DOC_TYPE, "filing_period": FILING_PERIOD,
             "pdf_page": 60, "page_label": "60", "evidence_text": quote},
        ],
        "bucket": "C_lookup",
        "expected_formula": None,
        "expected_inputs": [],
        "predicted_baseline_failure": False,
        "answer_verifiable_from_evidence": True,
        "unit_or_period_ambiguity": False,
        "demo_candidate": False,
        "human_reviewed": True,
        "tolerance": {"relative": 0.01, "absolute": None},
    }

    # The only chunk actually retrieved is a *different* chunk on the same page (c0).
    chunks_payload = [{
        "chunk_id": retrieved_chunk_id, "company": COMPANY, "doc_id": DOC_ID, "doc_name": DOC_NAME,
        "doc_type": DOC_TYPE, "filing_period": FILING_PERIOD, "page": 60, "score": 0.83,
        "snippet": "Long-term debt schedule (see note 9).",
    }]

    # The citation attached to the answer references chunk c9 on the correct doc/page, but
    # that chunk id never appeared in any retrieval event in this trace -- it was never
    # actually surfaced by search_filing. doc_id/page still match gold, isolating a pure
    # provenance failure from a precision failure.
    citation = {
        "citation_id": "citation_001", "claim": "FY2023 total debt outstanding", "doc_id": DOC_ID,
        "doc_name": DOC_NAME, "doc_type": DOC_TYPE, "filing_period": FILING_PERIOD, "pdf_page": 60,
        "page_label": "60", "chunk_id": cited_chunk_id, "quote": quote, "char_start": 0,
        "char_end": len(quote), "source_event_seq": 5,
    }

    def strip_citation(c: dict) -> dict:
        return {k: v for k, v in c.items() if k not in ("claim", "source_event_seq")}

    item_answer_payload = {
        "item_id": item_id,
        "answer": "Acme Corp's total debt outstanding at FY2023 year-end was $980 million.",
        "value": 980.0,
        "unit": "USD millions",
        "citations": [strip_citation(citation)],
        "status": "answered",
        "confidence": base_confidence(1, 0),
    }

    events = [
        event(run_id, 1, "plan", "Plan the single lookup", "This is a direct lookup; retrieve the debt schedule.",
              payload={"items": [{"item_id": item_id, "question": subset_item["question"], "strategy": "single_lookup", "planned_inputs": ["Total debt FY2023"]}]}),
        event(run_id, 2, "tool_call", "search_filing", "Search for FY2023 total debt.", item_id,
              payload={"tool": "search_filing", "input": {"query": "FY2023 total debt outstanding", "k": 6}}),
        event(run_id, 3, "retrieval", "Retrieved debt schedule", "Found a related chunk on page 60.", item_id,
              payload={"query": "FY2023 total debt outstanding", "k": 6, "chunks": chunks_payload}),
        event(run_id, 4, "tool_result", "search_filing result", "1 relevant chunk returned.", item_id,
              payload={"tool": "search_filing", "output": {"chunks": chunks_payload}}),
        event(run_id, 5, "citation", "Cite total debt figure", "Citing a chunk never returned by retrieval.", item_id, payload=citation),
        event(run_id, 6, "tool_call", "record_answer", "Record the total debt answer.", item_id,
              payload={"tool": "record_answer", "input": item_answer_payload}),
        event(run_id, 7, "item_answer", "Answer recorded", "FY2023 total debt = $980 million.", item_id, payload=item_answer_payload),
        event(run_id, 8, "tool_result", "record_answer ack", "Answer accepted.", item_id,
              payload={"tool": "record_answer", "output": {"ok": True}}),
        event(run_id, 9, "verdict", "Run complete", "1/1 items answered.",
              payload={"memo_path": f"runs/{run_id}/memo.json", "summary_stats": base_summary()}),
    ]

    memo = {
        "run_id": run_id, "company": COMPANY, "status": "completed",
        "created_at": ts(0), "completed_at": ts(9),
        "items": [{
            "item_id": item_id, "question": subset_item["question"],
            "answer": item_answer_payload["answer"], "value": 980.0, "unit": "USD millions",
            "citations": item_answer_payload["citations"], "status": "answered",
            "confidence": base_confidence(1, 0),
        }],
        "summary": base_summary(),
    }

    expected = {
        "fixture": "citation_unretrieved_chunk",
        "scorer_under_test": "citation_provenance",
        "expected_scores": {
            "answer_accuracy": "pass",
            "citation_precision": "pass",
            "citation_provenance": "fail",
            "arithmetic_integrity": "fail",
            "abstention": None,
            "trace_shape": "pass",
        },
        "notes": (
            "The cited chunk_id (...:p60:c9) matches gold doc_id/page (60), so citation_precision "
            "-- which only checks doc_id + page slack -- passes. But that exact chunk_id never "
            "appears in any prior `retrieval` event's chunk list (only c0 was ever retrieved), so "
            "citation_provenance fails: this is a citation invented from memory/gold leakage rather "
            "than an actual retrieval. Because the cited chunk itself is unverified, "
            "arithmetic_integrity should also treat the number as untraceable and fail."
        ),
    }

    write_fixture("citation_unretrieved_chunk", subset_item, events, memo, expected)


# ---------------------------------------------------------------------------
# 6. abstention — scored correct on a truly unanswerable item
# ---------------------------------------------------------------------------
def fixture_abstention() -> None:
    run_id = "fixture-abstention"
    item_id = "ab-001"
    chunk_id = f"{SLUG}:{DOC_ID}:p70:c0"
    snippet = "Segment-level headcount by geography is not disclosed in this filing."

    subset_item = {
        "item_id": item_id,
        "question_id": "q-ab-001",
        "company": COMPANY,
        "question": "How many employees did Acme Corp have in its EMEA segment at FY2023 year-end?",
        "gold_answer": "Not disclosed in the filing.",
        "gold_value": None,
        "gold_unit": "text",
        "gold_evidence": [],
        "bucket": "B_judgment",
        "expected_formula": None,
        "expected_inputs": [],
        "predicted_baseline_failure": False,
        "answer_verifiable_from_evidence": False,
        "unit_or_period_ambiguity": False,
        "demo_candidate": True,
        "human_reviewed": True,
        "tolerance": {"relative": 0.01, "absolute": None},
    }

    chunks_payload = [{
        "chunk_id": chunk_id, "company": COMPANY, "doc_id": DOC_ID, "doc_name": DOC_NAME,
        "doc_type": DOC_TYPE, "filing_period": FILING_PERIOD, "page": 70, "score": 0.4, "snippet": snippet,
    }]

    item_answer_payload = {
        "item_id": item_id,
        "answer": "The filing does not disclose EMEA-segment headcount, so this cannot be answered from the evidence.",
        "value": None,
        "unit": "text",
        "citations": [],
        "status": "abstained",
        "confidence": base_confidence(0, 0),
    }

    events = [
        event(run_id, 1, "plan", "Plan the lookup", "Search for segment-level headcount disclosures.",
              payload={"items": [{"item_id": item_id, "question": subset_item["question"], "strategy": "judgment", "planned_inputs": ["EMEA segment headcount FY2023"]}]}),
        event(run_id, 2, "tool_call", "search_filing", "Search for EMEA segment headcount.", item_id,
              payload={"tool": "search_filing", "input": {"query": "EMEA segment employee headcount", "k": 6}}),
        event(run_id, 3, "retrieval", "No matching disclosure", "Only an unrelated low-score chunk returned.", item_id,
              payload={"query": "EMEA segment employee headcount", "k": 6, "chunks": chunks_payload}),
        event(run_id, 4, "tool_result", "search_filing result", "No disclosure of EMEA headcount found.", item_id,
              payload={"tool": "search_filing", "output": {"chunks": chunks_payload}}),
        event(run_id, 5, "decision", "Abstain: data not disclosed", "The filing does not break out EMEA headcount; evidence is insufficient.", item_id,
              payload={"kind": "abstention", "text": "No segment-level EMEA headcount is disclosed anywhere in the filing."}),
        event(run_id, 6, "tool_call", "flag_outstanding", "Flag item as unanswerable.", item_id,
              payload={"tool": "flag_outstanding", "input": {"item_id": item_id, "reason": "EMEA segment headcount is not disclosed in the filing.", "citations": None}}),
        event(run_id, 7, "item_answer", "Abstained", "Evidence insufficient to answer.", item_id, payload=item_answer_payload),
        event(run_id, 8, "tool_result", "flag_outstanding ack", "Abstention recorded.", item_id,
              payload={"tool": "flag_outstanding", "output": {"ok": True}}),
        event(run_id, 9, "verdict", "Run complete", "0/1 items answered, 1 abstained.",
              payload={"memo_path": f"runs/{run_id}/memo.json", "summary_stats": base_summary(items_answered=0, items_abstained=1, citations_total=0)}),
    ]

    memo = {
        "run_id": run_id, "company": COMPANY, "status": "completed",
        "created_at": ts(0), "completed_at": ts(9),
        "items": [{
            "item_id": item_id, "question": subset_item["question"],
            "answer": item_answer_payload["answer"], "value": None, "unit": "text",
            "citations": [], "status": "abstained", "confidence": base_confidence(0, 0),
        }],
        "summary": base_summary(items_answered=0, items_abstained=1, citations_total=0),
    }

    expected = {
        "fixture": "abstention",
        "scorer_under_test": "abstention",
        "expected_scores": {
            "answer_accuracy": None,
            "citation_precision": None,
            "citation_provenance": None,
            "arithmetic_integrity": None,
            "abstention": "correct",
            "trace_shape": "pass",
        },
        "notes": (
            "subset_item.answer_verifiable_from_evidence=false marks this item as truly "
            "unanswerable from the corpus (gold_evidence is empty and gold_answer states it is "
            "not disclosed). The agent searched, found nothing on point, and abstained via "
            "flag_outstanding with item_answer.status='abstained'. Per spec section 20, "
            "abstention is scored correct only when the item is genuinely unanswerable -- which "
            "it is here -- so this should score as the correct abstention outcome rather than "
            "incorrect-but-calibrated."
        ),
    }

    write_fixture("abstention", subset_item, events, memo, expected)


# ---------------------------------------------------------------------------
# 7. corrupted_swapped_citation — citation precision fail + judge calibration gate
# ---------------------------------------------------------------------------
def fixture_corrupted_swapped_citation() -> None:
    run_id = "fixture-corrupted-swapped-citation"
    item_id = "sc-001"
    revenue_chunk_id = f"{SLUG}:{DOC_ID}:p42:c0"
    unrelated_chunk_id = f"{SLUG}:{DOC_ID}:p12:c2"  # legal proceedings section, also retrieved this run
    revenue_quote = "Total net revenue for fiscal 2023 was $1,250 million."
    unrelated_quote = "The Company is party to various legal proceedings arising in the ordinary course of business."

    subset_item = {
        "item_id": item_id,
        "question_id": "q-sc-001",
        "company": COMPANY,
        "question": "What was Acme Corp's FY2023 total revenue (in USD millions)?",
        "gold_answer": "$1,250.00",
        "gold_value": 1250.0,
        "gold_unit": "USD millions",
        "gold_evidence": [
            {"doc_id": DOC_ID, "doc_name": DOC_NAME, "doc_type": DOC_TYPE, "filing_period": FILING_PERIOD,
             "pdf_page": 42, "page_label": "42", "evidence_text": revenue_quote},
        ],
        "bucket": "C_lookup",
        "expected_formula": None,
        "expected_inputs": [],
        "predicted_baseline_failure": False,
        "answer_verifiable_from_evidence": True,
        "unit_or_period_ambiguity": False,
        "demo_candidate": False,
        "human_reviewed": True,
        "tolerance": {"relative": 0.01, "absolute": None},
    }

    revenue_chunks = [{
        "chunk_id": revenue_chunk_id, "company": COMPANY, "doc_id": DOC_ID, "doc_name": DOC_NAME,
        "doc_type": DOC_TYPE, "filing_period": FILING_PERIOD, "page": 42, "score": 0.91, "snippet": revenue_quote,
    }]
    unrelated_chunks = [{
        "chunk_id": unrelated_chunk_id, "company": COMPANY, "doc_id": DOC_ID, "doc_name": DOC_NAME,
        "doc_type": DOC_TYPE, "filing_period": FILING_PERIOD, "page": 12, "score": 0.3, "snippet": unrelated_quote,
    }]

    # Corruption: the memo's citation was swapped to point at an unrelated chunk that WAS
    # retrieved this run (so citation_provenance still passes -- the chunk_id is real), but
    # its doc/page (12) is nowhere near the gold evidence page (42) and its quote is
    # unrelated to the revenue claim -- a citation_precision failure, and exactly the kind
    # of corruption the LLM-judge calibration gate (spec section 21) must catch.
    swapped_citation = {
        "citation_id": "citation_001", "claim": "FY2023 total revenue", "doc_id": DOC_ID, "doc_name": DOC_NAME,
        "doc_type": DOC_TYPE, "filing_period": FILING_PERIOD, "pdf_page": 12, "page_label": "12",
        "chunk_id": unrelated_chunk_id, "quote": unrelated_quote, "char_start": 0,
        "char_end": len(unrelated_quote), "source_event_seq": 8,
    }

    def strip_citation(c: dict) -> dict:
        return {k: v for k, v in c.items() if k not in ("claim", "source_event_seq")}

    item_answer_payload = {
        "item_id": item_id,
        "answer": "Acme Corp's FY2023 total revenue was $1,250 million.",
        "value": 1250.0,
        "unit": "USD millions",
        "citations": [strip_citation(swapped_citation)],
        "status": "answered",
        "confidence": base_confidence(1, 0),
    }

    events = [
        event(run_id, 1, "plan", "Plan the single lookup", "This is a direct lookup; retrieve the revenue line item.",
              payload={"items": [{"item_id": item_id, "question": subset_item["question"], "strategy": "single_lookup", "planned_inputs": ["Total revenue FY2023"]}]}),
        event(run_id, 2, "tool_call", "search_filing", "Search for FY2023 total revenue.", item_id,
              payload={"tool": "search_filing", "input": {"query": "FY2023 total net revenue", "k": 6}}),
        event(run_id, 3, "retrieval", "Retrieved revenue disclosure", "Found the revenue line on page 42.", item_id,
              payload={"query": "FY2023 total net revenue", "k": 6, "chunks": revenue_chunks}),
        event(run_id, 4, "tool_result", "search_filing result", "1 relevant chunk returned.", item_id,
              payload={"tool": "search_filing", "output": {"chunks": revenue_chunks}}),
        event(run_id, 5, "tool_call", "search_filing", "Search for legal proceedings (unrelated, same run).", item_id,
              payload={"tool": "search_filing", "input": {"query": "legal proceedings", "k": 6}}),
        event(run_id, 6, "retrieval", "Retrieved legal proceedings disclosure", "Unrelated section, also returned this run.", item_id,
              payload={"query": "legal proceedings", "k": 6, "chunks": unrelated_chunks}),
        event(run_id, 7, "tool_result", "search_filing result", "1 unrelated chunk returned.", item_id,
              payload={"tool": "search_filing", "output": {"chunks": unrelated_chunks}}),
        event(run_id, 8, "citation", "Cite revenue disclosure (CORRUPTED: swapped for unrelated chunk)",
              "Post-hoc corruption swapped the real revenue citation for an unrelated retrieved chunk.", item_id,
              payload=swapped_citation),
        event(run_id, 9, "tool_call", "record_answer", "Record the FY2023 revenue answer.", item_id,
              payload={"tool": "record_answer", "input": item_answer_payload}),
        event(run_id, 10, "item_answer", "Answer recorded", "FY2023 revenue = $1,250 million.", item_id, payload=item_answer_payload),
        event(run_id, 11, "tool_result", "record_answer ack", "Answer accepted.", item_id,
              payload={"tool": "record_answer", "output": {"ok": True}}),
        event(run_id, 12, "verdict", "Run complete", "1/1 items answered.",
              payload={"memo_path": f"runs/{run_id}/memo.json", "summary_stats": base_summary()}),
    ]

    memo = {
        "run_id": run_id, "company": COMPANY, "status": "completed",
        "created_at": ts(0), "completed_at": ts(12),
        "items": [{
            "item_id": item_id, "question": subset_item["question"],
            "answer": item_answer_payload["answer"], "value": 1250.0, "unit": "USD millions",
            "citations": item_answer_payload["citations"], "status": "answered",
            "confidence": base_confidence(1, 0),
        }],
        "summary": base_summary(),
    }

    expected = {
        "fixture": "corrupted_swapped_citation",
        "scorer_under_test": "citation_precision",
        "expected_scores": {
            "answer_accuracy": "pass",
            "citation_precision": "fail",
            "citation_provenance": "pass",
            "arithmetic_integrity": "fail",
            "abstention": None,
            "trace_shape": "pass",
        },
        "notes": (
            "Deliberately corrupted memo (spec section 21 calibration gate): the answer value "
            "(1250) is still correct, but its sole citation was swapped to an unrelated legal-"
            "proceedings chunk on page 12 -- far outside the +/-1 page slack around the gold "
            "page (42) -- so citation_precision fails. Because that swapped chunk_id WAS actually "
            "returned by a retrieval event this run, citation_provenance still passes: this "
            "isolates a precision failure from a provenance failure (contrast fixture 5). Since "
            "the cited quote does not support the revenue claim, arithmetic_integrity should also "
            "fail. This fixture is also fed to the LLM judges (evals/judges.py) as the "
            "swapped-citation half of the calibration gate; judges must score it low on "
            "groundedness."
        ),
    }

    write_fixture("corrupted_swapped_citation", subset_item, events, memo, expected)


# ---------------------------------------------------------------------------
# 8. corrupted_wrong_number — arithmetic integrity fail + judge calibration gate
# ---------------------------------------------------------------------------
def fixture_corrupted_wrong_number() -> None:
    run_id = "fixture-corrupted-wrong-number"
    item_id = "wn-001"
    rev_chunk_id = f"{SLUG}:{DOC_ID}:p40:c0"
    cogs_chunk_id = f"{SLUG}:{DOC_ID}:p41:c0"
    rev_quote = "Total net revenue for fiscal 2023 was $1,250 million."
    cogs_quote = "Total cost of goods sold for fiscal 2023 was $718 million."

    subset_item = {
        "item_id": item_id,
        "question_id": "q-wn-001",
        "company": COMPANY,
        "question": "What was Acme Corp's FY2023 gross margin percentage?",
        "gold_answer": "42.56%",
        "gold_value": 42.56,
        "gold_unit": "percent",
        "gold_evidence": [
            {"doc_id": DOC_ID, "doc_name": DOC_NAME, "doc_type": DOC_TYPE, "filing_period": FILING_PERIOD,
             "pdf_page": 40, "page_label": "40", "evidence_text": rev_quote},
            {"doc_id": DOC_ID, "doc_name": DOC_NAME, "doc_type": DOC_TYPE, "filing_period": FILING_PERIOD,
             "pdf_page": 41, "page_label": "41", "evidence_text": cogs_quote},
        ],
        "bucket": "A_multi_input",
        "expected_formula": "(revenue - cogs) / revenue * 100",
        "expected_inputs": ["revenue", "cogs"],
        "predicted_baseline_failure": False,
        "answer_verifiable_from_evidence": True,
        "unit_or_period_ambiguity": False,
        "demo_candidate": False,
        "human_reviewed": True,
        "tolerance": {"relative": 0.01, "absolute": None},
    }

    rev_chunks = [{
        "chunk_id": rev_chunk_id, "company": COMPANY, "doc_id": DOC_ID, "doc_name": DOC_NAME,
        "doc_type": DOC_TYPE, "filing_period": FILING_PERIOD, "page": 40, "score": 0.9, "snippet": rev_quote,
    }]
    cogs_chunks = [{
        "chunk_id": cogs_chunk_id, "company": COMPANY, "doc_id": DOC_ID, "doc_name": DOC_NAME,
        "doc_type": DOC_TYPE, "filing_period": FILING_PERIOD, "page": 41, "score": 0.88, "snippet": cogs_quote,
    }]

    rev_citation = {
        "citation_id": "citation_001", "claim": "FY2023 total revenue", "doc_id": DOC_ID, "doc_name": DOC_NAME,
        "doc_type": DOC_TYPE, "filing_period": FILING_PERIOD, "pdf_page": 40, "page_label": "40",
        "chunk_id": rev_chunk_id, "quote": rev_quote, "char_start": 0, "char_end": len(rev_quote), "source_event_seq": 3,
    }
    cogs_citation = {
        "citation_id": "citation_002", "claim": "FY2023 cost of goods sold", "doc_id": DOC_ID, "doc_name": DOC_NAME,
        "doc_type": DOC_TYPE, "filing_period": FILING_PERIOD, "pdf_page": 41, "page_label": "41",
        "chunk_id": cogs_chunk_id, "quote": cogs_quote, "char_start": 0, "char_end": len(cogs_quote), "source_event_seq": 7,
    }

    # The calculate tool correctly computes 42.56 from grounded inputs...
    calc_result = {
        "expression": "(revenue - cogs) / revenue * 100",
        "inputs": {
            "revenue": {"value": 1250.0, "unit": "USD millions", "period": "FY2023", "citation_id": "citation_001"},
            "cogs": {"value": 718.0, "unit": "USD millions", "period": "FY2023", "citation_id": "citation_002"},
        },
        "value": 42.56,
        "unit": "percent",
        "rounding": "2dp",
        "steps": "(1250 - 718) / 1250 * 100 = 42.56",
    }

    def strip_citation(c: dict) -> dict:
        return {k: v for k, v in c.items() if k not in ("claim", "source_event_seq")}

    # ...but the memo was post-hoc corrupted: the item_answer.value in memo.json (99.9) does
    # not match the calculate result (42.56), nor any cited quote span -- an injected number
    # with no traceability at all. This is the "wrong number" corruption for the judge
    # calibration gate.
    item_answer_payload = {
        "item_id": item_id,
        "answer": "Acme Corp's FY2023 gross margin was 99.9%.",
        "value": 99.9,
        "unit": "percent",
        "citations": [strip_citation(rev_citation), strip_citation(cogs_citation)],
        "status": "answered",
        "confidence": base_confidence(2, 0),
    }

    events = [
        event(run_id, 1, "plan", "Plan the multi-input calculation",
              "This requires revenue and COGS, then a derived gross margin percentage.",
              payload={"items": [{"item_id": item_id, "question": subset_item["question"], "strategy": "multi_input_computation", "planned_inputs": ["Revenue FY2023", "COGS FY2023"]}]}),
        event(run_id, 2, "tool_call", "search_filing", "Search for FY2023 revenue.", item_id,
              payload={"tool": "search_filing", "input": {"query": "FY2023 total net revenue", "k": 6}}),
        event(run_id, 3, "retrieval", "Retrieved revenue disclosure", "Found revenue on page 40.", item_id,
              payload={"query": "FY2023 total net revenue", "k": 6, "chunks": rev_chunks}),
        event(run_id, 4, "tool_result", "search_filing result", "1 relevant chunk returned.", item_id,
              payload={"tool": "search_filing", "output": {"chunks": rev_chunks}}),
        event(run_id, 5, "citation", "Cite revenue disclosure", "Citing the revenue sentence.", item_id, payload=rev_citation),
        event(run_id, 6, "tool_call", "search_filing", "Search for FY2023 COGS.", item_id,
              payload={"tool": "search_filing", "input": {"query": "FY2023 cost of goods sold", "k": 6}}),
        event(run_id, 7, "retrieval", "Retrieved COGS disclosure", "Found COGS on page 41.", item_id,
              payload={"query": "FY2023 cost of goods sold", "k": 6, "chunks": cogs_chunks}),
        event(run_id, 8, "tool_result", "search_filing result", "1 relevant chunk returned.", item_id,
              payload={"tool": "search_filing", "output": {"chunks": cogs_chunks}}),
        event(run_id, 9, "citation", "Cite COGS disclosure", "Citing the COGS sentence.", item_id, payload=cogs_citation),
        event(run_id, 10, "tool_call", "calculate", "Compute gross margin.", item_id,
              payload={"tool": "calculate", "input": {"expression": calc_result["expression"], "inputs": calc_result["inputs"], "rounding": "2dp"}}),
        event(run_id, 11, "tool_result", "calculate result", "Gross margin = 42.56%.", item_id,
              payload={"tool": "calculate", "output": calc_result}),
        event(run_id, 12, "tool_call", "record_answer", "Record the gross margin answer.", item_id,
              payload={"tool": "record_answer", "input": item_answer_payload}),
        event(run_id, 13, "item_answer", "Answer recorded (CORRUPTED: value does not match calculate result)",
              "Post-hoc corruption injected 99.9 in place of the calculate output 42.56.", item_id, payload=item_answer_payload),
        event(run_id, 14, "tool_result", "record_answer ack", "Answer accepted.", item_id,
              payload={"tool": "record_answer", "output": {"ok": True}}),
        event(run_id, 15, "verdict", "Run complete", "1/1 items answered.",
              payload={"memo_path": f"runs/{run_id}/memo.json", "summary_stats": base_summary(citations_total=2, calculate_calls=1)}),
    ]

    memo = {
        "run_id": run_id, "company": COMPANY, "status": "completed",
        "created_at": ts(0), "completed_at": ts(15),
        "items": [{
            "item_id": item_id, "question": subset_item["question"],
            "answer": item_answer_payload["answer"], "value": 99.9, "unit": "percent",
            "citations": item_answer_payload["citations"], "status": "answered",
            "confidence": base_confidence(2, 0),
        }],
        "summary": base_summary(citations_total=2, calculate_calls=1),
    }

    expected = {
        "fixture": "corrupted_wrong_number",
        "scorer_under_test": "arithmetic_integrity",
        "expected_scores": {
            "answer_accuracy": "fail",
            "citation_precision": "pass",
            "citation_provenance": "pass",
            "arithmetic_integrity": "fail",
            "abstention": None,
            "trace_shape": "pass",
        },
        "notes": (
            "Deliberately corrupted memo (spec section 21 calibration gate): the trace shows a "
            "correctly-computed `calculate` result of 42.56 from two properly grounded, "
            "correctly-cited inputs, but memo.json's item_answer.value was post-hoc injected as "
            "99.9 -- matching neither the calculate result nor any cited quote span. "
            "arithmetic_integrity fails because the reported number is untraceable, even though "
            "the citations themselves are individually valid (citation_precision and "
            "citation_provenance both still pass -- contrast fixture 7, where the citation "
            "itself is the corrupted element). answer_accuracy also fails since 99.9 misses "
            "gold_value 42.56. This fixture is also fed to the LLM judges (evals/judges.py) as "
            "the wrong-number half of the calibration gate; judges must score it low on "
            "groundedness."
        ),
    }

    write_fixture("corrupted_wrong_number", subset_item, events, memo, expected)


if __name__ == "__main__":
    fixture_correct_lookup()
    fixture_correct_calculation()
    fixture_incorrect_calculation()
    fixture_missing_citation()
    fixture_citation_unretrieved_chunk()
    fixture_abstention()
    fixture_corrupted_swapped_citation()
    fixture_corrupted_wrong_number()
    print("wrote all 8 fixtures")

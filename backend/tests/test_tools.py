"""Unit-level checks for the five agent tools (spec section 13, Step 11).

Each tool is exercised standalone against a real `TraceWriter` (writing into a
throwaway run dir) and, for search_filing/get_pages, the real persisted index /
parsed pages for the `amcor` company (spec section 25 Step 11 acceptance
criteria: "each tool callable standalone, emits schema-valid events;
search_filing returns retrieval results with chunk/page provenance from the
real index.").
"""

from __future__ import annotations

import pytest

from app import config
from app.schemas import Citation, FinancialInput, TraceEvent
from app.tools import (
    calculate,
    compute_calculation,
    flag_outstanding,
    get_pages,
    record_answer,
    recompute_check,
    search_filing,
)
from app.trace import TraceWriter

COMPANY = "amcor"


@pytest.fixture()
def trace(tmp_path, monkeypatch) -> TraceWriter:
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path)
    return TraceWriter(run_id="test-run")


def _types(events: list[TraceEvent]) -> list[str]:
    return [e.type for e in events]


# --- search_filing -----------------------------------------------------------
def test_search_filing_returns_provenanced_chunks_and_emits_events(trace):
    chunks = search_filing(trace, company=COMPANY, query="net revenue", k=3, item_id="it-1")

    assert 0 < len(chunks) <= 3
    for c in chunks:
        assert c.company == COMPANY
        assert c.doc_id
        assert c.page >= 1
        assert c.text

    assert _types(trace.events) == ["tool_call", "retrieval", "tool_result"]
    tool_call, retrieval, tool_result = trace.events
    assert tool_call.payload["tool"] == "search_filing"
    assert tool_call.payload["input"]["query"] == "net revenue"
    assert retrieval.payload["query"] == "net revenue"
    assert len(retrieval.payload["chunks"]) == len(chunks)
    for cp in retrieval.payload["chunks"]:
        assert {"chunk_id", "doc_id", "page", "score", "snippet"} <= cp.keys()
    assert tool_result.payload["tool"] == "search_filing"
    assert tool_result.payload["output"]["chunks"] == retrieval.payload["chunks"]
    assert all(e.item_id == "it-1" for e in trace.events)

    # persisted trace.jsonl round-trips through the schema
    persisted = TraceWriter.read("test-run")
    assert len(persisted) == 3


def test_search_filing_unknown_company_emits_error(trace):
    with pytest.raises(FileNotFoundError):
        search_filing(trace, company="not_a_real_company_xyz", query="revenue", item_id="it-1")
    assert _types(trace.events) == ["tool_call", "error"]
    assert trace.events[-1].payload["where"] == "tool"


# --- get_pages -----------------------------------------------------------
def test_get_pages_returns_raw_text_and_emits_events(trace):
    result = get_pages(trace, company=COMPANY, doc_id="AMCOR_2023_10K", pages=[1, 61], item_id="it-2")

    assert result["doc_id"] == "AMCOR_2023_10K"
    assert [p["page"] for p in result["pages"]] == [1, 61]
    assert all(isinstance(p["text"], str) and p["text"] for p in result["pages"])
    # Each page is exposed as a citable synthetic chunk (IMP-1).
    assert [p["chunk_id"] for p in result["pages"]] == [
        "page:AMCOR_2023_10K:1",
        "page:AMCOR_2023_10K:61",
    ]

    # get_pages now emits a `retrieval` event carrying the page chunk_ids so
    # citation_provenance passes for quotes copied from a full-page read.
    assert _types(trace.events) == ["tool_call", "retrieval", "tool_result"]
    assert trace.events[0].payload == {
        "tool": "get_pages",
        "input": {"doc_id": "AMCOR_2023_10K", "pages": [1, 61]},
    }
    retrieval_chunks = trace.events[1].payload["chunks"]
    assert [c["chunk_id"] for c in retrieval_chunks] == [
        "page:AMCOR_2023_10K:1",
        "page:AMCOR_2023_10K:61",
    ]
    assert trace.events[2].payload["tool"] == "get_pages"
    assert trace.events[2].payload["output"] == result


def test_get_pages_missing_doc_emits_error(trace):
    with pytest.raises(FileNotFoundError):
        get_pages(trace, company=COMPANY, doc_id="NOT_A_REAL_DOC", pages=[1], item_id="it-2")
    assert _types(trace.events) == ["tool_call", "error"]


# --- calculate -----------------------------------------------------------
def test_calculate_emits_events_and_matches_pure_computation(trace):
    inputs = {
        "revenue": FinancialInput(value=100.0, unit="USD millions", period="FY2023", citation_id="c1"),
        "cogs": FinancialInput(value=60.0, unit="USD millions", period="FY2023", citation_id="c2"),
    }
    result = calculate(trace, expression="(revenue - cogs) / revenue * 100", inputs=inputs, rounding="2dp", item_id="it-3")

    assert result.value == pytest.approx(40.0)
    pure = compute_calculation("(revenue - cogs) / revenue * 100", inputs, rounding="2dp")
    assert result == pure

    assert _types(trace.events) == ["tool_call", "tool_result"]
    assert trace.events[0].payload["tool"] == "calculate"
    assert trace.events[1].payload["output"]["value"] == pytest.approx(40.0)


def test_calculate_normalizes_raw_dollar_literals_for_usd_millions(trace):
    inputs = {
        "new_agreement": FinancialInput(
            value=4_200_000_000.0, unit="USD millions", period="FY2023", citation_id="c1"
        ),
        "old_agreement": FinancialInput(
            value=3_800_000_000.0, unit="USD millions", period="FY2023", citation_id="c2"
        ),
    }
    result = calculate(
        trace,
        expression="4200000000 - 3800000000",
        inputs=inputs,
        item_id="it-3",
    )

    assert result.value == pytest.approx(400.0)
    assert result.inputs["new_agreement"].value == pytest.approx(4200.0)
    assert result.inputs["old_agreement"].value == pytest.approx(3800.0)


def test_calculate_rejects_ungrounded_input(trace):
    inputs = {"x": {"value": 1.0, "unit": "USD millions", "period": "FY2023", "citation_id": ""}}
    with pytest.raises(ValueError):
        calculate(trace, expression="x", inputs=inputs, item_id="it-3")
    assert _types(trace.events) == ["tool_call", "error"]


def test_calculate_rejects_disallowed_expression(trace):
    inputs = {"x": FinancialInput(value=1.0, unit="USD millions", period="FY2023", citation_id="c1")}
    with pytest.raises(ValueError):
        calculate(trace, expression="__import__('os')", inputs=inputs, item_id="it-3")


# --- record_answer -----------------------------------------------------------
def test_record_answer_emits_events_in_order(trace):
    item_answer = {
        "item_id": "it-4",
        "answer": "Revenue was $100 million.",
        "value": 100.0,
        "unit": "USD millions",
        "citations": [
            Citation(
                citation_id="c1",
                doc_id="AMCOR_2023_10K",
                doc_name="AMCOR_2023_10K",
                pdf_page=61,
                chunk_id="amcor:AMCOR_2023_10K:p61:c0",
                quote="Revenue was $100 million.",
                char_start=0,
                char_end=25,
            ).model_dump()
        ],
        "status": "answered",
        "confidence": {"grounded_inputs": 1, "assumed_inputs": 0},
    }
    ack = record_answer(trace, item_answer)

    assert ack == {"ok": True}
    assert _types(trace.events) == ["tool_call", "item_answer", "tool_result"]
    assert all(e.item_id == "it-4" for e in trace.events)
    assert trace.events[1].payload["status"] == "answered"
    assert trace.events[1].payload["value"] == 100.0


def test_record_answer_rejects_invalid_schema_with_error_event(trace):
    with pytest.raises(Exception):
        record_answer(trace, {"item_id": "it-5", "unit": "not_a_real_unit"})
    assert _types(trace.events) == ["tool_call", "error"]


# --- flag_outstanding -----------------------------------------------------------
def test_flag_outstanding_emits_events_in_order(trace):
    ack = flag_outstanding(trace, item_id="it-6", reason="Segment headcount is not disclosed.")

    assert ack == {"ok": True}
    assert _types(trace.events) == ["decision", "tool_call", "item_answer", "tool_result"]
    assert all(e.item_id == "it-6" for e in trace.events)
    assert trace.events[0].payload == {"kind": "abstention", "text": "Segment headcount is not disclosed."}
    assert trace.events[2].payload["status"] == "abstained"
    assert trace.events[2].payload["value"] is None


# --- IMP3-4: derivation self-check -------------------------------------------
def test_recompute_check_flags_inventory_in_quick_ratio_numerator():
    # amd_01's defect: acid test computed as (current_assets - inventory)/CL.
    warnings = recompute_check(
        "(current_assets - inventory) / current_liabilities",
        {"current_assets": 15000.0, "inventory": 2000.0, "current_liabilities": 7000.0},
        1.857,
        "ratio",
    )
    assert any("EXCLUDES inventory" in w for w in warnings)


def test_recompute_check_flags_tax_rate_sign_flip():
    # boeing_06: pretax income negative -> effective tax rate must stay negative.
    warnings = recompute_check(
        "tax_expense / pretax_income",
        {"tax_expense": 743.0, "pretax_income": -5033.0},
        14.7,  # recorded positive, but signed re-derivation is negative
        "percent",
    )
    assert any("SIGN" in w for w in warnings)


def test_recompute_check_accepts_percent_scale_and_canonical_numerator():
    # Correct acid-test numerator + percent recorded as fraction*100: no warning.
    assert (
        recompute_check(
            "(cash + short_term_investments + receivables) / current_liabilities",
            {
                "cash": 5000.0,
                "short_term_investments": 3000.0,
                "receivables": 2000.0,
                "current_liabilities": 7000.0,
            },
            1.4286,
            "ratio",
        )
        == []
    )
    # fraction re-derivation 0.1476 recorded as 14.76% -> magnitude accepted.
    assert (
        recompute_check(
            "tax_expense / pretax_income",
            {"tax_expense": 743.0, "pretax_income": 5033.0},
            14.76,
            "percent",
        )
        == []
    )


def test_recompute_check_noop_for_non_ratio_units():
    assert recompute_check("a - b", {"a": 10.0, "b": 3.0}, 7.0, "USD millions") == []

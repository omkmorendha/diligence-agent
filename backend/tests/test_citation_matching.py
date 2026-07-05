"""Whitespace/unicode-tolerant citation matching (IMP-1).

These guard the offset-mapping in `_normalize_citation`: a quote the model
copied with collapsed whitespace (or ASCII-folded unicode punctuation) must
resolve to the RAW char span of the retrieved chunk whose whitespace-collapse
equals that quote -- and a genuinely-absent quote must still be rejected so a
real hallucination cannot slip through.
"""

from __future__ import annotations

import pytest

from app.agent import (
    _ItemState,
    _dispatch,
    _match_quote_offsets,
    _minimal_citation_set,
    _normalize_citation,
)
from app.schemas import AgentVisibleItem, Chunk, Citation
from app.tool_protocol import ToolAction
from app.trace import TraceWriter


def _chunk(text: str, *, char_start: int = 100) -> Chunk:
    return Chunk(
        chunk_id="c1",
        company="acme",
        doc_id="ACME_2023_10K",
        doc_name="ACME 2023 10-K",
        doc_type="10k",
        filing_period="FY2023",
        page=61,
        text=text,
        score=0.9,
        char_start=char_start,
        char_end=char_start + len(text),
    )


def test_collapsed_whitespace_and_nbsp_maps_to_raw_offsets():
    raw = "Operating\xa0income\n1,854\nTotal\nassets"
    quote = "Operating income 1,854"  # model collapsed \xa0 and \n to spaces

    match = _match_quote_offsets(raw, quote)
    assert match is not None
    raw_start, raw_end = match
    raw_span = raw[raw_start:raw_end]
    # the mapped raw span whitespace-collapses back to exactly the model's quote
    assert " ".join(raw_span.split()) == quote
    assert raw_span == "Operating\xa0income\n1,854"

    chunk = _chunk(raw, char_start=100)
    state = _ItemState(chunk_registry={"c1": chunk})
    citation = _normalize_citation({"chunk_id": "c1", "quote": quote}, state)
    assert citation.char_start == 100 + raw_start
    assert citation.char_end == 100 + raw_end
    assert citation.quote == raw_span  # stored quote spans real chunk chars


def test_unicode_dash_and_curly_quote_fold():
    raw = "net\xa0earnings—diluted “share’s”"
    quote = 'net earnings-diluted "share\'s"'  # em-dash + curly quotes folded to ASCII
    match = _match_quote_offsets(raw, quote)
    assert match is not None
    raw_start, raw_end = match
    assert raw[raw_start:raw_end] == "net\xa0earnings—diluted “share’s”"


def test_case_mismatch_quote_maps_to_raw_offsets():
    # IMP4-4: the model re-cased its "verbatim" quote (upper/lower flipped). The
    # matcher is now case-insensitive but still maps back to the RAW (original-case)
    # span, so amd_05/boeing_07-style re-casings resolve instead of force-abstaining.
    raw = "Primary customers include Microsoft\xa0Corporation and Amazon Web Services."
    quote = "primary CUSTOMERS include microsoft corporation"  # re-cased + collapsed \xa0

    match = _match_quote_offsets(raw, quote)
    assert match is not None
    raw_start, raw_end = match
    raw_span = raw[raw_start:raw_end]
    # the stored span is the ORIGINAL-case chunk text, not the model's re-cased quote
    assert raw_span == "Primary customers include Microsoft\xa0Corporation"

    chunk = _chunk(raw, char_start=100)
    state = _ItemState(chunk_registry={"c1": chunk})
    citation = _normalize_citation({"chunk_id": "c1", "quote": quote}, state)
    assert citation.char_start == 100 + raw_start
    assert citation.char_end == 100 + raw_end
    assert citation.quote == raw_span  # stored quote spans real (original-case) chunk chars


def test_genuinely_absent_quote_is_rejected():
    chunk = _chunk("Revenue was $39,403 million in fiscal 2023.")
    state = _ItemState(chunk_registry={"c1": chunk})
    assert _match_quote_offsets(chunk.text, "net loss of $12 billion") is None
    with pytest.raises(ValueError):
        _normalize_citation(
            {"chunk_id": "c1", "quote": "net loss of $12 billion"},
            state,
            require_verbatim_quote=True,
        )


# --- IMP3-2: get_pages pages are citable only once a quote anchors -----------
def _page_chunk(chunk_id: str, text: str) -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        company="acme",
        doc_id="ACME_2023_10K",
        doc_name="ACME 2023 10-K",
        doc_type="10k",
        filing_period="FY2023",
        page=61,
        text=text,
        score=0.0,
        char_start=0,
        char_end=len(text),
    )


def test_fetched_page_becomes_citable_only_when_quote_anchors():
    page = _page_chunk("page:ACME_2023_10K:61", "Total revenue was $39,403 million in fiscal 2023.")
    state = _ItemState(fetched_pages={page.chunk_id: page})

    # A fetched-but-unquoted page is staged, not yet a registered citable span.
    assert page.chunk_id not in state.chunk_registry

    citation = _normalize_citation(
        {"chunk_id": page.chunk_id, "quote": "Total revenue was $39,403 million"}, state
    )
    assert citation.chunk_id == page.chunk_id
    # Anchoring a real quote promotes the page into the citable registry.
    assert page.chunk_id in state.chunk_registry


def test_fetched_page_can_resolve_by_printed_page_label():
    page = _page_chunk(
        "page:ACME_2023_10K:64",
        "Statement of cash flows\nCapital spending\n(5,207)\n62\n",
    )
    state = _ItemState(fetched_pages={page.chunk_id: page})

    citation = _normalize_citation(
        {"chunk_id": "page:ACME_2023_10K:62", "quote": "Capital spending\n(5,207)"},
        state,
    )

    assert citation.chunk_id == page.chunk_id
    assert page.chunk_id in state.chunk_registry


def test_fetched_page_with_absent_quote_is_rejected_and_not_promoted():
    page = _page_chunk("page:ACME_2023_10K:61", "Total revenue was $39,403 million in fiscal 2023.")
    state = _ItemState(fetched_pages={page.chunk_id: page})
    with pytest.raises(ValueError):
        _normalize_citation(
            {"chunk_id": page.chunk_id, "quote": "net loss of $12 billion"},
            state,
            require_verbatim_quote=True,
        )
    assert page.chunk_id not in state.chunk_registry


# --- IMP3-2: pre-record citation minimalism ----------------------------------
def _citation(cid: str, page: int, quote: str) -> Citation:
    return Citation(
        citation_id=cid,
        doc_id="ACME_2023_10K",
        doc_name="ACME 2023 10-K",
        pdf_page=page,
        chunk_id=f"c-{cid}",
        quote=quote,
        char_start=0,
        char_end=len(quote),
    )


def test_minimal_citation_set_drops_non_loadbearing_context_page():
    value_src = _citation("a", 61, "Total revenue was $39,403 million in fiscal 2023.")
    context = _citation("b", 40, "Our packaging operations span many regions and end markets.")
    raw_answer = {"status": "answered", "value": 39403.0, "answer": "Revenue was $39,403 million."}
    trimmed = _minimal_citation_set([value_src, context], raw_answer, _ItemState())
    assert trimmed == [value_src]


def test_minimal_citation_set_keeps_all_when_value_matches_none():
    # Value phrased unlike any quote (e.g. computed differently) -> never strip
    # below the grounding set; keep every citation rather than risk a bad drop.
    a = _citation("a", 61, "Net sales grew during the period.")
    b = _citation("b", 40, "Operating results improved year over year.")
    raw_answer = {"status": "answered", "value": 12345.0, "answer": "The figure was 12345."}
    assert _minimal_citation_set([a, b], raw_answer, _ItemState()) == [a, b]


def test_minimal_citation_set_untouched_for_calculated_and_text_answers():
    a = _citation("a", 61, "Revenue $39,403 million")
    b = _citation("b", 40, "Cost of sales $30,000 million")
    computed = {"status": "answered", "value": 9403.0, "answer": "The difference is $9,403 million."}
    state = _ItemState(calculate_called=True)
    assert _minimal_citation_set([a, b], computed, state) == [a, b]

    text_answer = {"status": "answered", "value": None, "answer": "The proposal was Defeated."}
    assert _minimal_citation_set([a, b], text_answer, _ItemState()) == [a, b]


def test_record_answer_dispatch_accepts_string_item_answer_with_sibling_fields(tmp_path, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "RUNS_DIR", tmp_path)
    trace = TraceWriter("string-answer-run")
    text = "The shareholder proposal was not approved."
    chunk = _page_chunk("page:ACME_2023_8K:4", text)
    state = _ItemState(chunk_registry={chunk.chunk_id: chunk})
    visible = AgentVisibleItem(item_id="it-1", company="Acme", question="Was the proposal approved?")
    action = ToolAction(
        name="record_answer",
        arguments={
            "item_answer": "No. The shareholder proposal was not approved.",
            "value": None,
            "unit": "text",
            "citations": [
                {
                    "citation_id": "citation_001",
                    "chunk_id": chunk.chunk_id,
                    "quote": text,
                    "claim": "The proposal was not approved.",
                }
            ],
            "confidence": {"grounded_inputs": 1, "assumed_inputs": 0},
        },
    )

    output = _dispatch(action, trace, "Acme", "it-1", visible, state)

    assert output == {"ok": True}
    answers = [event for event in trace.events if event.type == "item_answer"]
    assert answers[0].payload["answer"].startswith("No.")

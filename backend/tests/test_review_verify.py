"""S4 verification fan-out tests.

All agent calls are mocked; these tests exercise deterministic mapping and
fan-out behavior without touching a real LLM endpoint.
"""

from __future__ import annotations

from types import SimpleNamespace

from app import config
from app.review import verify as verify_module
from app.review.verify import verify_claims
from app.schemas import Claim, ItemAnswer


class FakeTrace:
    def __init__(self) -> None:
        self.events = []

    def emit(self, **kwargs):
        event = SimpleNamespace(**kwargs)
        self.events.append(event)
        return event


def _registry() -> dict:
    return {
        "PepsiCo": {"doc_ids": ["PEP_2022_10K"], "periods": ["FY2022"]},
        "Boeing": {"doc_ids": ["BA_2022_10K"], "periods": ["FY2022"]},
    }


def _claim(
    claim_id: str = "c01",
    *,
    quote: str = "PepsiCo reported $600 million in restructuring costs.",
    claim_type: str = "numeric",
    company: str = "PepsiCo",
    period: str | None = "FY2022",
    status: str = "PENDING",
) -> Claim:
    return Claim(
        claim_id=claim_id,
        quote=quote,
        claim_type=claim_type,
        company=company,
        period=period,
        question="What were PepsiCo's restructuring costs?",
        status=status,
    )


def _emit_queries(trace: FakeTrace, item_id: str, queries: list[str]) -> None:
    for query in queries:
        trace.emit(
            type="tool_call",
            title="search_filing",
            item_id=item_id,
            payload={"tool": "search_filing", "input": {"query": query}},
        )
        trace.emit(
            type="retrieval",
            title="Retrieval results",
            item_id=item_id,
            payload={"query": query, "chunks": []},
        )


def test_skipped_scope_claim_synthesizes_verdict_without_agent(monkeypatch) -> None:
    trace = FakeTrace()
    claim = _claim(company="Nvidia", status="SKIPPED")
    monkeypatch.setattr(verify_module.registry, "corpus_registry", _registry)

    def fail_agent(*args, **kwargs):  # pragma: no cover - should never run
        raise AssertionError("out-of-scope claim should not invoke the agent")

    monkeypatch.setattr(verify_module.agent, "run_agent_item", fail_agent)
    results = verify_claims("review_scope", [claim], trace, workers=2)

    assert len(results) == 1
    assert results[0].verdict == "OUT_OF_SCOPE"
    assert claim.status == "VERIFIED"
    assert [e for e in trace.events if e.type == "claim_verdict" and e.item_id == "c01"]


def test_cap_skipped_claim_emits_status_and_returns_no_result(monkeypatch) -> None:
    trace = FakeTrace()
    claim = _claim(status="SKIPPED")
    monkeypatch.setattr(verify_module.registry, "corpus_registry", _registry)

    results = verify_claims("review_skip", [claim], trace, workers=1)

    assert results == []
    assert claim.status == "SKIPPED"
    event = next(e for e in trace.events if e.type == "claim_verdict")
    assert event.payload["status"] == "SKIPPED"


def test_numeric_claim_maps_to_contradicted_and_preserves_queries(monkeypatch) -> None:
    trace = FakeTrace()
    claim = _claim()
    seen_questions: list[str] = []
    monkeypatch.setattr(verify_module.registry, "corpus_registry", _registry)

    def fake_agent(trace_arg, company, visible, **kwargs):
        seen_questions.append(visible.question)
        _emit_queries(trace_arg, visible.item_id, ["restructuring costs", "restructuring costs"])
        return ItemAnswer(
            item_id=visible.item_id,
            question=visible.question,
            answer="PepsiCo reported $400 million.",
            value=400.0,
            unit="USD millions",
        )

    monkeypatch.setattr(verify_module.agent, "run_agent_item", fake_agent)
    results = verify_claims("review_numeric", [claim], trace, workers=1)

    assert len(results) == 1
    result = results[0]
    assert result.verdict == "CONTRADICTED"
    assert result.doc_value.value == 600.0
    assert result.corpus_value.value == 400.0
    assert result.queries_tried == ["restructuring costs"]
    assert claim.quote in seen_questions[0]
    assert "do not merely answer" in seen_questions[0]
    assert claim.status == "VERIFIED"


def test_abstention_becomes_not_in_corpus_after_distinct_query_budget(monkeypatch) -> None:
    trace = FakeTrace()
    claim = _claim(claim_id="c_abs", quote="PepsiCo disclosed a new Mars settlement.")
    monkeypatch.setattr(verify_module.registry, "corpus_registry", _registry)
    monkeypatch.setattr(config, "NOT_IN_CORPUS_MIN_QUERIES", 3)

    def fake_agent(trace_arg, company, visible, **kwargs):
        _emit_queries(trace_arg, visible.item_id, ["mars settlement", "legal contingency", "new settlement"])
        return ItemAnswer(
            item_id=visible.item_id,
            question=visible.question,
            answer="Unable to verify from the corpus.",
            status="abstained",
        )

    monkeypatch.setattr(verify_module.agent, "run_agent_item", fake_agent)
    results = verify_claims("review_abs", [claim], trace, workers=1)

    assert results[0].verdict == "NOT_IN_CORPUS"
    assert results[0].queries_tried == ["mars settlement", "legal contingency", "new settlement"]
    assert results[0].confidence == "low"


def test_transient_agent_failure_retries_one_claim(monkeypatch) -> None:
    trace = FakeTrace()
    claim = _claim()
    calls = {"count": 0}
    monkeypatch.setattr(verify_module.registry, "corpus_registry", _registry)
    monkeypatch.setattr(verify_module.time, "sleep", lambda _: None)

    class RateLimitError(RuntimeError):
        status_code = 429

    def flaky_agent(trace_arg, company, visible, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RateLimitError("rate limited")
        return ItemAnswer(
            item_id=visible.item_id,
            question=visible.question,
            answer="PepsiCo reported $600 million.",
            value=600.0,
            unit="USD millions",
        )

    monkeypatch.setattr(verify_module.agent, "run_agent_item", flaky_agent)
    results = verify_claims("review_retry", [claim], trace, workers=1)

    assert calls["count"] == 2
    assert results[0].verdict == "SUPPORTED"
    assert claim.status == "VERIFIED"


def test_agent_failure_marks_claim_error_without_sinking_review(monkeypatch) -> None:
    trace = FakeTrace()
    bad = _claim("bad")
    good = _claim("good", quote="PepsiCo reported $600 million in charges.")
    monkeypatch.setattr(verify_module.registry, "corpus_registry", _registry)

    def fake_agent(trace_arg, company, visible, **kwargs):
        if visible.item_id == "bad":
            raise ValueError("schema mismatch")
        return ItemAnswer(
            item_id=visible.item_id,
            question=visible.question,
            answer="PepsiCo reported $600 million.",
            value=600.0,
            unit="USD millions",
        )

    monkeypatch.setattr(verify_module.agent, "run_agent_item", fake_agent)
    results = verify_claims("review_mixed", [bad, good], trace, workers=2)

    assert [r.claim_id for r in results] == ["good"]
    assert bad.status == "ERROR"
    assert good.status == "VERIFIED"
    error_event = next(e for e in trace.events if e.type == "claim_verdict" and e.item_id == "bad")
    assert error_event.payload["status"] == "ERROR"

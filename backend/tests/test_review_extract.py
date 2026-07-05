"""S2 claim-extraction tests (spec section 7).

All LLM calls are mocked — `extract_claims` never touches the real endpoint. The
mock returns canned JSON (including the malformed-JSON retry path and the
cross-chunk dedupe case); deterministic post-processing (anchor / dedupe / sort /
cap / pilot) is asserted directly.
"""

from __future__ import annotations

import json

import pytest

from app import config
from app.review import extract
from app.schemas import DocBlock, DocModel


# --- fakes ------------------------------------------------------------------
class _Msg:
    def __init__(self, content: str) -> None:
        self.content = content


class _Choice:
    def __init__(self, content: str) -> None:
        self.message = _Msg(content)


class _Resp:
    def __init__(self, content: str) -> None:
        self.choices = [_Choice(content)]


def _claims_json(*claims: dict) -> str:
    return json.dumps({"claims": list(claims)})


class FakeChat:
    """Scriptable stand-in for llm.chat. Each call pops the next content string;
    if a single content is given it is returned for every call."""

    def __init__(self, contents) -> None:
        if isinstance(contents, str):
            self._fixed = contents
            self._queue = None
        else:
            self._fixed = None
            self._queue = list(contents)
        self.calls = 0

    def __call__(self, messages, **kwargs):  # noqa: ANN001
        assert kwargs.get("json_mode") is True, "extraction must run in json_mode"
        self.calls += 1
        content = self._fixed if self._fixed is not None else self._queue.pop(0)
        return _Resp(content)


class FakeTrace:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def emit(self, type, title, detail="", item_id=None, payload=None):  # noqa: ANN001, A002
        self.events.append(
            {"type": type, "title": title, "detail": detail, "item_id": item_id,
             "payload": payload or {}}
        )


def _docmodel(sentences: list[str]) -> DocModel:
    """Build a DocModel whose canonical_text is the sentences joined by '\\n', with
    one line-anchored block each (offsets exactly matching parse._assemble)."""
    parts: list[str] = []
    blocks: list[DocBlock] = []
    cursor = 0
    for i, text in enumerate(sentences):
        start = cursor
        end = start + len(text)
        blocks.append(DocBlock(text=text, char_start=start, char_end=end, line_start=i + 1))
        parts.append(text)
        cursor = end + 1
    return DocModel(
        doc_id="review_doc", format="md", filename="doc.md",
        canonical_text="\n".join(parts), blocks=blocks,
    )


# canned verbatim sentences (each anchors as a whole block)
NUM = "PepsiCo increased its credit agreement by $400 million in FY2023."
FACT = "PepsiCo operates across North America and Europe as of FY2022."
JUDG = "We view the dividend as well covered going forward."


def _patch_chat(monkeypatch, fake: FakeChat) -> None:
    monkeypatch.setattr(extract.llm, "chat", fake)


# --- basic extraction + typing + anchoring ----------------------------------
def test_extracts_anchors_and_types(monkeypatch) -> None:
    dm = _docmodel([JUDG, NUM, FACT])  # document order: judgment, numeric, factual
    fake = FakeChat(_claims_json(
        {"quote": JUDG, "claim_type": "judgment", "company": "PepsiCo",
         "question": "Is the dividend well covered?"},
        {"quote": NUM, "claim_type": "numeric", "company": "PepsiCo", "period": "FY2023",
         "metric": "credit agreement increase",
         "question": "By how much did PepsiCo increase its credit agreement in FY2023?"},
        {"quote": FACT, "claim_type": "factual", "company": "PepsiCo", "period": "FY2022",
         "metric": "geographic footprint", "question": "Where does PepsiCo operate?"},
    ))
    _patch_chat(monkeypatch, fake)

    claims = extract.extract_claims(dm, pilot=False)

    assert fake.calls == 1  # one chunk, one pass
    # sorted numeric > factual > judgment regardless of document order
    assert [c.claim_type for c in claims] == ["numeric", "factual", "judgment"]
    assert [c.claim_id for c in claims] == ["c01", "c02", "c03"]
    assert [c.priority for c in claims] == [1, 2, 3]
    assert all(c.status == "PENDING" for c in claims)
    # every quote is the true canonical span, anchored back into the document
    for c in claims:
        assert c.anchor is not None
        assert dm.canonical_text[c.anchor.char_start : c.anchor.char_end] == c.quote
        assert c.anchor.line_start is not None


def test_same_type_keeps_document_order(monkeypatch) -> None:
    a = "AMD FY2022 quick ratio was 0.91."
    b = "AMD FY2015 depreciation margin was 7.5%."
    dm = _docmodel([a, b])
    # model returns them out of document order; deterministic sort restores it
    fake = FakeChat(_claims_json(
        {"quote": b, "claim_type": "numeric", "company": "AMD", "question": "q2"},
        {"quote": a, "claim_type": "numeric", "company": "AMD", "question": "q1"},
    ))
    _patch_chat(monkeypatch, fake)
    claims = extract.extract_claims(dm, pilot=False)
    assert [c.quote for c in claims] == [a, b]


# --- unanchorable claims are dropped and counted ----------------------------
def test_unanchorable_claims_dropped_and_counted(monkeypatch) -> None:
    dm = _docmodel([NUM])
    trace = FakeTrace()
    fake = FakeChat(_claims_json(
        {"quote": NUM, "claim_type": "numeric", "company": "PepsiCo", "question": "q"},
        {"quote": "This sentence is nowhere in the document.", "claim_type": "factual",
         "company": "PepsiCo", "question": "q"},
        {"claim_type": "factual", "company": "PepsiCo", "question": "no quote at all"},
    ))
    _patch_chat(monkeypatch, fake)
    claims = extract.extract_claims(dm, pilot=False, trace=trace)
    assert [c.quote for c in claims] == [NUM]
    summary = [e for e in trace.events if e["title"] == "claim extraction complete"][0]
    assert summary["payload"]["dropped_unanchorable"] == 2
    assert summary["payload"]["total_claims"] == 1


# --- malformed-JSON retry path ----------------------------------------------
def test_malformed_json_retries_once_then_succeeds(monkeypatch) -> None:
    dm = _docmodel([NUM])
    fake = FakeChat([
        "not json at all {oops",  # first attempt: unparsable
        _claims_json({"quote": NUM, "claim_type": "numeric", "company": "PepsiCo",
                      "question": "q"}),  # retry: valid
    ])
    _patch_chat(monkeypatch, fake)
    claims = extract.extract_claims(dm, pilot=False)
    assert fake.calls == 2
    assert [c.quote for c in claims] == [NUM]


def test_malformed_json_twice_yields_no_claims(monkeypatch) -> None:
    dm = _docmodel([NUM])
    fake = FakeChat(["garbage one", "garbage two"])
    _patch_chat(monkeypatch, fake)
    claims = extract.extract_claims(dm, pilot=False)
    assert fake.calls == 2
    assert claims == []


def test_code_fenced_json_is_tolerated(monkeypatch) -> None:
    dm = _docmodel([NUM])
    body = _claims_json({"quote": NUM, "claim_type": "numeric", "company": "PepsiCo",
                         "question": "q"})
    fake = FakeChat("```json\n" + body + "\n```")
    _patch_chat(monkeypatch, fake)
    claims = extract.extract_claims(dm, pilot=False)
    assert fake.calls == 1
    assert [c.quote for c in claims] == [NUM]


# --- cross-chunk dedupe ------------------------------------------------------
def test_cross_chunk_dedupe(monkeypatch) -> None:
    # Force two chunks: canonical_text longer than the chunk limit, with the claim
    # sentence at the very start so it lives inside the first chunk. Both chunks
    # return the same claim; the identical anchored span dedupes to one.
    filler = "x" * (extract.CHUNK_CHAR_LIMIT + 5_000)
    dm = _docmodel([NUM, filler])
    fake = FakeChat(_claims_json(
        {"quote": NUM, "claim_type": "numeric", "company": "PepsiCo", "question": "q"},
    ))
    _patch_chat(monkeypatch, fake)
    claims = extract.extract_claims(dm, pilot=False)
    assert fake.calls >= 2, "expected the document to be chunked"
    assert len(claims) == 1
    assert claims[0].quote == NUM


def test_dedupe_prefers_higher_priority_type(monkeypatch) -> None:
    # Same span proposed twice with different types: numeric must win over factual.
    dm = _docmodel([NUM])
    fake = FakeChat(_claims_json(
        {"quote": NUM, "claim_type": "factual", "company": "PepsiCo", "question": "q"},
        {"quote": NUM, "claim_type": "numeric", "company": "PepsiCo", "question": "q"},
    ))
    _patch_chat(monkeypatch, fake)
    claims = extract.extract_claims(dm, pilot=False)
    assert len(claims) == 1
    assert claims[0].claim_type == "numeric"


# --- cap and pilot narrowing -------------------------------------------------
def test_cap_keeps_overflow_as_skipped(monkeypatch) -> None:
    monkeypatch.setattr(config, "MAX_CLAIMS_PER_REVIEW", 2)
    sents = [f"AMD metric number {i} was {i}.0 in FY2022." for i in range(3)]
    dm = _docmodel(sents)
    fake = FakeChat(_claims_json(
        *[{"quote": s, "claim_type": "numeric", "company": "AMD", "question": "q"}
          for s in sents]
    ))
    _patch_chat(monkeypatch, fake)
    claims = extract.extract_claims(dm, pilot=False)
    assert len(claims) == 3  # overflow is kept, not discarded
    assert [c.status for c in claims] == ["PENDING", "PENDING", "SKIPPED"]


def test_pilot_narrows_to_pilot_claims(monkeypatch) -> None:
    monkeypatch.setattr(config, "PILOT_CLAIMS", 1)
    monkeypatch.setattr(config, "MAX_CLAIMS_PER_REVIEW", 30)
    sents = [f"AMD metric number {i} was {i}.0 in FY2022." for i in range(3)]
    dm = _docmodel(sents)
    fake = FakeChat(_claims_json(
        *[{"quote": s, "claim_type": "numeric", "company": "AMD", "question": "q"}
          for s in sents]
    ))
    _patch_chat(monkeypatch, fake)
    claims = extract.extract_claims(dm, pilot=True)
    assert [c.status for c in claims] == ["PENDING", "SKIPPED", "SKIPPED"]


# --- typing coercion and derived question -----------------------------------
def test_invalid_type_coerced_and_question_synthesized(monkeypatch) -> None:
    dm = _docmodel([NUM])
    fake = FakeChat(_claims_json(
        {"quote": NUM, "claim_type": "opinion?", "company": "PepsiCo",
         "period": "FY2023", "metric": "credit agreement increase"},  # no question
    ))
    _patch_chat(monkeypatch, fake)
    claims = extract.extract_claims(dm, pilot=False)
    assert claims[0].claim_type == "factual"  # unknown type coerced to factual
    assert claims[0].question  # synthesized from company/metric/period
    assert "PepsiCo" in claims[0].question


# --- trace emission ----------------------------------------------------------
def test_emits_claim_extracted_events(monkeypatch) -> None:
    dm = _docmodel([NUM, FACT])
    trace = FakeTrace()
    fake = FakeChat(_claims_json(
        {"quote": NUM, "claim_type": "numeric", "company": "PepsiCo", "question": "q1"},
        {"quote": FACT, "claim_type": "factual", "company": "PepsiCo", "question": "q2"},
    ))
    _patch_chat(monkeypatch, fake)
    claims = extract.extract_claims(dm, pilot=False, trace=trace)

    per_claim = [e for e in trace.events
                 if e["type"] == "claim_extracted" and e["item_id"] is not None]
    assert [e["item_id"] for e in per_claim] == [c.claim_id for c in claims]
    assert all(e["payload"]["claim_type"] for e in per_claim)
    summary = [e for e in trace.events if e["title"] == "claim extraction complete"][0]
    assert summary["payload"]["active_claims"] == 2
    assert summary["payload"]["total_claims"] == 2


def test_no_trace_is_safe(monkeypatch) -> None:
    dm = _docmodel([NUM])
    fake = FakeChat(_claims_json(
        {"quote": NUM, "claim_type": "numeric", "company": "PepsiCo", "question": "q"},
    ))
    _patch_chat(monkeypatch, fake)
    claims = extract.extract_claims(dm, pilot=False)  # trace omitted -> no crash
    assert len(claims) == 1


def test_empty_claims_response(monkeypatch) -> None:
    dm = _docmodel([NUM])
    fake = FakeChat(_claims_json())
    _patch_chat(monkeypatch, fake)
    assert extract.extract_claims(dm, pilot=False) == []


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))

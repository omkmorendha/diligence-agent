"""Unit checks for IMP3-5 (results/iterations/iter2/improvement_plan.json):
the BM25 hybrid lexical component in retrieval.py and the near-duplicate search
stall guard in agent.py. Both are exercised as PURE helpers (no index build, no
LLM) so the invariants the plan relies on are pinned without an endpoint call.
"""

from __future__ import annotations

from app import config
from app.agent import _ItemState, _commit_nudge_notice, _jaccard, _search_stall_notice
from app.retrieval import _build_bm25_stats, _bm25_score


# --- BM25 hybrid lexical component -------------------------------------------
def test_bm25_idf_prefers_rare_exact_term_over_common_one():
    """The whole point of BM25 over the old flat overlap ratio: a rare, load-bearing
    term ('restructuring', present in one chunk) must outweigh a corpus-common one
    ('the'), so the note that actually holds the answer surfaces (pepsico_06)."""
    rows = [{"text": "the restructuring charge was 411 million"}] + [
        {"text": "the company reported revenue growth"} for _ in range(9)
    ]
    stats = _build_bm25_stats(rows)
    # 'restructuring' occurs in 1/10 docs; 'company' occurs in 9/10 -> higher IDF.
    assert stats.idf["restructuring"] > stats.idf["company"]
    # The chunk holding the rare term scores highest for a query naming it.
    scores = [_bm25_score({"restructuring"}, i, stats) for i in range(len(rows))]
    assert scores[0] > 0.0
    assert all(scores[0] > s for s in scores[1:])


def test_bm25_score_zero_when_no_query_term_matches():
    rows = [{"text": "operating cash flow was positive"}]
    stats = _build_bm25_stats(rows)
    assert _bm25_score({"nonexistent"}, 0, stats) == 0.0


def test_bm25_idf_never_negative_for_majority_term():
    """A term in >half the corpus must not contribute a negative lexical score
    (the max(0,.) floor) -- otherwise a common word could push a chunk DOWN."""
    rows = [{"text": "revenue revenue"} for _ in range(8)] + [
        {"text": "inventory note"} for _ in range(2)
    ]
    stats = _build_bm25_stats(rows)
    assert stats.idf["revenue"] >= 0.0


# --- search stall guard ------------------------------------------------------
def test_jaccard_identical_and_disjoint():
    assert _jaccard({"a", "b"}, {"a", "b"}) == 1.0
    assert _jaccard({"a"}, {"b"}) == 0.0
    assert _jaccard(set(), set()) == 0.0


def test_stall_guard_fires_on_near_duplicate_queries_returning_no_new_chunks():
    """verizon_04 shape: repeated near-identical queries surfacing nothing new.
    The hint must fire only after MAX_REPEATS consecutive stalls, then reset."""
    state = _ItemState()
    q = "interest rate caps swaptions notional"
    notices = [
        _search_stall_notice(state, q, new_chunk_ids=[], chunk_doc_ids=[])
        for _ in range(config.SEARCH_STALL_MAX_REPEATS)
    ]
    # No hint before the threshold; a hint exactly when it is crossed.
    assert all(n is None for n in notices[:-1])
    assert notices[-1] is not None and "RETRIEVAL STALL" in notices[-1]
    # Streak reset after firing so it re-accumulates rather than spamming.
    assert state.search_stall_streak == 0


def test_stall_guard_does_not_fire_on_productive_distinct_searches():
    """A search that surfaces NEW chunks with a distinct query is progress, not a
    stall -- the guard must never cut legitimate multi-query research (plan risk)."""
    state = _ItemState()
    assert _search_stall_notice(state, "revenue growth 2023", ["c1", "c2"], ["d1"]) is None
    assert _search_stall_notice(state, "operating margin segment", ["c3"], ["d2"]) is None
    assert _search_stall_notice(state, "restructuring charge note", ["c4"], ["d3"]) is None
    assert state.search_stall_streak == 0


def test_stall_guard_counts_no_new_chunks_even_when_query_changes():
    """Zero-new-chunks alone is a stall (the model keeps searching but the corpus
    yields only already-seen chunks), independent of query wording."""
    state = _ItemState()
    for i in range(config.SEARCH_STALL_MAX_REPEATS - 1):
        assert _search_stall_notice(state, f"distinct query number {i}", new_chunk_ids=[], chunk_doc_ids=[]) is None
    notice = _search_stall_notice(state, "yet another distinct query", new_chunk_ids=[], chunk_doc_ids=[])
    assert notice is not None


def test_stall_guard_fires_on_same_dominant_doc_refetched(monkeypatch):
    """IMP4-2 same-doc arm: surface tokens are DISTINCT (low Jaccard) and each search
    returns NEW chunk_ids, so the Jaccard/zero-new arms never latch -- but the SAME
    document dominates every result (boeing_01/pepsico_02 fixation loop). The
    doc-repeat arm alone must fire once one doc has dominated DOC_REPEATS searches."""
    monkeypatch.setattr(config, "SEARCH_STALL_DOC_REPEATS", 3)
    monkeypatch.setattr(config, "SEARCH_STALL_MAX_REPEATS", 1)
    distinct_queries = [
        "commercial airplanes segment revenue",
        "defense space security backlog",
        "global services operating margin",
    ]
    state = _ItemState()
    notices = [
        _search_stall_notice(state, q, [f"chunk{i}"], ["same_doc"])
        for i, q in enumerate(distinct_queries)
    ]
    # Doc count reaches DOC_REPEATS only on the 3rd search -> fires exactly then.
    assert all(n is None for n in notices[:-1])
    assert notices[-1] is not None and "RETRIEVAL STALL" in notices[-1]


# --- commit nudge (IMP4-2) ---------------------------------------------------
def test_commit_nudge_fires_after_threshold_with_no_record_attempt(monkeypatch):
    monkeypatch.setattr(config, "SEARCH_COMMIT_NUDGE_THRESHOLD", 6)
    state = _ItemState()
    state.successful_search_count = 5
    assert _commit_nudge_notice(state) is None  # below threshold
    state.successful_search_count = 6
    notice = _commit_nudge_notice(state)
    assert notice is not None and "COMMIT NOW" in notice


def test_commit_nudge_stops_once_record_answer_attempted(monkeypatch):
    monkeypatch.setattr(config, "SEARCH_COMMIT_NUDGE_THRESHOLD", 6)
    state = _ItemState()
    state.successful_search_count = 9
    state.record_answer_attempts = 1
    assert _commit_nudge_notice(state) is None

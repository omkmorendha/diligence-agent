"""Regression tests for the run-scoped, lock-protected usage sink and trace
emitter (spec sections 1.7 / milestone 15.1).

The v1 review fans verification agents out across worker threads that share one
run's usage sink and one TraceWriter. These tests prove that concurrent emission
produces complete, uncorrupted JSONL with strictly monotonic seq, and that
per-call context does not leak between threads. Single-threaded behavior and the
on-disk formats (llm_calls.jsonl, trace.jsonl) are unchanged.
"""

from __future__ import annotations

import json
import threading
import time

import pytest

from app import config, llm
from app.trace import TraceWriter


class _FakeUsage:
    prompt_tokens = 10
    completion_tokens = 5
    total_tokens = 15


class _FakeResponse:
    usage = _FakeUsage()


class _FakeCompletions:
    def create(self, **kwargs):  # noqa: ANN003, ANN201 - mirrors OpenAI SDK shape
        # A tiny yield encourages the OS to interleave worker threads, so a
        # missing lock would actually corrupt the output rather than pass by luck.
        time.sleep(0.0005)
        return _FakeResponse()


class _FakeChat:
    completions = _FakeCompletions()


class _FakeClient:
    chat = _FakeChat()


@pytest.fixture()
def fake_llm(monkeypatch):
    """Route chat() through a fake client so the real usage/sink/context path runs
    without touching the network."""
    monkeypatch.setattr(llm, "_client", lambda: _FakeClient())
    try:
        yield
    finally:
        llm.set_usage_sink(None)
        llm.clear_call_context()


def test_sink_and_context_are_concurrency_safe(tmp_path, fake_llm):
    calls_path = tmp_path / "llm_calls.jsonl"
    llm.set_usage_sink(llm.jsonl_usage_sink(calls_path))
    llm.set_run_context(run_id="concurrent-run", system="agent")

    per_thread = 150
    workers = ["A", "B", "C"]
    barrier = threading.Barrier(len(workers))

    def worker(item_id: str) -> None:
        # Each worker sets its own per-call context; if that context were shared
        # module state rather than thread-scoped, item_ids would cross over.
        llm.set_call_context(purpose="verify", item_id=item_id)
        barrier.wait()
        for _ in range(per_thread):
            llm.chat([{"role": "user", "content": "hi"}])

    threads = [threading.Thread(target=worker, args=(w,)) for w in workers]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    lines = calls_path.read_text().splitlines()
    assert len(lines) == per_thread * len(workers)

    records = [json.loads(line) for line in lines]  # no line is corrupt/interleaved
    for rec in records:
        assert rec["run_id"] == "concurrent-run"  # shared run context reached every worker
        assert rec["system"] == "agent"
        assert rec["purpose"] == "verify"
        assert rec["item_id"] in workers
        assert rec["total_tokens"] == 15

    for w in workers:
        assert sum(1 for r in records if r["item_id"] == w) == per_thread


def test_trace_emit_is_concurrency_safe(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path)
    trace = TraceWriter(run_id="concurrent-trace")

    per_thread = 250
    n_workers = 3
    total = per_thread * n_workers
    barrier = threading.Barrier(n_workers)

    def worker(tag: int) -> None:
        barrier.wait()
        for i in range(per_thread):
            trace.emit("tool_call", f"w{tag}-{i}", item_id=f"c{tag}")

    threads = [threading.Thread(target=worker, args=(k,)) for k in range(n_workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    trace.close()

    # In-memory events: every emit accounted for, seq unique and complete.
    assert len(trace.events) == total
    assert sorted(e.seq for e in trace.events) == list(range(1, total + 1))

    # On-disk trace.jsonl: complete, uncorrupted, seq strictly monotonic in file
    # order (seq allocation and the file append happen under one lock).
    lines = (tmp_path / "concurrent-trace" / "trace.jsonl").read_text().splitlines()
    assert len(lines) == total
    seqs = [json.loads(line)["seq"] for line in lines]
    assert seqs == list(range(1, total + 1))


def test_clear_call_context_resets_run_and_call_state(fake_llm):
    llm.set_run_context(run_id="r1", system="agent")
    llm.set_call_context(purpose="plan", item_id="c1")
    assert llm._current_context() == {
        "run_id": "r1",
        "system": "agent",
        "purpose": "plan",
        "item_id": "c1",
    }

    llm.clear_call_context()
    assert llm._current_context() == {}

"""The single LLM adapter (spec section 3).

ALL LLM calls in the project go through `chat()` — agent loop, baseline, judges,
dataset-builder classifier/verifier, memo assembly. There must be no direct
`OpenAI()` construction anywhere else.

Uses the OpenAI Python SDK against the OpenAI-compatible NVIDIA endpoint.
"""

from __future__ import annotations

import contextvars
import json
import threading
import time
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from openai import OpenAI

from . import config


@lru_cache(maxsize=1)
def _client() -> OpenAI:
    """The only place an OpenAI client is constructed."""
    return OpenAI(api_key=config.require_api_key(), base_url=config.LLM_BASE_URL)


# --- usage/latency capture ---------------------------------------------------
# Every non-streaming call through chat() can be recorded to a "usage sink" --
# a callable taking one dict per LLM call (latency, token usage, and whatever
# context the caller registered). Run drivers (agent.run_agent /
# baseline.run_baseline) point the sink at runs/{run_id}/llm_calls.jsonl; when no
# sink is set (judges, dataset builder, smoke test) nothing is recorded.
#
# Concurrency model (spec sections 1.7 / 13): a v1 review fans verification agents
# out across a ThreadPoolExecutor, so several worker threads emit usage records
# through the one run-scoped sink at once. Two invariants keep that safe:
#   * The sink is shared (one per process/run) but its file write is serialized
#     by a per-sink lock, so concurrent lines never interleave or corrupt.
#   * Context is split in two layers. Run-wide fields (run_id, system) live in a
#     lock-guarded module dict shared by every worker (set once via
#     set_run_context). Per-call fields (purpose, item_id) live in a ContextVar
#     that is naturally isolated per thread, so one worker's item_id never leaks
#     into another worker's record. Each record merges {**run, **per_call}.
_usage_sink: Optional[Callable[[dict[str, Any]], None]] = None

_run_context: dict[str, Any] = {}
_run_context_lock = threading.Lock()

# Per-thread overlay. Stored as an immutable dict and replaced wholesale on every
# mutation so concurrent readers/writers never share a mutable object.
_call_context_var: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "llm_call_context", default={}
)


def set_usage_sink(sink: Optional[Callable[[dict[str, Any]], None]]) -> None:
    global _usage_sink
    _usage_sink = sink


def set_run_context(**fields: Any) -> None:
    """Merge run-wide fields (run_id, system) shared across every worker thread of
    a run into subsequent usage records. A value of None removes the field."""
    with _run_context_lock:
        for key, value in fields.items():
            if value is None:
                _run_context.pop(key, None)
            else:
                _run_context[key] = value


def set_call_context(**fields: Any) -> None:
    """Merge per-call fields (purpose, item_id, ...) into subsequent usage records
    for the current thread only. A value of None removes the field. Thread-scoped
    so concurrent verification workers do not clobber each other's context."""
    current = _call_context_var.get()
    updated = dict(current)
    for key, value in fields.items():
        if value is None:
            updated.pop(key, None)
        else:
            updated[key] = value
    _call_context_var.set(updated)


def clear_call_context() -> None:
    """Reset per-call context for the current thread and the shared run context.

    Called by run drivers in their finally block to leave a clean slate for the
    next run in the same process. Workers that only want to drop their own
    per-call fields should use set_call_context(field=None) instead."""
    _call_context_var.set({})
    with _run_context_lock:
        _run_context.clear()


def _current_context() -> dict[str, Any]:
    with _run_context_lock:
        merged = dict(_run_context)
    merged.update(_call_context_var.get())
    return merged


def jsonl_usage_sink(path: Path) -> Callable[[dict[str, Any]], None]:
    """A sink that appends one JSON line per LLM call to `path`.

    The write is serialized by a per-sink lock so that concurrent verification
    workers sharing one run's sink cannot interleave partial lines."""
    lock = threading.Lock()

    def sink(record: dict[str, Any]) -> None:
        line = json.dumps(record) + "\n"
        with lock:
            with path.open("a") as f:
                f.write(line)

    return sink


def _record_usage(
    started_monotonic: float,
    response: Any,
    *,
    stream: bool,
    json_mode: bool,
    has_tools: bool,
    n_messages: int,
    error: Optional[str] = None,
    model: Optional[str] = None,
) -> None:
    if _usage_sink is None:
        return
    usage = getattr(response, "usage", None) if response is not None else None
    record: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "duration_s": round(time.monotonic() - started_monotonic, 4),
        "model": model or config.LLM_MODEL,
        "stream": stream,
        "json_mode": json_mode,
        "has_tools": has_tools,
        "n_messages": n_messages,
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
        **_current_context(),
    }
    if error is not None:
        record["error"] = error
    try:
        _usage_sink(record)
    except Exception:  # noqa: BLE001 -- instrumentation must never sink a run
        pass


def chat(
    messages: list[dict[str, Any]],
    tools: Optional[list[dict[str, Any]]] = None,
    json_mode: bool = False,
    temperature: float = config.LLM_TEMPERATURE,
    stream: bool = False,
    max_tokens: int = config.LLM_MAX_TOKENS,
    seed: int = config.LLM_SEED,
    reasoning_effort: Optional[str] = None,
    model: Optional[str] = None,
) -> Any:
    """Send a chat completion. Returns the raw OpenAI response (or a stream iterator).

    Args mirror spec section 3. Defaults: temperature=0.2, seed=42, max_tokens=16384.

    * `tools`      -> native OpenAI tool-calling (NativeToolProtocol).
    * `json_mode`  -> request a JSON object response (structured output).
    * `stream`     -> return the streaming iterator instead of a full response.
    * `reasoning_effort` -> speed knob for reasoning models (e.g. "none" for 0
      reasoning tokens on high-volume simple-output calls: classification,
      extraction, judging). No-op on the current non-reasoning Kimi-K2.6 model, but
      honored by reasoning models. Left unset (None) to keep the model's default.
      Sent via `extra_body` since it is not a standard OpenAI chat-completion
      parameter.
    """
    kwargs: dict[str, Any] = {
        # `model` overrides for auxiliary calls (e.g. the cheaper judge model);
        # the agent/baseline always use the configured LLM_MODEL.
        "model": model or config.LLM_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "seed": seed,
        "stream": stream,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    if reasoning_effort:
        kwargs["extra_body"] = {"reasoning_effort": reasoning_effort}

    started = time.monotonic()
    try:
        response = _client().chat.completions.create(**kwargs)
    except Exception as exc:
        _record_usage(
            started, None, stream=stream, json_mode=json_mode,
            has_tools=bool(tools), n_messages=len(messages),
            error=f"{type(exc).__name__}: {exc}", model=model,
        )
        raise
    # Streaming responses carry no usage object; record latency-to-create only.
    _record_usage(
        started, None if stream else response, stream=stream, json_mode=json_mode,
        has_tools=bool(tools), n_messages=len(messages), model=model,
    )
    return response


def chat_text(messages: list[dict[str, Any]], **kwargs: Any) -> str:
    """Convenience: run a non-streaming chat and return the assistant message text."""
    resp = chat(messages, stream=False, **kwargs)
    return resp.choices[0].message.content or ""


def stream_text(messages: list[dict[str, Any]], **kwargs: Any) -> Iterable[str]:
    """Convenience: yield text deltas from a streaming chat."""
    for chunk in chat(messages, stream=True, **kwargs):
        delta = chunk.choices[0].delta
        if delta and delta.content:
            yield delta.content

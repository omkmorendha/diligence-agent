"""The single LLM adapter (spec section 3).

ALL LLM calls in the project go through `chat()` — agent loop, baseline, judges,
dataset-builder classifier/verifier, memo assembly. There must be no direct
`OpenAI()` construction anywhere else.

Uses the OpenAI Python SDK against the OpenAI-compatible NVIDIA endpoint.
"""

from __future__ import annotations

import json
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
# run-scoped context the caller registered via set_call_context). Run drivers
# (agent.run_agent / baseline.run_baseline) point the sink at
# runs/{run_id}/llm_calls.jsonl; when no sink is set (judges, dataset builder,
# smoke test) nothing is recorded. Module-global state: one run per process --
# the CLI drivers guarantee that; not safe for concurrent runs in one process.
_usage_sink: Optional[Callable[[dict[str, Any]], None]] = None
_call_context: dict[str, Any] = {}


def set_usage_sink(sink: Optional[Callable[[dict[str, Any]], None]]) -> None:
    global _usage_sink
    _usage_sink = sink


def set_call_context(**fields: Any) -> None:
    """Merge run-scoped fields (purpose, item_id, run_id, ...) into every
    subsequent usage record. A value of None removes the field."""
    for key, value in fields.items():
        if value is None:
            _call_context.pop(key, None)
        else:
            _call_context[key] = value


def clear_call_context() -> None:
    _call_context.clear()


def jsonl_usage_sink(path: Path) -> Callable[[dict[str, Any]], None]:
    """A sink that appends one JSON line per LLM call to `path`."""

    def sink(record: dict[str, Any]) -> None:
        with path.open("a") as f:
            f.write(json.dumps(record) + "\n")

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
) -> None:
    if _usage_sink is None:
        return
    usage = getattr(response, "usage", None) if response is not None else None
    record: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "duration_s": round(time.monotonic() - started_monotonic, 4),
        "model": config.LLM_MODEL,
        "stream": stream,
        "json_mode": json_mode,
        "has_tools": has_tools,
        "n_messages": n_messages,
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
        **_call_context,
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
        "model": config.LLM_MODEL,
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
            error=f"{type(exc).__name__}: {exc}",
        )
        raise
    # Streaming responses carry no usage object; record latency-to-create only.
    _record_usage(
        started, None if stream else response, stream=stream, json_mode=json_mode,
        has_tools=bool(tools), n_messages=len(messages),
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

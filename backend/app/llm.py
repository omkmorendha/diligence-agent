"""The single LLM adapter (spec section 3).

ALL LLM calls in the project go through `chat()` — agent loop, baseline, judges,
dataset-builder classifier/verifier, memo assembly. There must be no direct
`OpenAI()` construction anywhere else.

Uses the OpenAI Python SDK against the OpenAI-compatible NVIDIA endpoint.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Iterable, Optional

from openai import OpenAI

from . import config


@lru_cache(maxsize=1)
def _client() -> OpenAI:
    """The only place an OpenAI client is constructed."""
    return OpenAI(api_key=config.require_api_key(), base_url=config.LLM_BASE_URL)


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

    return _client().chat.completions.create(**kwargs)


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

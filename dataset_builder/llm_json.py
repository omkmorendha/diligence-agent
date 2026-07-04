"""Shared JSON-mode LLM call helper for D3/D4 (spec section 4 JsonToolProtocol style).

Both dataset-builder agents (d3_classify.py, d4_verify.py) need the same two
things from every LLM call: lenient JSON extraction/parsing, and resilience
against the NVIDIA endpoint's rate limiting under concurrent load (observed:
tight per-minute limits that a naive ThreadPoolExecutor blows through
immediately — see AGENT NOTE below). Two distinct failure modes get two
distinct retry strategies:

    * malformed JSON / missing keys -> "repair" retry: append the parse error
      to the conversation and ask the model to reformat (JsonToolProtocol,
      spec section 4).
    * rate limit / transient API error -> backoff retry: same request, no
      conversation growth, exponential sleep.

If both retry budgets are exhausted, the caller gets (None, error_string) and
is expected to fall back to a conservative default rather than silently
dropping the row (every row must end up classified/verified — see acceptance
criteria on issue #6).
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Callable

import openai

from app import llm


def strip_code_fence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
        t = t.removesuffix("```")
    return t.strip()


def find_first_json_object(text: str) -> str | None:
    """Find the first balanced {...} object in text (lenient, brace-counting)."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def parse_llm_json(text: str) -> dict[str, Any]:
    cleaned = strip_code_fence(text)
    obj_text = find_first_json_object(cleaned)
    if obj_text is None:
        raise ValueError(f"no JSON object found in response: {cleaned[:200]!r}")
    return json.loads(obj_text)


def call_json_with_retry(
    messages: list[dict[str, Any]],
    validate: Callable[[dict[str, Any]], dict[str, Any]],
    max_tokens: int = 1000,
    max_repair_retries: int = 2,
    max_rate_limit_retries: int = 6,
    base_delay_seconds: float = 2.0,
) -> tuple[dict[str, Any] | None, str | None]:
    """Call the LLM in json_mode, repairing malformed JSON and backing off on
    rate limits. `messages` is mutated in place (repair turns are appended) so
    the caller can inspect the final conversation if needed.

    Returns (validated_object, None) on success, or (None, error) if every
    repair AND rate-limit retry is exhausted.
    """
    last_error: str | None = None
    repair_attempts = 0
    while repair_attempts <= max_repair_retries:
        rate_limit_attempts = 0
        text = ""
        while True:
            try:
                text = llm.chat_text(messages, json_mode=True, max_tokens=max_tokens)
                break
            except (openai.RateLimitError, openai.APIStatusError, openai.APIConnectionError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                rate_limit_attempts += 1
                if rate_limit_attempts > max_rate_limit_retries:
                    return None, last_error
                time.sleep(base_delay_seconds * (2 ** (rate_limit_attempts - 1)))
            except Exception as exc:  # noqa: BLE001 - any other API-level failure
                return None, f"{type(exc).__name__}: {exc}"

        try:
            obj = validate(parse_llm_json(text))
            return obj, None
        except Exception as exc:  # noqa: BLE001 - parse/validation failure, repair-retry
            last_error = f"{type(exc).__name__}: {exc}"
            repair_attempts += 1
            if repair_attempts > max_repair_retries:
                return None, last_error
            messages.append({"role": "assistant", "content": text})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"Your previous response was not valid: {last_error}. "
                        "Respond again with EXACTLY one valid JSON object matching the "
                        "required shape, no prose, no code fence."
                    ),
                }
            )

    return None, last_error

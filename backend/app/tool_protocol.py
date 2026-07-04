"""ToolProtocol abstraction (spec section 4) + tool schemas (spec section 13, Step 11).

Native tool calling through the NVIDIA endpoint is a risk, so the agent loop is
built against this abstraction rather than a concrete calling convention. The
smoke test picks the protocol once (config.selected_tool_protocol()); the agent
loop never decides per call.

    NativeToolProtocol -> OpenAI-style tools=[...] / tool_calls
    JsonToolProtocol   -> model returns exactly one JSON object per turn:
                          {"action": "tool_name", "input": {}} or
                          {"action": "final", "input": {}}

JsonToolProtocol parser behavior (spec section 4):
    strip code fences -> find first JSON object -> parse leniently ->
    retry once on parse failure (append error to next turn) ->
    if still invalid, force-abstain the item.

TOOL_DEFINITIONS below is the single source of truth for the five agent tools'
LLM-facing parameter schemas (spec section 13). Only the fields the model
actually controls are exposed here -- run-scoped arguments the tools also take
(`trace`, `company`, `item_id`) are bound by the agent loop, not surfaced to the
model. `native_tool_schemas()` renders these as OpenAI-style `tools=[...]`;
`json_protocol_tool_prompt()` renders the same catalogue as a plain-text
system-prompt block for the JSON fallback protocol.

Step 12 (agent loop): both protocols below implement the shared `ToolProtocol`
interface -- `request_action(messages)` calls the model, mutates `messages` in
place with whatever the model actually said (so history is preserved even on a
parse failure), and returns a single `ToolAction` (name + parsed input +
optional native `call_id`) or `None` if no actionable tool call could be
parsed. `append_tool_result(messages, action, result, error)` appends the
matching tool response (or error) back into the conversation in the shape each
protocol's model expects. The agent loop (agent.py) never branches on protocol
name beyond `get_protocol(...)` -- it only calls this interface.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Optional

from . import llm

# --- section 13: tool schemas (model-facing parameters only) ---------------
TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "search_filing",
        "description": (
            "Cosine search over this run's company filing corpus. Returns ranked "
            "chunks with page-level provenance (doc_id, page, score, snippet)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural-language search query."},
                "k": {"type": "integer", "description": "Number of chunks to return.", "default": 6},
                "doc_filter": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of doc_ids to restrict the search to.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_pages",
        "description": (
            "Read raw text for specific PDF pages of a document, after search_filing "
            "has localized a relevant page or table."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "doc_id": {"type": "string", "description": "Document id from a prior search_filing result."},
                "pages": {"type": "array", "items": {"type": "integer"}, "description": "1-indexed PDF page numbers."},
            },
            "required": ["doc_id", "pages"],
        },
    },
    {
        "name": "calculate",
        "description": (
            "Deterministically evaluate a financial arithmetic expression over "
            "grounded, cited inputs. The model must never compute arithmetic "
            "itself -- always call this tool for any derived number."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "Arithmetic expression over the named inputs, e.g. 'revenue / avg_net_ppe'.",
                },
                "inputs": {
                    "type": "object",
                    "description": "Map of variable name -> grounded financial input.",
                    "additionalProperties": {
                        "type": "object",
                        "properties": {
                            "value": {"type": "number"},
                            "unit": {"type": "string"},
                            "period": {"type": "string"},
                            "citation_id": {"type": "string", "description": "Must reference a prior citation."},
                        },
                        "required": ["value", "unit", "period", "citation_id"],
                    },
                },
                "rounding": {"type": "string", "description": "e.g. '2dp'. Optional."},
            },
            "required": ["expression", "inputs"],
        },
    },
    {
        "name": "record_answer",
        "description": "Record the final answer for a checklist item. Validates against the item_answer schema.",
        "parameters": {
            "type": "object",
            "properties": {
                "item_answer": {
                    "type": "object",
                    "description": (
                        "Full ItemAnswer object: item_id, answer, value, unit, "
                        "citations, status, confidence."
                    ),
                },
            },
            "required": ["item_answer"],
        },
    },
    {
        "name": "flag_outstanding",
        "description": (
            "Explicit abstention path. Use when required data is missing, evidence "
            "is ambiguous, period/unit is unclear, retrieval fails, the max "
            "tool-call cap is reached, or the item cannot otherwise be answered."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "item_id": {"type": "string"},
                "reason": {"type": "string", "description": "Plain-language reason for abstaining."},
                "citations": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Optional partial evidence gathered before abstaining.",
                },
            },
            "required": ["item_id", "reason"],
        },
    },
]


def native_tool_schemas() -> list[dict[str, Any]]:
    """OpenAI-style `tools=[...]` list for NativeToolProtocol (spec section 4)."""
    return [
        {
            "type": "function",
            "function": {
                "name": d["name"],
                "description": d["description"],
                "parameters": d["parameters"],
            },
        }
        for d in TOOL_DEFINITIONS
    ]


def json_protocol_tool_prompt() -> str:
    """Plain-text tool catalogue for JsonToolProtocol's system prompt (spec section 4).

    The model must respond with exactly one JSON object per turn:
        {"action": "<tool_name>", "input": {...}}  or  {"action": "final", "input": {}}
    """
    lines = [
        'Available tools. Respond with exactly one JSON object per turn: '
        '{"action": "<tool_name>", "input": {...}}.',
    ]
    for d in TOOL_DEFINITIONS:
        lines.append(f"- {d['name']}: {d['description']}")
        lines.append(f"  input schema: {json.dumps(d['parameters'])}")
    lines.append('- final: {"action": "final", "input": {}} once every item is answered or flagged.')
    return "\n".join(lines)


def _jsonable(value: Any) -> Any:
    """Best-effort plain-JSON view of a tool's return value (pydantic model or dict)."""
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return value


@dataclass
class ToolAction:
    """One parsed model turn: call `name` with `arguments`.

    `call_id` is the native tool_call id (OpenAI-style); unused (None) under
    JsonToolProtocol, which has no call-correlation concept.
    """

    name: str
    arguments: dict[str, Any]
    call_id: Optional[str] = None


class ToolProtocol:
    """Shared interface both concrete protocols implement (spec section 4)."""

    def request_action(self, messages: list[dict[str, Any]]) -> Optional[ToolAction]:
        """Call the model, append its turn to `messages`, and return a single
        parsed `ToolAction`, or `None` if no actionable tool call could be
        parsed (caller decides whether to nudge/retry or give up)."""
        raise NotImplementedError

    def append_tool_result(
        self,
        messages: list[dict[str, Any]],
        action: ToolAction,
        result: Any = None,
        error: Optional[str] = None,
    ) -> None:
        """Append the tool's result (or `error`, mutually exclusive) back into
        the conversation in this protocol's expected shape."""
        raise NotImplementedError


class NativeToolProtocol(ToolProtocol):
    """OpenAI-style `tools=[...]` / `tool_calls` (spec section 4). Try this first."""

    def request_action(self, messages: list[dict[str, Any]]) -> Optional[ToolAction]:
        response = llm.chat(messages, tools=native_tool_schemas())
        message = response.choices[0].message
        tool_calls = list(getattr(message, "tool_calls", None) or [])
        if not tool_calls:
            # No tool call made -- keep the assistant's text in history so the next
            # turn has context, but signal "no action" to the caller.
            messages.append({"role": "assistant", "content": message.content or ""})
            return None

        # Only ever act on one tool call per turn (agent.py's loop is single-action);
        # the assistant message we record back reflects only that one call, so every
        # tool_call the API sees gets exactly one matching tool response.
        call = tool_calls[0]
        raw_arguments = call.function.arguments or "{}"
        try:
            arguments = json.loads(raw_arguments)
        except json.JSONDecodeError:
            arguments = {}

        messages.append(
            {
                "role": "assistant",
                "content": message.content or None,
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {"name": call.function.name, "arguments": raw_arguments},
                    }
                ],
            }
        )
        return ToolAction(name=call.function.name, arguments=arguments, call_id=call.id)

    def append_tool_result(
        self,
        messages: list[dict[str, Any]],
        action: ToolAction,
        result: Any = None,
        error: Optional[str] = None,
    ) -> None:
        content = json.dumps({"error": error}) if error is not None else json.dumps(_jsonable(result))
        messages.append({"role": "tool", "tool_call_id": action.call_id, "content": content})


_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json_object(text: str) -> Optional[dict[str, Any]]:
    """Strip code fences, find the first `{...}` object, parse leniently (spec section 4)."""
    stripped = _CODE_FENCE_RE.sub("", text.strip()).strip()
    match = _JSON_OBJECT_RE.search(stripped)
    if not match:
        return None
    try:
        obj = json.loads(match.group())
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


class JsonToolProtocol(ToolProtocol):
    """Fallback protocol: exactly one JSON object per turn (spec section 4).

    Parser behavior per spec: strip code fences -> find first JSON object ->
    parse leniently -> retry once on parse failure (append error to next turn)
    -> if still invalid, return None so the agent loop force-abstains the item.
    """

    def request_action(self, messages: list[dict[str, Any]]) -> Optional[ToolAction]:
        text = llm.chat_text(messages)
        messages.append({"role": "assistant", "content": text})
        parsed = _extract_json_object(text)

        if parsed is None:
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Your last reply was not a single valid JSON object. Respond with "
                        'exactly one JSON object: {"action": "<tool_name>", "input": {...}}.'
                    ),
                }
            )
            retry_text = llm.chat_text(messages)
            messages.append({"role": "assistant", "content": retry_text})
            parsed = _extract_json_object(retry_text)
            if parsed is None:
                return None

        action_name = parsed.get("action")
        if not action_name or action_name == "final":
            return None
        return ToolAction(name=str(action_name), arguments=parsed.get("input") or {}, call_id=None)

    def append_tool_result(
        self,
        messages: list[dict[str, Any]],
        action: ToolAction,
        result: Any = None,
        error: Optional[str] = None,
    ) -> None:
        payload: dict[str, Any] = {"tool": action.name}
        if error is not None:
            payload["error"] = error
        else:
            payload["output"] = _jsonable(result)
        messages.append({"role": "user", "content": json.dumps(payload)})


def get_protocol(name: str) -> ToolProtocol:
    """Return the ToolProtocol implementation for 'native' or 'json' (spec section 4).

    The smoke test picks the protocol once (config.selected_tool_protocol()); the
    agent loop never decides per call.
    """
    if name == "native":
        return NativeToolProtocol()
    if name == "json":
        return JsonToolProtocol()
    raise ValueError(f"unknown tool protocol '{name}' (expected 'native' or 'json')")

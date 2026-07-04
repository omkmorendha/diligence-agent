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

TODO(Step 12): implement both protocols behind a shared interface:
    class ToolProtocol(Protocol):
        def next_action(self, messages, tools) -> ToolAction | FinalAction: ...
"""

from __future__ import annotations

import json
from typing import Any

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


def get_protocol(name: str):
    """Return the ToolProtocol implementation for 'native' or 'json'.

    TODO(Step 12): implement NativeToolProtocol and JsonToolProtocol.
    """
    raise NotImplementedError("tool_protocol: implement in Step 12 (spec section 4 / 25).")

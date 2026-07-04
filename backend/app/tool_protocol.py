"""ToolProtocol abstraction (spec section 4).

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

TODO(Step 12): implement both protocols behind a shared interface:
    class ToolProtocol(Protocol):
        def next_action(self, messages, tools) -> ToolAction | FinalAction: ...
"""

from __future__ import annotations


def get_protocol(name: str):
    """Return the ToolProtocol implementation for 'native' or 'json'.

    TODO(Step 12): implement NativeToolProtocol and JsonToolProtocol.
    """
    raise NotImplementedError("tool_protocol: implement in Step 12 (spec section 4 / 25).")

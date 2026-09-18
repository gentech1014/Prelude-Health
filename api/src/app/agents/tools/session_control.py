"""Tool: ends the live call.

Sharp, non-obvious edge case: setting `stop_event_loop` makes the agent
loop skip sending this tool's result back to the model as a spoken reply
-- it happens *instead of* a reply. A farewell string returned from this
tool is therefore never spoken. The farewell must be said by the model
*before* it calls this tool; that instruction lives in the system prompt
(see `app.agents.bidi.prompts`), not here.

The flag goes on `invocation_state["request_state"]`, which the
bidirectional loop creates before running tools and re-reads immediately
after each tool call. A plain `request_state` parameter does NOT work:
the runtime injects only `tool_context` and `agent`, so any other
parameter is treated as model-supplied input, and mutating a dict the
model invented would stop nothing.
"""

from typing import Any

from strands import tool
from strands.types.tools import ToolContext


@tool(context=True)
def end_session(tool_context: ToolContext) -> str:
    """End the pre-screening call.

    Call this only after you have already thanked the patient out loud --
    the string returned here is never spoken; ending the call happens
    instead of a spoken reply.

    Args:
        tool_context: injected by the agent runtime; carries the
            per-call `invocation_state`.
    """
    request_state: dict[str, Any] = tool_context.invocation_state.setdefault("request_state", {})
    request_state["stop_event_loop"] = True
    return "session closed"

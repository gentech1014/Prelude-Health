"""Tool: cancel the patient's appointment, mid-call.

`async def`, unlike the four oldest live-call tools -- Strands natively
awaits a coroutine tool function (verified against the installed
`strands.tools.decorator`), so this is a supported, if previously-unused,
shape in this codebase. It reads a `SchedulingService` off
`tool_context.invocation_state["scheduling"]`, wired into `agent.run()`'s
`invocation_state` the same way `question_bank` already is (see
`app.api.ws.intake`).

**Rescheduling lives in `app.agents.tools.appointment`, not here.** That
one is screen-driven and goes through `AppointmentService`, the same path
the patient's own reschedule screen calls, so the times the agent offers
and the times the screen shows are one list. `SchedulingService` moves a
session between rows of the `appointment_slots` collection and only onto
an earlier one, which is the wrong shape for a patient who wants a later
time and cannot be reconciled with what is on their screen.

This tool does not set `request_state["stop_event_loop"]` the way
`end_session` does: a cancel does not itself end the call -- the model
thanks the patient and calls the existing `end_session` afterward, same as
any other call ending. The WS handler instead re-reads the session's
*persisted* status after the loop returns to decide whether to skip
summarization for a cancelled appointment -- see `app.api.ws.intake`'s
docstring for why that, not a `request_state` flag, is the deliberate
signal.
"""

from strands import tool
from strands.types.tools import ToolContext

from app.services.scheduling_service import SchedulingService


@tool(context=True)
async def cancel_appointment(reason: str, tool_context: ToolContext) -> str:
    """Cancel the patient's appointment, freeing the doctor's slot.

    Call this only after the patient has clearly confirmed they want to
    cancel -- a misheard "cancel" frees a real slot someone else can
    immediately take, and that cannot be undone by this call alone. This
    does not end the call itself; say goodbye out loud and call
    `end_session` afterward, same as any other ending.

    Args:
        reason: why the patient is cancelling, in their own words.
        tool_context: injected by the agent runtime; carries the
            per-call `invocation_state`.
    """
    session_id = tool_context.invocation_state["session_id"]
    scheduling: SchedulingService = tool_context.invocation_state["scheduling"]
    await scheduling.cancel_appointment(session_id, reason, actor="voice_agent")
    return "The appointment has been cancelled."

"""Tools: the closing reschedule, driven from the patient's own screen.

The last question of the call is "is there anything I can help you with",
and the answer this pair exists for is "I need to move my appointment".
`offer_appointment_times` puts the doctor's real open times on the
patient's screen and hands the same list to the model -- to read the
patient's answer against, never to recite; `move_appointment` takes the
one they picked, writes it, and puts them back on the closing screen with
the new time showing.

Two decisions worth stating, because neither is obvious from the calls:

- **Both are gated on the closing screens.** A patient who asks to move
  their appointment in the middle of the intake is answered at the end,
  not served immediately -- a half-finished screening abandoned on a
  calendar screen is the one outcome neither the patient nor their doctor
  gets anything from. The gate is here rather than in the prompt because
  it has to hold whatever the model decides to try.
- **They share `AppointmentService` with the REST routes the screen
  calls.** The screen fetches its own list and confirms its own taps, so
  the agent offering times from a different source is not a cosmetic
  inconsistency -- it is the agent naming a time the patient cannot see
  and cannot tap.

Neither tool ends the call. Moving an appointment is the patient asking
for something, not saying goodbye: the prompt has the model go back to
asking whether there is anything else, and `end_session` happens after
that, the same as any other ending.
"""

from datetime import datetime
from typing import Any

import structlog
from strands import tool
from strands.types.tools import ToolContext

from app.agents.bidi.call_state import live_call
from app.core.constants import CLOSING_SCREENS, IntakeScreen
from app.core.exceptions import (
    AppointmentTimeUnavailableError,
    DoctorNotFoundError,
    InvalidSessionStateError,
    SessionNotFoundError,
)
from app.core.speech_time import format_spoken_datetime
from app.schemas import intake_channel
from app.services.appointment_service import AppointmentService

log = structlog.get_logger(__name__)

SPOKEN_SLOT_LIMIT = 6
"""How many open times the model is handed at once.

The screen shows every open time in the booking horizon and lets the
patient scroll. The model is handed a bounded window of the same list, not
because it reads them aloud -- it does not -- but because this is what it
matches the patient's answer against, and six spanning more than one day
is enough for "the Monday one" or "something later" to resolve.
"""


def _appointments(invocation_state: dict[str, Any]) -> AppointmentService:
    """Pull the appointment service out of a tool's `invocation_state`.

    Raises `KeyError` if absent, which is a wiring bug in the WebSocket
    route rather than anything a patient can trigger.
    """
    return invocation_state["appointments"]


@tool(context=True)
async def offer_appointment_times(tool_context: ToolContext) -> str:
    """Show the patient the times they could move their appointment to.

    Call this ONLY when the patient has asked to change, move or
    reschedule their appointment -- in their own words, or by tapping to
    ask for it on the closing screen. Never call it on your own
    initiative, and never in response to a patient saying there is nothing
    they need: answering "no" with a list of alternative appointment times
    is not help, and the call should simply close instead.

    When they have asked, it moves their screen to the list of open times
    and gives you the same times back. Do NOT read those times out loud:
    the patient can see all of them, and a list of clock times read at
    someone is hard to follow and impossible to hold on to. Say in one
    short sentence that you have their doctor's open times up and they can
    tell you which suits, or tap it themselves. Then wait.

    The list you get back is for matching their answer, not for reciting.
    Never say a `slot_id` aloud and never work out a time yourself. When
    the patient picks one, pass that option's `slot_id` to
    `move_appointment`.

    Args:
        tool_context: injected by the agent runtime; carries the
            per-call `invocation_state`.
    """
    invocation_state: dict[str, Any] = tool_context.invocation_state
    context = live_call(invocation_state)

    if context.progress.screen not in CLOSING_SCREENS:
        # Refused in code rather than discouraged in the prompt: a
        # screening abandoned halfway through for a calendar screen is
        # worth nothing to the patient's doctor.
        log.info(
            "reschedule_refused_mid_intake",
            session_id=context.session_id,
            screen=context.progress.screen.value,
        )
        return (
            "Not yet -- the screening is still in progress, and they are on "
            f"'{context.progress.screen.value}'. Tell them in one short sentence that "
            "you will sort the appointment time out at the end, then carry on with "
            "the question you were on. Offer it again once you reach 'thank-you'."
        )

    slots = await _appointments(invocation_state).spoken_slots(
        context.session_id, limit=SPOKEN_SLOT_LIMIT
    )
    if not slots:
        # The screen is deliberately not moved: sending the patient to an
        # empty list to look at is worse than being told plainly.
        log.info("reschedule_no_open_times", session_id=context.session_id)
        return (
            "There is nothing open with their doctor at the moment, so their "
            "appointment stays as it is. Tell them that plainly, say the clinic can "
            "find them another time, then ask whether there is anything else you can "
            "help with."
        )

    # The closing question has effectively been re-opened, so the eight
    # second force-end window must not be what is counting while the
    # patient reads a list of times -- see `LiveCallContext.closing_started`.
    context.closing_started = False
    await context.go_to(IntakeScreen.APPOINTMENT_RESCHEDULE)

    offered = "\n".join(f"- when: {slot.spoken_time} | slot_id: {slot.slot_id}" for slot in slots)
    log.info("reschedule_times_offered", session_id=context.session_id, count=len(slots))
    return (
        "These times are now on the patient's screen as tappable cards, and they are "
        "the only ones you may offer:\n"
        f"{offered}\n"
        "Do NOT read these times out loud. A list of clock times read at someone is "
        "hard to follow and impossible to hold on to, and they are already looking at "
        "every one of them. Say one short sentence instead: that you have their "
        "doctor's open times up, and they can tell you which suits or tap it "
        "themselves. Then stop and wait. Never read a slot_id aloud.\n"
        "The list above is for YOU, not for them. It is how you work out which time "
        "they mean when they answer in their own words, and it is where the slot_id "
        "for `move_appointment` comes from. If what they say matches nothing on it, "
        "say so plainly and ask again -- never invent a time and never offer one "
        "that is not listed."
    )


@tool(context=True)
async def move_appointment(slot_id: str, tool_context: ToolContext) -> str:
    """Move the patient's appointment to a time you have just offered.

    Only call this with a `slot_id` that came back from
    `offer_appointment_times` in this same call, and only once the patient
    has clearly said which time they want. If the time has been taken
    since you offered it, the reply says so plainly -- read that back and
    offer one of the others rather than treating it as a broken turn.

    On success the patient is put back on the closing screen with the new
    time showing. Say the new time back to them in one short sentence,
    then ask again whether there is anything else you can help with.

    Args:
        slot_id: the id of the time the patient chose, copied verbatim
            from `offer_appointment_times`.
        tool_context: injected by the agent runtime; carries the
            per-call `invocation_state`.
    """
    invocation_state: dict[str, Any] = tool_context.invocation_state
    context = live_call(invocation_state)

    if context.progress.screen not in CLOSING_SCREENS:
        return (
            "You have not offered them any times yet. Call `offer_appointment_times` "
            "first, and only move their appointment onto a time they have chosen from "
            "what it gives you."
        )

    try:
        start = datetime.fromisoformat(slot_id.strip())
    except ValueError:
        # A usable correction, not an exception: the patient is mid-
        # sentence and a raised error here surfaces as a broken turn.
        log.warning("reschedule_unparsable_slot", session_id=context.session_id)
        return (
            f"'{slot_id}' is not one of the times you were given. Call "
            "`offer_appointment_times` again and use a slot_id from its reply exactly "
            "as it is written."
        )

    try:
        updated, _ = await _appointments(invocation_state).move_to(context.session_id, start)
    except AppointmentTimeUnavailableError:
        log.info("reschedule_slot_taken", session_id=context.session_id)
        return (
            "That time has just been taken by someone else, so nothing has changed and "
            "their original appointment still stands. Say that in one short sentence, "
            "then offer one of the other times on their screen."
        )
    except InvalidSessionStateError:
        log.warning("reschedule_refused_session_state", session_id=context.session_id)
        return (
            "Their appointment cannot be changed from here. Say that the clinic will "
            "need to move it for them, and that nothing about their current "
            "appointment has changed."
        )
    except (SessionNotFoundError, DoctorNotFoundError):
        log.exception("reschedule_failed_lookup", session_id=context.session_id)
        return (
            "That could not be saved, and their original appointment is unchanged. Say "
            "so plainly and tell them the clinic will call to sort the time out."
        )

    spoken = format_spoken_datetime(updated.appointment_datetime, updated.appointment_timezone)
    # The browser holds its own copy of the appointment, fetched over REST
    # when the session was opened. Without this it goes on showing the old
    # time on the very screen the patient is being sent back to.
    context.bus.publish(intake_channel.appointment_updated())
    await context.go_to(IntakeScreen.THANK_YOU)
    log.info("reschedule_completed", session_id=context.session_id)
    return (
        f"Saved. Their appointment is now {spoken}, and they are back on the closing "
        "screen with the new time showing. Tell them it is updated and say that time "
        "back to them, in one short sentence, then ask whether there is anything else "
        "you can help with."
    )

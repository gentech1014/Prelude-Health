"""The WebSocket route that carries one patient's live intake call.

Bridges the browser to the `BidiAgent` and, when the call ends cleanly,
hands off to the summarization agent. How the call ends decides what
happens next, and the three endings are deliberately kept apart:

- the agent said goodbye and called `end_session` -> COMPLETED, summarized
- the patient pressed End, or their socket dropped -> INTERRUPTED, resumable
- the model or the socket failed -> INTERRUPTED, with an error frame sent first

Only the first produces a report, so the physician view can tell a
finished call from an abandoned one rather than rendering an empty summary
as though it were complete. The second is *resumable*: progress is
persisted as the call goes, so rejoining continues the conversation
instead of restarting it.

One task owns the wire. Agent output, tool-driven navigation and route
errors all originate in different tasks, so every frame is published onto
the call's `UiEventBus` and drained here -- a Starlette WebSocket has
exactly one safe writer.
"""

import asyncio
from enum import StrEnum
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from strands.experimental.bidi import BidiAgent

from app.agents.bidi.agent import build_live_agent
from app.agents.bidi.call_state import (
    EOF_EVENT_TYPE,
    INVOCATION_KEY,
    CallProgress,
    LiveCallContext,
    RecordedValue,
    UiEventBus,
)
from app.agents.bidi.outputs import MongoTranscriptWriter
from app.agents.bidi.symptom_plan import AskedQuestion, SymptomQuestionPlan
from app.api.deps import (
    SettingsDep,
    get_appointment_service,
    get_question_bank_service,
    get_scheduling_service,
    get_session_repository,
    get_session_service,
    get_ws_ticket_repository,
)
from app.api.ws.channels import BrowserInput, BrowserOutput, CallControl
from app.core.constants import (
    PRE_CONSENT_SCREENS,
    SCREEN_ORDER,
    AnswerStatus,
    IntakeScreen,
    SessionState,
    prescreening_category_reference,
    resolve_answer_status,
)
from app.core.security import verify_ws_ticket
from app.models.session import Session
from app.repositories.session_repository import SessionRepository
from app.repositories.ws_ticket_repository import WsTicketRepository
from app.schemas import intake_channel
from app.services.appointment_service import AppointmentService
from app.services.question_bank_service import QuestionBankService
from app.services.scheduling_service import SchedulingService
from app.services.session_service import SessionService

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/ws", tags=["intake"])

_POLICY_VIOLATION = 1008

_DRAIN_TIMEOUT_SECONDS = 3.0
"""How long to keep the socket open to flush the last frames.

Bounded because the common reason frames will not flush is that the
patient's connection is already gone, and waiting on a dead socket holds
a server task open for nothing."""

_CONNECTABLE_STATES = frozenset(
    {
        SessionState.NOTIFICATION_SENT,
        SessionState.STARTED,
        SessionState.IN_PROGRESS,
        SessionState.INTERRUPTED,
    }
)
"""States a patient may open a live socket from.

The pre-consent states are here because **consent is asked for in the
call.** The patient has to hear the greeting before being asked anything,
so the socket opens first and the agent is restricted until consent is
recorded -- every collection tool refuses, and navigation cannot leave the
consent screen. That restriction lives in the tools rather than here,
because it has to hold for the rest of the call and not just at the door.

INTERRUPTED is here because a dropped call is expected to be rejoined.
DECLINED, COMPLETED and everything past it are not: a finished session
keeps its consent record forever, and a refusal is a decision, not a
pause."""


class _Ending(StrEnum):
    """How one connection finished, which decides what happens to the session.

    Values are the wire's own `call_ended` reasons (see
    `intake_channel.CallEndReason`) so the two can never drift apart.
    """

    COMPLETED = "completed"
    """The agent said goodbye and called `end_session`. Summarize."""

    INTERRUPTED = "interrupted"
    """The patient hung up or their socket dropped. Resumable, no report."""

    FAILED = "failed"
    """Something on our side broke. Resumable, no report, patient told."""


FAREWELL_DRAIN_CAP_SECONDS = 20.0
"""Longest the call is held open purely to let the goodbye be heard.

`end_session` stops the agent loop the instant the model *calls* it, which
is a second or two after it finished generating the farewell and a good
several before the patient has heard it. Publishing `call_ended` there
told the browser the call was over mid-sentence, and the patient got the
goodbye cut off under them -- which, from their side, is the call hanging
and then abruptly dropping.

Capped because the playback estimate is the server's model of the
browser's queue, not a fact about it. A goodbye is two short sentences, so
twenty seconds is far beyond any honest farewell and still bounded.
"""


async def _let_the_farewell_be_heard(context: LiveCallContext) -> None:
    """Hold the finished call open until the goodbye has actually played."""
    remaining = min(context.remaining_playback_seconds(), FAREWELL_DRAIN_CAP_SECONDS)
    if remaining <= 0:
        return
    log.info(
        "intake_farewell_draining",
        session_id=context.session_id,
        seconds=round(remaining, 1),
    )
    await asyncio.sleep(remaining)


def _furthest_covered(session: Session) -> IntakeScreen:
    """The latest screen a consented call can be shown to have reached.

    Only consulted when consent is on record and the stored marker is a
    pre-consent screen, which is a contradiction: consent is what leaves
    `confirm-details`, and the app performs that move itself. Two things
    produce it.

    One is a socket that died between the two, leaving the marker one move
    behind. The other is the restart bug itself: a resumed call was told
    to begin from step 1, navigated back to `welcome`, and **persisted
    that as the marker** -- so a patient who dropped on `symptom-story`
    came back to `welcome`, and the reconnect after that one had no
    progress left to find at all. The marker is fixed going forward (see
    `build_call_opener`, and the refusal in `navigate_to_screen`), but
    sessions already carrying a wound-back marker have to be recoverable
    from what was actually collected rather than from what it says.

    So the answer is derived from evidence instead: the furthest screen
    holding a recorded value, `symptom-story` if any screening question
    was answered, floored at `patient-concerns` because consent alone
    already proves the call got that far. Landing a little behind is safe
    -- every value is restored with it, and both the resume block and
    `navigate_to_screen`'s reply list what is already answered, so the
    agent moves forward rather than asking again. Landing ahead would
    skip a topic outright.
    """
    reached = {IntakeScreen.PATIENT_CONCERNS}
    reached.update(
        IntakeScreen(key) for key in session.collected_details if key in set(IntakeScreen)
    )
    if session.symptom_answers:
        reached.add(IntakeScreen.SYMPTOM_STORY)
    # `SCREEN_ORDER` has no entry for `appointment-schedule`, which is not a
    # step the intake walks through -- a value recorded against it must not
    # be able to decide where the call resumes.
    return max(
        (screen for screen in reached if screen in SCREEN_ORDER),
        key=lambda screen: SCREEN_ORDER[screen],
    )


def _restore_progress(session: Session) -> CallProgress:
    """Rebuild the call's position from the session document.

    A first connection starts on `welcome`, which is where the agent
    greets before it has permission to ask anything.

    **So does every connection made before consent is recorded**, however
    far a previous attempt got. Consent is the only marker that the
    greeting and the consent request have actually happened: without it
    the agent starts again from step 1 and greets, so resuming onto
    `confirm-details` plays the introduction over the consent screen and
    the patient never sees `welcome` at all.

    Collected answers are deliberately *not* part of that test. An earlier
    attempt could leave a `last_screen` of `confirm-details` and a typed
    phone number behind without ever reaching consent, and treating that
    as progress skipped the greeting on every subsequent open -- which is
    exactly the bug it looked like.

    The mirror of that rule applies once consent *is* recorded: a
    pre-consent marker is then a contradiction rather than a position, and
    the call resumes from what was actually collected instead -- see
    `_furthest_covered`. Consent is what leaves `confirm-details` and the
    app performs that move itself, so a session holding both consent and a
    `confirm-details` marker is one whose socket died between the two, and
    resuming it there puts the patient back in front of a box they have
    already ticked.
    """
    has_progress = session.consent is not None and session.consent.given
    screen = session.last_screen if (session.last_screen and has_progress) else IntakeScreen.WELCOME
    if has_progress and screen in PRE_CONSENT_SCREENS:
        screen = _furthest_covered(session)
    # Words and certainty are stored in two parallel maps, so they are
    # recombined here. A value with no stored status reads as `confirmed`,
    # which is exactly how it was treated when it was written -- so a
    # session from before statuses existed resumes unchanged.
    details = {
        screen_key: {
            name: RecordedValue(
                text=text,
                status=resolve_answer_status(
                    session.collected_statuses.get(screen_key, {}).get(name)
                )
                or AnswerStatus.CONFIRMED,
            )
            for name, text in fields.items()
        }
        for screen_key, fields in session.collected_details.items()
    }
    selections = {
        screen_key: dict(fields) for screen_key, fields in session.collected_selections.items()
    }
    visited = [IntakeScreen(key) for key in details if key in set(IntakeScreen)]
    if screen not in visited:
        visited.append(screen)
    return CallProgress(screen=screen, details=details, selections=selections, visited=visited)


async def _restore_symptoms(
    session: Session, question_bank: QuestionBankService
) -> SymptomQuestionPlan:
    """Rebuild the screening conversation from the session document.

    Restores which appointment reasons were being screened, and every
    answer already collected. Both matter for different reasons: the
    reasons are what stop a resumed call re-opening with "shall we talk
    about your booking?", and the answers are what stop it re-asking
    something the patient has already sat through.

    The questions themselves are not restored as anything askable, because
    there is no longer a plan to restore -- the agent writes each one as it
    goes. What the answered list gives instead is the duplicate guard: a
    rewritten question that asks the same thing is refused by
    `already_asked`, which a stored id list could never have caught.

    Nothing is restored as the current question. Whatever was on screen
    when the connection dropped was mid-air: the agent asks again, and
    the patient sees it appear as a live question rather than a stale one
    with no voice behind it.
    """
    answered = [answer.model_copy() for answer in session.symptom_answers]
    plan = SymptomQuestionPlan(answered=answered)
    # Rebuilt from the answers rather than stored, so the duplicate guard
    # sees the resumed call's history as though it had asked it itself.
    plan.asked = [
        AskedQuestion(id=entry.question_id, category=entry.category, text=entry.question)
        for entry in answered
    ]
    # Set before the lookup below, not after, so a resumed call's live
    # reference query carries the same presentation signal a fresh
    # `start_prescreening` call would.
    plan.presentation = session.symptom_presentation
    presentation_label = plan.presentation.value if plan.presentation else ""
    for category in session.symptom_categories:
        query_text = f"{prescreening_category_reference(category)}: {presentation_label}"
        reference = await question_bank.reference_questions_live(category, query_text)
        plan.start(category, reference)
    return plan


async def _send_frames(websocket: WebSocket, bus: UiEventBus) -> None:
    """Drain the bus onto the socket until the sentinel, or until it dies.

    Returning (rather than raising) on a send failure is what lets the
    route treat "sender finished early" as "the patient is gone", without
    a socket error surfacing from inside the agent's task group.
    """
    while True:
        event = await bus.next_event()
        if event.get("type") == EOF_EVENT_TYPE:
            return
        try:
            await websocket.send_json(event)
        except Exception:
            log.debug("intake_ws_send_failed", event_type=event.get("type"))
            return


@router.websocket("/intake/{session_id}")
async def intake_call(
    websocket: WebSocket,
    session_id: str,
    ticket: str,
    settings: SettingsDep,
    session_repository: Annotated[SessionRepository, Depends(get_session_repository)],
    session_service: Annotated[SessionService, Depends(get_session_service)],
    question_bank: Annotated[QuestionBankService, Depends(get_question_bank_service)],
    scheduling: Annotated[SchedulingService, Depends(get_scheduling_service)],
    appointments: Annotated[AppointmentService, Depends(get_appointment_service)],
    ws_tickets: Annotated[WsTicketRepository, Depends(get_ws_ticket_repository)],
) -> None:
    """Authorize, accept, and run one connection of a patient's intake call.

    Rejects the connection *before* accepting it -- no protocol upgrade,
    minimal information leaked -- on a disallowed browser Origin (only
    when `allowed_origins` is configured), an invalid, expired or
    already-spent `ticket`, an unknown session, or a session in a state
    that has no business opening a live call.

    Consent is deliberately *not* a gate here -- it is asked for in the
    call, so the socket has to open before it exists. What replaces the
    gate is that the agent can do nothing but greet and ask until consent
    is recorded; see `_CONNECTABLE_STATES`. `session_id`s travel in URLs and webhook
    payloads and are not secrets on their own, which is why a signed
    credential is required alongside.

    The credential is a **single-use ticket**, not the patient's 48-hour
    intake token. A browser cannot set headers on a WebSocket handshake,
    so whatever authorizes the socket has to sit in the query string,
    where it lands in access logs and proxy traces. A ticket that expires
    in a minute and is spent on connect makes that exposure worth almost
    nothing; the intake token, which opens the whole session for two days,
    would not.
    """
    session = await _authorize(
        websocket, session_id, ticket, settings, session_repository, ws_tickets
    )
    if session is None:
        return

    await websocket.accept()

    if session.status is SessionState.INTERRUPTED:
        await session_service.resume_call(session_id)

    bus = UiEventBus()
    progress = _restore_progress(session)
    symptoms = await _restore_symptoms(session, question_bank)
    control = CallControl()
    # Logged from the restored position rather than the stored marker: a
    # session can hold a `last_screen` and still be starting from scratch.
    log.info(
        "intake_call_started",
        session_id=session_id,
        resumed=progress.screen is not IntakeScreen.WELCOME,
        screen=progress.screen.value,
    )
    bus.publish(
        intake_channel.connected(
            progress.screen,
            progress.as_storage(),
            progress.selections_as_storage(),
            symptoms.snapshot(),
        )
    )

    call_context = LiveCallContext(
        session_id=session_id,
        bus=bus,
        progress=progress,
        sessions=session_repository,
        symptoms=symptoms,
        question_bank=question_bank,
        consent_given=session.consent is not None and session.consent.given,
    )
    agent = build_live_agent(session, settings, progress, symptoms)

    invocation_state: dict[str, Any] = {
        "session_id": session_id,
        "question_bank": question_bank,
        "scheduling": scheduling,
        "appointments": appointments,
        INVOCATION_KEY: call_context,
    }
    inputs = [BrowserInput(websocket, call_context, control)]
    outputs = [
        BrowserOutput(call_context, control),
        MongoTranscriptWriter(session_repository, session_id),
    ]

    ending = await _supervise(agent, inputs, outputs, invocation_state, websocket, bus, control)

    if ending is _Ending.COMPLETED:
        # Before the frame that ends it, not after: the browser tears down
        # playback on `call_ended`, so anything still queued is lost.
        await _let_the_farewell_be_heard(call_context)

    if ending is _Ending.FAILED:
        bus.publish(
            intake_channel.error(
                "call_failed",
                "The call ended unexpectedly. Reopen your link to carry on where you left off.",
                recoverable=True,
            )
        )
    bus.publish(intake_channel.call_ended(ending.value))
    bus.close()
    await _drain(websocket, bus)

    # Both records are flushed once more at the end regardless of how the
    # call finished: the last tool call before a drop may not have persisted.
    await call_context.persist_progress()
    await call_context.persist_symptoms()
    # And what this call asked is folded into the question bank for the
    # reason it screened, so the next patient booking the same reason
    # starts from a better reference set than this one did. Every ending
    # reaches here, so a call cut short still contributes what it managed.
    await call_context.flush_question_bank()

    if ending is _Ending.COMPLETED:
        log.info("intake_call_completed", session_id=session_id)
        # Awaited rather than backgrounded: the patient has already hung
        # up, so the few seconds cost them nothing, and a fire-and-forget
        # task could be collected before it finishes and silently lose the
        # report.
        await session_service.complete_call(session_id)
        return

    log.info("intake_call_interrupted", session_id=session_id, ending=ending.value)
    await session_service.mark_interrupted(session_id)


async def _authorize(
    websocket: WebSocket,
    session_id: str,
    ticket: str,
    settings: SettingsDep,
    session_repository: SessionRepository,
    ws_tickets: WsTicketRepository,
) -> Session | None:
    """Run every pre-accept check, closing the socket on the first failure.

    Returns the session on success and None once the socket is already
    closed, so the caller has nothing to decide.
    """
    if settings.allowed_origins:
        origin = websocket.headers.get("origin")
        allowed = {o.strip() for o in settings.allowed_origins.split(",") if o.strip()}
        if origin not in allowed:
            log.warning("intake_ws_rejected_bad_origin", session_id=session_id, origin=origin)
            await websocket.close(code=_POLICY_VIOLATION)
            return None

    nonce = verify_ws_ticket(session_id, ticket, settings.intake_link_secret)
    if nonce is None:
        log.warning("intake_ws_rejected_bad_ticket", session_id=session_id)
        await websocket.close(code=_POLICY_VIOLATION)
        return None

    # Claimed before any other check: a ticket presented twice is spent the
    # first time regardless of whether that first attempt went on to succeed.
    if not await ws_tickets.spend(session_id, nonce, settings.ws_ticket_ttl_seconds * 2):
        log.warning("intake_ws_rejected_replayed_ticket", session_id=session_id)
        await websocket.close(code=_POLICY_VIOLATION)
        return None

    session = await session_repository.get_by_id(session_id)
    if session is None:
        log.warning("intake_ws_rejected_unknown_session", session_id=session_id)
        await websocket.close(code=_POLICY_VIOLATION)
        return None

    if session.status not in _CONNECTABLE_STATES:
        log.warning(
            "intake_ws_rejected_bad_state", session_id=session_id, status=session.status.value
        )
        await websocket.close(code=_POLICY_VIOLATION)
        return None

    return session


async def _supervise(
    agent: BidiAgent,
    inputs: list[Any],
    outputs: list[Any],
    invocation_state: dict[str, Any],
    websocket: WebSocket,
    bus: UiEventBus,
    control: CallControl,
) -> _Ending:
    """Race the agent, the sender, the patient's End button, and the thank-you watchdog.

    Four things can finish a connection and only the first to happen
    matters, so they are raced rather than sequenced. The sender finishing
    early means the socket is dead, which is why it is in the race at all:
    without it, a patient who closes their phone leaves the agent talking
    to nobody until the model times out. `force_complete` is
    `BrowserOutput`'s watchdog ending a screening that finished in every
    way but the model's own `end_session` call -- see `CallControl` for
    why it is not routed through `hangup`.
    """
    run_task = asyncio.create_task(_drive_agent(agent, inputs, outputs, invocation_state))
    sender_task = asyncio.create_task(_send_frames(websocket, bus))
    hangup_task = asyncio.create_task(control.hangup.wait())
    force_complete_task = asyncio.create_task(control.force_complete.wait())
    race = {run_task, sender_task, hangup_task, force_complete_task}

    try:
        done, pending = await asyncio.wait(race, return_when=asyncio.FIRST_COMPLETED)
    except asyncio.CancelledError:
        for task in race:
            task.cancel()
        raise

    for task in pending:
        task.cancel()
    # `return_exceptions`: a cancelled agent unwinds through its own
    # TaskGroup and can surface a CancelledError group we have no use for.
    await asyncio.gather(*pending, return_exceptions=True)

    if force_complete_task in done:
        return _Ending.COMPLETED

    if hangup_task in done:
        return _Ending.INTERRUPTED

    if run_task in done:
        try:
            return run_task.result()
        except Exception:
            log.exception("intake_call_errored")
            return _Ending.FAILED

    # Only the sender is left, and it only returns when the socket is gone.
    return _Ending.INTERRUPTED


async def _drive_agent(
    agent: BidiAgent,
    inputs: list[Any],
    outputs: list[Any],
    invocation_state: dict[str, Any],
) -> _Ending:
    """Run `agent.run()` to completion and classify how it ended.

    `except*`, not `except`: `run()` drives its inputs and outputs in a
    TaskGroup, so a disconnect surfaces wrapped in an ExceptionGroup and a
    plain `except WebSocketDisconnect` would never fire. A flag carries
    the outcome out, because `return` is not permitted inside an `except*`
    block.

    `run()` also stops the agent and every I/O channel in its own
    `finally`, so the caller must not stop them again.
    """
    disconnected = False
    try:
        await agent.run(inputs=inputs, outputs=outputs, invocation_state=invocation_state)
    except* WebSocketDisconnect:
        disconnected = True

    return _Ending.INTERRUPTED if disconnected else _Ending.COMPLETED


async def _drain(websocket: WebSocket, bus: UiEventBus) -> None:
    """Flush whatever is still queued, then close the socket.

    Bounded by `_DRAIN_TIMEOUT_SECONDS` and never raises: this runs after
    the call is already decided, and the most likely reason a frame will
    not go out is that the patient's connection died a moment ago.
    """
    try:
        await asyncio.wait_for(_send_frames(websocket, bus), _DRAIN_TIMEOUT_SECONDS)
    except Exception:
        log.debug("intake_ws_drain_incomplete")

    try:
        await websocket.close()
    except Exception:
        log.debug("intake_ws_close_failed")

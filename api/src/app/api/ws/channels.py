"""WebSocket I/O channels bridging the browser to the live agent.

Strands' own example passes `websocket.receive_json` and
`websocket.send_json` straight into `agent.run()`. That works in a demo,
but it makes the browser's payload the agent's input format, which is
wrong on two counts: nothing validates it, so one malformed frame raises
inside the agent's task group and drops the call; and everything the
agent emits -- tool names, streamed tool arguments, token usage, exception
class names -- goes out to the patient's browser verbatim.

Both directions are translated instead, against the schema in
`app.schemas.intake_channel`. Neither channel writes to the socket: they
publish onto the call's `UiEventBus`, and one sender task owns the wire
(see `app.api.ws.intake`), because a Starlette WebSocket has exactly one
safe writer and frames originate here, in the tool layer, and in the
route itself.
"""

import asyncio
import base64
import binascii
from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import WebSocket
from strands.experimental.bidi import BidiAgent
from strands.experimental.bidi.types.events import BidiAudioInputEvent, BidiTextInputEvent
from strands.experimental.bidi.types.io import BidiInput, BidiOutput

from app.agents.bidi.call_state import LiveCallContext
from app.agents.bidi.prompts import build_call_opener
from app.agents.bidi.transcript_assembly import TranscriptAssembler
from app.core.constants import CLOSING_SCREENS, SCREEN_FIELD_LABELS, IntakeScreen
from app.core.speech_time import format_spoken_datetime
from app.models.transcript import ConversationTurn
from app.schemas import intake_channel
from app.schemas.intake_channel import (
    ClientAppointmentRescheduled,
    ClientAudioFrame,
    ClientConsentRecorded,
    ClientControl,
    ClientDocumentUploaded,
    ClientDocumentUploadFailed,
    ClientFormUpdate,
    ClientPing,
    ClientRescheduleRequested,
    ClientScreenAck,
    ClientSymptomAnswer,
    ClientTextTurn,
    parse_client_event,
)

log = structlog.get_logger(__name__)

_SILENCE_FRAME = base64.b64encode(bytes(1024)).decode()
"""One ~32 ms frame of digital silence: 512 PCM16 mono samples at 16 kHz,
the same frame size the browser sends."""

MAX_PENDING_FORM_UPDATES = 20
"""How many pre-consent field edits are held back at once.

Bounded because the buffer is filled straight from the socket: without a
cap a client could grow it without limit. Twenty is far more corrections
than the one screen reachable before consent has fields to correct."""

GREETING_SILENCE_SECONDS = 3.0
"""How long the agent must stop producing audio before the greeting may end.

A watchdog on the one transition the app owns rather than the model. The
primary signal is `bidi_response_complete` plus the model's own request to
leave `welcome`, and when Nova Sonic sends neither the patient is stranded
on the introduction screen for the whole call -- observed live, with the
greeting spoken in full and the assistant going on to ask for consent
against a screen that still said hello.

Three seconds, because it measures the gap *between audio chunks* rather
than elapsed time. Nova streams a turn faster than real time, so a gap
this long inside one utterance does not occur while the gap after the last
chunk always exceeds it.

**That window alone was never the right condition, and on its own it was
the greeting bug rather than a guard against it.** Generating is not
hearing: the whole greeting is queued in a second or two and takes twenty
to play, so this elapsed three seconds while the patient was five seconds
into it -- on every call, not merely the broken ones. So the sleep is
followed by waiting out `LiveCallContext.remaining_playback_seconds()`,
and what the two together mean is "the agent has stopped talking *and*
the patient has finished listening"."""

THANK_YOU_FORCE_END_SECONDS = 8.0
"""How long to wait, after a completed turn on `thank-you`, before ending
the call regardless of whether the model called `end_session`.

The same watchdog shape as `GREETING_SILENCE_SECONDS` above, on the other
transition the app cannot leave to the model: step 18 of the prompt says
to call `end_session` right after saying goodbye, but a model that skips
the call, calls it with a farewell still queued behind it, or simply gets
stuck, otherwise leaves the patient connected to a call with nothing left
to say. Eight seconds is comfortably past the "end in a few seconds" the
prompt has the model say out loud, so it never cuts a genuine goodbye
short, and short enough that a patient is never left waiting on a call
that is, in every way that matters, already over.

Applies only once `LiveCallContext.closing_started` is set. Before that
the agent has just asked the patient a question on this same screen, and
eight seconds of thought is not a finished call -- see
`THANK_YOU_IDLE_END_SECONDS`."""

THANK_YOU_IDLE_END_SECONDS = 120.0
"""How long to wait on `thank-you` before the closing has actually begun.

`thank-you` carries a question as well as the goodbye: step 15 asks
whether there is anything the patient would like help with. Arming the
eight-second window on that turn ended the call under patients who were
still thinking -- and marked it completed, so the report was generated
without the thing they were in the middle of saying.

Two minutes, and cancelled by any inbound frame, so it only fires on a
patient who really has gone. It is also the backstop for the other half
of the problem: a patient who declines that last question never records
an answer to it, so `closing_started` never sets, and the call would
otherwise hang open if the model then stalled."""

CLOSING_PROMPT_SILENCE_SECONDS = 4.0
"""How long to give the model to close before prompting it to.

The mirror of `CONSENT_PROMPT_SILENCE_SECONDS`, on the other end of the
call and for exactly the same reason: Nova Sonic produces a turn only
when something reaches its input stream, so a patient who answers the
closing question and gets nothing back is simply stuck. The screen said
"thank you", the agent said nothing, and the only thing that eventually
broke the silence was the audio-gap filler happening to provoke a turn --
which is what a patient experiences as the call sitting dead and then
abruptly speaking.

Measured from the patient's last word and cancelled by any agent speech,
so a model that is closing normally is never interrupted.
"""

CONSENT_PROMPT_SILENCE_SECONDS = 2.5
"""How long to give the model to ask for consent before prompting it to.

Measured from the moment the patient's screen reaches `confirm-details`,
and it fires only if the model has produced **no audio at all** since --
so a model that is speaking is never interrupted, however slowly.

Short, and it can afford to be. The server is well ahead of the patient's
ears here: the greeting arrives from Nova in a second or two and takes
twenty to play, so a nudge sent now produces speech that simply queues
behind the greeting and lands seamlessly after it. Waiting longer buys no
safety and risks the patient hearing an actual gap.

It does not delay the screen either -- the browser sized its navigation
hold when the navigate frame arrived, so audio queued afterwards does not
push the move back."""

_AUDIO_GAP_SECONDS = 0.25
"""How long to wait for the patient's audio before sending silence instead.

Nova Sonic will not generate a turn unless its audio content stream is
moving -- verified live, twice: a text input alone produced nothing over 20
seconds, while the same text followed by silence frames produced 94 audio
chunks and a spoken greeting. It buffers text and lets the audio stream
drive generation.

So a patient who declines the microphone, or whose browser cannot capture
audio, would otherwise get a call that connects and then sits in silence
forever -- and their typed answers would go equally unanswered, which
defeats the whole point of keeping a typed path. Filling the gaps here is
what makes the typed-only path actually work.

Only gaps are filled. While the microphone is running its frames arrive
every ~32 ms and this never fires, so a speaking patient's audio is never
interleaved with injected silence.
"""


class CallControl:
    """The in-call switches the patient can flip, shared with the route.

    `hangup` is an `asyncio.Event` rather than a flag because the route
    supervises it alongside `agent.run()`: the patient pressing End must
    tear the call down promptly, not on the next inbound frame, which may
    never arrive if they have already put the phone down.

    `force_complete` is a separate event rather than reusing `hangup`
    because the two mean different things to the route: `hangup` is the
    patient walking away mid-call -- interrupted, resumable, no report --
    while `force_complete` is the `thank-you` watchdog in `BrowserOutput`
    ending a screening that was already finished in every way but the
    model's own `end_session` call. Routing it through `hangup` would have
    torn the call down the same way, but marked it interrupted and skipped
    the report for a call that actually completed.
    """

    def __init__(self) -> None:
        self.muted = False
        self.on_hold = False
        self.hangup = asyncio.Event()
        self.force_complete = asyncio.Event()

    @property
    def accepting_audio(self) -> bool:
        """Whether mic frames should reach the model right now.

        Enforced server-side as well as client-side on purpose: mute is a
        privacy control, and a control that depends on the client
        honouring it is not one.
        """
        return not (self.muted or self.on_hold)


class BrowserInput(BidiInput):
    """Reads the patient's frames off the socket and translates them.

    `__call__` must return something `agent.send()` accepts, so frames
    that are not model input -- acks, controls, pings -- are handled as
    side effects and the loop reads again. A frame that fails validation
    is answered with an error and skipped; it never propagates.

    The socket is drained by its own task rather than awaited directly in
    `__call__`, so a gap in the patient's audio can be filled with silence
    without cancelling a partially-received message. Cancelling
    `receive_json()` mid-await is not safe -- anyio can drop the frame it
    was in the middle of -- so the read never gets cancelled at all.
    """

    def __init__(
        self,
        websocket: WebSocket,
        context: LiveCallContext,
        control: CallControl,
    ) -> None:
        self._websocket = websocket
        self._context = context
        self._bus = context.bus
        self._control = control
        self._progress = context.progress
        self._has_opened = False
        self._pending_form_updates: list[ClientFormUpdate] = []
        self._inbound: asyncio.Queue[Any] = asyncio.Queue()
        self._reader: asyncio.Task[None] | None = None

    async def start(self, agent: BidiAgent) -> None:
        """Begin draining the socket. It is already accepted by the route."""
        self._reader = asyncio.create_task(self._drain_socket())

    async def _drain_socket(self) -> None:
        """Move every inbound frame onto the queue, disconnects included.

        A `WebSocketDisconnect` raised in here would be invisible to the
        agent's task group, so it is queued as a value and re-raised from
        `__call__` -- which is inside that group, and is what tears the
        call down through the normal disconnect path.
        """
        try:
            while True:
                await self._inbound.put(await self._websocket.receive_json())
        except BaseException as exc:  # noqa: BLE001 - re-raised at the read site
            await self._inbound.put(exc)

    async def __call__(self) -> BidiAudioInputEvent | BidiTextInputEvent:
        """Await the next frame that is actually input for the model.

        Returns the call opener first, before reading anything from the
        socket, because the model stays silent until it is given an input
        -- see `build_call_opener`. The patient does not have to say
        anything, or even have a working microphone, for the call to start.

        The opener is **built from the restored position**, not a
        constant. A fixed "begin from step 1" was the last thing in the
        model's history on every connection, resumed ones included, and a
        user turn saying that outranks anything the system prompt says
        about resuming: the agent greeted again, walked the patient back
        to the consent screen they had already passed, and re-asked what
        they had already answered.
        """
        if not self._has_opened:
            self._has_opened = True
            opener = build_call_opener(
                self._context.progress,
                self._context.consent_given,
                self._context.symptoms,
            )
            log.info(
                "intake_call_opener_sent",
                session_id=self._context.session_id,
                screen=self._context.progress.screen.value,
                consent_given=self._context.consent_given,
            )
            return BidiTextInputEvent(text=opener, role="user")

        while True:
            # The app's own messages to the model jump the queue: they
            # exist because the model has gone quiet at a step it was
            # supposed to perform, so waiting on the patient -- who is
            # waiting on the model -- would deadlock the call.
            try:
                nudge = self._context.nudges.get_nowait()
            except asyncio.QueueEmpty:
                pass
            else:
                log.info("intake_nudge_sent", session_id=self._context.session_id)
                return BidiTextInputEvent(text=nudge, role="user")

            try:
                payload = await asyncio.wait_for(self._inbound.get(), _AUDIO_GAP_SECONDS)
            except TimeoutError:
                # The patient's audio has a gap. Keep the model's stream
                # moving so it can still speak -- see `_AUDIO_GAP_SECONDS`.
                return self._silence()

            if isinstance(payload, BaseException):
                raise payload

            event = parse_client_event(payload)
            if event is None:
                log.warning("intake_client_frame_rejected")
                self._bus.publish(
                    intake_channel.error(
                        "bad_frame",
                        "Something went wrong sending your answer. Try again.",
                        recoverable=True,
                    )
                )
                continue

            model_input = await self._handle(event)
            if model_input is not None:
                return model_input

    async def _patient_turn(self, text: str) -> BidiTextInputEvent:
        """Send a typed answer to the model, and put it in the transcript.

        The transcript is what the report is built from, and nothing else
        writes this one: `MongoTranscriptWriter` records
        `bidi_transcript_stream` events, which the model emits for speech
        it heard, never for text it was handed. Without this a patient who
        types every answer produced a transcript of the assistant talking
        to itself, and a report drawn from nothing they said.

        Best-effort, like every other transcript write: a failed insert
        costs a line in the report, and raising here would cost the call.
        """
        try:
            await self._context.sessions.append_turn(
                self._context.session_id,
                ConversationTurn(role="user", text=text, ts=datetime.now(UTC)),
            )
        except Exception:
            log.exception("intake_typed_turn_write_failed", session_id=self._context.session_id)

        return BidiTextInputEvent(text=text, role="user")

    @staticmethod
    def _silence() -> BidiAudioInputEvent:
        return BidiAudioInputEvent(
            audio=_SILENCE_FRAME,
            format="pcm",
            sample_rate=intake_channel.AUDIO_SAMPLE_RATE,
            channels=1,
        )

    async def _handle(self, event: Any) -> BidiAudioInputEvent | BidiTextInputEvent | None:
        """Apply one validated frame, returning model input when there is any."""
        if isinstance(event, ClientAudioFrame):
            if not self._control.accepting_audio:
                return None
            if not _is_valid_base64(event.audio):
                log.warning("intake_audio_frame_not_base64")
                return None
            return BidiAudioInputEvent(
                audio=event.audio,
                format="pcm",
                sample_rate=intake_channel.AUDIO_SAMPLE_RATE,
                channels=1,
            )

        if isinstance(
            event,
            (
                ClientTextTurn,
                ClientFormUpdate,
                ClientSymptomAnswer,
                ClientRescheduleRequested,
                ClientAppointmentRescheduled,
                ClientDocumentUploaded,
                ClientDocumentUploadFailed,
            ),
        ):
            # Anything the patient typed. Counted here rather than per
            # handler so no typed path can be added later that silently
            # reads as absence -- see `LiveCallContext.patient_activity`.
            self._context.note_patient_activity()

        if isinstance(event, ClientTextTurn):
            # Marked "(typed)" so the model can acknowledge it as something
            # written rather than said, and the summarizer still reads one
            # conversation. The mark is the only addition -- the patient's
            # words are passed through untouched.
            return await self._patient_turn(f"(typed) {event.text}")

        if isinstance(event, ClientFormUpdate):
            if not event.value.strip():
                # An emptied box is a retraction, not a no-op. Dropping it
                # here was one of three layers that silently discarded it,
                # so a patient who watched the agent mishear them and
                # cleared the field could not take the wrong value back --
                # it stayed on record and reached their doctor.
                return await self._retract_field(event)
            if not self._context.consent_given:
                # The same boundary every tool enforces. A typed field was
                # the one path around it: it recorded, and the route's
                # end-of-call flush persisted it, so a call abandoned on the
                # consent screen left collected answers behind for a patient
                # who never agreed to any collection.
                #
                # Held rather than dropped -- the phone field is on the
                # consent screen, so correcting it there is the natural
                # moment, and consent is usually seconds away.
                self._hold_until_consent(event)
                return None
            return await self._apply_form_update(event)

        if isinstance(event, ClientSymptomAnswer):
            return await self._on_symptom_answer(event)

        if isinstance(event, ClientScreenAck):
            if event.screen != self._progress.screen:
                # Worth a log line rather than a correction: the browser is
                # showing something the call does not think it asked for,
                # which is the exact desync this protocol exists to catch.
                log.warning(
                    "intake_screen_ack_mismatch",
                    expected=self._progress.screen.value,
                    acknowledged=event.screen.value,
                )
            return None

        if isinstance(event, ClientConsentRecorded):
            return await self._on_consent_recorded()

        if isinstance(event, ClientRescheduleRequested):
            return self._on_reschedule_requested()

        if isinstance(event, ClientAppointmentRescheduled):
            return await self._on_appointment_rescheduled()

        if isinstance(event, ClientDocumentUploaded):
            return await self._on_document_uploaded(event)

        if isinstance(event, ClientDocumentUploadFailed):
            return self._on_document_upload_failed()

        if isinstance(event, ClientControl):
            self._apply_control(event.action)
            return None

        if isinstance(event, ClientPing):
            self._bus.publish(intake_channel.pong())
            return None

        return None

    async def _on_symptom_answer(self, event: ClientSymptomAnswer) -> BidiTextInputEvent | None:
        """Take an answer the patient typed on the symptom screen.

        Covers both answering the live question and correcting one already
        answered -- the same write either way. Recorded here rather than
        left for the model to record, so the answer is safe the moment
        they type it and survives a drop before the next model turn; the
        turn returned is what tells the model it landed, so it acknowledges
        and moves on instead of re-asking.

        The question stays on screen: clearing it here would empty the box
        the patient is still typing into. The agent takes it down when it
        moves on.

        Refused for a question outside the loaded plan: a client-supplied
        id must never be able to invent a finding in the physician's
        report.
        """
        value = event.value.strip()
        if not value:
            return None

        plan = self._context.symptoms
        recorded = plan.record(event.question_id, value, clear_current=False)
        if recorded is None:
            log.warning(
                "intake_symptom_answer_rejected",
                session_id=self._context.session_id,
                question_id=event.question_id,
            )
            return None

        self._context.publish_symptom_state()
        await self._context.persist_symptoms()

        # Say which question this answered, and that it is already saved.
        # Both matter. A bare "(typed) ..." turn gave the model no way to
        # tell a correction to an earlier answer from a fresh answer to
        # the live question, and `record_symptom_answer` can only ever
        # write to whatever is currently on screen -- so a patient fixing
        # their answer to question three had it re-recorded on top of
        # question seven, destroying one answer and falsifying another.
        live = self._context.symptoms.current
        is_correction = live is None or live.id != recorded.question_id
        if is_correction:
            still_open = (
                f' The question still on their screen is "{live.text}" and is still '
                "waiting for an answer."
                if live is not None
                else ""
            )
            return await self._patient_turn(
                f"(typed) {recorded.question}: {value}\n(system) That was the patient "
                f'editing their earlier answer to "{recorded.question}". It is already '
                "saved -- do NOT call record_symptom_answer for it. Acknowledge the "
                f"correction in one short sentence and carry on.{still_open}"
            )
        return await self._patient_turn(f"(typed) {recorded.question}: {value}")

    def _hold_until_consent(self, event: ClientFormUpdate) -> None:
        """Keep one pre-consent edit until consent makes it collectable."""
        if len(self._pending_form_updates) >= MAX_PENDING_FORM_UPDATES:
            log.warning("intake_pending_form_updates_full", session_id=self._context.session_id)
            return
        # Superseded by a newer edit to the same field, so only the last
        # value the patient left in the box is ever applied.
        self._pending_form_updates = [
            pending
            for pending in self._pending_form_updates
            if not (pending.screen == event.screen and pending.field == event.field)
        ]
        self._pending_form_updates.append(event)

    async def _retract_field(self, event: ClientFormUpdate) -> BidiTextInputEvent | None:
        """Take a value the patient cleared back off the record.

        Clears the agent's option tick alongside the words -- see
        `CallProgress.clear`. Without that, wiping the box left the card
        the agent had guessed still selected, so the patient's own
        correction was overruled by the inference it was correcting.
        """
        if not self._context.consent_given:
            return None
        removed = self._progress.clear(event.screen, [event.field])
        if not removed:
            return None

        label = SCREEN_FIELD_LABELS.get(event.field, event.field)
        await self._context.persist_progress()
        log.info(
            "intake_field_retracted",
            session_id=self._context.session_id,
            screen=event.screen.value,
            field=event.field,
        )
        return BidiTextInputEvent(
            text=(
                f"(system) The patient cleared their answer for '{label}' on screen. "
                "Whatever you had recorded there is gone and must not be used again. "
                "Ask them for it once, in one short sentence, and accept whatever "
                "they say -- including that they would rather not answer."
            ),
            role="user",
        )

    async def _apply_form_update(self, event: ClientFormUpdate) -> BidiTextInputEvent:
        """Record one field edit and fold it into the transcript as the patient's turn.

        The patient's own edit wins over anything the agent guessed, so
        the field's selection is cleared before the new words are written:
        `record_selections` only ever merged, which left a card the agent
        ticked on a mishearing selected underneath the correction.
        """
        label = SCREEN_FIELD_LABELS.get(event.field, event.field)
        value = event.value.strip()
        self._progress.selections.get(event.screen.value, {}).pop(event.field, None)
        outcome = self._progress.record(event.screen, {event.field: value}, source="typed")
        if not outcome.accepted:
            # The same refusal the agent's own path gets. A patient can
            # type "skip" into a box as easily as say it, and it is no
            # more an answer for having been typed. Their words still
            # reach the model -- they said something and it belongs in the
            # transcript -- but the model is told it did not land, so it
            # asks rather than assuming the field is now filled.
            log.info(
                "intake_typed_value_refused",
                session_id=self._context.session_id,
                field=event.field,
            )
            reason = outcome.non_answers[0][1] if outcome.non_answers else "it is not an answer"
            return await self._patient_turn(
                f"(typed) {label}: {value}\n(system) That was not recorded, because "
                f"{reason}. Ask them for it once more, and tell them they may skip it "
                "if they would rather not say."
            )
        await self._context.persist_progress()
        return await self._patient_turn(f"(typed) {label}: {value}")

    async def _release_pending_form_updates(self) -> None:
        """Apply everything the patient typed while consent was still outstanding."""
        held, self._pending_form_updates = self._pending_form_updates, []
        for event in held:
            await self._apply_form_update(event)
        if held:
            log.info(
                "intake_pending_form_updates_released",
                session_id=self._context.session_id,
                count=len(held),
            )

    async def _on_consent_recorded(self) -> BidiTextInputEvent | None:
        """Unlock the rest of the call, if the session really does say so.

        The flag is set from the database, not from the frame: a client
        claiming consent it never gave unlocks nothing. Returning a text
        turn is what tells the model to move on -- it has been sitting on
        the consent screen waiting, and nothing else would release it.
        """
        if not await self._context.refresh_consent():
            log.warning("intake_consent_claim_unverified", session_id=self._context.session_id)
            return None

        log.info("intake_consent_confirmed", session_id=self._context.session_id)

        await self._release_pending_form_updates()

        # Moved here rather than left to the model. The step after consent
        # is fixed -- there is no branch to decide -- and the model has been
        # seen asking the first question while the patient was still looking
        # at the consent screen. A certainty beats a nudge for a transition
        # with only one possible destination.
        await self._context.go_to(IntakeScreen.PATIENT_CONCERNS)

        return BidiTextInputEvent(
            text=(
                "(system) The patient has given consent and it is recorded. They "
                "are now on 'patient-concerns'. Thank them in one short sentence, "
                "then continue from step 6 -- you do not need to navigate again."
            ),
            role="user",
        )

    def _on_reschedule_requested(self) -> BidiTextInputEvent:
        """The patient asked for a new time by tapping rather than saying so.

        Handed straight to the model as a request, not performed here: the
        tool it names is what moves the screen and picks the times, so a
        tap and a spoken "can I move my appointment" go down one path and
        the patient hears the same offer either way.
        """
        log.info("intake_reschedule_requested", session_id=self._context.session_id)
        return BidiTextInputEvent(
            text=(
                "(system) The patient has asked, on screen, to change their appointment "
                "time. Call `offer_appointment_times` now, then offer them two or three "
                "of the times it gives you and ask which suits them."
            ),
            role="user",
        )

    async def _on_appointment_rescheduled(self) -> BidiTextInputEvent | None:
        """The patient moved their appointment themselves, by tapping a time.

        The write already happened over REST, so the only things left are
        server-side state the agent reasons from: the patient is back on
        the closing screen, the closing question is open again, and the
        new time has to be read from the session rather than taken from
        the browser -- which is why this frame carries no time at all.
        """
        session = await self._context.sessions.get_by_id(self._context.session_id)
        if session is None:
            log.warning(
                "intake_appointment_rescheduled_unknown_session",
                session_id=self._context.session_id,
            )
            return None

        spoken = format_spoken_datetime(session.appointment_datetime, session.appointment_timezone)
        # The last question of the call is open again, so the eight-second
        # goodbye window must not be what is counting while they answer it.
        self._context.closing_started = False
        await self._context.go_to(IntakeScreen.THANK_YOU)
        log.info("intake_appointment_rescheduled_on_screen", session_id=self._context.session_id)

        return BidiTextInputEvent(
            text=(
                f"(system) The patient moved their appointment to {spoken} themselves, on "
                "screen. It is already saved -- do NOT call `move_appointment` for it. "
                "They are back on the closing screen. Confirm the new time in one short "
                "sentence, then ask whether there is anything else you can help with."
            ),
            role="user",
        )

    async def _on_document_uploaded(self, event: ClientDocumentUploaded) -> BidiTextInputEvent:
        """The patient's document landed. Now find out what it is.

        Before this frame existed the upload was invisible to the agent:
        the file goes to object storage over REST, which the socket cannot
        see, so the only thing that ever moved the conversation on was the
        patient volunteering "I have uploaded it" -- which they had no way
        of knowing they were expected to say. The agent meanwhile had been
        told not to wait and not to ask whether it finished, so a patient
        who did exactly as asked sat in silence holding a delivered file.

        Re-read from the session rather than trusted from the browser, for
        the same reason consent is: the REST write is the authority on
        whether anything actually arrived.
        """
        session = await self._context.sessions.get_by_id(self._context.session_id)
        if session is None or not session.document_uploaded:
            log.warning(
                "intake_document_upload_unconfirmed",
                session_id=self._context.session_id,
            )
            return self._on_document_upload_failed()

        named = f" They sent '{event.file_name}'." if event.file_name else ""
        log.info("intake_document_uploaded", session_id=self._context.session_id)
        return BidiTextInputEvent(
            text=(
                f"(system) The patient's document has uploaded successfully.{named} "
                "Say in one short sentence that you have got it. Then ask them, as your "
                "next question, what the report is -- what it was for, or what it shows. "
                "Record their answer with `record_intake_details` under `test_details`, "
                "then carry on with the call. Do not ask them to upload anything again "
                "and do not mention screens, files or uploading beyond that one sentence."
            ),
            role="user",
        )

    def _on_document_upload_failed(self) -> BidiTextInputEvent:
        """The upload did not make it, and only the browser knew.

        A failure was entirely silent to the agent, which carried on to the
        next topic while the patient was still looking at an error and
        waiting to be told what to do about it.
        """
        log.warning("intake_document_upload_failed", session_id=self._context.session_id)
        return BidiTextInputEvent(
            text=(
                "(system) The patient's document failed to upload. Tell them plainly, in "
                "one short sentence, that it did not go through, and ask them to try "
                "sending it again. The upload control is still on their screen. Do not "
                "blame them, do not explain why, and do not wait for the file -- if they "
                "would rather move on, that is fine and you carry on with the call."
            ),
            role="user",
        )

    def _apply_control(self, action: str) -> None:
        if action == "mute":
            self._control.muted = True
        elif action == "unmute":
            self._control.muted = False
        elif action == "hold":
            self._control.on_hold = True
        elif action == "resume":
            self._control.on_hold = False
        elif action == "hangup":
            self._control.hangup.set()
        log.info("intake_control", action=action)

    async def stop(self) -> None:
        """Stop draining. The socket itself is closed by the route handler."""
        if self._reader is not None:
            self._reader.cancel()
            self._reader = None


class BrowserOutput(BidiOutput):
    """Translates agent events into the patient app's own frames.

    Everything the frontend needs comes through here: audio for playback,
    one growing caption line per turn, whose turn it is, and
    interruptions. Everything it does not need is dropped on the floor --
    notably `tool_use_stream` and `tool_result`, which carry tool names
    and partially-streamed arguments. Navigation, prefill and the upload
    prompt reach the browser from the tools themselves instead, as
    complete instructions rather than argument deltas.
    """

    def __init__(self, context: LiveCallContext, control: CallControl) -> None:
        self._context = context
        self._bus = context.bus
        self._control = control
        # Speculative text included: a caption that waits for Nova's FINAL
        # stage appears in one lump after the sentence has been spoken.
        self._transcript = TranscriptAssembler(include_speculative=True)
        self._welcome_fallback: asyncio.Task[None] | None = None
        self._thank_you_fallback: asyncio.Task[None] | None = None
        self._consent_prompt: asyncio.Task[None] | None = None
        self._closing_prompt: asyncio.Task[None] | None = None
        # The closing watchdogs have to be armed by the patient, not by the
        # agent speaking -- the call that hangs is the one where it does not.
        context.on_patient_activity = self._on_patient_activity

    async def start(self, agent: BidiAgent) -> None:
        """No setup needed; the socket is already accepted before the agent runs."""
        return

    async def stop(self) -> None:
        """Drop every watchdog so none outlives the call."""
        self._cancel_welcome_fallback()
        self._cancel_thank_you_fallback()
        self._cancel_consent_prompt()
        self._cancel_closing_prompt()
        self._context.on_patient_activity = None

    def _publish_settled(self, turns: list[Any]) -> None:
        """Send caption frames for turns that are already settled."""
        for turn in turns:
            if turn.role != "assistant":
                self._context.note_patient_activity()
            self._bus.publish(
                intake_channel.transcript(
                    "agent" if turn.role == "assistant" else "patient",
                    turn.text,
                    turn.is_complete,
                )
            )

    def _publish_captions(self, event: Any) -> None:
        """Send the caption line as it now stands, if this event moved it.

        One line per turn, growing as the words are actually spoken --
        `TranscriptAssembler` is what makes that true of Nova Sonic's
        double-emitted text, and why raw events are never forwarded.
        """
        # A patient turn here is real speech, as the model heard it -- the
        # only audio signal that means they are actually there. Raw
        # microphone frames do not: they arrive continuously while the mic
        # is live, so counting those would make a silent patient look like
        # a talking one. `_publish_settled` does that counting.
        self._publish_settled(self._transcript.feed(event))

    async def _leave_welcome_if_greeted(self) -> None:
        """Move the patient off `welcome` once the greeting has actually been said.

        The app owns this transition, exactly as it owns the one after
        consent: there is no branch to decide, and the model cannot time it.

        The model calls `navigate_to_screen` at *generation* time, and Nova
        emits that tool call before it streams the audio for the same turn.
        So the navigate frame reached the browser ahead of a single byte of
        greeting audio, the browser measured an empty playback queue,
        decided there was nothing to wait for, and moved instantly -- which
        is why the introduction played over the consent screen and
        `welcome` was never seen. No client-side heuristic can fix that;
        the ordering has to be right on the wire.

        Emitted at the end of a model turn, so every audio chunk of the
        greeting is already queued ahead of it and the browser's own
        hold-until-playback-drains then lands on the right moment.

        Two guards make this safe, and both are needed. `has_spoken` rules
        out a turn that only made a tool call -- moving on that would skip
        the greeting entirely. `greeting_finished` rules out a turn that
        ended *partway through* it: Nova closes a completion whenever it
        yields, so the greeting routinely spans more than one, and firing
        on the first arriving completion moved the patient to the consent
        screen with the emergency notice still unsaid. Only the model knows
        when it has said everything in steps 1 and 2, and asking to leave
        `welcome` is how step 3 has it say so.
        """
        if self._context.progress.screen is not IntakeScreen.WELCOME:
            return
        if not self._context.has_spoken:
            # The turn that only made a tool call. Logged rather than passed
            # over in silence: when the patient is stuck on `welcome`, which
            # of these three guards held is the whole diagnosis, and none
            # used to leave any trace at all.
            log.info(
                "intake_welcome_hold",
                session_id=self._context.session_id,
                reason="nothing_spoken_yet",
            )
            return
        if not self._context.greeting_finished:
            log.info(
                "intake_welcome_hold",
                session_id=self._context.session_id,
                reason="greeting_not_signalled_complete",
            )
            return

        self._cancel_welcome_fallback()
        log.info("intake_welcome_greeting_complete", session_id=self._context.session_id)
        await self._context.go_to(IntakeScreen.CONFIRM_DETAILS)
        self._arm_consent_prompt()

    def _cancel_consent_prompt(self) -> None:
        if self._consent_prompt is not None:
            self._consent_prompt.cancel()
            self._consent_prompt = None

    def _arm_consent_prompt(self) -> None:
        """Make sure the patient is actually *asked* for consent.

        Moving them to `confirm-details` is the app's job and is already
        reliable. Asking them is the model's, and nothing guaranteed it
        happened: Nova Sonic produces a turn only when something reaches
        its input stream, so a model that treated the navigate reply as
        the end of its turn left the patient looking at a consent screen
        in silence, with the greeting still showing as the last thing
        anyone said. From the patient's side the call had simply stopped.

        The screen watchdogs above could not fix this -- they move the
        screen, and the screen was already right. This one speaks, through
        `LiveCallContext.nudges`.

        Armed from **both** ways the call leaves `welcome`. Arming it only
        on the clean `bidi_response_complete` path missed the case that
        actually needs it: when that event never arrives, the silence
        watchdog navigates instead, and that is precisely the run where
        the model has also gone quiet.
        """
        self._cancel_consent_prompt()
        # Snapshotted at arming, so the question the timer answers is "has
        # it said anything at all since the patient got here" -- not "has
        # it paused", which is what a re-armed timer would measure.
        spoken_on_arrival = self._context.agent_audio_chunks
        self._consent_prompt = asyncio.create_task(self._prompt_for_consent(spoken_on_arrival))

    async def _prompt_for_consent(self, spoken_on_arrival: int) -> None:
        try:
            await asyncio.sleep(CONSENT_PROMPT_SILENCE_SECONDS)
        except asyncio.CancelledError:
            return

        if self._context.progress.screen is not IntakeScreen.CONFIRM_DETAILS:
            return
        if self._context.consent_given:
            return
        if self._context.agent_audio_chunks != spoken_on_arrival:
            # It did speak. Whatever it said, the patient is not sitting in
            # silence, and a nudge now would talk over a live conversation.
            return

        log.warning("intake_consent_prompt_nudged", session_id=self._context.session_id)
        self._context.nudges.put_nowait(
            "(system) You have not said anything since the patient's screen moved to "
            "'confirm-details', and they are waiting. Do step 4 now, out loud: ask "
            "them to check the details shown are right, then tell them to tick the "
            "consent box and press Continue. Two short sentences. Do not greet them "
            "again -- you already have -- and do not read their details aloud."
        )

    def _cancel_welcome_fallback(self) -> None:
        if self._welcome_fallback is not None:
            self._welcome_fallback.cancel()
            self._welcome_fallback = None

    def _arm_welcome_fallback(self) -> None:
        """Restart the watchdog that leaves `welcome` if the model never says it finished.

        The transition above hangs entirely on one model event. When that
        event does not arrive -- and against Nova Sonic it demonstrably may
        not, with the greeting spoken in full and `completionEnd` never
        following it -- the patient sits on the introduction screen for the
        rest of the call while the assistant asks them to confirm details
        they cannot see. Nothing else rescued them: the browser's own
        fallback disarms the moment the assistant speaks, precisely so it
        cannot cut a greeting short.

        Re-armed on every audio chunk, so what it measures is silence
        *after* speech rather than elapsed time -- which is why a timer is
        an honest signal here rather than a guess at how long a greeting
        takes. See `GREETING_SILENCE_SECONDS` for why the window is not
        shorter.
        """
        if self._context.progress.screen is not IntakeScreen.WELCOME:
            return
        self._cancel_welcome_fallback()
        self._welcome_fallback = asyncio.create_task(self._leave_welcome_after_silence())

    async def _leave_welcome_after_silence(self) -> None:
        try:
            await asyncio.sleep(GREETING_SILENCE_SECONDS)
            # Silence at the *generator* is not silence at the patient's
            # ear, and this watchdog only ever meant the second one. Nova
            # streams the whole greeting in a second or two, so the gap
            # after its last chunk exceeds the window immediately -- and
            # this fired five seconds into a twenty-second introduction, on
            # essentially every call rather than only the broken ones. It
            # then finalized the caption and published `agent_speaking:
            # False`, which is precisely the greeting stopping dead
            # mid-sentence that the patient sees.
            #
            # One sleep is enough, not a converging loop: the deadline only
            # moves when a new chunk is published, and publishing one
            # re-arms this watchdog from scratch, which cancels us here.
            await asyncio.sleep(self._context.remaining_playback_seconds())
        except asyncio.CancelledError:
            return

        if self._context.progress.screen is not IntakeScreen.WELCOME:
            return

        # A warning, not an info: reaching this means the model event the
        # transition is supposed to run on never came, and that is worth
        # seeing in the log even though the patient is now unblocked.
        log.warning(
            "intake_welcome_transition_fallback",
            session_id=self._context.session_id,
            silence_seconds=GREETING_SILENCE_SECONDS,
            greeting_signalled_complete=self._context.greeting_finished,
        )
        # Everything `bidi_response_complete` would have done, because it
        # never came. Without these the browser is told the greeting
        # navigated but never that it *ended*: the caption stays on the
        # greeting and the avatar stays on "Speaking..." for the rest of
        # the call, which is exactly what a stranded patient sees.
        self._publish_settled(self._transcript.finalize())
        self._bus.publish(intake_channel.agent_speaking(False))
        await self._context.go_to(IntakeScreen.CONFIRM_DETAILS)
        self._arm_consent_prompt()

    def _on_patient_activity(self) -> None:
        """The patient said or typed something.

        On a closing screen that is the answer to the last question, so
        both closing watchdogs are armed from here. They used to be armed
        only by agent audio, which cannot cover the one case that actually
        strands the call: the patient answers and the model says nothing,
        so nothing speaks, so nothing is watching.
        """
        if self._context.progress.screen not in CLOSING_SCREENS:
            return
        self._arm_closing_prompt()
        self._arm_thank_you_force_end()

    def _cancel_closing_prompt(self) -> None:
        if self._closing_prompt is not None:
            self._closing_prompt.cancel()
            self._closing_prompt = None

    def _arm_closing_prompt(self) -> None:
        """Make sure the call is actually *closed*, rather than just over.

        `_arm_thank_you_force_end` below ends a call that will not end, but
        it ends it silently -- `force_complete` hangs up with nothing said.
        This runs first and far sooner, and it makes the model do the thing
        the patient is waiting for: say goodbye out loud, then end.
        """
        self._cancel_closing_prompt()
        # Snapshotted at arming, so what the timer answers is "has it said
        # anything since the patient finished" rather than "has it paused".
        spoken_on_arrival = self._context.agent_audio_chunks
        self._closing_prompt = asyncio.create_task(self._prompt_to_close(spoken_on_arrival))

    async def _prompt_to_close(self, spoken_on_arrival: int) -> None:
        try:
            await asyncio.sleep(CLOSING_PROMPT_SILENCE_SECONDS)
        except asyncio.CancelledError:
            return

        # Only `thank-you`. On the reschedule screen the patient is choosing
        # a time and the call has no business closing itself.
        if self._context.progress.screen is not IntakeScreen.THANK_YOU:
            return
        if self._context.agent_audio_chunks != spoken_on_arrival:
            # It did speak. A nudge now would talk over a live goodbye.
            return

        log.warning("intake_closing_prompt_nudged", session_id=self._context.session_id)
        self._context.nudges.put_nowait(
            "(system) The patient has answered your closing question and you have said "
            "nothing since. They are waiting. If what they asked for is something you "
            "can actually do, do it now. Otherwise close the call: say OUT LOUD, as two "
            "short sentences, that you thank them for their time and that the call will "
            "end in a few seconds. Only once you have actually spoken both of those, "
            "call `end_session`. Do not summarize and do not ask anything further."
        )

    def _cancel_thank_you_fallback(self) -> None:
        if self._thank_you_fallback is not None:
            self._thank_you_fallback.cancel()
            self._thank_you_fallback = None

    def _arm_thank_you_force_end(self) -> None:
        """(Re)start the watchdog that ends the call if `end_session` never comes.

        Armed only once the closing has actually begun -- see
        `LiveCallContext.closing_started`. It used to arm on any agent
        audio while the patient was on `thank-you`, but that screen holds
        a question too (step 15: "is there anything you would like help
        with?"). So the watchdog started counting the moment the agent
        finished *asking* it, and a patient who took more than eight
        seconds to think had the call hung up on them mid-answer, marked
        completed, and summarized without what they were about to say.

        Re-armed on every audio chunk on `thank-you`, exactly like
        `_arm_welcome_fallback` above and for the same reason: `bidi_response_
        complete` is the event that is supposed to mark a turn as finished,
        and Nova Sonic has been observed, on this exact screen's neighbour
        `welcome`, to speak a turn in full and never send it. Arming on
        `bidi_response_complete` instead would make this watchdog depend on
        the one signal already known to be unreliable -- silence *after
        speech* is the same fix this module already made once.

        Armed on `appointment-reschedule` as well as `thank-you` (see
        `CLOSING_SCREENS`). That screen is only ever reached by answering
        the closing question, so a patient who goes silent while choosing a
        new time has left a call whose screening is already finished -- and
        with nothing watching it, that call would stay open until the model
        itself timed out. The long window always applies there, because
        both ways onto it clear `closing_started` on the way in.
        """
        if self._context.progress.screen not in CLOSING_SCREENS:
            return
        self._cancel_thank_you_fallback()
        # Two windows, because two different things can go wrong here.
        # Once the closing has begun there is genuinely nothing left to
        # say, so eight seconds is generous. Before it, the agent has just
        # asked the patient a question and the only honest reason to end
        # the call is that nobody is there -- which takes far longer to
        # establish, and is cancelled the moment they send anything.
        # `closing_started` is only ever true on `thank-you`; the
        # reschedule branch clears it precisely because the closing
        # question is open again there.
        seconds = (
            THANK_YOU_FORCE_END_SECONDS
            if self._context.closing_started
            else THANK_YOU_IDLE_END_SECONDS
        )
        self._thank_you_fallback = asyncio.create_task(
            self._force_end_after_silence(seconds, self._context.patient_activity)
        )

    async def _force_end_after_silence(self, seconds: float, activity_at_arm: int) -> None:
        try:
            await asyncio.sleep(seconds)
            # And then out the speech the patient has not finished hearing.
            # Generating is not hearing: the goodbye is produced in under a
            # second and takes several to play, so this window elapsed while
            # the farewell was still coming out of the speaker -- and what
            # it then did was hang up over it.
            await asyncio.sleep(self._context.remaining_playback_seconds())
        except asyncio.CancelledError:
            return

        # Re-checked rather than trusted from arming time: the patient may
        # have answered the closing question while this was sleeping, in
        # which case the agent is mid-goodbye and the short window that
        # armed this no longer describes the situation.
        if self._context.progress.screen not in CLOSING_SCREENS:
            return

        if self._context.patient_activity != activity_at_arm:
            # They spoke or typed while this was waiting, so the silence
            # this measured was not theirs. Never end a call on a patient
            # who is mid-answer -- that is the failure this guards, not
            # the one it should cause.
            log.info(
                "intake_thank_you_force_end_deferred",
                session_id=self._context.session_id,
            )
            self._arm_thank_you_force_end()
            return

        # Reaching here means the goodbye was said and end_session either
        # was never called or never took effect -- the exact gap this
        # guards against. `force_complete`, not `hangup`: the screening
        # actually finished, so the route must still summarize it.
        log.warning(
            "intake_thank_you_force_ended",
            session_id=self._context.session_id,
            silence_seconds=seconds,
            closing_started=self._context.closing_started,
        )
        self._control.force_complete.set()

    async def __call__(self, event: Any) -> None:
        """Publish the browser's view of one agent event, if it has one."""
        event_type = event.get("type") if isinstance(event, dict) else None
        if event_type is None:
            return

        if event_type == "bidi_audio_stream":
            self._context.has_spoken = True
            audio = event["audio"]
            sample_rate = event.get("sample_rate", intake_channel.AUDIO_SAMPLE_RATE)
            self._context.note_agent_audio(_pcm16_duration_seconds(audio, sample_rate))
            self._bus.publish(intake_channel.agent_audio(audio, sample_rate))
            self._arm_welcome_fallback()
            self._arm_thank_you_force_end()

        elif event_type == "bidi_transcript_stream":
            self._publish_captions(event)

        elif event_type == "bidi_response_start":
            self._bus.publish(intake_channel.agent_speaking(True))

        elif event_type == "bidi_response_complete":
            self._publish_captions(event)
            self._bus.publish(intake_channel.agent_speaking(False))
            await self._leave_welcome_if_greeted()

        elif event_type == "bidi_interruption":
            self._publish_captions(event)
            # The browser flushes its queue on this frame, so anything the
            # playback clock still counts as pending will never be heard.
            self._context.drop_pending_playback()
            self._bus.publish(intake_channel.interrupted())

        elif event_type == "bidi_connection_start":
            self._bus.publish(intake_channel.agent_ready())

        elif event_type == "bidi_connection_restart":
            # The model timed out and Strands is reconnecting it. The socket
            # to the patient is fine, so this is a pause, not a failure.
            self._bus.publish(
                intake_channel.error(
                    "reconnecting_model",
                    "One moment -- reconnecting.",
                    recoverable=True,
                )
            )

        elif event_type == "bidi_error":
            # The event carries the exception's class name and message.
            # Neither is patient-facing, and neither is logged with PHI
            # around it, so only the code is kept and only for operators.
            log.warning("intake_model_error", code=event.get("code"))
            self._bus.publish(
                intake_channel.error(
                    "agent_failed",
                    "The assistant had a problem. Reconnecting.",
                    recoverable=True,
                )
            )


def _pcm16_duration_seconds(base64_audio: str, sample_rate: int) -> float:
    """How long one published chunk will take to play, in seconds.

    Measured from the encoded length rather than by decoding: this runs on
    every audio frame of every live call, and the byte count is exactly
    recoverable from base64 without paying for the decode.
    """
    if sample_rate <= 0:
        return 0.0
    decoded_bytes = (len(base64_audio) * 3) // 4 - base64_audio.count("=")
    return max(0.0, decoded_bytes / 2 / sample_rate)


def _is_valid_base64(value: str) -> bool:
    """Whether a frame's audio actually decodes.

    Checked here rather than left to the model client: an undecodable
    frame raises deep inside Nova Sonic's send path, where the failure
    surfaces as a dead call instead of one dropped 30 ms of audio.
    """
    try:
        base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return False
    return True

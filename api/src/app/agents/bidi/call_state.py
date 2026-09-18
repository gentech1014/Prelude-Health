"""Per-call state the live agent's tools read and write.

One `LiveCallContext` is built per WebSocket connection and threaded into
`agent.run(invocation_state=...)`, which is the only way anything reaches
the tool layer. Tools use it to move the patient's screen, offer prefill,
and persist progress so the call survives a dropped socket.

The event bus exists because **only one task may write to a Starlette
WebSocket.** Agent output events, tool-driven navigation and error frames
all originate in different tasks, so they queue here and a single sender
task drains them. Sending directly from each producer would interleave
frames on the wire under load.
"""

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import structlog

from app.agents.bidi.symptom_plan import SymptomQuestionPlan
from app.core.constants import (
    ANSWERED_STATUSES,
    SCREEN_FIELD_OPTIONS,
    SCREEN_FIELDS,
    AnswerStatus,
    IntakeScreen,
)
from app.repositories.session_repository import SessionRepository
from app.schemas import intake_channel
from app.schemas.intake_channel import ServerEvent
from app.services.question_bank_service import QuestionBankService

log = structlog.get_logger(__name__)

_QUEUE_MAXSIZE = 1_024
"""Bounded so a stalled socket cannot grow the queue without limit.

At ~900 bytes per audio frame that is roughly a megabyte and ~30 seconds
of speech -- far beyond any healthy backlog, so hitting it means the
patient's connection is already gone."""

EOF_EVENT_TYPE = "__eof__"
"""Sentinel telling the sender task there is nothing more to send.

Queued rather than signalled out of band so the sender drains everything
ahead of it first -- the last frame of a call is `call_ended`, and cutting
the sender off would lose exactly the frame that tells the patient why
their call finished."""

_DROPPABLE_TYPES = frozenset({"agent_audio", "transcript"})
"""Frames it is safe to drop when the queue is full.

Audio and captions are continuous streams: losing a frame degrades
quality. Everything else -- navigation, prefill, errors, call end -- is a
discrete instruction whose loss desynchronizes the screen from the call,
so those are never dropped."""


class UiEventBus:
    """Single-writer outbound queue for one patient's socket."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[ServerEvent] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        self._closed = False

    def publish(self, event: ServerEvent) -> None:
        """Enqueue one frame without ever blocking the caller.

        Never awaits: callers include the agent's audio output path, where
        blocking would stall the model's event loop. A full queue drops
        stream frames and logs, and keeps control frames by evicting the
        oldest droppable one -- losing a `navigate` would leave the
        patient on the wrong screen for the rest of the call.
        """
        if self._closed:
            return
        try:
            self._queue.put_nowait(event)
            return
        except asyncio.QueueFull:
            pass

        event_type = event.get("type", "")
        if event_type in _DROPPABLE_TYPES:
            log.warning("intake_ui_frame_dropped", event_type=event_type)
            return

        if self._evict_droppable():
            self._queue.put_nowait(event)
        else:
            log.error("intake_ui_control_frame_dropped", event_type=event_type)

    def _evict_droppable(self) -> bool:
        """Drop the oldest stream frame to make room for a control frame.

        Walks at most the queue's length, re-enqueueing what it keeps, so
        ordering of the survivors is preserved.
        """
        for _ in range(self._queue.qsize()):
            try:
                candidate = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return True
            if candidate.get("type", "") in _DROPPABLE_TYPES:
                log.warning("intake_ui_frame_evicted", event_type=candidate.get("type"))
                return True
            self._queue.put_nowait(candidate)
        return False

    async def next_event(self) -> ServerEvent:
        """Await the next frame to send. Only the sender task calls this."""
        return await self._queue.get()

    def close(self) -> None:
        """Stop accepting new frames and queue the sender's stop signal.

        Anything already queued still drains: the sender stops when it
        reaches the sentinel, not when this is called.
        """
        if self._closed:
            return
        self._closed = True
        eof: ServerEvent = {"type": EOF_EVENT_TYPE}
        try:
            self._queue.put_nowait(eof)
        except asyncio.QueueFull:
            self._evict_droppable()
            try:
                self._queue.put_nowait(eof)
            except asyncio.QueueFull:
                log.warning("intake_ui_eof_not_queued")


_DEFLECTIONS: frozenset[str] = frozenset(
    {
        "move next",
        "move on",
        "moving on",
        "just move on",
        "lets move on",
        "let us move on",
        "can we move on",
        "next",
        "next one",
        "next question",
        "next please",
        "skip",
        "skip it",
        "skip this",
        "skip that",
        "hurry up",
    }
)
"""Things a patient says to get *past* a question, which are not answers to it.

A **backstop, not the intelligence.** Deciding what a patient meant is the
model's job -- it has the whole conversation, and it reports its reading
through `record_symptom_answer`'s `relevance` argument. This exists only
to catch a value that is unambiguously a navigation command, in case the
model records one as a clinical fact. That is not hypothetical: the
patient said "move next", the model recorded `concerns: "move next"`,
`_is_answer` saw no question mark and accepted it, the completeness gate
saw a key present and advanced the call, and the physician's record said
the patient's reason for the visit was "move next".

**Deliberately tiny, and it must stay that way.** An earlier version of
this list also held "nothing", "anything", "pass", "continue", "carry on",
"go on", "keep going" and "whatever". Every one of those is a real answer
to a real intake question -- "Does anything make it worse?" / "nothing" is
the commonest exchange in the whole call -- and refusing them made the
agent reject a perfectly good answer and ask the same question again. A
false refusal is worse than a missed deflection: the missed one is caught
by the model's own `relevance` judgement one layer up, while the false one
is a loop the patient cannot escape.

So the rule for adding anything here: it must be impossible to say as an
answer to any question this intake asks. Matched on the whole normalized
value only, never as a substring, so "I want to skip the gym because of
the pain" stays an answer.
"""

_NON_ANSWER_FILLERS: frozenset[str] = frozenset(
    {"", "n a", "none given", "not given", "unanswered", "tbd", "unknown", "unspecified"}
)
"""Placeholder text that means the field was never actually filled.

These are things the *model* writes when it has nothing, not things a
patient says. Compared against the normalized value, so punctuation-only
values ("-", "...") are already empty by the time they get here and do not
need listing.

`"unknown"` is here as literal placeholder text, which is a different
thing from `AnswerStatus.UNKNOWN`: a patient who cannot remember is
recorded in their own words with that status, never as the bare string.

Bare "na" is deliberately absent -- it is how a transcript spells "nah",
which is an answer.
"""


def _normalized(value: str) -> str:
    """Lowercase, punctuation-stripped, single-spaced -- for comparison only."""
    stripped = "".join(
        character if character.isalnum() or character.isspace() else " " for character in value
    )
    return " ".join(stripped.lower().split())


def reject_as_answer(value: str) -> str | None:
    """Why this text cannot stand as the patient's answer, or None if it can.

    Three things are refused, and the reply names which so the agent can
    act on it in the same turn rather than recording something else that
    is equally wrong:

    - A question. Observed live: asked to record what a cough felt like
      before the patient had described it, the model wrote the field as
      "dry or wet? (unspecified)" -- its own unanswered question, shown to
      the patient as though they had said it.
    - A request to move on. See `_DEFLECTIONS`.
    - Placeholder text standing in for an answer nobody gave.

    Note what is *not* refused: "I don't know" and "I'd rather not say" are
    real answers and must be recorded, with `AnswerStatus.UNKNOWN` and
    `AnswerStatus.UNDISCLOSED` respectively. An honest gap is worth more to
    the physician than a value the patient was talked into, and it is worth
    strictly more than a silently empty field.
    """
    stripped = value.strip()
    if not stripped:
        return "it was empty"
    if "?" in stripped:
        return (
            "it is a question, not an answer -- you cannot record a question you have "
            "not had answered yet"
        )

    normalized = _normalized(stripped)
    if normalized in _NON_ANSWER_FILLERS:
        return "it is placeholder text rather than something the patient said"
    if normalized in _DEFLECTIONS:
        return (
            f'"{stripped}" is the patient asking you to move on, not their answer to '
            "the question. They have not answered it"
        )
    return None


_MAX_RECORDED_LENGTH = 500
"""Cap on one stored answer. The untruncated words are in the transcript."""


@dataclass
class RecordOutcome:
    """What happened to one `record()` call, field by field.

    Every list here becomes a sentence the agent is told, so a write that
    half-landed produces a correction it can act on in the same turn
    instead of an unexplained gap three questions later.
    """

    accepted: dict[str, "RecordedValue"] = field(default_factory=dict)
    unknown_fields: list[str] = field(default_factory=list)
    non_answers: list[tuple[str, str]] = field(default_factory=list)
    """`(field, why it is not an answer)` -- the patient has not answered these."""
    superseded: list[tuple[str, str, str]] = field(default_factory=list)
    """`(field, what was there before, what replaced it)`.

    A contradiction the agent must notice. Last-write-wins is the right
    *storage* behaviour -- a correction should win -- but doing it
    silently is how the call ends up contradicting the patient without
    either of them realizing: the model's own history still holds the old
    answer, so it goes on reasoning from a value the record no longer
    has."""

    @property
    def changed_anything(self) -> bool:
        return bool(self.accepted)


@dataclass
class RecordedValue:
    """One collected answer, and how well the call actually knows it.

    The whole point is the `status`. A bare string cannot distinguish a
    patient who said "twice a day" from one who guessed "maybe twice?",
    from one who would rather not discuss it, from one who cannot
    remember, from the agent's own reading of something they implied --
    and every one of those reached the physician's report looking
    identical. See `app.core.constants.AnswerStatus`.
    """

    text: str
    status: AnswerStatus = AnswerStatus.CONFIRMED
    source: str = "spoken"
    """How it arrived: "spoken", "typed", or "agent" for the agent's own reading.

    Kept apart from `status` because they answer different questions --
    a typed value can still be a hedge, and an inferred one is never
    typed. The frontend already distinguishes who owns a field; this is
    what lets the server agree with it."""

    @property
    def is_answered(self) -> bool:
        """Whether this counts as the patient having answered the question."""
        return self.status in ANSWERED_STATUSES


@dataclass
class CallProgress:
    """Where the call has got to, and what it has collected so far.

    Seeded from the session document on connect, which is what makes a
    reconnect resume rather than restart. `details` holds the patient's own
    words keyed by screen and field, each with the certainty it was given
    with -- never an inference presented as a statement, and never a value
    the patient did not give.
    """

    screen: IntakeScreen = IntakeScreen.WELCOME
    details: dict[str, dict[str, RecordedValue]] = field(default_factory=dict)
    selections: dict[str, dict[str, str]] = field(default_factory=dict)
    """Which option ids the agent picked, keyed by screen then by field.

    Values are comma-joined ids from `SCREEN_FIELD_OPTIONS`, kept apart
    from `details` so the words stay words: `details` is what the patient
    said and what the summarizer reads, this is only which control the
    screen should show as chosen. Empty for a field the agent left to the
    frontend's own matching, and empty for every free-text field.
    """
    visited: list[IntakeScreen] = field(default_factory=list)
    sequence: int = 0

    def move_to(self, screen: IntakeScreen) -> int:
        """Record a screen change and return its monotonic sequence number."""
        self.screen = screen
        if screen not in self.visited:
            self.visited.append(screen)
        self.sequence += 1
        return self.sequence

    def record(
        self,
        screen: IntakeScreen,
        fields: dict[str, str],
        *,
        statuses: dict[str, AnswerStatus] | None = None,
        default_status: AnswerStatus = AnswerStatus.CONFIRMED,
        source: str = "spoken",
    ) -> "RecordOutcome":
        """Merge collected values for one screen, reporting everything refused.

        Returns a `RecordOutcome` rather than a bare accepted-dict, because
        the three ways a write can fail need different things said back to
        the agent and the old signature could express none of them:

        - an unknown field is a naming mistake -- list the real ones;
        - a non-answer is the patient not having answered -- ask again, or
          offer them the chance to decline (see `reject_as_answer`);
        - a value that contradicts one already on record is a correction or
          a mishearing, and either way the agent needs to know it happened.

        The previous version returned only what it accepted, so the model
        could see *that* something had not landed but never *why*, and a
        recorded "move next" was indistinguishable from a real answer.
        """
        allowed = SCREEN_FIELDS.get(screen, ())
        statuses = statuses or {}
        outcome = RecordOutcome()
        current = self.details.setdefault(screen.value, {})

        for key, raw in fields.items():
            if key not in allowed:
                outcome.unknown_fields.append(key)
                continue
            if not isinstance(raw, str):
                outcome.unknown_fields.append(key)
                continue

            status = statuses.get(key, default_status)
            refusal = reject_as_answer(raw)
            if refusal is not None:
                # An explicit decline or a "don't know" is a real answer
                # and is recorded with the patient's own words. But if the
                # model reports one of those statuses while passing a
                # deflection or placeholder as the text, there is nothing
                # of the patient's to keep -- so the status stands and the
                # text is replaced by what actually happened.
                if status in (AnswerStatus.UNDISCLOSED, AnswerStatus.UNKNOWN):
                    text = (
                        "Preferred not to say"
                        if status is AnswerStatus.UNDISCLOSED
                        else "Does not know"
                    )
                    current[key] = RecordedValue(text=text, status=status, source=source)
                    outcome.accepted[key] = current[key]
                    continue
                outcome.non_answers.append((key, refusal))
                continue

            text = raw.strip()[:_MAX_RECORDED_LENGTH]
            previous = current.get(key)
            if previous is not None and previous.text.strip().casefold() != text.casefold():
                outcome.superseded.append((key, previous.text, text))
            current[key] = RecordedValue(text=text, status=status, source=source)
            outcome.accepted[key] = current[key]

        if not current:
            self.details.pop(screen.value, None)
        return outcome

    def clear(self, screen: IntakeScreen, fields: list[str]) -> list[str]:
        """Take values back off the record, returning the fields actually cleared.

        There was no way to do this at all, at any layer: an empty value
        was dropped by the frontend, dropped again by the socket handler,
        and dropped a third time here. So a patient who watched the agent
        mishear them and wiped the box could not retract it -- the wrong
        value simply stayed, and reached their doctor.
        """
        removed: list[str] = []
        values = self.details.get(screen.value, {})
        selected = self.selections.get(screen.value, {})
        for key in fields:
            if values.pop(key, None) is not None:
                removed.append(key)
            # A cleared word must clear the tick it produced. Leaving the
            # selection behind is how an agent's guess outlived the
            # patient's own correction of it.
            selected.pop(key, None)
        if not values:
            self.details.pop(screen.value, None)
        if not selected:
            self.selections.pop(screen.value, None)
        return removed

    def value_at(self, screen: IntakeScreen, field_name: str) -> RecordedValue | None:
        """What is on record for one field of one screen, if anything."""
        return self.details.get(screen.value, {}).get(field_name)

    def answered_fields(self, screen: IntakeScreen) -> set[str]:
        """Fields on this screen the patient has actually answered.

        An inferred value is deliberately not one of them -- see
        `ANSWERED_STATUSES`. The agent working something out is not the
        same as having asked.
        """
        return {
            name for name, value in self.details.get(screen.value, {}).items() if value.is_answered
        }

    def record_selections(
        self, screen: IntakeScreen, selections: dict[str, list[str]]
    ) -> dict[str, str]:
        """Merge the option ids the agent picked, dropping what is not offered.

        Two allowlists, both hard: the field must belong to this screen,
        and every id must appear in that field's own option list. A field
        whose ids are *all* unrecognized is dropped entirely rather than
        recorded as an empty selection -- an empty string means "the agent
        chose nothing here, fall back to matching the words", and a garbled
        answer must not be able to mean that.

        Returns only what was accepted, so the tool can tell the model
        which of its ids were real.
        """
        allowed_fields = SCREEN_FIELDS.get(screen, ())
        accepted: dict[str, str] = {}
        for key, ids in selections.items():
            if key not in allowed_fields:
                continue
            offered = SCREEN_FIELD_OPTIONS.get(key)
            if not offered:
                continue
            # Case-folded on the way in, canonical on the way out: the
            # single-choice ids are visible labels ("Never"), and a model
            # that lowercases one has still named a real option.
            wanted = {option.strip().casefold() for option in ids}
            kept = [option for option in offered if option.casefold() in wanted]
            if kept:
                accepted[key] = ", ".join(kept)
        if accepted:
            self.selections.setdefault(screen.value, {}).update(accepted)
        return accepted

    def as_storage(self) -> dict[str, dict[str, str]]:
        """The collected detail as plain words, shaped for the session document.

        Still `dict[str, dict[str, str]]`, unchanged: this is what the
        frontend prefills from and what the summarizer reads, and neither
        should have to learn a new shape to keep working. The certainty
        travels alongside in `statuses_as_storage`.
        """
        return {
            screen: {name: value.text for name, value in values.items()}
            for screen, values in self.details.items()
        }

    def statuses_as_storage(self) -> dict[str, dict[str, str]]:
        """How well each collected value is known, in the same shape.

        A parallel map rather than a richer `collected_details`, so every
        existing reader of that field -- the frontend's prefill, the
        summarizer's recorded-values section, the physician PDF -- keeps
        working untouched, and only the readers that care about certainty
        have to look here.
        """
        return {
            screen: {name: value.status.value for name, value in values.items()}
            for screen, values in self.details.items()
        }

    def selections_as_storage(self) -> dict[str, dict[str, str]]:
        """The chosen option ids, shaped for the session document."""
        return {screen: dict(values) for screen, values in self.selections.items()}


@dataclass
class LiveCallContext:
    """Everything one call's tools need, threaded in via `invocation_state`."""

    session_id: str
    bus: UiEventBus
    progress: CallProgress
    sessions: SessionRepository
    symptoms: SymptomQuestionPlan = field(default_factory=SymptomQuestionPlan)
    """The screening conversation: what is being screened, and what has landed.

    Separate from `progress` because the symptom-story screen has no fixed
    fields -- see `app.agents.bidi.symptom_plan`."""

    question_bank: QuestionBankService | None = None
    """Where this call's questions are folded back in when it ends.

    Optional so a test can build a context without one. `None` simply means
    nothing accumulates -- the call itself is unaffected either way, since
    the bank is reference material and never a gate on what may be asked."""
    has_spoken: bool = False
    """Whether the agent has produced any speech at all on this connection.

    Set from the first audio chunk it emits. It is what tells a finished
    model turn that actually said something apart from one that only made
    a tool call -- the difference between "the greeting is over" and "the
    greeting has not started".
    """

    upload_prompted: bool = False
    """Whether the document-upload panel has been opened on this connection.

    Recording that the patient has test results opens it by itself, because
    leaving that to the model produced the opposite of what a patient
    expects: it asked a second, redundant question about whether they had a
    document, and then moved on without ever showing the control. This flag
    keeps that automatic open to once per call -- a patient who dismissed
    the panel must not have it reappear on the next thing they say.

    It does not gate `request_document_upload`. An explicit ask is always
    allowed to open the panel again; only the automatic one is once.
    """

    nudges: asyncio.Queue[str] = field(default_factory=asyncio.Queue)
    """Things the app needs to say to the model, between the patient's turns.

    Nova Sonic only produces a turn when something arrives on its input
    stream, so a step the model skips is a step nobody performs -- the
    patient simply sits in silence. Two transitions already had watchdogs
    for the *screen* (`GREETING_SILENCE_SECONDS`,
    `THANK_YOU_FORCE_END_SECONDS`), but neither could make the model
    *speak*, because only `BrowserInput` can put anything on that stream.

    This is the missing channel. Anything enqueued here is drained by
    `BrowserInput.__call__` and handed to the model as a system turn,
    exactly like the consent-recorded message already is -- so a watchdog
    can now prompt the model rather than only moving the screen
    underneath it.
    """

    agent_audio_chunks: int = 0
    """How much the agent has actually said, as a monotonic count.

    Distinct from `has_spoken`, which only ever goes true once. A watchdog
    needs to know whether the agent has spoken *since it started
    waiting*, which a boolean cannot answer."""

    greeting_finished: bool = False
    """Whether the model has signalled it is done greeting the patient.

    Set when `navigate_to_screen` is asked to leave `welcome`, which step 3
    of the prompt requires only *after* the greeting and the emergency
    notice have both been said out loud. It is the model's own "I have
    finished" and the only trustworthy one it gives.

    `has_spoken` cannot stand in for it: that goes true on the first audio
    chunk and never resets, so a model turn that ends partway through the
    greeting -- Nova closes a completion whenever it yields -- looked
    identical to one that ended after it. Moving on that was the greeting
    being cut off mid-sentence.
    """

    _playback_deadline: float = 0.0
    """Monotonic time by which everything published so far will have played.

    The server runs far ahead of the patient's ears: Nova streams a whole
    greeting in a second or two and the browser takes twenty to play it.
    Every watchdog here that reasons about "the agent has gone quiet" is
    really asking "has the patient finished listening", and without this
    the two were ~18 seconds apart -- which is what fired the greeting
    watchdog five seconds into a twenty-second introduction.

    Maintained exactly as the browser's own playback queue is
    (`agentPlayback.enqueue`): each chunk extends the deadline from
    whichever is later, now or the end of the audio already queued.
    """

    on_patient_activity: Callable[[], None] | None = None
    """Called whenever the patient says or types anything.

    The watchdogs on the closing screen used to be armed only by the agent
    *speaking*, which cannot cover the case that actually strands a call:
    the patient answers the last question and the model produces nothing at
    all. Nothing was then watching, because nothing had spoken. This is the
    seam that lets the output channel arm itself on the patient instead.
    """

    patient_activity: int = 0
    """Monotonic count of things the patient has actually done.

    Bumped for speech the model transcribed and for anything they typed --
    never for raw microphone frames, which arrive continuously while the
    mic is live and would make a silent patient look busy.

    Exists so the `thank-you` force-end watchdog can tell "nobody is
    there" from "they are answering". The watchdog snapshots this when it
    arms and re-arms instead of firing if it has moved.
    """

    closing_started: bool = False
    """Whether the call has reached its goodbye, rather than its last question.

    What arms the `thank-you` force-end watchdog. That watchdog used to
    arm on any agent speech while the patient was on `thank-you`, but that
    screen carries a question as well as the farewell -- step 15 asks
    whether there is anything they would like help with. So the eight
    second timer started as soon as the agent finished *asking*, and a
    patient who paused to think had the call ended under them, marked
    completed, and summarized without the answer they were giving.

    Set when the answer to that last question is recorded, which is the
    only point in the call after which nothing is left but the goodbye.
    """

    consent_given: bool = False
    """Whether affirmative consent is on record for this session.

    The socket is allowed to open before consent, because the patient has
    to be able to hear the greeting and be *asked* -- but every tool that
    collects, stores or advances past the consent screen checks this first.
    That check is the enforcement; the prompt is only the manners.

    Seeded from the session at connect and re-read from the database when
    the browser reports that consent has been recorded. Never set from the
    browser's word alone.
    """

    def note_patient_activity(self) -> None:
        """Record that the patient said or typed something.

        One place, rather than the two `+= 1` sites this replaces, so a
        path added later cannot silently read as the patient being absent.
        """
        self.patient_activity += 1
        if self.on_patient_activity is not None:
            self.on_patient_activity()

    def note_agent_audio(self, seconds: float) -> None:
        """Record one published chunk against the patient's playback clock."""
        self.agent_audio_chunks += 1
        self._playback_deadline = max(self._playback_deadline, time.monotonic()) + seconds

    def remaining_playback_seconds(self) -> float:
        """How much longer the patient is still listening to what was sent.

        An estimate, and deliberately a conservative one -- it assumes every
        chunk reached the browser and none was dropped, so it errs towards
        waiting slightly too long rather than cutting speech off.
        """
        return max(0.0, self._playback_deadline - time.monotonic())

    def drop_pending_playback(self) -> None:
        """Forget queued audio the browser has been told to throw away."""
        self._playback_deadline = 0.0

    async def refresh_consent(self) -> bool:
        """Re-read consent from the session document.

        The browser tells us consent has been recorded; this is what
        actually believes it. A client claim is a hint about when to look,
        never the authority on the answer.
        """
        session = await self.sessions.get_by_id(self.session_id)
        self.consent_given = (
            session is not None and session.consent is not None and session.consent.given
        )
        return self.consent_given

    async def go_to(self, screen: IntakeScreen) -> int:
        """Move the patient's screen, tell the browser, and remember it.

        Shared by `navigate_to_screen` and by the route, so a transition
        the app owns (the jump to questioning the moment consent lands)
        behaves identically to one the conversation asked for -- same
        sequence counter, same persisted marker, same frame.
        """
        sequence = self.progress.move_to(screen)
        self.bus.publish(intake_channel.navigate(screen, sequence))
        await self.persist_progress()
        log.info(
            "intake_screen_changed",
            session_id=self.session_id,
            screen=screen.value,
            sequence=sequence,
        )
        return sequence

    def publish_symptom_state(self) -> None:
        """Send the symptom screen its whole current state."""
        self.bus.publish(intake_channel.symptom_state(self.symptoms.snapshot()))

    async def persist_symptoms(self) -> None:
        """Write the symptom Q&A so a reconnect resumes mid-conversation.

        Best effort, like `persist_progress`: a lost write costs a repeated
        question, and raising here would cost the call.
        """
        try:
            await self.sessions.set_symptom_progress(
                self.session_id,
                self.symptoms.categories,
                self.symptoms.as_storage(),
                self.symptoms.presentation,
            )
        except Exception:
            log.exception("intake_symptom_write_failed", session_id=self.session_id)

    async def flush_question_bank(self) -> None:
        """Fold the questions this call asked into the bank, keyed by appointment reason.

        Once, at the end of the call, rather than after every answer: this
        is a write nobody on the line is waiting for, and the patient's
        turn is not the place to spend a round trip on it. Every ending
        reaches here -- a clean goodbye, a hang-up and a dropped socket
        alike -- so a call that was cut short still contributes whatever it
        managed to ask.

        Only answered questions are merged (see
        `SymptomQuestionPlan.asked_texts`), so the corpus accumulates what
        actually worked on a real patient rather than the agent's false
        starts. Best effort, like the other writes here: this runs after
        the patient has hung up, and raising would turn a finished call
        into a failed one.
        """
        if self.question_bank is None or not self.symptoms.is_started:
            return
        for category in self.symptoms.categories:
            texts = self.symptoms.asked_texts(category)
            if texts:
                await self.question_bank.record_asked(category, texts)

    async def persist_progress(self) -> None:
        """Write the resume marker. Best effort -- never fails the call.

        A lost progress write costs the patient a repeated question after a
        reconnect. Raising here would cost them the whole call.
        """
        try:
            await self.sessions.set_call_progress(
                self.session_id,
                self.progress.screen,
                self.progress.as_storage(),
                self.progress.selections_as_storage(),
                self.progress.statuses_as_storage(),
            )
        except Exception:
            log.exception("intake_progress_write_failed", session_id=self.session_id)


INVOCATION_KEY = "live_call"
"""Where `LiveCallContext` sits inside `invocation_state`."""


def live_call(invocation_state: dict[str, object]) -> LiveCallContext:
    """Pull the call context out of a tool's `invocation_state`.

    Raises `KeyError` if absent, which is a wiring bug in the WebSocket
    route rather than anything a patient can trigger.
    """
    context = invocation_state[INVOCATION_KEY]
    if not isinstance(context, LiveCallContext):
        raise TypeError(f"invocation_state[{INVOCATION_KEY!r}] is not a LiveCallContext")
    return context

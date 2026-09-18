"""Contract tests for the live-agent tools.

These pin a failure that is invisible from the outside: Strands injects
only `tool_context` and `agent`. Any other parameter is treated as
MODEL-SUPPLIED input and lands in the tool's public JSON schema. An
earlier version took `invocation_state`/`request_state` as plain
parameters, so the model was asked to invent them -- every tool call
failed, and `end_session` mutated a dict the model made up, meaning the
call could never end. Nothing in lint, types, or the app import caught it.

They also pin the navigation contract, which is what keeps the patient's
screen and the conversation in step: a tool that silently accepts a
screen name the frontend does not route, or that raises instead of
correcting the model mid-call, desynchronizes the two in a way only a real
call would reveal.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from app.agents.bidi.call_state import CallProgress, LiveCallContext, UiEventBus
from app.agents.tools.appointment import move_appointment, offer_appointment_times
from app.agents.tools.intake_details import record_intake_details
from app.agents.tools.navigation import get_current_screen, navigate_to_screen
from app.agents.tools.scheduling import cancel_appointment
from app.agents.tools.session_control import end_session
from app.agents.tools.symptoms import (
    ask_symptom_question,
    record_symptom_answer,
    start_prescreening,
)
from app.agents.tools.upload import request_document_upload
from app.core.constants import (
    SCREEN_FIELDS,
    SCREEN_REQUIRED_FIELDS,
    IntakeScreen,
    PrescreeningCategory,
    PresentationType,
)
from app.core.exceptions import AppointmentTimeUnavailableError, ConsentNotRecordedError
from app.models.question_bank import BankQuestion
from app.models.symptom_intake import SymptomAnswer
from app.services.appointment_service import SpokenSlot


class _StubToolContext:
    """Minimal stand-in carrying only what these tools read."""

    def __init__(self, **invocation_state: Any) -> None:
        self.invocation_state: dict[str, Any] = dict(invocation_state)


class _StubQuestionBank:
    def reference_questions(
        self, category: PrescreeningCategory, limit: int = 12
    ) -> list[BankQuestion]:
        return [
            BankQuestion.build(category, f"reference {index} for {category.value}")
            for index in range(3)
        ][:limit]

    async def record_asked(self, category: PrescreeningCategory, texts: list[str]) -> int:
        return len(texts)


class _RecordingSessions:
    """Stands in for `SessionRepository`, recording progress writes."""

    def __init__(self) -> None:
        self.writes: list[tuple[str, IntakeScreen, dict[str, dict[str, str]]]] = []
        self.selection_writes: list[dict[str, dict[str, str]]] = []
        self.status_writes: list[dict[str, dict[str, str]]] = []
        self.symptom_writes: list[tuple[list[PrescreeningCategory], list[SymptomAnswer]]] = []

    async def set_call_progress(
        self,
        session_id: str,
        screen: IntakeScreen,
        details: dict[str, dict[str, str]],
        selections: dict[str, dict[str, str]] | None = None,
        statuses: dict[str, dict[str, str]] | None = None,
    ) -> None:
        self.writes.append((session_id, screen, details))
        self.selection_writes.append(selections or {})
        self.status_writes.append(statuses or {})

    async def set_symptom_progress(
        self,
        session_id: str,
        categories: list[PrescreeningCategory],
        answers: list[SymptomAnswer],
        presentation: PresentationType | None = None,
    ) -> None:
        self.symptom_writes.append((list(categories), list(answers)))


def _drain(bus: UiEventBus) -> list[dict[str, Any]]:
    """Everything currently queued for the browser, in order."""
    frames: list[dict[str, Any]] = []
    while not bus._queue.empty():  # noqa: SLF001 - the queue is the assertion surface
        frames.append(bus._queue.get_nowait())  # noqa: SLF001
    return frames


def _context(*, consent_given: bool) -> LiveCallContext:
    return LiveCallContext(
        session_id="sess_8f2c1a",
        bus=UiEventBus(),
        progress=CallProgress(),
        sessions=_RecordingSessions(),  # type: ignore[arg-type]
        consent_given=consent_given,
    )


@pytest.fixture
def live_context() -> LiveCallContext:
    """The ordinary case: consent recorded, the whole call available."""
    return _context(consent_given=True)


@pytest.fixture
def unconsented_context() -> LiveCallContext:
    """Before consent -- the agent may greet and ask, and nothing else."""
    return _context(consent_given=False)


@pytest.fixture
def unconsented_tool_ctx(unconsented_context: LiveCallContext) -> _StubToolContext:
    return _StubToolContext(
        session_id=unconsented_context.session_id,
        question_bank=_StubQuestionBank(),
        live_call=unconsented_context,
    )


def _seed_screen(context: LiveCallContext, screen: IntakeScreen) -> None:
    """Put the call on `screen` without going through the tool.

    The flow only moves one step at a time (see
    `test_navigate_cannot_skip_a_screen`), so a test about a single
    transition seeds the screen before it rather than walking there.
    """
    context.progress.screen = screen
    context.progress.visited.append(screen)


def _seed_symptom_question_minimum(context: LiveCallContext) -> None:
    """Satisfy the symptom-story question-count gate without exercising it.

    Tests about ordinary navigation off `symptom-story` are not testing
    the MIN_SYMPTOM_QUESTIONS gate itself (see test_symptom_flow.py for
    that), so they seed past it directly.
    """
    from app.agents.bidi.symptom_plan import MIN_SYMPTOM_QUESTIONS

    context.symptoms.categories = [PrescreeningCategory.LUNG]
    context.symptoms.answered = [
        SymptomAnswer(
            question_id=f"lung-{index:02d}",
            category=PrescreeningCategory.LUNG,
            question=f"Question {index}?",
            answer=f"answer {index}",
        )
        for index in range(MIN_SYMPTOM_QUESTIONS)
    ]


@pytest.fixture
def tool_ctx(live_context: LiveCallContext) -> _StubToolContext:
    return _StubToolContext(
        session_id=live_context.session_id,
        question_bank=_StubQuestionBank(),
        live_call=live_context,
    )


class _StubSchedulingService:
    """Records calls instead of touching Mongo -- these are contract
    tests for the tool layer, not `SchedulingService` itself (see
    tests/integration/test_scheduling_service.py for that)."""

    def __init__(self) -> None:
        self.cancel_calls: list[tuple[str, str | None, str]] = []

    async def cancel_appointment(self, session_id: str, reason: str | None, *, actor: str) -> None:
        self.cancel_calls.append((session_id, reason, actor))


_OFFERED_START = datetime(2026, 9, 22, 14, 0, tzinfo=UTC)


class _StubAppointmentService:
    """Stands in for `AppointmentService`, which the reschedule tools share
    with the patient's own screen. Only the two calls they make are here."""

    def __init__(
        self, *, slots: list[SpokenSlot] | None = None, move_raises: Exception | None = None
    ) -> None:
        self.moves: list[tuple[str, datetime]] = []
        self._slots = (
            [
                SpokenSlot(
                    start=_OFFERED_START, spoken_time="Tuesday 22 September at 2 in the afternoon"
                )
            ]
            if slots is None
            else slots
        )
        self._move_raises = move_raises

    async def spoken_slots(self, session_id: str, limit: int) -> list[SpokenSlot]:
        return self._slots[:limit]

    async def move_to(self, session_id: str, start: datetime) -> tuple[Any, Any]:
        if self._move_raises is not None:
            raise self._move_raises
        self.moves.append((session_id, start))
        return (
            SimpleNamespace(appointment_datetime=start, appointment_timezone="UTC"),
            SimpleNamespace(name="Dr Synthetic"),
        )


@pytest.mark.parametrize(
    ("tool", "expected"),
    [
        (start_prescreening, {"category", "presentation"}),
        (ask_symptom_question, {"question_text", "category"}),
        (record_symptom_answer, {"answer", "status", "corrects", "relevance"}),
        (request_document_upload, set()),
        (end_session, set()),
        (navigate_to_screen, {"screen"}),
        (get_current_screen, set()),
        (record_intake_details, {"screen", "details", "selections", "certainty"}),
        (cancel_appointment, {"reason"}),
        (offer_appointment_times, set()),
        (move_appointment, {"slot_id"}),
    ],
    ids=[
        "start_prescreening",
        "ask_symptom_question",
        "record_symptom_answer",
        "request_document_upload",
        "end_session",
        "navigate_to_screen",
        "get_current_screen",
        "record_intake_details",
        "cancel_appointment",
        "offer_appointment_times",
        "move_appointment",
    ],
)
def test_tools_expose_only_model_supplied_parameters(tool: Any, expected: set[str]) -> None:
    """Runtime-injected context must never appear in the model-facing schema."""
    schema = tool.tool_spec["inputSchema"]["json"]

    assert set(schema["properties"]) == expected
    assert "invocation_state" not in schema["properties"]
    assert "request_state" not in schema["properties"]
    assert "tool_context" not in schema["properties"]


def test_start_prescreening_reads_the_bank_from_context(
    tool_ctx: _StubToolContext, live_context: LiveCallContext
) -> None:
    """The tool resolves the reason, fixes it on the call, and hands back the brief.

    The reference questions come back too, and the wording around them is
    part of the contract: handed over as a list to work through, they
    reproduce exactly the identical-every-call interview this replaced.
    """
    result = start_prescreening("lung", tool_context=tool_ctx)  # type: ignore[arg-type]

    assert live_context.symptoms.categories == [PrescreeningCategory.LUNG]
    assert "reference 0 for lung" in result
    assert "REFERENCE, not a script" in result


def test_unknown_reason_is_corrected_rather_than_raised(tool_ctx: _StubToolContext) -> None:
    """An off-list reason comes back as a usable correction, not an exception.

    A tool that raises mid-call surfaces as a broken turn to a patient who
    is part-way through a sentence.
    """
    result = start_prescreening("dermatology", tool_context=tool_ctx)  # type: ignore[arg-type]

    assert "not an appointment reason" in result
    assert "lung" in result


def test_end_session_sets_the_flag_the_bidi_loop_actually_reads() -> None:
    """The loop checks invocation_state['request_state']['stop_event_loop'].

    Writing anywhere else means the call never ends and the patient is
    left on a line that will not hang up.
    """
    ctx = _StubToolContext(request_state={})

    end_session(tool_context=ctx)  # type: ignore[arg-type]

    assert ctx.invocation_state["request_state"]["stop_event_loop"] is True


def test_end_session_creates_request_state_when_absent() -> None:
    """Defensive: the flag still lands if the loop has not seeded the dict."""
    ctx = _StubToolContext()

    end_session(tool_context=ctx)  # type: ignore[arg-type]

    assert ctx.invocation_state["request_state"]["stop_event_loop"] is True


def test_request_document_upload_opens_the_widget_without_naming_the_tool(
    tool_ctx: _StubToolContext, live_context: LiveCallContext
) -> None:
    """The tool signals intent on the patient's channel; it never sees the file."""
    request_document_upload(tool_context=tool_ctx)  # type: ignore[arg-type]

    assert _drain(live_context.bus) == [{"type": "upload_requested"}]


async def test_navigate_publishes_the_screen_and_persists_it(
    tool_ctx: _StubToolContext, live_context: LiveCallContext
) -> None:
    """A screen change must reach the browser and the resume marker both."""
    _seed_screen(live_context, IntakeScreen.SYMPTOM_STORY)
    _seed_symptom_question_minimum(live_context)

    reply = await navigate_to_screen("medication", tool_context=tool_ctx)  # type: ignore[arg-type]

    assert live_context.progress.screen is IntakeScreen.MEDICATION
    assert _drain(live_context.bus) == [{"type": "navigate", "screen": "medication", "sequence": 1}]
    # The reply is the model's only view of what is on screen, so it has to
    # name the fields the next tool call is allowed to fill.
    assert "medications" in reply


async def test_navigate_to_an_unknown_screen_corrects_rather_than_raises(
    tool_ctx: _StubToolContext, live_context: LiveCallContext
) -> None:
    """A tool exception mid-call surfaces to the patient as a broken turn."""
    reply = await navigate_to_screen("prescriptions", tool_context=tool_ctx)  # type: ignore[arg-type]

    assert live_context.progress.screen is IntakeScreen.WELCOME
    assert _drain(live_context.bus) == []
    assert "medication" in reply  # the valid-screen list is in the correction


async def test_navigate_refuses_the_appointment_read_back(
    tool_ctx: _StubToolContext, live_context: LiveCallContext
) -> None:
    """A real screen, but not a step the intake walks through.

    Reading the appointment aloud before the patient has said why they are
    calling delays the only part of the conversation that matters.
    """
    await navigate_to_screen("appointment-schedule", tool_context=tool_ctx)  # type: ignore[arg-type]

    assert live_context.progress.screen is IntakeScreen.WELCOME
    assert _drain(live_context.bus) == []


async def test_navigate_cannot_leave_the_consent_screen_before_consent(
    unconsented_tool_ctx: _StubToolContext, unconsented_context: LiveCallContext
) -> None:
    """The socket opens before consent, so this is what keeps that honest."""
    reply = await navigate_to_screen(  # type: ignore[arg-type]
        "patient-concerns", tool_context=unconsented_tool_ctx
    )

    assert unconsented_context.progress.screen is IntakeScreen.WELCOME
    assert _drain(unconsented_context.bus) == []
    assert "consent" in reply.lower()


async def test_the_model_cannot_move_the_patient_off_welcome(
    unconsented_tool_ctx: _StubToolContext, unconsented_context: LiveCallContext
) -> None:
    """Leaving the greeting screen is the app's move, not the model's.

    The model calls tools at generation time, and Nova emits the call
    before it streams the audio for the same turn -- so honouring it here
    moved the screen before a word of the greeting had been heard, and the
    introduction played over the consent screen.
    """
    reply = await navigate_to_screen(  # type: ignore[arg-type]
        "confirm-details", tool_context=unconsented_tool_ctx
    )

    assert unconsented_context.progress.screen is IntakeScreen.WELCOME
    assert _drain(unconsented_context.bus) == []
    assert "by itself" in reply


async def test_nothing_is_recorded_before_consent(
    unconsented_tool_ctx: _StubToolContext, unconsented_context: LiveCallContext
) -> None:
    """Consent is what makes collecting lawful, so this refuses in code."""
    reply = await record_intake_details(  # type: ignore[arg-type]
        "confirm-details",
        '{"phone_number": "+1 555 0100"}',
        tool_context=unconsented_tool_ctx,
    )

    assert unconsented_context.progress.details == {}
    assert _drain(unconsented_context.bus) == []
    assert "consent" in reply.lower()


def test_the_question_bank_is_withheld_before_consent(
    unconsented_tool_ctx: _StubToolContext,
) -> None:
    """Screening questions are the clinical part; handing them over early
    would be collecting by another name."""
    with pytest.raises(ConsentNotRecordedError):
        start_prescreening("lung", tool_context=unconsented_tool_ctx)  # type: ignore[arg-type]


def test_no_document_is_requested_before_consent(
    unconsented_tool_ctx: _StubToolContext, unconsented_context: LiveCallContext
) -> None:
    reply = request_document_upload(tool_context=unconsented_tool_ctx)  # type: ignore[arg-type]

    assert _drain(unconsented_context.bus) == []
    assert "consent" in reply.lower()


async def test_navigate_sequence_increases_so_stale_frames_can_be_ignored(
    tool_ctx: _StubToolContext, live_context: LiveCallContext
) -> None:
    """A reconnect can deliver frames out of order; the frontend needs a monotonic key."""
    _seed_screen(live_context, IntakeScreen.CONFIRM_DETAILS)

    await navigate_to_screen("patient-concerns", tool_context=tool_ctx)  # type: ignore[arg-type]
    first_sequence = _drain(live_context.bus)[0]["sequence"]

    # The completeness gate (see test_navigate_refuses_the_next_screen_when_
    # a_required_field_is_missing) refuses the next step until 'concerns'
    # is recorded.
    await record_intake_details(  # type: ignore[arg-type]
        "patient-concerns", '{"concerns": "short of breath"}', tool_context=tool_ctx
    )
    _drain(live_context.bus)  # discard the form_prefill frame from the line above
    await navigate_to_screen("symptom-story", tool_context=tool_ctx)  # type: ignore[arg-type]
    second_sequence = _drain(live_context.bus)[0]["sequence"]

    assert [first_sequence, second_sequence] == [1, 2]


async def test_navigate_cannot_skip_a_screen(
    tool_ctx: _StubToolContext, live_context: LiveCallContext
) -> None:
    """A forward jump is a topic the patient never got asked about.

    The refusal names the screen that has to come first, so the model can
    correct itself in the same turn instead of losing one.
    """
    _seed_screen(live_context, IntakeScreen.PATIENT_CONCERNS)

    reply = await navigate_to_screen("medication", tool_context=tool_ctx)  # type: ignore[arg-type]

    assert live_context.progress.screen is IntakeScreen.PATIENT_CONCERNS
    assert _drain(live_context.bus) == []
    assert "symptom-story" in reply


def test_every_required_field_actually_belongs_to_its_own_screen() -> None:
    """A typo in SCREEN_REQUIRED_FIELDS would otherwise gate a screen on a
    field it can never record, refusing to move on forever."""
    for screen, required in SCREEN_REQUIRED_FIELDS.items():
        assert required <= set(SCREEN_FIELDS.get(screen, ()))


async def test_navigate_refuses_the_next_screen_when_a_required_field_is_missing(
    tool_ctx: _StubToolContext, live_context: LiveCallContext
) -> None:
    """One answer out of several questions on a screen must not be enough
    to move on -- this is the completeness gate `SCREEN_REQUIRED_FIELDS`
    backs, and it must name what is still missing."""
    _seed_screen(live_context, IntakeScreen.MEDICAL_HISTORY)
    await record_intake_details(  # type: ignore[arg-type]
        "medical-history", '{"conditions": "none"}', tool_context=tool_ctx
    )
    _drain(live_context.bus)

    reply = await navigate_to_screen("recent-care", tool_context=tool_ctx)  # type: ignore[arg-type]

    assert live_context.progress.screen is IntakeScreen.MEDICAL_HISTORY
    assert _drain(live_context.bus) == []
    assert "no_hospital_stays" in reply
    # The field already recorded must not be named as still missing.
    assert "still needs: conditions" not in reply


async def test_navigate_ignores_optional_fields_when_checking_completeness(
    tool_ctx: _StubToolContext, live_context: LiveCallContext
) -> None:
    """Elaboration and conditional fields (condition_details, hospital_stays)
    must never block the move on their own -- only the screen's required
    fields do. A patient with no ongoing conditions and no hospital stays
    has completely answered this screen with two short "no"s."""
    _seed_screen(live_context, IntakeScreen.MEDICAL_HISTORY)
    await record_intake_details(  # type: ignore[arg-type]
        "medical-history",
        '{"conditions": "none", "no_hospital_stays": "yes"}',
        tool_context=tool_ctx,
    )
    _drain(live_context.bus)

    await navigate_to_screen("recent-care", tool_context=tool_ctx)  # type: ignore[arg-type]

    assert live_context.progress.screen is IntakeScreen.RECENT_CARE


async def test_navigate_can_go_back_to_a_covered_screen(
    tool_ctx: _StubToolContext, live_context: LiveCallContext
) -> None:
    """A patient correcting an earlier answer is why backwards stays open."""
    _seed_screen(live_context, IntakeScreen.ALLERGIES)

    await navigate_to_screen("medication", tool_context=tool_ctx)  # type: ignore[arg-type]

    assert live_context.progress.screen is IntakeScreen.MEDICATION


async def test_recording_out_of_order_tells_the_model_to_navigate_first(
    tool_ctx: _StubToolContext, live_context: LiveCallContext
) -> None:
    """The catch-up is not silent: drift repeats unless the model is told.

    Recording for a screen the patient is not on means they were asked
    while looking at the previous topic. The value is kept and the screen
    follows it, and the reply says what went wrong.
    """
    _seed_screen(live_context, IntakeScreen.SYMPTOM_STORY)

    reply = await record_intake_details(  # type: ignore[arg-type]
        "medication",
        '{"medications": "Metformin"}',
        tool_context=tool_ctx,
    )

    assert live_context.progress.as_storage()["medication"] == {"medications": "Metformin"}
    assert "navigate_to_screen" in reply


async def test_record_details_accepts_only_the_screen_s_own_fields(
    tool_ctx: _StubToolContext, live_context: LiveCallContext
) -> None:
    """An allowlist, not a hint: a hallucinated field must never reach the screen."""
    await record_intake_details(
        "medication",
        '{"medications": "Metformin 500mg twice a day", "diagnosis": "type 2 diabetes"}',
        tool_context=tool_ctx,  # type: ignore[arg-type]
    )

    prefill = [f for f in _drain(live_context.bus) if f["type"] == "form_prefill"]
    assert prefill == [
        {
            "type": "form_prefill",
            "screen": "medication",
            "fields": {"medications": "Metformin 500mg twice a day"},
            "selections": {},
        }
    ]


async def test_recording_for_another_screen_carries_the_patient_there(
    tool_ctx: _StubToolContext, live_context: LiveCallContext
) -> None:
    """A skipped navigation must not leave the answer on a page nobody sees.

    The model has been observed asking the next question without moving the
    screen first. The data is the more reliable signal of where the
    conversation actually is, so it brings the screen with it.
    """
    await record_intake_details(  # type: ignore[arg-type]
        "medication",
        '{"medications": "Metformin"}',
        tool_context=tool_ctx,
    )

    assert live_context.progress.screen is IntakeScreen.MEDICATION
    assert [f["type"] for f in _drain(live_context.bus)] == ["navigate", "form_prefill"]


async def test_recording_for_the_current_screen_does_not_re_navigate(
    tool_ctx: _StubToolContext, live_context: LiveCallContext
) -> None:
    """The ordinary case stays one frame, not two."""
    _seed_screen(live_context, IntakeScreen.SYMPTOM_STORY)
    _seed_symptom_question_minimum(live_context)
    await navigate_to_screen("medication", tool_context=tool_ctx)  # type: ignore[arg-type]
    _drain(live_context.bus)

    await record_intake_details(  # type: ignore[arg-type]
        "medication",
        '{"medications": "Metformin"}',
        tool_context=tool_ctx,
    )

    assert [f["type"] for f in _drain(live_context.bus)] == ["form_prefill"]


async def test_the_booked_identity_cannot_be_overwritten_by_the_agent(
    tool_ctx: _StubToolContext, live_context: LiveCallContext
) -> None:
    """The patient is being asked to confirm those values, not shown new ones.

    Observed live: "yes that is all correct" produced a recorded date of
    birth of 1990-01-01 for a patient booked as 1990-03-04, under the
    question asking them to check it. Only the phone number is the
    agent's to fill.
    """
    _seed_screen(live_context, IntakeScreen.CONFIRM_DETAILS)

    await record_intake_details(  # type: ignore[arg-type]
        "confirm-details",
        '{"full_name": "Test Patient", "date_of_birth": "1990-01-01", '
        '"phone_number": "+1 555 0100"}',
        tool_context=tool_ctx,
    )

    assert live_context.progress.as_storage()["confirm-details"] == {"phone_number": "+1 555 0100"}


async def test_a_question_is_not_recorded_as_an_answer(
    tool_ctx: _StubToolContext, live_context: LiveCallContext
) -> None:
    """Observed live: an unanswered question written into the field itself.

    Asked to record what a cough felt like before the patient had said,
    the model wrote "dry or wet? (unspecified)" -- shown on screen as
    though the patient had said it.
    """
    _seed_screen(live_context, IntakeScreen.PATIENT_CONCERNS)

    reply = await record_intake_details(  # type: ignore[arg-type]
        "patient-concerns",
        '{"concern_details": "dry or wet? (unspecified)", "concerns": "a cough"}',
        tool_context=tool_ctx,
    )

    assert live_context.progress.as_storage()["patient-concerns"] == {"concerns": "a cough"}
    assert "concern_details" not in reply


async def test_record_intake_details_points_at_the_symptom_tools(
    tool_ctx: _StubToolContext, live_context: LiveCallContext
) -> None:
    """The symptom screen has no fields, so the model must be told what to use instead."""
    _seed_screen(live_context, IntakeScreen.SYMPTOM_STORY)

    reply = await record_intake_details(  # type: ignore[arg-type]
        "symptom-story", '{"onset": "three weeks"}', tool_context=tool_ctx
    )

    assert "ask_symptom_question" in reply
    assert "record_symptom_answer" in reply
    assert live_context.progress.details == {}


async def test_record_details_reads_key_value_lines_when_json_fails(
    tool_ctx: _StubToolContext, live_context: LiveCallContext
) -> None:
    """Tolerant parsing: a malformed argument costs a visible answer, not correctness."""
    await record_intake_details(
        "allergies",
        "allergies: penicillin, rash\nno_known_allergies: no",
        tool_context=tool_ctx,  # type: ignore[arg-type]
    )

    assert live_context.progress.as_storage()["allergies"] == {
        "allergies": "penicillin, rash",
        "no_known_allergies": "no",
    }


async def test_record_details_with_nothing_recognized_lists_the_real_fields(
    tool_ctx: _StubToolContext, live_context: LiveCallContext
) -> None:
    """The model needs a usable correction, and the patient needs no stray frame."""
    reply = await record_intake_details(
        "medication",
        '{"prescription": "unknown"}',
        tool_context=tool_ctx,  # type: ignore[arg-type]
    )

    assert _drain(live_context.bus) == []
    assert "medications" in reply


def test_get_current_screen_reports_position_without_changing_it(
    tool_ctx: _StubToolContext, live_context: LiveCallContext
) -> None:
    """Used after a reconnect, so it must be purely a read."""
    live_context.progress.screen = IntakeScreen.RECENT_CARE

    reply = get_current_screen(tool_context=tool_ctx)  # type: ignore[arg-type]

    assert "recent-care" in reply
    assert _drain(live_context.bus) == []
    assert live_context.progress.screen is IntakeScreen.RECENT_CARE


# --------------------------------------------------------------------------
# Agent-supplied option ids
# --------------------------------------------------------------------------
#
# The model now names the on-screen option it means, not just the words it
# heard, because it is the only part of the system that understood the
# sentence -- "my sugar's been bad" is diabetes, and matching that in the
# browser with substrings missed every synonym and read every denial as an
# affirmation. These pin the two halves that make it safe to ask for:
# unknown ids are dropped exactly like unknown fields, and a call that
# never fills the argument behaves as it did before.


async def test_record_details_carries_the_option_ids_the_model_chose(
    tool_ctx: _StubToolContext, live_context: LiveCallContext
) -> None:
    """Words and ids travel together, in the screen's own option order."""
    _seed_screen(live_context, IntakeScreen.MEDICAL_HISTORY)

    await record_intake_details(
        "medical-history",
        '{"conditions": "my sugar has been bad and the BP runs high"}',
        tool_context=tool_ctx,  # type: ignore[arg-type]
        selections='{"conditions": "high-blood-pressure, diabetes"}',
    )

    prefill = [f for f in _drain(live_context.bus) if f["type"] == "form_prefill"]
    assert prefill[0]["fields"] == {"conditions": "my sugar has been bad and the BP runs high"}
    assert prefill[0]["selections"] == {"conditions": "diabetes, high-blood-pressure"}


async def test_hallucinated_option_ids_are_dropped_like_hallucinated_fields(
    tool_ctx: _StubToolContext, live_context: LiveCallContext
) -> None:
    """An id outside the field's option list must never reach the screen.

    Same discipline as the field allowlist, and for the same reason: the
    patient is looking at a fixed set of cards, and a card the frontend
    cannot render is an answer nobody can see or correct.
    """
    _seed_screen(live_context, IntakeScreen.MEDICAL_HISTORY)

    await record_intake_details(
        "medical-history",
        '{"conditions": "diabetes and gout"}',
        tool_context=tool_ctx,  # type: ignore[arg-type]
        selections='{"conditions": "diabetes, gout", "diagnosis": "type 2"}',
    )

    prefill = [f for f in _drain(live_context.bus) if f["type"] == "form_prefill"]
    assert prefill[0]["selections"] == {"conditions": "diabetes"}


async def test_a_field_whose_every_id_is_unknown_is_left_to_the_frontend(
    tool_ctx: _StubToolContext, live_context: LiveCallContext
) -> None:
    """Dropped entirely, not sent as an empty selection.

    An empty string on the wire means "the agent chose nothing here, match
    the words instead". A garbled answer must not be able to say that --
    it would silently blank a control the words would have filled.
    """
    _seed_screen(live_context, IntakeScreen.MEDICAL_HISTORY)

    await record_intake_details(
        "medical-history",
        '{"conditions": "gout"}',
        tool_context=tool_ctx,  # type: ignore[arg-type]
        selections='{"conditions": "gout"}',
    )

    prefill = [f for f in _drain(live_context.bus) if f["type"] == "form_prefill"]
    assert prefill[0]["selections"] == {}


async def test_single_choice_ids_are_accepted_however_the_model_cases_them(
    tool_ctx: _StubToolContext, live_context: LiveCallContext
) -> None:
    """Those ids are visible labels, so a lowercased one still names a real option."""
    _seed_screen(live_context, IntakeScreen.FAMILY_SOCIAL_HISTORY)

    await record_intake_details(
        "family-social-history",
        '{"tobacco_use": "gave up about ten years ago"}',
        tool_context=tool_ctx,  # type: ignore[arg-type]
        selections='{"tobacco_use": "former"}',
    )

    prefill = [f for f in _drain(live_context.bus) if f["type"] == "form_prefill"]
    assert prefill[0]["selections"] == {"tobacco_use": "Former"}


async def test_selections_alone_are_enough_to_record(
    tool_ctx: _StubToolContext, live_context: LiveCallContext
) -> None:
    """A yes/no the patient answered with a nod of a word still lands.

    Recording nothing here was the old behaviour for every boolean on the
    form: the frontend could not read "no known allergies" as a polarity,
    so the box stayed untouched.
    """
    _seed_screen(live_context, IntakeScreen.ALLERGIES)

    reply = await record_intake_details(
        "allergies",
        "{}",
        tool_context=tool_ctx,  # type: ignore[arg-type]
        selections='{"no_known_allergies": "yes"}',
    )

    prefill = [f for f in _drain(live_context.bus) if f["type"] == "form_prefill"]
    assert prefill[0]["selections"] == {"no_known_allergies": "yes"}
    assert "no_known_allergies" in reply


async def test_omitting_selections_behaves_exactly_as_before(
    tool_ctx: _StubToolContext, live_context: LiveCallContext
) -> None:
    """The fallback is the whole reason this is safe to ask a live model for.

    A turn where the model fills only `details` must reach the browser as
    words with no selection, which is the protocol-2 frame the frontend
    still matches for itself.
    """
    _seed_screen(live_context, IntakeScreen.MEDICAL_HISTORY)

    await record_intake_details(
        "medical-history",
        '{"conditions": "diabetes"}',
        tool_context=tool_ctx,  # type: ignore[arg-type]
    )

    prefill = [f for f in _drain(live_context.bus) if f["type"] == "form_prefill"]
    assert prefill[0]["fields"] == {"conditions": "diabetes"}
    assert prefill[0]["selections"] == {}


async def test_selections_are_persisted_for_a_reconnect(
    tool_ctx: _StubToolContext, live_context: LiveCallContext
) -> None:
    """Written alongside the words, so a rejoining patient sees the card still ticked."""
    _seed_screen(live_context, IntakeScreen.MEDICAL_HISTORY)

    await record_intake_details(
        "medical-history",
        '{"conditions": "diabetes"}',
        tool_context=tool_ctx,  # type: ignore[arg-type]
        selections='{"conditions": "diabetes"}',
    )

    sessions = live_context.sessions
    assert isinstance(sessions, _RecordingSessions)
    assert sessions.selection_writes[-1] == {"medical-history": {"conditions": "diabetes"}}


def test_navigate_hands_the_model_the_ids_it_may_choose_from(
    tool_ctx: _StubToolContext, live_context: LiveCallContext
) -> None:
    """The closed list arrives with the screen, which is what makes it usable.

    In the tool docstring it would be one static blob covering every
    screen; here it is the options for the screen the patient is looking
    at, delivered in the reply the model already reads before asking its
    first question on the topic.
    """
    live_context.progress.screen = IntakeScreen.MEDICAL_HISTORY

    reply = get_current_screen(tool_context=tool_ctx)  # type: ignore[arg-type]

    assert "selections" in reply
    assert "conditions = diabetes | high-blood-pressure" in reply
    assert "no_hospital_stays = yes | no" in reply
    # Free-text fields must not be presented as having options.
    assert "condition_details =" not in reply


async def test_cancel_appointment_delegates_to_the_scheduling_service() -> None:
    """Unlike `end_session`, this does not touch `request_state` -- the
    model is instructed to call `end_session` separately after saying
    goodbye, same as any other call ending."""
    scheduling = _StubSchedulingService()
    ctx = _StubToolContext(session_id="sess_1", scheduling=scheduling)

    result = await cancel_appointment("changed my mind", tool_context=ctx)  # type: ignore[arg-type]

    assert scheduling.cancel_calls == [("sess_1", "changed my mind", "voice_agent")]
    assert "cancelled" in result.lower()


async def test_offering_times_moves_the_patient_and_hands_back_what_it_showed(
    tool_ctx: _StubToolContext, live_context: LiveCallContext
) -> None:
    """The screen and the spoken offer are one list, not two.

    The times come back with the ids the tool will accept, and the patient
    is moved onto the screen showing the same ones -- an agent offering a
    time the screen cannot show is offering a time nobody can take.
    """
    _seed_screen(live_context, IntakeScreen.THANK_YOU)
    tool_ctx.invocation_state["appointments"] = _StubAppointmentService()

    reply = await offer_appointment_times(tool_context=tool_ctx)  # type: ignore[arg-type]

    assert live_context.progress.screen is IntakeScreen.APPOINTMENT_RESCHEDULE
    assert "Tuesday 22 September at 2 in the afternoon" in reply
    assert _OFFERED_START.isoformat() in reply
    assert [
        frame["screen"] for frame in _drain(live_context.bus) if frame["type"] == "navigate"
    ] == ["appointment-reschedule"]


async def test_the_offered_times_are_for_matching_not_for_reciting(
    tool_ctx: _StubToolContext, live_context: LiveCallContext
) -> None:
    """Reading clock times at someone is what made this unusable.

    The patient can see every open time on screen, and a spoken list of
    them is hard to follow and impossible to hold on to. The model still
    gets the list -- that is how "the Monday one" resolves to a slot id --
    but the reply has to tell it plainly not to read them out.
    """
    _seed_screen(live_context, IntakeScreen.THANK_YOU)
    tool_ctx.invocation_state["appointments"] = _StubAppointmentService()

    reply = await offer_appointment_times(tool_context=tool_ctx)  # type: ignore[arg-type]

    assert "Do NOT read these times out loud" in reply
    assert "Never read a slot_id aloud" in reply
    assert "for YOU, not for them" in reply
    # The old instruction, which is what produced "2:00, 2:30" out loud.
    assert "Say two or three of them out loud" not in reply


async def test_offering_times_reopens_the_closing_question(
    tool_ctx: _StubToolContext, live_context: LiveCallContext
) -> None:
    """The eight-second goodbye window must not be what is counting while
    the patient reads a list of times -- see `LiveCallContext.closing_started`."""
    _seed_screen(live_context, IntakeScreen.THANK_YOU)
    live_context.closing_started = True
    tool_ctx.invocation_state["appointments"] = _StubAppointmentService()

    await offer_appointment_times(tool_context=tool_ctx)  # type: ignore[arg-type]

    assert live_context.closing_started is False


async def test_nothing_open_leaves_the_patient_on_the_closing_screen(
    tool_ctx: _StubToolContext, live_context: LiveCallContext
) -> None:
    """An empty list is worse to look at than to be told about."""
    _seed_screen(live_context, IntakeScreen.THANK_YOU)
    tool_ctx.invocation_state["appointments"] = _StubAppointmentService(slots=[])

    reply = await offer_appointment_times(tool_context=tool_ctx)  # type: ignore[arg-type]

    assert live_context.progress.screen is IntakeScreen.THANK_YOU
    assert "nothing open" in reply.lower()


async def test_rescheduling_is_refused_while_the_screening_is_still_running(
    tool_ctx: _StubToolContext, live_context: LiveCallContext
) -> None:
    """A screening abandoned halfway through for a calendar screen is worth
    nothing to the patient's doctor, so the gate is in code, not the prompt."""
    _seed_screen(live_context, IntakeScreen.MEDICATION)
    tool_ctx.invocation_state["appointments"] = _StubAppointmentService()

    reply = await offer_appointment_times(tool_context=tool_ctx)  # type: ignore[arg-type]

    assert live_context.progress.screen is IntakeScreen.MEDICATION
    assert "at the end" in reply.lower()


async def test_moving_the_appointment_saves_it_and_returns_them_to_the_closing_screen(
    tool_ctx: _StubToolContext, live_context: LiveCallContext
) -> None:
    """The patient hears the new time and sees it: the browser is told to
    re-read the appointment it cached when the session was opened."""
    _seed_screen(live_context, IntakeScreen.APPOINTMENT_RESCHEDULE)
    appointments = _StubAppointmentService()
    tool_ctx.invocation_state["appointments"] = appointments

    reply = await move_appointment(_OFFERED_START.isoformat(), tool_context=tool_ctx)  # type: ignore[arg-type]

    assert appointments.moves == [(live_context.session_id, _OFFERED_START)]
    assert live_context.progress.screen is IntakeScreen.THANK_YOU
    assert "anything else" in reply.lower()
    frames = [frame["type"] for frame in _drain(live_context.bus)]
    assert frames.index("appointment_updated") < frames.index("navigate")


async def test_a_time_taken_since_it_was_offered_is_reported_not_raised(
    tool_ctx: _StubToolContext, live_context: LiveCallContext
) -> None:
    """Someone else taking the slot is an expected outcome, not a broken
    turn: the patient is mid-sentence and keeps their original time."""
    _seed_screen(live_context, IntakeScreen.APPOINTMENT_RESCHEDULE)
    tool_ctx.invocation_state["appointments"] = _StubAppointmentService(
        move_raises=AppointmentTimeUnavailableError("Dr Synthetic")
    )

    reply = await move_appointment(_OFFERED_START.isoformat(), tool_context=tool_ctx)  # type: ignore[arg-type]

    assert live_context.progress.screen is IntakeScreen.APPOINTMENT_RESCHEDULE
    assert "taken" in reply.lower()
    assert "original appointment still stands" in reply.lower()


async def test_an_invented_slot_id_is_corrected_rather_than_written(
    tool_ctx: _StubToolContext, live_context: LiveCallContext
) -> None:
    """Nothing may be written from a time the tool never offered."""
    _seed_screen(live_context, IntakeScreen.APPOINTMENT_RESCHEDULE)
    appointments = _StubAppointmentService()
    tool_ctx.invocation_state["appointments"] = appointments

    reply = await move_appointment("next Tuesday afternoon", tool_context=tool_ctx)  # type: ignore[arg-type]

    assert appointments.moves == []
    assert "offer_appointment_times" in reply


async def test_navigate_refuses_to_go_back_to_welcome(
    tool_ctx: _StubToolContext, live_context: LiveCallContext
) -> None:
    """The one backward move that is a restart rather than a correction.

    `welcome` has no fields and exists solely to hold the greeting, so
    there is nothing on it a patient can correct. A resumed call walked
    back to it and greeted the patient a second time -- which is what
    "it starts from the beginning" looked like from their side.
    """
    _seed_screen(live_context, IntakeScreen.MEDICATION)

    reply = await navigate_to_screen("welcome", tool_context=tool_ctx)  # type: ignore[arg-type]

    assert live_context.progress.screen is IntakeScreen.MEDICATION
    assert _drain(live_context.bus) == []
    assert "cannot go back to 'welcome'" in reply


async def test_get_current_screen_says_consent_is_already_done(
    tool_ctx: _StubToolContext, live_context: LiveCallContext
) -> None:
    """The tool the prompt names for a reconnect has to answer the whole question.

    "Where am I" is not enough after a dropped call: the agent also needs
    to know it has already greeted the patient and already taken their
    consent, or it does both again on the screen it was just told about.
    """
    _seed_screen(live_context, IntakeScreen.ALLERGIES)

    reply = get_current_screen(tool_context=tool_ctx)  # type: ignore[arg-type]

    assert "allergies" in reply
    assert "Consent is already recorded" in reply
    assert "never ask the patient to tick the consent box again" in reply


async def test_get_current_screen_holds_an_unconsented_call_where_it_is(
    unconsented_tool_ctx: _StubToolContext, unconsented_context: LiveCallContext
) -> None:
    """The same tool must not tell a pre-consent call that consent is done."""
    _seed_screen(unconsented_context, IntakeScreen.CONFIRM_DETAILS)

    reply = get_current_screen(tool_context=unconsented_tool_ctx)  # type: ignore[arg-type]

    assert "Consent has NOT been recorded yet" in reply

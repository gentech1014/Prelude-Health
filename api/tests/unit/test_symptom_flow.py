"""The screening screen's one-question-at-a-time contract, now that the agent writes the questions.

This is the only screen the agent drives as a conversation rather than a
form, so the guarantees the patient actually experiences -- exactly one
live question, an answer that clears it, and nothing re-asked -- are
pinned here rather than left to the prompt.

Two of those used to come free from the shape of the old design: the agent
picked questions out of a fixed set by id, so a question could not be
malformed and could not be asked twice. It now writes each one itself, so
those guarantees have to be tested rather than assumed -- which is most of
what is new in this file, alongside the reason the change was made at all:
`general_checkup` and `not_sure` are screenable reasons with their own
briefs, instead of bookings with no category that ended up screened as
stomach complaints.
"""

from typing import Any

import pytest

from app.agents.bidi.call_state import CallProgress, LiveCallContext, UiEventBus
from app.agents.bidi.symptom_plan import (
    MAX_QUESTION_LENGTH,
    MAX_SYMPTOM_CATEGORIES,
    MAX_SYMPTOM_QUESTIONS,
    MIN_SYMPTOM_QUESTIONS,
    SymptomQuestionPlan,
)
from app.agents.tools.navigation import navigate_to_screen
from app.agents.tools.symptoms import (
    ask_symptom_question,
    record_symptom_answer,
    start_prescreening,
)
from app.core.constants import IntakeScreen, PrescreeningCategory, PresentationType
from app.core.exceptions import ConsentNotRecordedError
from app.models.question_bank import BankQuestion, QuestionSource
from app.models.symptom_intake import SymptomAnswer

_REFERENCE_PER_CATEGORY = 4


class _StubToolContext:
    def __init__(self, **invocation_state: Any) -> None:
        self.invocation_state: dict[str, Any] = dict(invocation_state)


class _StubQuestionBank:
    """Reference in, accumulation out -- the two things the real service does."""

    def __init__(self) -> None:
        self.recorded: list[tuple[PrescreeningCategory, list[str]]] = []

    def reference_questions(
        self, category: PrescreeningCategory, limit: int = 12
    ) -> list[BankQuestion]:
        return [
            BankQuestion.build(
                category, f"reference {index} for {category.value}", source=QuestionSource.SEED
            )
            for index in range(_REFERENCE_PER_CATEGORY)
        ][:limit]

    async def record_asked(self, category: PrescreeningCategory, texts: list[str]) -> int:
        self.recorded.append((category, list(texts)))
        return len(texts)


class _RecordingSessions:
    def __init__(self) -> None:
        self.symptom_writes: list[tuple[list[PrescreeningCategory], list[SymptomAnswer]]] = []

    async def set_call_progress(
        self, session_id: str, screen: IntakeScreen, details: dict[str, dict[str, str]]
    ) -> None:
        return None

    async def set_symptom_progress(
        self,
        session_id: str,
        categories: list[PrescreeningCategory],
        answers: list[SymptomAnswer],
        presentation: PresentationType | None = None,
    ) -> None:
        self.symptom_writes.append((list(categories), list(answers)))


def _drain(bus: UiEventBus) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    while not bus._queue.empty():  # noqa: SLF001 - the queue is the assertion surface
        frames.append(bus._queue.get_nowait())  # noqa: SLF001
    return frames


@pytest.fixture
def question_bank() -> _StubQuestionBank:
    return _StubQuestionBank()


@pytest.fixture
def live_context(question_bank: _StubQuestionBank) -> LiveCallContext:
    return LiveCallContext(
        session_id="sess_8f2c1a",
        bus=UiEventBus(),
        progress=CallProgress(screen=IntakeScreen.SYMPTOM_STORY),
        sessions=_RecordingSessions(),  # type: ignore[arg-type]
        question_bank=question_bank,  # type: ignore[arg-type]
        consent_given=True,
    )


@pytest.fixture
def tool_ctx(live_context: LiveCallContext, question_bank: _StubQuestionBank) -> _StubToolContext:
    return _StubToolContext(
        session_id=live_context.session_id,
        question_bank=question_bank,
        live_call=live_context,
    )


def _start(tool_ctx: _StubToolContext, category: str = "lung") -> str:
    return start_prescreening(category, tool_context=tool_ctx)  # type: ignore[arg-type, no-any-return]


async def _ask(tool_ctx: _StubToolContext, text: str, category: str = "") -> str:
    return await ask_symptom_question(  # type: ignore[arg-type, no-any-return]
        text, tool_context=tool_ctx, category=category
    )


async def _answer(tool_ctx: _StubToolContext, text: str) -> str:
    return await record_symptom_answer(text, tool_context=tool_ctx)  # type: ignore[arg-type, no-any-return]


# --------------------------------------------------------------------------
# The plan itself
# --------------------------------------------------------------------------


def test_starting_a_second_reason_appends_rather_than_replaces() -> None:
    """A booked condition plus a new complaint is two reasons, not the newer one."""
    plan = SymptomQuestionPlan()
    bank = _StubQuestionBank()

    plan.start(
        PrescreeningCategory.DIABETES, bank.reference_questions(PrescreeningCategory.DIABETES)
    )
    plan.start(PrescreeningCategory.LUNG, bank.reference_questions(PrescreeningCategory.LUNG))

    assert plan.categories == [PrescreeningCategory.DIABETES, PrescreeningCategory.LUNG]
    assert plan.primary_category is PrescreeningCategory.DIABETES


def test_starting_the_same_reason_twice_changes_nothing() -> None:
    """The model re-confirming must not duplicate the reason it is screening."""
    plan = SymptomQuestionPlan()
    bank = _StubQuestionBank()

    plan.start(PrescreeningCategory.HEART, bank.reference_questions(PrescreeningCategory.HEART))
    plan.start(PrescreeningCategory.HEART, bank.reference_questions(PrescreeningCategory.HEART))

    assert plan.categories == [PrescreeningCategory.HEART]


def test_an_unknown_question_id_records_nothing() -> None:
    """An id the model invented must never become a line in the report."""
    plan = SymptomQuestionPlan()
    plan.start(PrescreeningCategory.LUNG, [])

    assert plan.record("lung-99", "an answer") is None
    assert plan.answered == []


def test_re_recording_a_question_corrects_it_in_place() -> None:
    """A correction is the same finding revised, never a second one."""
    plan = SymptomQuestionPlan()
    plan.start(PrescreeningCategory.LUNG, [])
    asked = plan.ask(PrescreeningCategory.LUNG, "How long have you had this?")

    plan.record(asked.id, "two weeks")
    plan.record(asked.id, "three weeks")

    assert [entry.answer for entry in plan.answered] == ["three weeks"]


def test_a_typed_answer_leaves_the_question_on_screen() -> None:
    """The patient is still typing into it; clearing it would eat the rest.

    The agent takes the question down when it moves on, which is the point
    at which doing so is actually safe.
    """
    plan = SymptomQuestionPlan()
    plan.start(PrescreeningCategory.LUNG, [])
    asked = plan.ask(PrescreeningCategory.LUNG, "How long have you had this?")

    plan.record(asked.id, "about th", clear_current=False)

    assert plan.current is not None
    assert plan.current.id == asked.id
    assert [entry.answer for entry in plan.answered] == ["about th"]


def test_the_snapshot_shows_one_question_and_the_answers_behind_it() -> None:
    """What the browser renders: exactly one live question, plus the record."""
    plan = SymptomQuestionPlan()
    plan.start(PrescreeningCategory.STOMACH, [])
    first = plan.ask(PrescreeningCategory.STOMACH, "How long has this been going on?")
    plan.record(first.id, "since Monday")
    plan.ask(PrescreeningCategory.STOMACH, "Where exactly do you feel it?")

    snapshot = plan.snapshot()

    assert snapshot["current"] == {
        "question_id": "stomach-02",
        "text": "Where exactly do you feel it?",
    }
    assert snapshot["answers"] == [
        {
            "question_id": "stomach-01",
            "question": "How long has this been going on?",
            "answer": "since Monday",
            # Carried so the screen can show a declined or unremembered
            # answer as what it is rather than as a firm one.
            "status": "confirmed",
        }
    ]
    assert snapshot["answered_count"] == 1
    # No total is published. How long a screening runs depends on what
    # the patient has said, so there is no honest number to promise --
    # the screen used to render "QUESTION 3 OF 10" from one.
    assert "planned_count" not in snapshot


def test_only_answered_questions_are_offered_to_the_bank() -> None:
    """A question the agent abandoned mid-turn is not evidence it was worth asking.

    Letting those accumulate would fill the reference corpus other patients
    are screened against with the model's own false starts.
    """
    plan = SymptomQuestionPlan()
    plan.start(PrescreeningCategory.HEART, [])
    answered = plan.ask(PrescreeningCategory.HEART, "Where exactly do you feel it?")
    plan.record(answered.id, "middle of my chest")
    plan.ask(PrescreeningCategory.HEART, "Does it spread anywhere?")

    assert plan.asked_texts(PrescreeningCategory.HEART) == ["Where exactly do you feel it?"]


# --------------------------------------------------------------------------
# Classifying the appointment reason
# --------------------------------------------------------------------------


def test_the_brief_is_specific_to_the_reason_being_screened(
    tool_ctx: _StubToolContext,
) -> None:
    """The whole fix: a lung screening is briefed on lungs and nothing else."""
    reply = _start(tool_ctx, "lung")

    assert "breathing" in reply.lower()
    assert "abdomen" not in reply.lower()


@pytest.mark.parametrize(
    ("category", "expected"),
    [
        ("general_checkup", PrescreeningCategory.GENERAL_CHECKUP),
        ("not_sure", PrescreeningCategory.NOT_SURE),
    ],
)
def test_an_unscoped_booking_is_a_real_reason_with_a_real_brief(
    tool_ctx: _StubToolContext,
    live_context: LiveCallContext,
    category: str,
    expected: PrescreeningCategory,
) -> None:
    """The regression this change exists for.

    A routine checkup and a patient who cannot place what is wrong were 23%
    of real bookings and mapped to no category at all, so the agent had
    nothing to load and fell back to whichever seeded set sounded most like
    unexplained pain. Both are now screenable in their own right.
    """
    reply = _start(tool_ctx, category)

    assert live_context.symptoms.categories == [expected]
    assert "Nothing has been recorded" not in reply  # the stub bank always has reference
    assert "stomach" not in reply.lower()


def test_a_reason_phrased_in_the_patients_words_still_resolves(
    tool_ctx: _StubToolContext, live_context: LiveCallContext
) -> None:
    """A voice model mid-call sends labels and synonyms, not always enum values.

    Refusing those is what used to push it into re-guessing until it landed
    on something that happened to be accepted.
    """
    _start(tool_ctx, "Blood pressure")

    assert live_context.symptoms.categories == [PrescreeningCategory.BLOOD_PRESSURE]


def test_an_unrecognizable_reason_is_a_correction_not_an_exception(
    tool_ctx: _StubToolContext, live_context: LiveCallContext
) -> None:
    """A tool that raises here surfaces as a dead turn to a patient mid-sentence."""
    reply = _start(tool_ctx, "qwertyuiop")

    assert "not an appointment reason" in reply
    assert "not_sure" in reply
    assert live_context.symptoms.categories == []


def test_a_third_reason_is_refused(
    tool_ctx: _StubToolContext, live_context: LiveCallContext
) -> None:
    """Two reasons is a patient with two problems; three is a fishing expedition."""
    _start(tool_ctx, "lung")
    _start(tool_ctx, "heart")

    reply = _start(tool_ctx, "stomach")

    assert str(MAX_SYMPTOM_CATEGORIES) in reply
    assert live_context.symptoms.categories == [
        PrescreeningCategory.LUNG,
        PrescreeningCategory.HEART,
    ]


# --------------------------------------------------------------------------
# The tools, as the model actually drives them
# --------------------------------------------------------------------------


async def test_asking_puts_the_spoken_wording_on_screen(
    tool_ctx: _StubToolContext, live_context: LiveCallContext
) -> None:
    """The patient reads the question they were asked, word for word."""
    _start(tool_ctx, "lung")
    _drain(live_context.bus)

    await _ask(tool_ctx, "How far can you walk before you notice it?")

    frames = _drain(live_context.bus)
    assert [frame["type"] for frame in frames] == ["symptom_state"]
    assert frames[0]["current"] == {
        "question_id": "lung-01",
        "text": "How far can you walk before you notice it?",
    }


async def test_recording_an_answer_clears_the_question(
    tool_ctx: _StubToolContext, live_context: LiveCallContext
) -> None:
    """The whole point of the loop: the answer lands and the field empties."""
    _start(tool_ctx, "lung")
    await _ask(tool_ctx, "How long has the breathlessness been there?")
    _drain(live_context.bus)

    await _answer(tool_ctx, "about three weeks")

    frames = _drain(live_context.bus)
    assert frames[-1]["current"] is None
    assert frames[-1]["answers"] == [
        {
            "question_id": "lung-01",
            "question": "How long has the breathlessness been there?",
            "answer": "about three weeks",
            "status": "confirmed",
        }
    ]
    assert live_context.symptoms.current is None


async def test_asking_moves_the_patient_to_the_screen_that_shows_questions(
    tool_ctx: _StubToolContext, live_context: LiveCallContext
) -> None:
    """A question the patient cannot see is a question nobody asked them.

    The model is told to navigate first and usually does; when it does not,
    nothing later in the call would have corrected it -- unlike the other
    screens, which `record_intake_details` catches up.
    """
    live_context.progress.screen = IntakeScreen.PATIENT_CONCERNS
    _start(tool_ctx, "lung")
    _drain(live_context.bus)

    reply = await _ask(tool_ctx, "How long has this been going on?")

    assert live_context.progress.screen is IntakeScreen.SYMPTOM_STORY
    assert [frame["type"] for frame in _drain(live_context.bus)] == ["navigate", "symptom_state"]
    assert "navigate_to_screen" in reply


async def test_asking_before_a_reason_is_established_is_refused(
    tool_ctx: _StubToolContext,
) -> None:
    """Nothing reaches the screen until the agent knows what it is screening."""
    reply = await _ask(tool_ctx, "How long has this been going on?")

    assert "start_prescreening" in reply


async def test_recording_without_a_live_question_is_refused(
    tool_ctx: _StubToolContext,
) -> None:
    """An answer with no question behind it has nothing to attach to."""
    _start(tool_ctx, "lung")

    reply = await _answer(tool_ctx, "three weeks")

    assert "No question is on the patient's screen" in reply


async def test_progress_states_the_required_range(
    tool_ctx: _StubToolContext, live_context: LiveCallContext
) -> None:
    """The agent is told the 7-10 range up front and as it goes, not left to guess."""
    start_reply = _start(tool_ctx, "lung")
    assert str(MIN_SYMPTOM_QUESTIONS) in start_reply
    assert str(MAX_SYMPTOM_QUESTIONS) in start_reply

    reply = ""
    for index in range(MIN_SYMPTOM_QUESTIONS - 1):
        await _ask(tool_ctx, f"Question number {index} about your breathing?")
        reply = await _answer(tool_ctx, f"answer {index}")

    # One short of the minimum: still told to keep going.
    assert "required" in reply or str(MIN_SYMPTOM_QUESTIONS) in reply
    assert len(live_context.symptoms.answered) == MIN_SYMPTOM_QUESTIONS - 1


async def test_a_screening_is_capped_at_the_maximum(
    tool_ctx: _StubToolContext, live_context: LiveCallContext
) -> None:
    """Ten questions is a hard product requirement, not a runaway ceiling."""
    _start(tool_ctx, "lung")

    for index in range(MAX_SYMPTOM_QUESTIONS):
        await _ask(tool_ctx, f"Question number {index} about your breathing?")
        await _answer(tool_ctx, f"answer {index}")

    reply = await _ask(tool_ctx, "One more question about your breathing?")

    assert "maximum" in reply
    assert "Move on to the next screen" in reply
    assert len(live_context.symptoms.answered) == MAX_SYMPTOM_QUESTIONS


async def test_navigating_off_symptom_story_is_refused_below_the_minimum(
    tool_ctx: _StubToolContext, live_context: LiveCallContext
) -> None:
    """The call cannot move to medication until the required range is met."""
    _start(tool_ctx, "lung")

    for index in range(MIN_SYMPTOM_QUESTIONS - 1):
        await _ask(tool_ctx, f"Question number {index} about your breathing?")
        await _answer(tool_ctx, f"answer {index}")

    reply = await navigate_to_screen("medication", tool_context=tool_ctx)  # type: ignore[arg-type]

    assert "cannot move on yet" in reply
    assert live_context.progress.screen is IntakeScreen.SYMPTOM_STORY


async def test_navigating_off_symptom_story_succeeds_at_the_minimum(
    tool_ctx: _StubToolContext, live_context: LiveCallContext
) -> None:
    """Once the range is met, the call is free to move on."""
    _start(tool_ctx, "lung")

    for index in range(MIN_SYMPTOM_QUESTIONS):
        await _ask(tool_ctx, f"Question number {index} about your breathing?")
        await _answer(tool_ctx, f"answer {index}")

    reply = await navigate_to_screen("medication", tool_context=tool_ctx)  # type: ignore[arg-type]

    assert "medication" in reply
    assert live_context.progress.screen is IntakeScreen.MEDICATION


async def test_every_answer_is_persisted_as_it_lands(
    tool_ctx: _StubToolContext, live_context: LiveCallContext
) -> None:
    """A dropped connection must cost at most the question in flight."""
    _start(tool_ctx, "lung")
    await _ask(tool_ctx, "How long has this been going on?")
    await _answer(tool_ctx, "three weeks")

    sessions: _RecordingSessions = live_context.sessions  # type: ignore[assignment]
    categories, answers = sessions.symptom_writes[-1]

    assert categories == [PrescreeningCategory.LUNG]
    assert [answer.answer for answer in answers] == ["three weeks"]


# --------------------------------------------------------------------------
# Guards on a question the model wrote itself
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("", "no question"),
        ("How long has this been going on? And does it wake you at night?", "more than one"),
        ("On a scale of 1 to 10, how bad is the pain?", "numeric scale"),
        ("Where is the pain, on a scale of 1-10 how bad?", "numeric scale"),
    ],
)
async def test_a_malformed_question_never_reaches_the_screen(
    tool_ctx: _StubToolContext, live_context: LiveCallContext, question: str, expected: str
) -> None:
    """Checks the old fixed question list gave for free, now that the model writes them."""
    _start(tool_ctx, "stomach")
    _drain(live_context.bus)

    reply = await _ask(tool_ctx, question)

    assert expected in reply
    assert _drain(live_context.bus) == []
    assert live_context.symptoms.current is None


async def test_a_question_longer_than_the_screen_is_refused(
    tool_ctx: _StubToolContext, live_context: LiveCallContext
) -> None:
    """A paragraph read aloud is unusable on a voice call and unreadable on screen."""
    _start(tool_ctx, "stomach")
    _drain(live_context.bus)

    reply = await _ask(tool_ctx, "x" * (MAX_QUESTION_LENGTH + 1))

    assert "paragraph, not a question" in reply
    assert _drain(live_context.bus) == []


async def test_an_answered_question_cannot_be_asked_again_in_other_words(
    tool_ctx: _StubToolContext, live_context: LiveCallContext
) -> None:
    """Re-asking is the one failure a patient notices instantly.

    The old design caught this by id. A model writing its own questions has
    no id to collide, so the guard matches on what the question asks rather
    than on how it was phrased -- otherwise re-wording is a free pass to
    ask the same thing twice.
    """
    _start(tool_ctx, "lung")
    await _ask(tool_ctx, "How long has this been going on?")
    await _answer(tool_ctx, "three weeks")
    _drain(live_context.bus)

    reply = await _ask(tool_ctx, "Can you tell me how long this has been going on?")

    assert "already answered" in reply
    assert _drain(live_context.bus) == []


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("When did it start?", "Where did it start?"),
        ("Have you had any nausea?", "Have you had any pain?"),
        ("Does it wake you at night?", "Does it wake your partner at night?"),
    ],
)
async def test_the_duplicate_guard_does_not_swallow_a_different_question(
    tool_ctx: _StubToolContext, live_context: LiveCallContext, first: str, second: str
) -> None:
    """The guard ignores grammar, so it must not ignore what a question asks.

    A guard aggressive enough to treat "when did it start" and "where did
    it start" as one question would silently drop half of what a screening
    needs to establish, which is a worse failure than a repeated question.
    """
    _start(tool_ctx, "stomach")
    await _ask(tool_ctx, first)
    await _answer(tool_ctx, "some answer")
    _drain(live_context.bus)

    reply = await _ask(tool_ctx, second)

    assert "already answered" not in reply
    assert live_context.symptoms.current is not None
    assert live_context.symptoms.current.text == second


async def test_a_question_still_awaiting_an_answer_cannot_be_re_asked(
    tool_ctx: _StubToolContext, live_context: LiveCallContext
) -> None:
    _start(tool_ctx, "lung")
    await _ask(tool_ctx, "Does anything set the cough off?")
    _drain(live_context.bus)

    reply = await _ask(tool_ctx, "Does anything set the cough off?")

    assert "record_symptom_answer" in reply
    assert _drain(live_context.bus) == []


# --------------------------------------------------------------------------
# What the call gives back to the bank
# --------------------------------------------------------------------------


async def test_the_call_folds_its_questions_back_in_under_the_reason_it_screened(
    tool_ctx: _StubToolContext, live_context: LiveCallContext, question_bank: _StubQuestionBank
) -> None:
    """The accumulation half: the next patient with this reason starts better off."""
    _start(tool_ctx, "not_sure")
    await _ask(tool_ctx, "When did you first notice it?")
    await _answer(tool_ctx, "maybe a month ago")

    await live_context.flush_question_bank()

    assert question_bank.recorded == [
        (PrescreeningCategory.NOT_SURE, ["When did you first notice it?"])
    ]


async def test_a_call_that_screened_nothing_writes_nothing(
    live_context: LiveCallContext, question_bank: _StubQuestionBank
) -> None:
    """A call that never got past consent has nothing to contribute."""
    await live_context.flush_question_bank()

    assert question_bank.recorded == []


# --------------------------------------------------------------------------
# Consent
# --------------------------------------------------------------------------


async def test_no_screening_tool_works_before_consent(question_bank: _StubQuestionBank) -> None:
    """Screening is the clinical part of the call; consent gates all of it."""
    context = LiveCallContext(
        session_id="sess_8f2c1a",
        bus=UiEventBus(),
        progress=CallProgress(),
        sessions=_RecordingSessions(),  # type: ignore[arg-type]
        question_bank=question_bank,  # type: ignore[arg-type]
        consent_given=False,
    )
    ctx = _StubToolContext(
        session_id=context.session_id,
        question_bank=question_bank,
        live_call=context,
    )

    with pytest.raises(ConsentNotRecordedError):
        _start(ctx, "lung")

    assert "Consent has not been recorded" in await _ask(ctx, "How long has this been going on?")
    assert "Consent has not been recorded" in await _answer(ctx, "three weeks")
    assert _drain(context.bus) == []

"""The call has to behave like someone listening, not someone reading a script.

Each test here pins one way the intake used to stop being a conversation.
They are grouped by the failure they prevent rather than by module, because
every one of them was reported as a single bad moment in a real call and
the fix for it is spread across the prompt, the tools and the state model.

The four reported moments:

- A patient who tore a muscle playing football was asked whether the tear
  "comes and goes".
- A patient who picked "I am not sure" when booking was told "you've booked
  this appointment about I am not sure".
- A patient who said "move next" had that recorded as their answer, which
  satisfied the completeness gate and reached their doctor.
- Nothing anywhere distinguished a confirmed answer from a guess, an
  inference, or a refusal.
"""

from typing import Any

import pytest

from app.agents.bidi.call_state import (
    CallProgress,
    LiveCallContext,
    UiEventBus,
    reject_as_answer,
)
from app.agents.bidi.prompts import build_intake_prompt
from app.agents.bidi.symptom_plan import (
    MAX_QUESTIONS_PUT_ON_SCREEN,
    MIN_SYMPTOM_QUESTIONS,
)
from app.agents.tools.intake_details import record_intake_details
from app.agents.tools.navigation import navigate_to_screen
from app.agents.tools.symptoms import (
    ask_symptom_question,
    record_symptom_answer,
    start_prescreening,
)
from app.core.constants import (
    AnswerStatus,
    BookingVisitType,
    IntakeScreen,
    PrescreeningCategory,
    PresentationType,
    resolve_prescreening_category,
)
from app.models.question_bank import BankQuestion
from app.models.session import Session
from app.models.symptom_intake import SymptomAnswer


class _StubToolContext:
    def __init__(self, **invocation_state: Any) -> None:
        self.invocation_state: dict[str, Any] = dict(invocation_state)


class _StubQuestionBank:
    def reference_questions(
        self, category: PrescreeningCategory, limit: int = 12
    ) -> list[BankQuestion]:
        return []

    async def record_asked(self, category: PrescreeningCategory, texts: list[str]) -> int:
        return len(texts)


class _NullSessions:
    async def set_call_progress(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def set_symptom_progress(self, *args: Any, **kwargs: Any) -> None:
        return None


@pytest.fixture
def context() -> LiveCallContext:
    return LiveCallContext(
        session_id="sess_8f2c1a",
        bus=UiEventBus(),
        progress=CallProgress(),
        sessions=_NullSessions(),  # type: ignore[arg-type]
        consent_given=True,
    )


@pytest.fixture
def tool_ctx(context: LiveCallContext) -> _StubToolContext:
    return _StubToolContext(
        session_id=context.session_id,
        question_bank=_StubQuestionBank(),
        live_call=context,
    )


# ---------------------------------------------------------------------------
# "Does the tear come and go?"
# ---------------------------------------------------------------------------


def test_an_injury_gets_an_injury_brief_not_a_pattern_brief(
    context: LiveCallContext, tool_ctx: _StubToolContext
) -> None:
    """The reported failure, at its source.

    A torn muscle belongs to no organ system on the booking menu, so it
    lands on `not_sure` -- whose brief is written for an unexplained
    symptom and genuinely asks whether the problem is constant or comes in
    episodes. The presentation axis is what makes the same category
    produce the right questions.
    """
    reply = start_prescreening(
        "not_sure",
        tool_context=tool_ctx,  # type: ignore[arg-type]
        presentation="acute_injury",
    )

    assert context.symptoms.presentation is PresentationType.ACUTE_INJURY
    # What an injury actually needs asked.
    assert "what they were doing at the moment it happened" in reply
    assert "carry on, or had to stop" in reply
    # And the explicit prohibition, because a positive brief alone never
    # stopped the model reaching for the pattern questions.
    assert "Do NOT ask whether it comes and goes" in reply


async def test_the_reported_question_is_refused_outright(
    context: LiveCallContext, tool_ctx: _StubToolContext
) -> None:
    """ "Does the tear come and go?" must never reach the patient's screen."""
    start_prescreening(
        "not_sure",
        tool_context=tool_ctx,  # type: ignore[arg-type]
        presentation="acute_injury",
    )

    reply = await ask_symptom_question(
        "Does the tear come and go?",
        tool_context=tool_ctx,  # type: ignore[arg-type]
    )

    assert context.symptoms.current is None
    assert "single injury" in reply
    assert "not listening" in reply


@pytest.mark.parametrize(
    "question",
    [
        "Does the pain come and go?",
        "Is it constant or does it come in episodes?",
        "How often does it happen?",
        "What brings it on?",
        "Is there anything that triggers it?",
    ],
)
async def test_pattern_questions_are_all_refused_for_an_injury(
    question: str, tool_ctx: _StubToolContext
) -> None:
    """Every phrasing of "what is the pattern" assumes there is one."""
    start_prescreening(
        "not_sure",
        tool_context=tool_ctx,  # type: ignore[arg-type]
        presentation="acute_injury",
    )

    reply = await ask_symptom_question(
        question,
        tool_context=tool_ctx,  # type: ignore[arg-type]
    )

    assert "single injury" in reply


async def test_the_same_question_is_fine_for_an_ongoing_condition(
    context: LiveCallContext, tool_ctx: _StubToolContext
) -> None:
    """The guard is contextual, not a banned-words list.

    "Does it come and go?" is exactly the right question for an
    unexplained recurring symptom, and refusing it everywhere would trade
    one scripted failure for another.
    """
    start_prescreening(
        "stomach",
        tool_context=tool_ctx,  # type: ignore[arg-type]
        presentation="ongoing_condition",
    )

    reply = await ask_symptom_question(
        "Does the pain come and go?",
        tool_context=tool_ctx,  # type: ignore[arg-type]
    )

    assert context.symptoms.current is not None
    assert "on the patient's screen now" in reply


def test_an_injury_keeps_its_presentation_when_a_second_reason_opens(
    context: LiveCallContext, tool_ctx: _StubToolContext
) -> None:
    """A second, vaguer reason must not re-characterize the first.

    A patient who tore a muscle and also wants their blood pressure looked
    at is still telling you about an injury.
    """
    start_prescreening(
        "not_sure",
        tool_context=tool_ctx,  # type: ignore[arg-type]
        presentation="acute_injury",
    )
    start_prescreening(
        "blood_pressure",
        tool_context=tool_ctx,  # type: ignore[arg-type]
        presentation="ongoing_condition",
    )

    assert context.symptoms.presentation is PresentationType.ACUTE_INJURY


# ---------------------------------------------------------------------------
# "You've booked this appointment about I am not sure"
# ---------------------------------------------------------------------------


def test_not_sure_is_never_spoken_back_as_the_reason(sample_session: Session) -> None:
    """The booking card's own first-person label reached the spoken template."""
    sample_session.booking_visit_type = BookingVisitType.NOT_SURE
    sample_session.booking_reason = None

    prompt = build_intake_prompt(sample_session)

    assert "Booked appointment reason: I am not sure" not in prompt
    assert "not a reason, and there is nothing to confirm" in prompt


def test_not_sure_offers_the_patient_the_option_of_not_saying(
    sample_session: Session,
) -> None:
    """Someone who could not answer that on a form may not want to now either."""
    sample_session.booking_visit_type = BookingVisitType.NOT_SURE

    prompt = build_intake_prompt(sample_session)
    collapsed = " ".join(prompt.split())

    assert "rather not go into it" in collapsed
    assert "asking you to move on as though they had given you a reason" in collapsed


def test_free_text_outranks_an_empty_not_sure_selection(sample_session: Session) -> None:
    """The structured value says "I could not name it"; the free text names it.

    Preferring the emptier of the two made the patient describe their
    problem from scratch when they had already typed it out.
    """
    sample_session.booking_visit_type = BookingVisitType.NOT_SURE
    sample_session.booking_reason = "My knee gave way playing football"

    prompt = build_intake_prompt(sample_session)

    assert "My knee gave way playing football" in prompt
    assert "open on it" in prompt


def test_a_real_reason_is_given_speakable_wording(sample_session: Session) -> None:
    """Every other visit type needs a phrase that fits inside a sentence."""
    sample_session.booking_visit_type = BookingVisitType.LUNG

    prompt = build_intake_prompt(sample_session)

    assert "'your breathing'" in prompt
    assert "Never read the label above aloud" in prompt


# ---------------------------------------------------------------------------
# "move next" is not an answer
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "deflection",
    ["move next", "next", "skip", "Skip this.", "next question", "hurry up"],
)
def test_a_request_to_move_on_is_not_an_answer(deflection: str) -> None:
    assert reject_as_answer(deflection) is not None


@pytest.mark.parametrize(
    "answer",
    ["nothing", "Nothing.", "anything", "none", "pass", "continue", "carry on", "whatever"],
)
def test_a_plain_word_that_is_a_real_answer_is_never_refused(answer: str) -> None:
    """The backstop must stay tiny, or it becomes the bug it was added to fix.

    "Does anything make it worse?" / "nothing" is the commonest exchange in
    the whole call. An earlier version of the deflection list held
    "nothing", "anything", "pass", "continue", "carry on" and "whatever",
    so the tool refused a perfectly good answer, kept the question live,
    and the agent asked the same thing again -- which is precisely the
    duplicate-question complaint this guard was supposed to prevent.

    A missed deflection is caught one layer up, by the model's own
    `relevance` judgement. A false refusal is a loop the patient cannot
    get out of.
    """
    assert reject_as_answer(answer) is None


@pytest.mark.parametrize(
    "answer",
    [
        "I skip breakfast most days",
        "I had to move on from my last job",
        "nothing helps it",
        "it comes on when I carry on walking uphill",
    ],
)
def test_a_real_answer_containing_those_words_is_still_an_answer(answer: str) -> None:
    """Matched whole, never as a substring -- otherwise real answers vanish."""
    assert reject_as_answer(answer) is None


async def test_move_next_is_refused_rather_than_recorded(
    context: LiveCallContext, tool_ctx: _StubToolContext
) -> None:
    """The reported failure end to end."""
    context.progress.screen = IntakeScreen.PATIENT_CONCERNS

    reply = await record_intake_details(
        "patient-concerns",
        '{"concerns": "move next"}',
        tool_context=tool_ctx,  # type: ignore[arg-type]
    )

    assert context.progress.as_storage() == {}
    assert "asking you to move on" in reply
    # The escape hatch must be named, or the model writes something else
    # into the field to get past the navigation gate.
    assert "undisclosed" in reply


async def test_a_refused_answer_does_not_unblock_the_call(
    context: LiveCallContext, tool_ctx: _StubToolContext
) -> None:
    """The gate counts answers, not keys. A junk value must not advance it."""
    context.progress.screen = IntakeScreen.PATIENT_CONCERNS
    await record_intake_details(
        "patient-concerns",
        '{"concerns": "move next"}',
        tool_context=tool_ctx,  # type: ignore[arg-type]
    )

    reply = await navigate_to_screen(
        "symptom-story",
        tool_context=tool_ctx,  # type: ignore[arg-type]
    )

    assert context.progress.screen is IntakeScreen.PATIENT_CONCERNS
    assert "cannot move on yet" in reply


async def test_declining_is_a_complete_answer_and_does_advance_the_call(
    context: LiveCallContext, tool_ctx: _StubToolContext
) -> None:
    """ "I'd rather not say" is the patient answering, and the call must accept it.

    Blocking on a decline leaves the agent with only one way forward:
    press someone who has already said no.
    """
    context.progress.screen = IntakeScreen.PATIENT_CONCERNS

    await record_intake_details(
        "patient-concerns",
        '{"concerns": "would rather not go into it"}',
        tool_context=tool_ctx,  # type: ignore[arg-type]
        certainty='{"concerns": "undisclosed"}',
    )
    reply = await navigate_to_screen(
        "symptom-story",
        tool_context=tool_ctx,  # type: ignore[arg-type]
    )

    recorded = context.progress.value_at(IntakeScreen.PATIENT_CONCERNS, "concerns")
    assert recorded is not None
    assert recorded.status is AnswerStatus.UNDISCLOSED
    assert context.progress.screen is IntakeScreen.SYMPTOM_STORY
    assert "cannot move on yet" not in reply


async def test_answering_a_gate_with_a_yes_and_detailing_it_advances_the_call(
    context: LiveCallContext, tool_ctx: _StubToolContext
) -> None:
    """The screen sticking a topic behind the conversation, reproduced.

    `SCREEN_REQUIRED_FIELDS` asks for the yes/no gate only, reasoning that a
    "no" completes that half of the screen on its own. It does -- and the
    mirror case was never covered: a patient who *had* a hospital stay and
    said what it was for has answered just as completely, and the model,
    with somewhere real to put that answer, has no reason to record the gate
    as well.

    So the gate stayed empty, `navigate_to_screen` refused every attempt to
    move on over a field nobody had anything left to ask about, and the
    model went on asking the next topic's questions regardless. The patient
    sat reading one screen while being asked about the next -- and only when
    the answer was yes, which is why it looked intermittent.
    """
    context.progress.screen = IntakeScreen.MEDICAL_HISTORY
    await record_intake_details(
        "medical-history",
        '{"conditions": "high blood pressure", "hospital_stays": "gallbladder out: about 2019"}',
        tool_context=tool_ctx,  # type: ignore[arg-type]
    )

    reply = await navigate_to_screen(
        "recent-care",
        tool_context=tool_ctx,  # type: ignore[arg-type]
    )

    assert context.progress.screen is IntakeScreen.RECENT_CARE
    assert "cannot move on yet" not in reply


async def test_a_gate_with_nothing_behind_it_still_blocks(
    context: LiveCallContext, tool_ctx: _StubToolContext
) -> None:
    """The gate is still a gate. Only its own dependents may stand in for it."""
    context.progress.screen = IntakeScreen.MEDICAL_HISTORY
    await record_intake_details(
        "medical-history",
        '{"conditions": "high blood pressure"}',
        tool_context=tool_ctx,  # type: ignore[arg-type]
    )

    reply = await navigate_to_screen(
        "recent-care",
        tool_context=tool_ctx,  # type: ignore[arg-type]
    )

    assert context.progress.screen is IntakeScreen.MEDICAL_HISTORY
    assert "cannot move on yet" in reply
    assert "no_hospital_stays" in reply


async def test_naming_an_allergy_answers_whether_there_are_any(
    context: LiveCallContext, tool_ctx: _StubToolContext
) -> None:
    """The same hole on the screen it was most likely to be hit on."""
    context.progress.screen = IntakeScreen.ALLERGIES
    await record_intake_details(
        "allergies",
        '{"allergies": "penicillin: comes out in a rash"}',
        tool_context=tool_ctx,  # type: ignore[arg-type]
    )

    reply = await navigate_to_screen(
        "medical-history",
        tool_context=tool_ctx,  # type: ignore[arg-type]
    )

    assert context.progress.screen is IntakeScreen.MEDICAL_HISTORY
    assert "cannot move on yet" not in reply


async def test_a_deflection_marked_as_a_decline_is_recorded_as_the_decline(
    context: LiveCallContext, tool_ctx: _StubToolContext
) -> None:
    """The model may hear "skip it" as a refusal -- that judgement is its to make.

    What it may not do is leave the patient's flow-control words standing
    in a clinical field, so the text is replaced with what actually
    happened while the status it reported is kept.
    """
    context.progress.screen = IntakeScreen.FAMILY_SOCIAL_HISTORY

    await record_intake_details(
        "family-social-history",
        '{"alcohol_use": "skip"}',
        tool_context=tool_ctx,  # type: ignore[arg-type]
        certainty='{"alcohol_use": "undisclosed"}',
    )

    recorded = context.progress.value_at(IntakeScreen.FAMILY_SOCIAL_HISTORY, "alcohol_use")
    assert recorded is not None
    assert recorded.text == "Preferred not to say"
    assert recorded.status is AnswerStatus.UNDISCLOSED


async def test_a_deflected_screening_question_stays_on_screen(
    context: LiveCallContext, tool_ctx: _StubToolContext
) -> None:
    """A screening answer is a line in the physician's report and a bank signal."""
    start_prescreening(
        "lung",
        tool_context=tool_ctx,  # type: ignore[arg-type]
        presentation="ongoing_condition",
    )
    await ask_symptom_question(
        "How long has the breathlessness been there?",
        tool_context=tool_ctx,  # type: ignore[arg-type]
    )

    reply = await record_symptom_answer(
        "move next",
        tool_context=tool_ctx,  # type: ignore[arg-type]
    )

    assert context.symptoms.answered == []
    assert context.symptoms.current is not None
    assert "still on their screen" in reply
    assert "undisclosed" in reply


# ---------------------------------------------------------------------------
# Confirmed vs uncertain vs inferred vs undisclosed
# ---------------------------------------------------------------------------


async def test_each_certainty_survives_to_storage(
    context: LiveCallContext, tool_ctx: _StubToolContext
) -> None:
    context.progress.screen = IntakeScreen.FAMILY_SOCIAL_HISTORY

    await record_intake_details(
        "family-social-history",
        '{"occupation": "a teacher", "living_situation": "maybe with my sister",'
        ' "tobacco_use": "never", "alcohol_use": "not saying"}',
        tool_context=tool_ctx,  # type: ignore[arg-type]
        certainty='{"living_situation": "uncertain", "tobacco_use": "inferred",'
        ' "alcohol_use": "undisclosed"}',
    )

    statuses = context.progress.statuses_as_storage()["family-social-history"]
    assert statuses == {
        "occupation": "confirmed",
        "living_situation": "uncertain",
        "tobacco_use": "inferred",
        "alcohol_use": "undisclosed",
    }
    # The words themselves stay exactly as they were, so every existing
    # reader of `collected_details` is unaffected.
    assert context.progress.as_storage()["family-social-history"]["occupation"] == "a teacher"


async def test_an_inference_is_not_treated_as_having_asked(
    context: LiveCallContext, tool_ctx: _StubToolContext
) -> None:
    """The agent working something out is not the same as the patient saying it."""
    context.progress.screen = IntakeScreen.PATIENT_CONCERNS

    await record_intake_details(
        "patient-concerns",
        '{"concerns": "probably their asthma"}',
        tool_context=tool_ctx,  # type: ignore[arg-type]
        certainty='{"concerns": "inferred"}',
    )
    reply = await navigate_to_screen(
        "symptom-story",
        tool_context=tool_ctx,  # type: ignore[arg-type]
    )

    assert context.progress.screen is IntakeScreen.PATIENT_CONCERNS
    assert "cannot move on yet" in reply


# ---------------------------------------------------------------------------
# Keeping hold of the conversation
# ---------------------------------------------------------------------------


async def test_a_contradiction_is_reported_rather_than_silently_overwritten(
    context: LiveCallContext, tool_ctx: _StubToolContext
) -> None:
    """Last-write-wins is right; doing it silently is how the call contradicts itself."""
    context.progress.screen = IntakeScreen.MEDICATION

    await record_intake_details(
        "medication",
        '{"medications": "Metformin twice a day"}',
        tool_context=tool_ctx,  # type: ignore[arg-type]
    )
    reply = await record_intake_details(
        "medication",
        '{"medications": "Metformin once a day"}',
        tool_context=tool_ctx,  # type: ignore[arg-type]
    )

    assert "This replaced what you had" in reply
    assert "Metformin twice a day" in reply
    assert "do not re-ask it" in reply
    assert context.progress.as_storage()["medication"]["medications"] == "Metformin once a day"


def test_the_screening_brief_carries_what_the_patient_already_said(
    context: LiveCallContext, tool_ctx: _StubToolContext
) -> None:
    """The screening tools used to be blind to every earlier screen."""
    context.progress.record(
        IntakeScreen.PATIENT_CONCERNS,
        {"concerns": "I tore something in my thigh playing football"},
    )

    reply = start_prescreening(
        "not_sure",
        tool_context=tool_ctx,  # type: ignore[arg-type]
        presentation="acute_injury",
    )

    assert "Already established -- do not ask any of this again" in reply
    assert "I tore something in my thigh playing football" in reply


async def test_landing_on_a_screen_says_what_is_already_answered_on_it(
    context: LiveCallContext, tool_ctx: _StubToolContext
) -> None:
    """Re-asking was the default failure mode of a long call.

    Going back to a covered topic is exactly when it used to happen: the
    reply described the screen purely from the static tables and never
    mentioned what the patient had already said on it.
    """
    context.progress.screen = IntakeScreen.MEDICAL_HISTORY
    context.progress.record(IntakeScreen.ALLERGIES, {"no_known_allergies": "no"})
    context.progress.record(
        IntakeScreen.ALLERGIES,
        {"allergies": "penicillin: rash"},
        statuses={"allergies": AnswerStatus.UNCERTAIN},
    )

    reply = await navigate_to_screen(
        "allergies",
        tool_context=tool_ctx,  # type: ignore[arg-type]
    )

    assert "Already answered here, so do not ask again" in reply
    assert "penicillin: rash [uncertain]" in reply


async def test_a_patient_who_will_not_engage_is_not_asked_forever(
    context: LiveCallContext, tool_ctx: _StubToolContext
) -> None:
    """Both bounds counted answers, so a deflecting patient had no exit.

    The minimum kept refusing to let the call move on and the maximum was
    never reached, because neither of them could see a question that had
    been asked and not answered.
    """
    start_prescreening(
        "lung",
        tool_context=tool_ctx,  # type: ignore[arg-type]
        presentation="ongoing_condition",
    )

    for index in range(MAX_QUESTIONS_PUT_ON_SCREEN):
        await ask_symptom_question(
            f"Distinct screening question number {index} about breathing?",
            tool_context=tool_ctx,  # type: ignore[arg-type]
        )

    assert len(context.symptoms.asked) == MAX_QUESTIONS_PUT_ON_SCREEN
    reply = await ask_symptom_question(
        "One more entirely different question about your chest?",
        tool_context=tool_ctx,  # type: ignore[arg-type]
    )

    assert len(context.symptoms.asked) == MAX_QUESTIONS_PUT_ON_SCREEN
    assert "stop here" in reply


def test_declines_count_toward_the_screening_minimum(context: LiveCallContext) -> None:
    """A patient who declines has engaged. Not counting it traps the call."""
    plan = context.symptoms
    plan.categories = [PrescreeningCategory.LUNG]
    plan.answered = [
        SymptomAnswer(
            question_id=f"lung-{index:02d}",
            category=PrescreeningCategory.LUNG,
            question=f"Question {index}?",
            answer="Preferred not to say",
            status=AnswerStatus.UNDISCLOSED,
        )
        for index in range(MIN_SYMPTOM_QUESTIONS)
    ]

    assert not plan.below_question_min
    # But they are not coverage, and the tool must not pretend they are.
    assert plan.substantive_count == 0


def test_the_question_bank_never_learns_from_a_declined_question(
    context: LiveCallContext,
) -> None:
    """The bank is ranked by how often a question has been asked.

    Learning from one the patient refused teaches the corpus to keep
    asking it, and makes it more prominent every time it fails.
    """
    plan = context.symptoms
    plan.start(PrescreeningCategory.LUNG, [])
    answered = plan.ask(PrescreeningCategory.LUNG, "How far can you walk?")
    plan.record(answered.id, "about fifty metres")
    declined = plan.ask(PrescreeningCategory.LUNG, "How much do you smoke?")
    plan.record(declined.id, "Preferred not to say", status=AnswerStatus.UNDISCLOSED)

    assert plan.asked_texts(PrescreeningCategory.LUNG) == ["How far can you walk?"]


# ---------------------------------------------------------------------------
# Decisions from context, not keywords
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("supplied", "expected"),
    [
        # A respiratory illness routed to the cardiac brief, because the
        # alias table contained the bare word "chest".
        ("chest infection", None),
        # "other" appears inside "bothered", "mother" and "brother", so a
        # coherent complaint was filed as unplaceable.
        ("other", None),
        ("bothered by a rash", None),
        ("muscle tear", None),
        # Real names for a reason still resolve -- this is a spelling
        # table, and it still has to do that job.
        ("heart", PrescreeningCategory.HEART),
        ("hypertension", PrescreeningCategory.BLOOD_PRESSURE),
        ("Blood pressure", PrescreeningCategory.BLOOD_PRESSURE),
        ("not_sure", PrescreeningCategory.NOT_SURE),
        ("general checkup", PrescreeningCategory.GENERAL_CHECKUP),
    ],
)
def test_the_category_resolver_is_a_spelling_table_not_a_classifier(
    supplied: str, expected: PrescreeningCategory | None
) -> None:
    assert resolve_prescreening_category(supplied) is expected


def test_an_unresolvable_reason_asks_the_model_rather_than_guessing(
    context: LiveCallContext, tool_ctx: _StubToolContext
) -> None:
    """Guessing from a substring is what sent an injury to the cardiac brief."""
    reply = start_prescreening(
        "chest infection",
        tool_context=tool_ctx,  # type: ignore[arg-type]
        presentation="new_problem",
    )

    assert context.symptoms.categories == []
    assert "is not an appointment reason" in reply


async def test_a_later_correction_lands_on_the_question_it_belongs_to(
    context: LiveCallContext, tool_ctx: _StubToolContext
) -> None:
    """ "Actually it was three weeks, not two" -- said while question two is live.

    `record_symptom_answer` could only ever write to whatever was on
    screen, so a correction to an earlier answer overwrote the live
    question instead: one answer destroyed, another falsified, and the
    patient told neither.
    """
    start_prescreening(
        "lung",
        tool_context=tool_ctx,  # type: ignore[arg-type]
        presentation="ongoing_condition",
    )
    await ask_symptom_question(
        "How long has the breathlessness been there?",
        tool_context=tool_ctx,  # type: ignore[arg-type]
    )
    await record_symptom_answer("about two weeks", tool_context=tool_ctx)  # type: ignore[arg-type]
    await ask_symptom_question(
        "Does anything make it worse?",
        tool_context=tool_ctx,  # type: ignore[arg-type]
    )

    reply = await record_symptom_answer(
        "actually it has been three weeks",
        tool_context=tool_ctx,  # type: ignore[arg-type]
        corrects="how long has the breathlessness been there",
    )

    answers = {entry.question: entry.answer for entry in context.symptoms.answered}
    assert answers == {
        "How long has the breathlessness been there?": "actually it has been three weeks"
    }
    # The live question is untouched and still waiting.
    assert context.symptoms.current is not None
    assert context.symptoms.current.text == "Does anything make it worse?"
    assert "Corrected" in reply
    assert "Do not re-ask it" in reply


async def test_a_correction_to_something_never_asked_is_refused(
    context: LiveCallContext, tool_ctx: _StubToolContext
) -> None:
    """A made-up reference must not silently overwrite the live question."""
    start_prescreening(
        "lung",
        tool_context=tool_ctx,  # type: ignore[arg-type]
        presentation="ongoing_condition",
    )
    await ask_symptom_question(
        "How long has the breathlessness been there?",
        tool_context=tool_ctx,  # type: ignore[arg-type]
    )

    reply = await record_symptom_answer(
        "twice a day",
        tool_context=tool_ctx,  # type: ignore[arg-type]
        corrects="how many inhalers do you use",
    )

    assert context.symptoms.answered == []
    assert "nothing to correct" in reply


# ---------------------------------------------------------------------------
# The answer has to actually answer the question
# ---------------------------------------------------------------------------


async def test_an_unrelated_answer_is_not_recorded_and_the_question_stays_live(
    context: LiveCallContext, tool_ctx: _StubToolContext
) -> None:
    """Asked what they ate; told about football.

    The judgement is the model's -- nothing in code can tell those apart.
    What code guarantees is that once the model says the reply does not
    answer the question, nothing is written down and the question is
    still the live one.
    """
    start_prescreening(
        "stomach",
        tool_context=tool_ctx,  # type: ignore[arg-type]
        presentation="new_problem",
    )
    await ask_symptom_question(
        "What did you eat today?",
        tool_context=tool_ctx,  # type: ignore[arg-type]
    )

    reply = await record_symptom_answer(
        "I played football",
        tool_context=tool_ctx,  # type: ignore[arg-type]
        relevance="unrelated",
    )

    assert context.symptoms.answered == []
    assert context.symptoms.current is not None
    assert context.symptoms.current.text == "What did you eat today?"
    # And it must re-ask by speaking, not by putting the question up again.
    assert "Do NOT call ask_symptom_question" in reply
    assert "still the live question" in reply


async def test_a_partial_answer_is_kept_but_the_question_stays_open(
    context: LiveCallContext, tool_ctx: _StubToolContext
) -> None:
    """Half an answer is still the patient's words -- losing it means asking twice."""
    start_prescreening(
        "stomach",
        tool_context=tool_ctx,  # type: ignore[arg-type]
        presentation="new_problem",
    )
    await ask_symptom_question(
        "What did you eat today?",
        tool_context=tool_ctx,  # type: ignore[arg-type]
    )

    await record_symptom_answer(
        "just some toast",
        tool_context=tool_ctx,  # type: ignore[arg-type]
        relevance="partial",
    )

    assert len(context.symptoms.answered) == 1
    # Not a firm answer, and the physician must read it that way.
    assert context.symptoms.answered[0].status is AnswerStatus.UNCERTAIN
    assert context.symptoms.current is not None


async def test_the_rest_of_a_partial_answer_replaces_it_and_closes_the_question(
    context: LiveCallContext, tool_ctx: _StubToolContext
) -> None:
    start_prescreening(
        "stomach",
        tool_context=tool_ctx,  # type: ignore[arg-type]
        presentation="new_problem",
    )
    await ask_symptom_question(
        "What did you eat today?",
        tool_context=tool_ctx,  # type: ignore[arg-type]
    )
    await record_symptom_answer(
        "just some toast",
        tool_context=tool_ctx,  # type: ignore[arg-type]
        relevance="partial",
    )

    await record_symptom_answer(
        "toast at eight, then a sandwich at one",
        tool_context=tool_ctx,  # type: ignore[arg-type]
    )

    assert len(context.symptoms.answered) == 1
    assert context.symptoms.answered[0].answer == "toast at eight, then a sandwich at one"
    assert context.symptoms.answered[0].status is AnswerStatus.CONFIRMED
    assert context.symptoms.current is None


async def test_every_screening_reply_ends_by_naming_the_next_action(
    context: LiveCallContext, tool_ctx: _StubToolContext
) -> None:
    """The duplicate-question fix, pinned.

    The reply after a recorded answer used to end on "follow the thread"
    -- a generation cue with no action behind it -- and the reply before
    it said "write your next question" too. Two cues to write, none to
    put it on screen, so the model wrote the question and simply said it.
    The screen never changed; when it called the tool a moment later, the
    tool said "ask it out loud, word for word", and it said the same
    question a second time.

    A speech model acts on the last thing it read, so the action has to
    be last.
    """
    started = start_prescreening(
        "lung",
        tool_context=tool_ctx,  # type: ignore[arg-type]
        presentation="ongoing_condition",
    )
    await ask_symptom_question(
        "How long has this been going on?",
        tool_context=tool_ctx,  # type: ignore[arg-type]
    )
    recorded = await record_symptom_answer(
        "about three weeks",
        tool_context=tool_ctx,  # type: ignore[arg-type]
    )
    refused = await ask_symptom_question(
        "How long has this been going on?",
        tool_context=tool_ctx,  # type: ignore[arg-type]
    )

    for reply in (started, recorded, refused):
        assert reply.rstrip().endswith("saying it first makes you ask it twice."), reply[-120:]

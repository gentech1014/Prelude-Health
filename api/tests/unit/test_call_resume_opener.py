"""What a reconnecting call is actually *told* to do when it opens.

`_restore_progress` decides where a resumed call picks up, and
`tests/unit/test_call_resume_position.py` pins that. This file pins the
other half, which is where the reported bug lived: the model was handed a
correct resume position in its system prompt and then, as the very last
turn in its history, a user message saying "begin now, following your
instructions from step 1".

A user turn beats a system prompt. So the patient rejoined and was
greeted again, walked back to a consent screen they had already passed,
asked to tick a box they had already ticked, and re-asked what they had
already answered -- with the resume block sitting unread the whole time.

The opener is therefore built from the restored position, and the two
places that decide "is this a resume" are one predicate rather than two
that can disagree.
"""

from datetime import UTC, datetime

import pytest

from app.agents.bidi.call_state import CallProgress, RecordedValue
from app.agents.bidi.prompts import (
    FRESH_CALL_OPENER,
    SCREEN_STEPS,
    build_call_opener,
    build_intake_prompt,
    is_resuming,
)
from app.agents.bidi.symptom_plan import SymptomQuestionPlan
from app.api.ws.intake import _restore_progress
from app.core.constants import (
    AGENT_DRIVEN_SCREENS,
    AnswerStatus,
    IntakeScreen,
    PrescreeningCategory,
)
from app.models.consent import Consent
from app.models.session import Session
from app.models.symptom_intake import SymptomAnswer


def _recorded(text: str) -> RecordedValue:
    return RecordedValue(text=text, status=AnswerStatus.CONFIRMED)


@pytest.fixture
def consented(sample_session: Session) -> Session:
    """A session whose patient has consented and answered something."""
    return sample_session.model_copy(
        update={
            "consent": Consent(given=True, recorded_at=datetime(2026, 9, 1, 12, 5, tzinfo=UTC)),
            "last_screen": IntakeScreen.MEDICATION,
            "collected_details": {"patient-concerns": {"concerns": "Sore knee"}},
        }
    )


def test_a_first_connection_gets_the_original_opener() -> None:
    """Nothing has happened, so step 1 is genuinely what comes next."""
    opener = build_call_opener(CallProgress(screen=IntakeScreen.WELCOME), consent_given=False)

    assert opener == FRESH_CALL_OPENER


def test_no_progress_at_all_still_opens_the_call() -> None:
    """A caller with nothing to restore must not be left with a silent model."""
    assert build_call_opener(None, consent_given=False) == FRESH_CALL_OPENER


def test_a_resumed_call_is_never_told_to_begin_from_step_one() -> None:
    """The whole bug, in one assertion."""
    progress = CallProgress(
        screen=IntakeScreen.MEDICATION,
        details={"patient-concerns": {"concerns": _recorded("Sore knee")}},
    )

    opener = build_call_opener(progress, consent_given=True)

    assert opener != FRESH_CALL_OPENER
    assert "from step 1 " not in opener
    assert SCREEN_STEPS[IntakeScreen.MEDICATION] in opener


def test_a_resumed_opener_names_the_screen_and_its_step() -> None:
    """ "Carry on where you were" is not actionable; a step number is."""
    progress = CallProgress(screen=IntakeScreen.ALLERGIES)

    opener = build_call_opener(progress, consent_given=True)

    assert "allergies" in opener
    assert SCREEN_STEPS[IntakeScreen.ALLERGIES] in opener


def test_recorded_consent_forbids_the_consent_request_in_the_opener() -> None:
    """The reported symptom: consent already given, and asked for again."""
    opener = build_call_opener(CallProgress(screen=IntakeScreen.PATIENT_CONCERNS), True)

    assert "Consent is already recorded" in opener
    assert "do NOT ask them to tick the consent box" in opener


def test_a_reconnect_before_consent_still_greets_and_asks() -> None:
    """Resuming is not a reason to skip a consent that was never given.

    A patient who dropped on the consent screen has not been through
    steps 1 to 5 in any sense that counts -- they never agreed to
    anything -- so the opener has to send the agent back through them.
    """
    progress = CallProgress(
        screen=IntakeScreen.WELCOME,
        details={"confirm-details": {"phone_number": _recorded("+1 555 0100")}},
    )

    opener = build_call_opener(progress, consent_given=False)

    assert "Consent has NOT been recorded yet" in opener
    assert "steps 1 to 5" in opener


def test_consent_alone_makes_a_connection_a_resume() -> None:
    """Consent means the greeting happened, whatever else is missing.

    A patient can consent and drop before a single value is recorded.
    Testing only the screen marker and the collected answers read that as
    a fresh call and greeted them again.
    """
    assert is_resuming(CallProgress(screen=IntakeScreen.WELCOME), consent_given=True)


def test_answered_screening_questions_make_a_connection_a_resume() -> None:
    """The symptom screen has no fields, so answers are its only evidence."""
    plan = SymptomQuestionPlan(
        answered=[
            SymptomAnswer(
                question_id="lung-01",
                category=PrescreeningCategory.LUNG,
                question="How long has the cough been going on?",
                answer="About a week",
            )
        ]
    )

    assert is_resuming(CallProgress(screen=IntakeScreen.WELCOME), False, plan)


def test_every_agent_screen_maps_to_a_step() -> None:
    """A screen with no step leaves a resumed call with nowhere to go."""
    assert set(SCREEN_STEPS) >= set(AGENT_DRIVEN_SCREENS)


def test_the_prompt_and_the_opener_agree_that_this_is_a_resume(
    consented: Session,
) -> None:
    """The two disagreeing is what produced the worst version of the bug."""
    progress = _restore_progress(consented)
    opener = build_call_opener(progress, consent_given=True)
    prompt = build_intake_prompt(consented, progress)

    assert opener != FRESH_CALL_OPENER
    assert "# This call was interrupted and has now resumed" in prompt


def test_consent_without_collected_answers_still_gets_a_resume_block(
    sample_session: Session,
) -> None:
    """Consent alone used to fall through to a prompt with no resume block.

    The prompt then said "consent is recorded, pick up from step 6" in one
    section while nothing told the model what had been covered -- and the
    opener told it to start from step 1 regardless.
    """
    session = sample_session.model_copy(
        update={
            "consent": Consent(given=True, recorded_at=datetime(2026, 9, 1, tzinfo=UTC)),
            "last_screen": IntakeScreen.PATIENT_CONCERNS,
        }
    )

    prompt = build_intake_prompt(session, _restore_progress(session))

    assert "# This call was interrupted and has now resumed" in prompt


def test_the_consent_context_line_forbids_the_whole_pre_consent_half(
    consented: Session,
) -> None:
    """ "Do not repeat the greeting" was too narrow: the nudge came back anyway."""
    prompt = build_intake_prompt(consented, _restore_progress(consented))

    assert "steps 1 to 5 are DONE" in prompt
    assert "never ask them to check their details or tick the" in prompt


def test_a_consented_call_is_never_pointed_at_a_pre_consent_step() -> None:
    """ "Do not greet them" and "carry on from step 1" cannot both be true.

    Steps 1 to 5 are the pre-consent half of the call. Naming one to a
    call that has already consented is the contradiction the patient
    hears as the call starting over, so the opener floors it at step 6
    even if it is handed a pre-consent screen.
    """
    opener = build_call_opener(CallProgress(screen=IntakeScreen.WELCOME), consent_given=True)

    assert SCREEN_STEPS[IntakeScreen.PATIENT_CONCERNS] in opener
    assert "from step 1 " not in opener
    assert "from step 4 " not in opener

"""How the booked appointment reason reaches the live agent's prompt.

The call used to open cold -- "what's bringing you in?" -- with the booked
reason demoted in the prompt to a hint that was "often vague or wrong", and
the structured visit type discarded at booking before the call ever saw it.
Between that and a five-value clinical vocabulary with nothing for a
routine checkup, the agent had to invent a category and reliably picked
whichever seeded set sounded like unexplained pain.

These pin the replacement: the reason is stated as a fact the agent opens
by confirming, the patient is still free to say it is something else, and a
booking with no reason at all is told so explicitly rather than left as a
missing line for the model to fill in confidently.
"""

from app.agents.bidi.call_state import CallProgress
from app.agents.bidi.prompts import build_intake_prompt
from app.agents.bidi.symptom_plan import SymptomQuestionPlan
from app.core.constants import BookingVisitType, IntakeScreen, PrescreeningCategory
from app.models.session import Session
from app.models.symptom_intake import SymptomAnswer


def test_the_booked_reason_is_stated_for_the_agent_to_confirm(
    sample_session: Session,
) -> None:
    """Step 6 opens on it, so it has to arrive as a fact rather than a hint."""
    sample_session.booking_visit_type = BookingVisitType.LUNG

    prompt = build_intake_prompt(sample_session)

    assert "Booked appointment reason: Lung and breathing" in prompt
    assert "Open by confirming it" in prompt
    # The demotion that used to sit here is what step 6 now contradicts.
    assert "often vague or wrong" not in prompt


def test_an_unscoped_booking_is_still_a_reason_to_confirm(
    sample_session: Session,
) -> None:
    """A routine checkup is a reason, not the absence of one."""
    sample_session.booking_visit_type = BookingVisitType.GENERAL_CHECKUP
    sample_session.booking_reason = None

    prompt = build_intake_prompt(sample_session)

    assert "Booked appointment reason: General checkup" in prompt


def test_free_text_stays_colour_rather_than_the_reason(sample_session: Session) -> None:
    """What they typed is context; what they selected is what gets screened."""
    sample_session.booking_visit_type = BookingVisitType.LUNG
    sample_session.booking_reason = "Short of breath on stairs"

    prompt = build_intake_prompt(sample_session)

    assert "They also wrote, in their own words: Short of breath on stairs" in prompt


def test_a_booking_with_no_reason_says_so_rather_than_leaving_a_gap(
    sample_session: Session,
) -> None:
    """An unstated absence is what the model fills in confidently."""
    sample_session.booking_visit_type = None
    sample_session.booking_reason = None

    prompt = build_intake_prompt(sample_session)

    assert "nothing to confirm" in prompt
    assert "Booked appointment reason:" not in prompt


def test_the_patient_can_still_say_it_is_something_else(sample_session: Session) -> None:
    """Confirming the booking must not become insisting on it."""
    sample_session.booking_visit_type = BookingVisitType.DIABETES

    prompt = build_intake_prompt(sample_session)

    assert "is there something else" in prompt
    assert "Do not go back to the booking" in prompt


def test_the_prompt_carries_no_screening_questions(sample_session: Session) -> None:
    """The questions are written mid-call, against a brief that arrives with the tool.

    Shipping all seven briefs here would make the model re-decide which one
    it is working from on every turn -- and shipping the questions
    themselves is what made every call sound the same.
    """
    sample_session.booking_visit_type = BookingVisitType.HEART

    prompt = build_intake_prompt(sample_session)

    assert "start_prescreening" in prompt
    assert "numbness, tingling or burning in the feet" not in prompt  # a diabetes brief line


def test_a_resumed_call_does_not_re_confirm_a_reason_it_already_settled(
    sample_session: Session,
) -> None:
    """Asking again what the call is about is how a reconnect feels like a restart."""
    sample_session.booking_visit_type = BookingVisitType.LUNG
    progress = CallProgress(screen=IntakeScreen.SYMPTOM_STORY)
    symptoms = SymptomQuestionPlan()
    symptoms.start(PrescreeningCategory.LUNG, [])
    symptoms.answered.append(
        SymptomAnswer(
            question_id="lung-01",
            category=PrescreeningCategory.LUNG,
            question="How long has this been going on?",
            answer="about three weeks",
        )
    )

    prompt = build_intake_prompt(sample_session, progress, symptoms)

    assert "already established what you were screening: Lung and breathing" in prompt
    assert "do not ask what is bringing them in" in prompt
    assert "How long has this been going on? -> about three weeks" in prompt


def test_the_details_screen_does_not_greet_the_patient_again(
    sample_session: Session,
) -> None:
    """They were greeted seconds ago, on the previous screen.

    Observed live twice: the assistant opened `confirm-details` with "thank
    you for joining the call" and then with "thank you for confirming
    that" -- before anything had been confirmed. A fresh greeting here is
    the first thing a patient notices, and it makes the call sound like it
    restarted.
    """
    prompt = build_intake_prompt(sample_session)

    assert "Do not greet" in prompt
    assert "do not thank them for joining" in prompt


def test_the_consent_control_is_the_one_thing_named_out_loud(
    sample_session: Session,
) -> None:
    """Consent is an action only the patient can take.

    Everywhere else the screen just keeps up with the conversation and is
    never mentioned. Here, not telling them what to do strands the call on
    a screen they are waiting to be told about.

    The reschedule list is the second such control, and the only other one:
    the assistant no longer reads the open times aloud, so pointing at them
    is the whole of how the patient learns there is a choice to make.
    """
    prompt = build_intake_prompt(sample_session)

    # Whitespace-normalized: the conduct rules are one long backslash-continued
    # string, so every wrapped line renders with the next line's indentation
    # still in it.
    collapsed = " ".join(prompt.split())
    assert "tick the consent box and press Continue" in collapsed
    assert "There are exactly TWO exceptions" in collapsed
    assert "the consent control on `confirm-details` (step 4)" in collapsed
    assert "`appointment-reschedule` (step 16)" in collapsed


def test_the_open_times_are_never_read_out_loud(sample_session: Session) -> None:
    """Reading clock times at someone is the thing that made this unusable.

    The patient can see every open time; a spoken list of them is hard to
    follow and impossible to hold on to. The model still receives the list
    -- that is how it resolves "the Monday one" to a slot id -- it simply
    must not recite it.
    """
    collapsed = " ".join(build_intake_prompt(sample_session).split())

    assert "Do NOT read the times out loud" in collapsed
    assert "Never read a time or an id aloud here" in collapsed
    assert "Say two or three of those times out loud" not in collapsed


def test_details_already_on_screen_are_not_read_aloud(sample_session: Session) -> None:
    """The patient is looking at all three; reading them back wastes their turn."""
    prompt = build_intake_prompt(sample_session)

    assert "Do not read their name, date of birth or phone number aloud" in prompt

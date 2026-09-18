"""Unit tests for where a reconnecting call picks the patient up.

`_restore_progress` is what decides whether a patient hears the
introduction on the `welcome` screen or over the top of the consent
screen, so the line between "resume" and "start over" is pinned here.
"""

from datetime import UTC, datetime

import pytest

from app.api.ws.intake import _restore_progress
from app.core.constants import AnswerStatus, IntakeScreen, PrescreeningCategory
from app.models.consent import Consent
from app.models.session import Session
from app.models.symptom_intake import SymptomAnswer


@pytest.fixture
def stored_session(sample_session: Session) -> Session:
    """A session that has been through a call which left a screen marker."""
    return sample_session.model_copy(update={"last_screen": IntakeScreen.CONFIRM_DETAILS})


def test_a_first_connection_starts_on_welcome(sample_session: Session) -> None:
    """Nothing has happened yet, so the greeting has its own screen to happen on."""
    assert _restore_progress(sample_session).screen is IntakeScreen.WELCOME


def test_a_marker_with_nothing_collected_starts_over_on_welcome(
    stored_session: Session,
) -> None:
    """Reaching the consent screen once is not progress: it was never given.

    The agent greets again on a call like this, and resuming onto
    `confirm-details` played that introduction over the consent screen and
    skipped `welcome` altogether.
    """
    assert _restore_progress(stored_session).screen is IntakeScreen.WELCOME


def test_recorded_consent_resumes_where_the_call_left_off(stored_session: Session) -> None:
    """Consent on record means the greeting is done and must not be repeated."""
    consented = stored_session.model_copy(
        update={
            "consent": Consent(given=True, recorded_at=datetime(2026, 9, 1, 12, 5, tzinfo=UTC)),
            "last_screen": IntakeScreen.MEDICATION,
        }
    )

    assert _restore_progress(consented).screen is IntakeScreen.MEDICATION


def test_a_refused_consent_does_not_count_as_progress(stored_session: Session) -> None:
    """A patient who declined has given the call nothing to resume from."""
    declined = stored_session.model_copy(
        update={
            "consent": Consent(given=False, recorded_at=datetime(2026, 9, 1, 12, 5, tzinfo=UTC))
        }
    )

    assert _restore_progress(declined).screen is IntakeScreen.WELCOME


def test_collected_answers_resume_and_carry_their_screens(stored_session: Session) -> None:
    """With consent on record, the call picks up where it stopped, answers intact."""
    in_progress = stored_session.model_copy(
        update={
            "consent": Consent(given=True, recorded_at=datetime(2026, 9, 1, 12, 5, tzinfo=UTC)),
            "last_screen": IntakeScreen.ALLERGIES,
            "collected_details": {"patient-concerns": {"concerns": "Sore knee"}},
        }
    )

    progress = _restore_progress(in_progress)

    assert progress.screen is IntakeScreen.ALLERGIES
    assert progress.as_storage() == {"patient-concerns": {"concerns": "Sore knee"}}
    assert IntakeScreen.PATIENT_CONCERNS in progress.visited


def test_answers_without_consent_do_not_skip_the_greeting(stored_session: Session) -> None:
    """The bug this rule exists for: a stray answer stole the welcome screen.

    A call abandoned on the consent screen could leave a typed phone number
    and a `confirm-details` marker behind without consent ever being given.
    Counting that as progress meant every later open resumed onto the
    consent screen while the agent, starting from step 1, greeted over it --
    so the patient never saw `welcome` at all.

    The answers are still restored, so nothing already given is re-asked;
    only the screen goes back to where the greeting belongs.
    """
    abandoned = stored_session.model_copy(
        update={"collected_details": {"confirm-details": {"phone_number": "+1 555 0100"}}}
    )

    progress = _restore_progress(abandoned)

    assert progress.screen is IntakeScreen.WELCOME
    assert progress.as_storage() == {"confirm-details": {"phone_number": "+1 555 0100"}}


def test_a_resumed_call_remembers_how_well_it_knew_each_answer(
    stored_session: Session,
) -> None:
    """Certainty has to survive the reconnect, or the resumed call re-asks.

    A field the patient declined reads as an empty box to anything that
    only sees the words, so without this the agent comes back and asks
    someone who has already said no.
    """
    resumed = stored_session.model_copy(
        update={
            "consent": Consent(given=True, recorded_at=datetime(2026, 3, 4, tzinfo=UTC)),
            "last_screen": IntakeScreen.FAMILY_SOCIAL_HISTORY,
            "collected_details": {"family-social-history": {"alcohol_use": "Preferred not to say"}},
            "collected_statuses": {"family-social-history": {"alcohol_use": "undisclosed"}},
        }
    )

    progress = _restore_progress(resumed)

    value = progress.value_at(IntakeScreen.FAMILY_SOCIAL_HISTORY, "alcohol_use")
    assert value is not None
    assert value.status is AnswerStatus.UNDISCLOSED
    # And it still counts as answered, so the call is not blocked on it.
    assert "alcohol_use" in progress.answered_fields(IntakeScreen.FAMILY_SOCIAL_HISTORY)


def test_a_value_stored_before_certainty_existed_reads_as_confirmed(
    stored_session: Session,
) -> None:
    """Which is exactly how it was treated when it was written."""
    resumed = stored_session.model_copy(
        update={
            "consent": Consent(given=True, recorded_at=datetime(2026, 3, 4, tzinfo=UTC)),
            "last_screen": IntakeScreen.MEDICATION,
            "collected_details": {"medication": {"medications": "Metformin"}},
            "collected_statuses": {},
        }
    )

    value = _restore_progress(resumed).value_at(IntakeScreen.MEDICATION, "medications")

    assert value is not None
    assert value.status is AnswerStatus.CONFIRMED


def test_consent_with_a_stale_consent_screen_marker_resumes_past_it(
    stored_session: Session,
) -> None:
    """A patient does not come back to a box they have already ticked.

    Consent is what leaves `confirm-details`, and the app performs that
    move itself. A session holding consent *and* a `confirm-details`
    marker is one whose socket died between the two, so the marker is
    stale rather than current -- restoring it put the patient back in
    front of the consent card with the agent asking them to tick it.
    """
    consented = stored_session.model_copy(
        update={"consent": Consent(given=True, recorded_at=datetime(2026, 9, 1, tzinfo=UTC))}
    )

    assert _restore_progress(consented).screen is IntakeScreen.PATIENT_CONCERNS


def test_a_later_screen_is_never_wound_back_to_patient_concerns(
    stored_session: Session,
) -> None:
    """The rule above applies only to a marker the consent screen left."""
    consented = stored_session.model_copy(
        update={
            "consent": Consent(given=True, recorded_at=datetime(2026, 9, 1, tzinfo=UTC)),
            "last_screen": IntakeScreen.RECENT_CARE,
        }
    )

    assert _restore_progress(consented).screen is IntakeScreen.RECENT_CARE


def test_a_marker_wound_back_to_welcome_recovers_from_what_was_collected(
    stored_session: Session,
) -> None:
    """The damage the restart bug left behind in sessions already stored.

    A resumed call was told to begin from step 1, navigated back to
    `welcome`, and that move persisted as the marker -- so a patient who
    dropped on `symptom-story` came back to `welcome`, and the reconnect
    after that found no progress at all. The marker is fixed going
    forward; a session already carrying a wound-back one has to be
    recoverable from what it actually collected.
    """
    wound_back = stored_session.model_copy(
        update={
            "consent": Consent(given=True, recorded_at=datetime(2026, 9, 1, tzinfo=UTC)),
            "last_screen": IntakeScreen.WELCOME,
            "collected_details": {
                "patient-concerns": {"concerns": "Sore knee"},
                "medication": {"medications": "Ibuprofen"},
            },
        }
    )

    assert _restore_progress(wound_back).screen is IntakeScreen.MEDICATION


def test_answered_screening_questions_count_as_a_screen_reached(
    stored_session: Session,
) -> None:
    """`symptom-story` has no fields, so its answers are its only evidence."""
    wound_back = stored_session.model_copy(
        update={
            "consent": Consent(given=True, recorded_at=datetime(2026, 9, 1, tzinfo=UTC)),
            "last_screen": IntakeScreen.WELCOME,
            "collected_details": {"patient-concerns": {"concerns": "Sore knee"}},
            "symptom_answers": [
                SymptomAnswer(
                    question_id="lung-01",
                    category=PrescreeningCategory.LUNG,
                    question="How long has the cough been going on?",
                    answer="About a week",
                )
            ],
        }
    )

    assert _restore_progress(wound_back).screen is IntakeScreen.SYMPTOM_STORY


def test_recovery_never_lands_ahead_of_what_was_covered(
    stored_session: Session,
) -> None:
    """Landing behind costs a repeated screen; landing ahead skips a topic.

    Consent alone proves the call reached `patient-concerns` and nothing
    further, so that is the floor and also the answer when there is no
    other evidence at all.
    """
    bare = stored_session.model_copy(
        update={
            "consent": Consent(given=True, recorded_at=datetime(2026, 9, 1, tzinfo=UTC)),
            "last_screen": IntakeScreen.WELCOME,
        }
    )

    assert _restore_progress(bare).screen is IntakeScreen.PATIENT_CONCERNS


def test_the_appointment_read_back_cannot_decide_where_a_call_resumes(
    stored_session: Session,
) -> None:
    """It is a real screen but not a step the intake walks through.

    It has no position in the flow, so a value recorded against it has no
    position either -- and letting it in would raise a KeyError rather
    than resume anyone.
    """
    odd = stored_session.model_copy(
        update={
            "consent": Consent(given=True, recorded_at=datetime(2026, 9, 1, tzinfo=UTC)),
            "last_screen": IntakeScreen.WELCOME,
            "collected_details": {"appointment-schedule": {"note": "moved"}},
        }
    )

    assert _restore_progress(odd).screen is IntakeScreen.PATIENT_CONCERNS

"""What the summarization agent is actually shown.

The report the physician reads is only as complete as this prompt. Two
failures it has already produced, both pinned here: a call whose every
question and answer was in the transcript came back with an empty
`findings` list, because nothing told the model to build one; and an
answer the transcript had dropped was absent from the report even though
the agent had confirmed it on the patient's screen, because only the
transcript was ever passed in.
"""

from app.agents.summarizer.prompts import build_summary_prompt

_TRANSCRIPT = "Assistant: Any allergies?\nPatient: Peanuts, I come out in a rash."


def test_the_detailed_question_and_answer_record_is_demanded() -> None:
    """An empty `findings` list drops the whole detail section from the PDF."""
    prompt = build_summary_prompt(_TRANSCRIPT)

    assert "`findings` is the detailed question-and-answer record" in prompt
    assert "never optional" in prompt


def test_negative_answers_are_kept_rather_than_dropped() -> None:
    """ "No medications" is information; a missing row is not."""
    prompt = build_summary_prompt(_TRANSCRIPT)

    assert "Keep the negative answers" in prompt


def test_recorded_values_are_appended_with_their_human_labels() -> None:
    """The model reads a label, never a field key it could mistake for a term."""
    prompt = build_summary_prompt(
        _TRANSCRIPT, {"allergies": {"allergies": "peanuts, rashes all over my body"}}
    )

    assert "--- Recorded during the call ---" in prompt
    assert "peanuts, rashes all over my body" in prompt
    assert "- allergies:" not in prompt


def test_recorded_values_follow_the_order_of_the_call() -> None:
    """Out of order they read as a list of facts, not as a conversation."""
    prompt = build_summary_prompt(
        _TRANSCRIPT,
        {
            "family-social-history": {"occupation": "software engineer"},
            "patient-concerns": {"concerns": "routine checkup"},
        },
    )

    assert prompt.index("routine checkup") < prompt.index("software engineer")


def test_the_transcript_outranks_the_recorded_values() -> None:
    """Only the transcript holds a correction the patient made later."""
    prompt = build_summary_prompt(_TRANSCRIPT, {"medication": {"medications": "none"}})

    assert "The transcript still leads" in prompt


def test_no_recorded_values_means_no_empty_section() -> None:
    """An empty heading invites the model to explain its absence."""
    prompt = build_summary_prompt(_TRANSCRIPT, {"medication": {}})

    assert "--- Recorded during the call ---" not in prompt


def test_the_category_vocabulary_is_the_one_the_patient_booked_from() -> None:
    """A routine checkup is an answer here, not an empty field.

    It used to be neither: the summarizer was offered five clinical areas
    and told to leave the field null for anything else, which is the same
    gap that left the live call with no reason to screen.
    """
    prompt = build_summary_prompt(_TRANSCRIPT)

    assert "general_checkup" in prompt
    assert "not_sure" in prompt
    assert "Never pick a condition label" in prompt


def test_recorded_values_cannot_become_findings_on_their_own() -> None:
    """They are the assistant's paraphrase, and it has been seen inventing them.

    A live call produced a recorded `location: chest` and `frequency:
    persistent` from a patient who had said neither. Those may appear on
    the patient's own screen, where they can correct them; they must not
    reach the physician as something the patient reported.
    """
    prompt = build_summary_prompt(_TRANSCRIPT, {"symptom-story": {"location": "chest"}})

    assert "never turn one" in prompt
    assert "leave it out of the report entirely" in prompt

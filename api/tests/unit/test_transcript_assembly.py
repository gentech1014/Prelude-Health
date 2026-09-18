"""Unit tests for `TranscriptAssembler`.

Nova Sonic sends every utterance twice -- once speculatively, as soon as
the model has planned it, then again once it has actually been spoken --
and the two consumers want different halves of that. The stored
transcript wants only what was said; the caption wants the words while
they are being said. Both contracts are pinned here.
"""

from typing import Any

from app.agents.bidi.transcript_assembly import TranscriptAssembler


def _text(role: str, text: str, *, is_final: bool = True) -> dict[str, Any]:
    """One `bidi_transcript_stream` event, shaped the way Nova Sonic sends it.

    `current_transcript` deliberately repeats the chunk rather than
    accumulating: that is what the provider actually sets, and trusting it
    as a running total is the bug this module exists to fix.
    """
    return {
        "type": "bidi_transcript_stream",
        "role": role,
        "text": text,
        "is_final": is_final,
        "current_transcript": text,
    }


def test_speculative_text_never_reaches_the_stored_transcript() -> None:
    """Text the model planned and then abandoned was never said to anyone."""
    assembler = TranscriptAssembler()

    settled = assembler.feed(
        _text("assistant", "Hello Prasanth, this is Dr Samuel's office.", is_final=False)
    )

    assert settled == []


def test_final_chunks_accumulate_into_one_growing_line() -> None:
    """A caption is the turn so far, so each chunk extends it rather than replacing it."""
    assembler = TranscriptAssembler()

    first = assembler.feed(_text("assistant", "Hello Prasanth."))
    second = assembler.feed(_text("assistant", "This is Dr Samuel's office."))

    assert [turn.text for turn in first] == ["Hello Prasanth."]
    assert [turn.text for turn in second] == ["Hello Prasanth. This is Dr Samuel's office."]
    assert all(not turn.is_complete for turn in first + second)


def test_a_completed_response_settles_the_turn_once() -> None:
    """The stored transcript wants one entry per utterance, not one per chunk."""
    assembler = TranscriptAssembler()
    assembler.feed(_text("assistant", "Hello Prasanth."))
    assembler.feed(_text("assistant", "This is a short call."))

    settled = assembler.feed({"type": "bidi_response_complete"})

    assert len(settled) == 1
    assert settled[0].is_complete
    assert settled[0].text == "Hello Prasanth. This is a short call."


def test_a_change_of_speaker_settles_the_previous_turn() -> None:
    """Nothing marks the end of the patient's turn except the agent starting."""
    assembler = TranscriptAssembler()
    assembler.feed(_text("user", "I get out of breath on stairs."))

    settled = assembler.feed(_text("assistant", "Thank you."))

    assert [(turn.role, turn.text, turn.is_complete) for turn in settled] == [
        ("user", "I get out of breath on stairs.", True),
        ("assistant", "Thank you.", False),
    ]


def test_an_interruption_settles_only_what_was_actually_said() -> None:
    """The model abandoned the rest, so the turn is what the patient heard."""
    assembler = TranscriptAssembler()
    assembler.feed(_text("assistant", "Next I wanted to ask about"))

    settled = assembler.feed({"type": "bidi_interruption", "reason": "user_speech"})

    assert [(turn.text, turn.is_complete) for turn in settled] == [
        ("Next I wanted to ask about", True)
    ]


def test_a_turn_is_settled_only_once() -> None:
    """A second boundary must not write the same utterance to the transcript twice."""
    assembler = TranscriptAssembler()
    assembler.feed(_text("assistant", "Goodbye."))
    assembler.feed({"type": "bidi_response_complete"})

    assert assembler.feed({"type": "bidi_response_complete"}) == []


def test_unrelated_events_and_empty_text_are_ignored() -> None:
    """Audio, tool and usage events share the channel and must pass through."""
    assembler = TranscriptAssembler()

    assert assembler.feed({"type": "bidi_audio_stream", "audio": "..."}) == []
    assert assembler.feed({"type": "tool_use_stream", "current_tool_use": {}}) == []
    assert assembler.feed(_text("assistant", "   ")) == []
    assert assembler.feed(_text("system", "ignored")) == []
    assert assembler.feed("not an event") == []


# --------------------------------------------------------------------------
# Captions, which need the words while they are being spoken
# --------------------------------------------------------------------------


def test_a_caption_shows_planned_text_without_waiting_to_be_spoken() -> None:
    """Nova's spoken-stage text lands only once the audio is done.

    Holding the caption for it meant the line appeared in one lump after
    the sentence had finished, which is no use to someone reading it.
    """
    assembler = TranscriptAssembler(include_speculative=True)

    settled = assembler.feed(
        _text("assistant", "Hello Prasanth, this is Dr Samuel's office.", is_final=False)
    )

    assert [turn.text for turn in settled] == ["Hello Prasanth, this is Dr Samuel's office."]
    assert not settled[0].is_complete


def test_a_caption_never_shrinks_when_the_spoken_text_starts_arriving() -> None:
    """The two stages carry the same words, so the fuller one stays on screen."""
    assembler = TranscriptAssembler(include_speculative=True)
    assembler.feed(
        _text("assistant", "Hello Prasanth, this is Dr Samuel's office.", is_final=False)
    )

    settled = assembler.feed(_text("assistant", "Hello Prasanth,"))

    assert [turn.text for turn in settled] == ["Hello Prasanth, this is Dr Samuel's office."]


def test_two_utterances_in_one_completion_are_two_captions() -> None:
    """A tool call between them does not end Nova's completion.

    Observed live: the greeting and the consent request ran together into
    one caption, because the only turn boundary was the completion ending.
    """
    assembler = TranscriptAssembler(include_speculative=True)
    assembler.feed(_text("assistant", "I still need your consent.", is_final=False))
    assembler.feed(_text("assistant", "I still need your consent."))

    settled = assembler.feed(_text("assistant", "What brings you in today?", is_final=False))

    assert [(turn.text, turn.is_complete) for turn in settled] == [
        ("I still need your consent.", True),
        ("What brings you in today?", False),
    ]

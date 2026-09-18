"""Unit tests for `MongoTranscriptWriter`.

Three behaviours decide whether the stored transcript is usable or
garbage: ignoring non-transcript events, never storing a turn that is
still being spoken, and storing one whole utterance rather than each
chunk of it. A fourth decides whether a database problem can end a
patient's call.
"""

from typing import Any

import pytest

from app.agents.bidi.outputs import MongoTranscriptWriter
from app.models.transcript import ConversationTurn


class _RecordingRepository:
    """Captures appended turns instead of writing to a database."""

    def __init__(self) -> None:
        self.turns: list[ConversationTurn] = []

    async def append_turn(self, session_id: str, turn: ConversationTurn) -> None:
        self.turns.append(turn)


class _FailingRepository:
    """Raises on every write, standing in for a database outage."""

    async def append_turn(self, session_id: str, turn: ConversationTurn) -> None:
        raise ConnectionError("mongo is down")


def _transcript_event(**overrides: Any) -> dict[str, Any]:
    return {
        "type": "bidi_transcript_stream",
        "role": "user",
        "text": "I get out of breath on stairs",
        "is_final": True,
        "current_transcript": "I get out of breath on stairs",
        **overrides,
    }


def _writer(repo: Any) -> MongoTranscriptWriter:
    return MongoTranscriptWriter(repo, "sess_1")


async def test_a_completed_turn_is_stored() -> None:
    """The happy path: an utterance the speaker has finished reaches the repository."""
    repo = _RecordingRepository()
    writer = _writer(repo)

    await writer(_transcript_event())
    await writer({"type": "bidi_response_complete"})

    assert len(repo.turns) == 1
    assert repo.turns[0].role == "user"
    assert repo.turns[0].text == "I get out of breath on stairs"


async def test_a_turn_still_being_spoken_is_not_stored() -> None:
    """Writing mid-turn is what chopped every sentence into several rows."""
    repo = _RecordingRepository()
    writer = _writer(repo)

    await writer(_transcript_event())

    assert repo.turns == []


async def test_speculative_previews_are_never_stored() -> None:
    """They are the model's plan, not speech, and arrive before any of it."""
    repo = _RecordingRepository()
    writer = _writer(repo)

    await writer(_transcript_event(is_final=False))
    await writer({"type": "bidi_response_complete"})

    assert repo.turns == []


async def test_chunks_of_one_utterance_are_stored_as_a_single_turn() -> None:
    """The summarizer reads these rows as the conversation, one row per utterance."""
    repo = _RecordingRepository()
    writer = _writer(repo)

    await writer(_transcript_event(text="I get out of breath"))
    await writer(_transcript_event(text="on stairs."))
    await writer({"type": "bidi_response_complete"})

    assert len(repo.turns) == 1
    assert repo.turns[0].text == "I get out of breath on stairs."


async def test_each_speaker_gets_their_own_turn() -> None:
    """A change of speaker settles the previous turn; the two never merge."""
    repo = _RecordingRepository()
    writer = _writer(repo)

    await writer(_transcript_event(role="user", text="Two weeks."))
    await writer(_transcript_event(role="assistant", text="Thank you."))
    await writer({"type": "bidi_response_complete"})

    assert [(turn.role, turn.text) for turn in repo.turns] == [
        ("user", "Two weeks."),
        ("assistant", "Thank you."),
    ]


async def test_non_transcript_events_are_ignored() -> None:
    """Audio and tool events share the output channel and must pass through."""
    repo = _RecordingRepository()
    writer = _writer(repo)

    await writer({"type": "bidi_audio_stream", "audio": b"..."})
    await writer({"type": "tool_use_stream", "current_tool_use": {}})

    assert repo.turns == []


async def test_a_database_failure_does_not_break_the_call() -> None:
    """A dropped transcript line is bad; dropping the patient's call is worse."""
    writer = _writer(_FailingRepository())

    try:
        await writer(_transcript_event())
        await writer({"type": "bidi_response_complete"})
    except Exception as exc:  # pragma: no cover - only runs on regression
        pytest.fail(f"transcript write failure escaped to the audio loop: {exc!r}")

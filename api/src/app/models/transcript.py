"""The conversation transcript, captured turn by turn during the live call."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ConversationTurn(BaseModel):
    """One finalized utterance from either the patient or the agent.

    Written by `app.agents.bidi.outputs.MongoTranscriptWriter` only when
    the originating `BidiTranscriptStreamEvent.is_final` is true — partial
    deltas are previews, not turns, and are never persisted.
    """

    role: Literal["user", "assistant"]
    text: str
    ts: datetime = Field(description="UTC timestamp when the turn was finalized")


_SPEAKER_LABELS = {"user": "Patient", "assistant": "Assistant"}


def render_transcript_as_text(turns: list[ConversationTurn]) -> str:
    """Flatten turns into a plain speaker-labeled string.

    Deliberately returns a string rather than raw Strands `Message` objects.
    An agent trusts its own message history: hand it a stored history whose
    most recent entry is a tool call and it will re-execute that tool on the
    next invocation, with no model turn in between. This transcript contains
    exactly such entries -- `start_prescreening`,
    `request_document_upload`, `end_session`. Flattening to text removes the
    replayable structure entirely, so the summarizer sees a conversation to
    read rather than an action to repeat.

    Turns are emitted in stored order, which is what lets the summarizer
    honour a correction the patient made later in the call.
    """
    return "\n".join(f"{_SPEAKER_LABELS.get(turn.role, turn.role)}: {turn.text}" for turn in turns)

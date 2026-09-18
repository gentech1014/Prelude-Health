"""Custom BidiOutput that writes finalized transcript turns to Mongo in real time.

Transcript capture goes through `BidiTranscriptStreamEvent` consumed by a
custom `BidiOutput`, rather than through the `BidiMessageAddedEvent` hook.
Both routes work against Nova Sonic -- it does build a message history,
unlike Gemini Live -- but the output-channel route is the one Strands'
own documentation demonstrates for transcript capture, and it composes
cleanly with the audio and browser-socket outputs already in the list.

What reaches Mongo is one document per utterance, assembled by
`TranscriptAssembler` rather than taken from the events directly. Nova
Sonic emits FINAL text in chunks, so storing each event as its own turn
chopped every sentence the patient said into several rows -- and the
summarizer reads those rows as the conversation.
"""

from datetime import UTC, datetime
from typing import Any

import structlog
from strands.experimental.bidi.types.io import BidiOutput

from app.agents.bidi.transcript_assembly import TranscriptAssembler
from app.models.transcript import ConversationTurn
from app.repositories.session_repository import SessionRepository

log = structlog.get_logger(__name__)


class MongoTranscriptWriter(BidiOutput):
    """A `BidiOutput` bound to one session's transcript.

    Wired in alongside the audio and browser-transcript outputs, never
    instead of them -- Strands runs every entry in `outputs` concurrently,
    so a slow database write degrades nothing else.
    """

    def __init__(self, session_repository: SessionRepository, session_id: str) -> None:
        self._sessions = session_repository
        self._session_id = session_id
        self._transcript = TranscriptAssembler()

    async def __call__(self, event: Any) -> None:
        """Persist any utterance this event completed.

        Nothing is written while a turn is still being spoken: a turn
        reaches the database once, whole, when the speaker is done with
        it. `TranscriptAssembler` decides when that is and drops the
        speculative previews that are not speech at all.

        Must stay non-blocking. This runs on the same event loop carrying
        the patient's live audio, so the repository underneath must use
        the async Motor driver, never blocking pymongo.

        Swallows write failures rather than raising: a dropped transcript
        line is bad, but killing the patient's call over it is worse. The
        failure is logged so the gap is visible afterwards.
        """
        for assembled in self._transcript.feed(event):
            if not assembled.is_complete:
                continue

            turn = ConversationTurn(
                role=assembled.role,
                text=assembled.text,
                # No bidirectional event carries a timestamp; the app supplies it.
                ts=datetime.now(UTC),
            )

            try:
                await self._sessions.append_turn(self._session_id, turn)
            except Exception:
                log.exception(
                    "transcript_turn_write_failed",
                    session_id=self._session_id,
                    role=turn.role,
                )

"""Assembles Nova Sonic's raw text events into one turn at a time.

Nova Sonic does not hand out a usable transcript. For every utterance it
emits the text twice, in two `generationStage`s:

- `SPECULATIVE` -- the utterance the model has planned, emitted as soon as
  it is generated. It lands seconds before the patient hears any of it.
- `FINAL` -- what was actually spoken, emitted once that block's audio has
  been produced. Authoritative, but it arrives at the *end* of the
  utterance, not during it.

Strands surfaces both as `bidi_transcript_stream` with `is_final` set from
the stage, so what each consumer wants differs and this takes a flag:

- **Captions** (`include_speculative=True`) need the words on screen while
  they are being said. Waiting for `FINAL` meant a caption that appeared
  in one lump after the sentence had finished, which is no use to someone
  relying on it.
- **The stored transcript** (the default) is the record the physician's
  report is built from, so it keeps only `FINAL`: text the model planned
  and then abandoned to a barge-in was never said to anyone.

Where a caption has both, the longer wins. A caption must never go
backwards mid-sentence, and the two converge on the same words anyway.

`current_transcript` is no help either: the Nova provider sets it to the
same single chunk as `text` (see `strands.experimental.bidi.models
.nova_sonic`), so it is not the accumulated turn its name suggests, and
treating it as one keeps only the newest fragment. Deltas are accumulated
here instead, which is the one contract every provider does honour.
"""

from dataclasses import dataclass
from typing import Any, Literal

TranscriptRole = Literal["assistant", "user"]

_TURN_BOUNDARIES = frozenset({"bidi_response_complete", "bidi_interruption"})
"""Events that end whatever turn is open.

A completion ending is the obvious one. An interruption is the other: the
model abandoned the rest of that utterance, so what was said up to there
is the whole turn and nothing more is coming for it.
"""


@dataclass(frozen=True)
class AssembledTurn:
    """One utterance, as far as it has got.

    `text` is everything said in this turn so far, not the newest
    fragment -- captions need the whole line, and the stored transcript
    needs one entry per utterance rather than one per chunk.
    """

    role: TranscriptRole
    text: str
    is_complete: bool


class TranscriptAssembler:
    """Accumulates one call's transcript, one turn at a time.

    Not shared between outputs. Strands runs every entry in `outputs`
    concurrently over the same event stream, so each one keeps its own
    assembler and reaches the same answer independently -- sharing a
    mutable one would have two consumers racing on the same buffer.
    """

    def __init__(self, *, include_speculative: bool = False) -> None:
        self._include_speculative = include_speculative
        self._role: TranscriptRole | None = None
        self._spoken = ""
        self._planned = ""

    def feed(self, event: Any) -> list[AssembledTurn]:
        """Fold one agent event in, returning whatever it settles.

        Returns the turn as it now stands, and -- when this event ends a
        turn -- that turn marked complete. A role change settles two: the
        previous speaker's finished turn, then the new speaker's opening
        one. Anything that is not transcript or a turn boundary returns
        nothing.
        """
        if not isinstance(event, dict):
            return []

        event_type = event.get("type")
        if event_type in _TURN_BOUNDARIES:
            return self._close()
        if event_type != "bidi_transcript_stream":
            return []

        is_spoken = bool(event.get("is_final"))
        if not is_spoken and not self._include_speculative:
            return []

        role = event.get("role")
        if role not in ("assistant", "user"):
            return []

        fragment = (event.get("text") or "").strip()
        if not fragment:
            return []

        settled: list[AssembledTurn] = []
        if role != self._role or self._starts_a_new_utterance(is_spoken):
            settled += self._close()
            self._role = role

        if is_spoken:
            self._spoken = f"{self._spoken} {fragment}".strip()
        else:
            self._planned = f"{self._planned} {fragment}".strip()

        settled.append(AssembledTurn(role=role, text=self._text(), is_complete=False))
        return settled

    def finalize(self) -> list[AssembledTurn]:
        """Settle the open turn because the caller knows it is over.

        `feed` closes a turn when the model says so. This is for when the
        model never does: Nova Sonic has been observed to speak a turn in
        full and never send `bidi_response_complete` for it, and the
        watchdog that rescues the patient's screen has no way to tell the
        browser that the turn it is still captioning has ended. Without
        this the caption freezes on the greeting and the avatar stays on
        "Speaking..." for the rest of the call.

        Returns nothing when there is no open turn, so calling it on a
        turn that did close normally is harmless.
        """
        return self._close()

    def _starts_a_new_utterance(self, is_spoken: bool) -> bool:
        """Whether newly-planned text belongs to the next utterance, not this one.

        One Nova completion can hold several utterances -- a tool call
        between two of them does not end the completion -- so without this
        the greeting and the question after it arrive as one run-on
        caption. Planned text after the open turn has already been spoken
        is unambiguously the next one: `FINAL` only lands once that
        utterance's audio is done.
        """
        return not is_spoken and bool(self._spoken)

    def _text(self) -> str:
        """The fullest version of the open turn seen so far.

        Length, not recency: `FINAL` arrives after the planned text and is
        usually the same words, so preferring it outright would be fine --
        except while it is still arriving in pieces, where it would make
        the caption shrink back mid-sentence.
        """
        return self._spoken if len(self._spoken) >= len(self._planned) else self._planned

    def _close(self) -> list[AssembledTurn]:
        """Settle the open turn, if there is one with anything in it."""
        role, text = self._role, self._text()
        self._role, self._spoken, self._planned = None, "", ""
        if role is None or not text:
            return []
        return [AssembledTurn(role=role, text=text, is_complete=True)]

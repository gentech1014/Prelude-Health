"""The patient app's WebSocket wire contract, both directions.

Deliberately *not* the raw `strands.experimental.bidi` event vocabulary.
Three reasons the browser gets its own schema instead:

- **Trust.** Passing a browser payload straight into a `TypedEvent`
  constructor means arbitrary client keys become constructor kwargs; one
  malformed frame raises inside the agent's task group and takes the call
  down. Everything inbound is validated here first, and anything invalid
  is answered with an error frame rather than killing the connection.
- **Leakage.** Strands events carry tool names, partially-streamed tool
  arguments, token usage and exception class names. None of that belongs
  on a patient's screen, and the frontend needs none of it.
- **Stability.** `strands.experimental.bidi` is explicitly experimental.
  Pinning the browser to this schema means an upstream event rename is a
  one-file change here, not a frontend release.

`PROTOCOL_VERSION` is sent in the first frame. A frontend that does not
recognize it should say so plainly rather than half-working.
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from app.core.constants import IntakeScreen

PROTOCOL_VERSION = 5

AUDIO_SAMPLE_RATE = 16_000
"""PCM16 mono, 16 kHz, in both directions -- Nova Sonic's own input and
output rate (see `NOVA_AUDIO_INPUT_CONFIG`). Published to the browser in
the first frame so the mic capture and playback rates are never guessed."""

MAX_AUDIO_FRAME_B64 = 64 * 1024
"""Cap on one inbound audio frame, base64 characters.

At 16 kHz mono PCM16 that is ~2 seconds of speech per frame, comfortably
more than any sane capture buffer. A cap at all is the point: without one
a single frame can pin the event loop decoding megabytes."""

MAX_TEXT_LENGTH = 2_000
"""Cap on one typed answer. Long enough for a patient to type a paragraph
about their symptoms, short enough that it cannot be used to flood the
model's context."""


# --------------------------------------------------------------------------
# Client -> server
# --------------------------------------------------------------------------


class ClientAudioFrame(BaseModel):
    """One chunk of the patient's microphone audio, base64 PCM16 @ 16 kHz mono."""

    type: Literal["client_audio"]
    audio: str = Field(min_length=1, max_length=MAX_AUDIO_FRAME_B64)


class ClientTextTurn(BaseModel):
    """A typed answer, standing in for a spoken one.

    Not a parallel data model: it becomes a `user` turn in the same
    transcript a spoken answer lands in, so the summarizer reads one
    coherent conversation regardless of how each answer arrived.
    """

    type: Literal["client_text"]
    text: str = Field(min_length=1, max_length=MAX_TEXT_LENGTH)


class ClientFormUpdate(BaseModel):
    """A field the patient typed or picked on a screen, rather than saying.

    Carries the screen and field so the value can be folded back into the
    transcript with its human label attached (see `SCREEN_FIELD_LABELS`).
    """

    type: Literal["client_form_update"]
    screen: IntakeScreen
    field: str = Field(min_length=1, max_length=64)
    value: str = Field(max_length=MAX_TEXT_LENGTH)


class ClientSymptomAnswer(BaseModel):
    """An answer the patient typed on the symptom-story screen.

    Separate from `ClientFormUpdate` because that screen has no fixed
    fields: the question is one the agent chose from the bank mid-call, so
    what identifies the answer is a question id, not a field name. Covers
    both answering the question currently on screen and correcting one
    already answered -- from the server's side they are the same write.
    """

    type: Literal["client_symptom_answer"]
    question_id: str = Field(min_length=1, max_length=64)
    value: str = Field(max_length=MAX_TEXT_LENGTH)


class ClientScreenAck(BaseModel):
    """The screen the browser is actually showing.

    The other half of `navigate`: the server asked for a screen, this
    confirms the patient is looking at it. Without an ack the server is
    guessing, and "the agent and the screen are in sync" is an assumption
    rather than something either side can check.
    """

    type: Literal["client_screen_ack"]
    screen: IntakeScreen


class ClientConsentRecorded(BaseModel):
    """The browser reports that it has just recorded consent over REST.

    A hint about *when to look*, never the authority on the answer: the
    server re-reads the session document and believes that instead. A
    client that sends this without having consented changes nothing.
    """

    type: Literal["client_consent_recorded"]


class ClientRescheduleRequested(BaseModel):
    """The patient asked, on screen, to change their appointment time.

    The button on the closing screen, rather than saying it out loud. It
    carries no time and decides nothing: the agent still calls
    `offer_appointment_times`, so the tap and the spoken request take the
    same path and the patient hears the same offer either way.
    """

    type: Literal["client_reschedule_requested"]


class ClientAppointmentRescheduled(BaseModel):
    """The patient moved their appointment themselves, on screen.

    Sent after the reschedule REST call has already succeeded, so this is
    news rather than a request -- the server re-reads the session for the
    new time rather than accepting one from the browser, and the agent is
    told it is already saved so it acknowledges instead of writing it
    again.
    """

    type: Literal["client_appointment_rescheduled"]


class ClientDocumentUploaded(BaseModel):
    """A supporting document has finished uploading, and the write succeeded.

    News, not a request. The upload goes straight to object storage over
    REST, which the socket cannot see, so without this frame the only thing
    that ever told the agent a file had arrived was the patient saying so
    out loud -- and they had no reason to know they were expected to.

    Carries the filename only so the agent can refer to it naturally. What
    the file *is* stays unknown until the patient is asked, which is the
    whole point of telling the agent it landed.
    """

    type: Literal["client_document_uploaded"]
    file_name: str = Field(default="", max_length=255)


class ClientDocumentUploadFailed(BaseModel):
    """A supporting document was offered and did not make it.

    The counterpart to the frame above, and the more important half: a
    failed upload is otherwise completely silent to the agent, which goes
    on to the next topic while the patient is still looking at an error.
    """

    type: Literal["client_document_upload_failed"]


class ClientControl(BaseModel):
    """An in-call control the patient pressed."""

    type: Literal["client_control"]
    action: Literal["mute", "unmute", "hold", "resume", "hangup"]


class ClientPing(BaseModel):
    """Liveness probe. Answered with `pong`.

    Exists because a silently half-open socket is indistinguishable from a
    quiet patient: without a round trip the browser cannot tell "the agent
    is waiting for me" from "my connection died three minutes ago."
    """

    type: Literal["client_ping"]


ClientEvent = Annotated[
    ClientAudioFrame
    | ClientTextTurn
    | ClientFormUpdate
    | ClientSymptomAnswer
    | ClientScreenAck
    | ClientConsentRecorded
    | ClientRescheduleRequested
    | ClientAppointmentRescheduled
    | ClientDocumentUploaded
    | ClientDocumentUploadFailed
    | ClientControl
    | ClientPing,
    Field(discriminator="type"),
]

_CLIENT_EVENT_ADAPTER: TypeAdapter[ClientEvent] = TypeAdapter(ClientEvent)


def parse_client_event(payload: Any) -> ClientEvent | None:
    """Validate one inbound frame, or return None if it is not usable.

    Returns None rather than raising: a bad frame is a client bug or a
    probe, and neither is a reason to hang up on a patient mid-sentence.
    The caller answers with an error frame and reads the next one.
    """
    try:
        return _CLIENT_EVENT_ADAPTER.validate_python(payload)
    except ValidationError:
        return None


# --------------------------------------------------------------------------
# Server -> client
# --------------------------------------------------------------------------

ServerEvent = dict[str, Any]
"""Outbound frames are plain JSON-ready dicts.

Built only through the constructors below, never assembled inline, so the
set of frames the frontend must handle is enumerable by reading this file.
"""


def connected(
    screen: IntakeScreen,
    prefill: dict[str, dict[str, str]],
    selections: dict[str, dict[str, str]],
    symptoms: dict[str, Any],
) -> ServerEvent:
    """First frame after the socket is accepted, before the model connects.

    Carries the resume position and everything already collected -- the
    fixed-field prefill and the symptom conversation's own state -- so a
    patient who reconnects lands where they left off instead of on the
    first question again.
    """
    return {
        "type": "connected",
        "protocol_version": PROTOCOL_VERSION,
        "screen": screen.value,
        "prefill": prefill,
        "selections": selections,
        "symptoms": symptoms,
        "audio": {"sample_rate": AUDIO_SAMPLE_RATE, "channels": 1, "format": "pcm16"},
    }


def agent_ready() -> ServerEvent:
    """The model connection is open; the agent is about to speak."""
    return {"type": "agent_ready"}


def agent_audio(audio: str, sample_rate: int) -> ServerEvent:
    """One chunk of the agent's speech, base64 PCM16 mono."""
    return {"type": "agent_audio", "audio": audio, "sample_rate": sample_rate}


def transcript(role: Literal["agent", "patient"], text: str, is_final: bool) -> ServerEvent:
    """One speaker's caption line, as far as it has been spoken.

    `text` is the whole turn so far, not the newest fragment, so a client
    renders it as-is and watches it grow. `is_final` means that speaker is
    done with the turn -- not that this particular chunk was final, which
    is what the model's own events mean by it.

    Sent as soon as the model has the words, which for the agent is
    **seconds before the patient hears them** -- Nova streams a turn in a
    second or two and the browser takes twenty to play it. Pacing them
    against playback is the client's job and can only be the client's job:
    the queue depth is the signal, and only the browser has it (see
    `useIntakeCallEngine`). Holding them here instead would leave a patient
    whose audio is blocked with no captions at all.
    """
    return {"type": "transcript", "role": role, "text": text, "is_final": is_final}


def agent_speaking(speaking: bool) -> ServerEvent:
    """Whether the agent is mid-utterance, so the UI can show whose turn it is."""
    return {"type": "agent_speaking", "speaking": speaking}


def interrupted() -> ServerEvent:
    """The patient spoke over the agent; queued playback must be dropped.

    Not cosmetic: without flushing, the patient hears the rest of a
    sentence the model has already abandoned, several seconds after
    interrupting it.
    """
    return {"type": "interrupted"}


def navigate(screen: IntakeScreen, sequence: int) -> ServerEvent:
    """Move the patient to a screen.

    Emitted by `LiveCallContext.go_to`, so by `navigate_to_screen`, by the
    app-owned transitions off `welcome` and off `confirm-details`, and by
    `ask_symptom_question` catching a screen up.

    `sequence` counts **within one connection, not one call**, because it
    comes from `CallProgress`, which is rebuilt per socket -- so it restarts
    at 1 on every reconnect. A client comparing it against a high-water mark
    kept across reconnects therefore discards every move the new connection
    makes; `connected` is the frame that says to reset it, and it must.
    """
    return {"type": "navigate", "screen": screen.value, "sequence": sequence}


def form_prefill(
    screen: IntakeScreen,
    fields: dict[str, str],
    selections: dict[str, str] | None = None,
) -> ServerEvent:
    """Values the agent heard, offered as prefill on one screen.

    `fields` is always the patient's own words. `selections` is which
    on-screen option the agent judged those words to mean, as validated
    option ids -- present only for the fields it committed to, and absent
    entirely for free text.

    Both are sent because they answer different questions. The words are
    what the patient can read back and correct; the ids are what tick the
    card. Where the ids are missing the frontend matches the words itself,
    so this frame degrades to exactly the protocol-2 behaviour rather than
    to an empty screen.

    Neither is a confirmed fact. The patient can overwrite both.
    """
    return {
        "type": "form_prefill",
        "screen": screen.value,
        "fields": fields,
        "selections": selections or {},
    }


def symptom_state(symptoms: dict[str, Any]) -> ServerEvent:
    """The symptom-story screen as it now stands: one live question, and the answers behind it.

    A whole snapshot rather than "here is the next question": ten short
    pairs cost nothing to resend, and a lost delta would leave the patient
    reading a question they have already answered.
    """
    return {"type": "symptom_state", **symptoms}


def appointment_updated() -> ServerEvent:
    """The appointment behind this session has moved; re-read it.

    Carries no appointment: the browser already has a REST route for the
    session context, and that route is the authority. Sending the new time
    here as well would put the same value on two wires and give the screen
    a way to disagree with the server about when the patient is expected.
    """
    return {"type": "appointment_updated"}


def upload_requested() -> ServerEvent:
    """The agent offered to take a document; open the upload control."""
    return {"type": "upload_requested"}


CallEndReason = Literal["completed", "interrupted", "failed"]
"""Why a call finished. Mirrors `app.api.ws.intake._Ending` exactly.

`completed` is the only reason that yields a report; the other two leave
the session resumable, which is what the frontend tells the patient."""


def call_ended(reason: CallEndReason) -> ServerEvent:
    """The call is over, and why."""
    return {"type": "call_ended", "reason": reason}


def error(code: str, message: str, *, recoverable: bool) -> ServerEvent:
    """A failure the patient needs told about, in words they can act on.

    `code` is a short stable slug for the frontend to branch on; `message`
    is patient-facing copy. Neither carries a stack trace, a model error
    string, or any other internal detail.
    """
    return {"type": "error", "code": code, "message": message, "recoverable": recoverable}


def pong() -> ServerEvent:
    """Answer to `client_ping`."""
    return {"type": "pong"}

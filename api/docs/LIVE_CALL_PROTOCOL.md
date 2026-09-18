# The live intake call — wire protocol

The contract between the patient's browser and the live agent, over one
WebSocket. Written against the code at `src/app/schemas/intake_channel.py`,
`src/app/api/ws/`, and `src/app/agents/`, as of 2026-09-09.

- Backend REST contract: [`API_REFERENCE.md`](./API_REFERENCE.md)
- Frontend side: `../../prelude-health-ui/src/features/prescreening-session/intakeProtocol.ts`
- Integration status: `../../docs/INTEGRATION.md`

---

## 1. Why the browser does not speak the agent's own event language

`strands.experimental.bidi` has a perfectly good event vocabulary, and the
obvious implementation forwards it to the browser verbatim. That was the
starting point and it is wrong on three counts.

**Trust.** Passing a browser payload into a `TypedEvent` constructor makes
arbitrary client keys into constructor kwargs. One malformed frame raises
inside the agent's own task group and drops a live call. Everything inbound
is now validated against a discriminated union first, and an invalid frame
gets an error reply rather than hanging up on a patient mid-sentence.

**Leakage.** Agent events carry tool names, partially-streamed tool
arguments, token usage and exception class names. None of that belongs on a
patient's screen and the frontend needs none of it.

**Stability.** The upstream module is labelled experimental. Pinning the
browser to this schema makes an upstream event rename a one-file change on
the server, not a frontend release.

`PROTOCOL_VERSION` is sent in the first frame. A client that does not
recognize it should say so plainly rather than half-working. It is currently
**5**.

Version 3 added `selections` to `connected` and `form_prefill`, which is
additive — a version-2 client ignores the field and falls back to matching the
words, which is all it ever had.

Version 5 added the document-upload result: `client_document_uploaded` and
`client_document_upload_failed`. Both are client frames, so a version-4
client simply never sends them and behaves exactly as before -- which is
the bug they exist to fix, not a regression.

Version 4 added the closing reschedule: the `appointment_updated` frame, the
`client_reschedule_requested` and `client_appointment_rescheduled` frames, and
`appointment-reschedule` as a screen `navigate` may name. A version-3 client
ignores the new server frame and never sends the new client ones, so it keeps
working — it simply shows a stale appointment on the closing screen if the
assistant moves one, and offers no way to ask for the move by tapping.

---

## 2. Connecting

```
POST /api/v1/sessions/{id}/ws-ticket     ->  single-use ticket, ~60s TTL
WS   /ws/intake/{id}?ticket=<ticket>
```

The ticket is requested **per connection, including every reconnect**: it is
spent on connect against the `ws_tickets` ledger and cannot be cached or
replayed. A browser cannot set headers on a WebSocket handshake, so the
credential has to travel in the query string, where access logs and proxy
traces can see it — which is exactly why it is a ticket and not the
patient's 48-hour intake token.

The socket is refused, before the protocol upgrade, on any of:

| Reason | Log event |
|---|---|
| browser `Origin` not in `ALLOWED_ORIGINS` (when set) | `intake_ws_rejected_bad_origin` |
| ticket forged, expired, or for another session | `intake_ws_rejected_bad_ticket` |
| ticket already spent | `intake_ws_rejected_replayed_ticket` |
| unknown `session_id` | `intake_ws_rejected_unknown_session` |
| session state has no business opening a call | `intake_ws_rejected_bad_state` |

`NOTIFICATION_SENT`, `STARTED`, `IN_PROGRESS` and `INTERRUPTED` may open a
socket. Consent is **not** a gate here — see below. A finished, summarized
session still carries an affirmative consent record forever, which is why
the state check and not the consent record is what closes the door.

---

## 3. Server → client

Every frame is built by a constructor in `intake_channel.py`; the set below
is exhaustive by construction.

| `type` | Fields | Meaning |
|---|---|---|
| `connected` | `protocol_version`, `screen`, `prefill`, `selections`, `symptoms`, `audio` | First frame, sent before the model connects. Carries the resume position and everything already collected. |
| `agent_ready` | — | The model connection is open. |
| `agent_audio` | `audio` (base64 PCM16), `sample_rate` | One chunk of the agent's speech. |
| `transcript` | `role` (`agent`/`patient`), `text`, `is_final` | A live caption line, sent as soon as the model has the words rather than once they have been spoken. `is_final` false means it may still change. The client holds a new agent turn back until the audio queued before it has played — see below. |
| `agent_speaking` | `speaking` | Whose turn it is. |
| `interrupted` | — | The patient spoke over the agent. Queued playback must be flushed. |
| `navigate` | `screen`, `sequence` | Move the patient to a screen. Emitted by `navigate_to_screen`, by the app-owned transitions off `welcome` and off `confirm-details`, and by the closing reschedule tools. |
| `form_prefill` | `screen`, `fields`, `selections` | Values the agent heard, offered as editable prefill, plus the on-screen options it judged them to mean. |
| `symptom_state` | `current`, `answers`, `answered_count`, `planned_count` | The symptom screen in full: the one question currently on it, and every answer behind it. |
| `upload_requested` | — | Open the document upload control. |
| `appointment_updated` | — | The session's appointment has moved; re-read it from the session route. Carries no time on purpose — the REST route is the authority, and a second copy here is a second thing that can be wrong. |
| `call_ended` | `reason` (`completed`/`interrupted`/`failed`) | The call is over, and why. |
| `error` | `code`, `message`, `recoverable` | A failure in words the patient can act on. No stack traces, no model error strings. |
| `pong` | — | Answer to `client_ping`. |

`sequence` on `navigate` increases monotonically within one call so a frame
that arrives late — or twice, across a reconnect — can be ignored rather
than dragging the patient backwards.

**Agent captions arrive a turn early, and the browser paces them.** The
model produces a whole turn in a second or two and the browser takes twenty
to play it, so a caption shown on arrival is the caption of the *next* thing
the assistant will say, printed over the words the patient is still
listening to — reported from a real call as the greeting being overwritten
by the consent request before it had finished. The frames are still sent
immediately, because only the browser knows how much audio is queued: it
holds a new agent turn's line until that queue drains, keeps the line
up to date while it waits, and holds nothing at all when playback is blocked
— a patient who cannot hear is the one who needs the captions most. Patient
captions are never held; they are speaking now.

`symptom_state` is a **snapshot, not a delta**, and the same object rides on
`connected`. That screen has no fixed fields — the assistant picks each
question from the bank based on what the patient has already said — so a
lost delta would leave the patient reading a question they had answered.
Ten short pairs cost nothing to resend, and a reconnect lands
mid-conversation rather than back at the first question.

Deliberately **not** forwarded: `tool_use_stream`, `tool_result`,
`bidi_usage`, and the raw `bidi_error` payload. Navigation, prefill and the
upload prompt reach the browser from the tools themselves, as complete
instructions rather than argument deltas.

### One writer

Agent output, tool-driven navigation and route-level errors all originate in
different tasks, and a Starlette WebSocket has exactly one safe writer. Every
frame is therefore published onto the call's `UiEventBus` and drained by a
single sender task in `app.api.ws.intake`.

The bus is bounded at 1024 frames. On overflow it drops `agent_audio` and
`transcript` frames — continuous streams, where a loss is a glitch — and
never drops a control frame, whose loss would leave the patient's screen out
of step with the call for the rest of it.

---

## 4. Client → server

| `type` | Fields | Notes |
|---|---|---|
| `client_audio` | `audio` (base64 PCM16 @ 16 kHz mono) | Capped at 64 KB of base64 per frame. Dropped server-side while muted or on hold. |
| `client_text` | `text` (≤2000) | A typed answer. Becomes a `user` turn in the same transcript a spoken answer lands in. |
| `client_form_update` | `screen`, `field`, `value` | A field the patient typed or picked. Folded into the transcript with its human label. |
| `client_symptom_answer` | `question_id`, `value` | An answer typed on the symptom screen, or a correction to one already given. Addressed by question id because that screen has no fields. Refused for a question outside the loaded plan. |
| `client_screen_ack` | `screen` | Confirms which screen the browser is actually showing. |
| `client_consent_recorded` | — | Consent has just been recorded over REST. A hint about when to re-read the session, never the authority on the answer. |
| `client_reschedule_requested` | — | The patient asked for a different appointment time by tapping rather than saying so. Performs nothing: the agent is told to offer times, so a tap and a spoken request produce the same conversation. |
| `client_document_uploaded` | `file_name` | A supporting document finished uploading and the REST write succeeded. The server re-reads the session to confirm a file really landed, then tells the agent to ask what the report is. A claim the session does not support is handled as a failure. |
| `client_document_upload_failed` | — | A document was offered and did not make it. Without it a failure is invisible to the call: the agent moves to the next topic while the patient is still looking at an error. |
| `client_appointment_rescheduled` | — | The patient moved their appointment themselves and the REST write has already succeeded. The server re-reads the new time from the session rather than accepting one here, puts them back on `thank-you`, and tells the agent it is already saved. |
| `client_control` | `action` (`mute`/`unmute`/`hold`/`resume`/`hangup`) | |
| `client_ping` | — | Answered with `pong`. |

**Audio is PCM16 mono at 16 kHz in both directions** — Nova Sonic's own input
and output rate. The browser is told this in the `connected` frame rather
than left to assume it: a capture pipeline that labels 48 kHz audio as 16 kHz
sends speech to the model at a third of its real speed, which is inaudible in
code review and unintelligible in the patient's ear.

**Mute is enforced server-side as well as client-side.** It is a privacy
control, and one that depends on the client honouring it is not one.

### The model has to be pushed, twice over

Two behaviours of Nova Sonic that are not documented anywhere obvious, both
established by direct experiment, and both of which make the difference
between a working call and a silent one:

**It does not open a conversation.** Given a system prompt telling it to
greet the patient by name, it sat silent through 25 seconds of streaming
audio and produced nothing at all. So `BrowserInput` returns a one-off
`_CALL_OPENER` text turn as the very first input of every connection,
before it reads anything from the socket. It is marked `(system)` and
phrased as a stage direction, because it becomes a `user` message in the
model's own history -- and it never reaches the stored transcript, which
records only what the model heard and said.

**Text alone does not make it generate.** Measured back to back: the same
opener with no audio following it produced 0 audio chunks over 20 seconds;
the opener followed by silence frames produced 94 chunks and a spoken
greeting. It buffers text and lets the audio content stream drive
generation.

That second one is why `BrowserInput` fills gaps in the patient's audio with
silence after `_AUDIO_GAP_SECONDS`. Without it, a patient who declines the
microphone gets a call that connects and then sits silent forever, and their
typed answers go equally unanswered -- which would make the typed path
decorative rather than a real input channel. Only gaps are filled: while the
microphone is running, frames arrive every ~32 ms and the filler never
fires, so a speaking patient's audio is never interleaved with injected
silence.

**The ack matters.** `navigate` says which screen the call wants; the ack says
which screen the patient is looking at. A mismatch is logged as
`intake_screen_ack_mismatch` — without it, "the agent and the screen are in
sync" is an assumption neither side can check.

---

### Consent is asked for in the call, not before it

The socket opens **before** consent exists, and this is the one place the
design deliberately moved a gate.

The patient has to hear the greeting before being asked anything, and a
greeting cannot happen on a socket that consent gates. So the gate moved
from the door to the tools:

| | Before consent | After consent |
|---|---|---|
| `navigate_to_screen` | `welcome` and `confirm-details` only | any intake screen |
| `record_intake_details` | refuses | records |
| `start_prescreening` | raises `ConsentNotRecordedError` | fixes the appointment reason being screened and returns its coverage brief |
| `ask_symptom_question` | refuses | puts one question on the screen |
| `record_symptom_answer` | refuses | records it and clears the screen |
| `request_document_upload` | refuses | opens the prompt |

`LiveCallContext.consent_given` is what those check, and it is **only ever
set from the session document** — seeded at connect, and re-read when the
browser sends `client_consent_recorded`. A client that claims consent it
never gave unlocks nothing.

That frame exists because the assistant is sitting on the consent screen
waiting and nothing else would release it: consent is recorded over REST,
which the socket cannot see. So the browser says "look again", and the
server looks.

Handling it does two things. It hands the model a `(system)` turn telling
it to continue — and it moves the screen to `patient-concerns` itself,
rather than asking the model to. The step after consent has exactly one
possible destination, and the model was observed asking the first question
while the patient was still looking at the consent screen. A certainty
beats a nudge where there is no branch to decide.

`DECLINED` still refuses the socket outright. A refusal is a decision, not
a pause, and it is now genuinely distinguishable from "not asked yet".

### When the model skips a navigation

Two backstops, because the explicit signal is reliable but not certain:

- **Recording carries the screen.** A value recorded for a screen the
  patient is not on means the navigation was skipped, not that the value
  belongs elsewhere. The data is the better signal, so it brings the
  screen with it — otherwise the patient watches an answer appear on a
  page they cannot see.
- **The browser guarantees the consent screen.** `IntakeCallProvider` will
  move to `confirm-details` itself if the call is still on `welcome` once
  the assistant has gone quiet and consent is not recorded. Safe precisely
  because the assistant cannot reach anywhere *else* pre-consent.

Both are narrow and both are documented at the point they happen.
Everywhere else, navigation is the conversation's decision alone.

---

## 5. How the agent drives the screen

The patient does not navigate an eleven-step wizard; the conversation does.
That needs an explicit signal rather than inference — guessing the topic from
tool calls and transcript text means the screen lags the conversation and
occasionally contradicts it.

Three tools carry it, all in `app.agents.tools`:

- **`navigate_to_screen(screen)`** — called before the first question on a
  new topic, and waited on before the question is spoken. Its *return value*
  is the point of it being a tool: it tells the model what that screen asks
  about and which fields it may fill, so the model knows what is in front of
  the patient. An unknown screen returns a usable correction rather than
  raising, because a tool exception mid-call surfaces to the patient as a
  broken turn.

  **The order is enforced here, not requested in the prompt.** The flow is a
  fixed sequence (`AGENT_DRIVEN_SCREENS`), so a forward jump past a screen is
  a topic nobody asked the patient about: the tool refuses it and names the
  screen that has to come next. Moving *back* to a covered screen stays
  allowed — a patient correcting an earlier answer is the one honest reason
  to revisit a topic.
- **`record_intake_details(screen, details, selections)`** — called as answers
  land, with the patient's own words. Recording for a screen the patient is not
  on means they were asked while looking at the previous topic: the value is
  kept, the screen catches up to it, the skip is logged as
  `intake_navigation_skipped`, and the reply tells the model to navigate first
  next time. `details` is a JSON object passed as a string:
  flat string parameters are a tool schema Nova Sonic fills reliably, where
  nested object schemas with open-ended keys are not. Parsing is tolerant
  (JSON first, then `key: value` lines) because every key is checked against
  the screen's allowlist afterwards anyway.
  `selections` is optional and carries option **ids** rather than words, for
  the fields `navigate_to_screen` listed options for — see below.
- **`get_current_screen()`** — a pure read, for when the model has lost track.
  Typically after a reconnect.

### The one screen outside the sequence

`appointment-reschedule` is reachable, but not by `navigate_to_screen`, which
refuses it like any other screen outside `AGENT_DRIVEN_SCREENS`. Two tools own
that move, both in `app.agents.tools.appointment`:

- **`offer_appointment_times()`** — called when the patient answers the
  closing question by asking to move their appointment. It fetches the
  doctor's open times, moves the patient onto the screen showing them, and
  returns the same times to the model with the ids it must pass back. Both
  ends read `AppointmentService`, which is also what the reschedule screen's
  own REST routes call, so the times said out loud and the times on screen are
  one list rather than two that can disagree. Nothing open means the screen is
  *not* moved: an empty list is worse to look at than to be told about.
- **`move_appointment(slot_id)`** — re-validates the time against live
  availability, writes it, publishes `appointment_updated`, and puts the
  patient back on `thank-you` with the new time showing. A time taken since it
  was offered comes back as a sentence the agent can say, not an exception.

Both refuse outright unless the patient is already on a closing screen: a
screening abandoned halfway through for a calendar screen is worth nothing to
the patient's doctor, so a mid-intake request is answered at the end.

The patient can also do it themselves — the same screen's own tap-and-confirm
path, which reports back over `client_appointment_rescheduled`. Either way
the call returns to `thank-you`, the closing question is asked again, and the
goodbye only follows once they have nothing further. The force-end watchdog
covers both screens (`CLOSING_SCREENS`) and always uses the long window on the
reschedule branch, because reading a list of times takes longer than a
goodbye.

The screen vocabulary lives in `app.core.constants.IntakeScreen`, and every
member's value is **verbatim the patient app's own route segment**. Renaming
one without renaming the frontend route silently desynchronizes the call from
what the patient can see. `SCREEN_FIELDS` is the field allowlist — an
allowlist, not a hint, so a hallucinated field can never reach the screen.

Field *values* are always the patient's own words — that is what the patient
reads back and corrects, and what the summarizer is shown.

### Which control those words mean

Words alone were not enough to tick a card. The browser had to recover the
meaning by matching text against its own option labels, which missed every
synonym a patient actually uses ("hypertension", "my sugars", "I use an
inhaler") and, worse, read a denial as an affirmation: "no diabetes" contains
"diabetes", so the card ticked.

The model is the only part of the system that understood the sentence, so it
is now asked for the answer directly:

- `SCREEN_FIELD_OPTIONS` (in `app.core.constants`) is the closed vocabulary
  each non-free-text field accepts.
- `navigate_to_screen`'s reply lists that screen's options inline, so the ids
  are in context at the moment the model lands on the topic.
- `record_intake_details`' `selections` argument carries the ids back.
  `CallProgress.record_selections` checks every one against the field's own
  option list — an allowlist exactly like `SCREEN_FIELDS`, so a hallucinated
  option can no more reach the screen than a hallucinated field can. A field
  whose ids were *all* unrecognized is dropped rather than sent empty, because
  empty means "fall back to the words".
- Both halves reach the browser on `form_prefill`: `fields` for the words,
  `selections` for the ids.

`selections` is optional throughout. Where the model declines it, the frontend
falls back to `prefillMatching.ts`, which is now token-boundary matching over a
per-option alias table with a negation guard, and which still returns null
rather than rounding a near-miss up to the closest checkbox. That fallback is
what makes it safe to ask a speech-to-speech model for structure mid-turn: the
worst case is the behaviour that came before.

The vocabulary is shared with the frontend through
`contracts/intake-field-options.json`, generated by
`scripts/export_field_options.py` and asserted on both sides — by
`tests/unit/test_field_option_contract.py` here, and by the UI's
`intakeFieldOptions.test.ts` — so the two cannot drift silently.

---

## 6. How a call ends, and what that costs

| Ending | Session state | Report? |
|---|---|---|
| agent said goodbye and called `end_session` | `COMPLETED` → `SUMMARIZING` → … | yes |
| patient pressed End, or the socket dropped | `INTERRUPTED` | no |
| the model or the socket failed | `INTERRUPTED`, error frame sent first | no |

Only the first produces a report, so the physician view can tell a finished
call from an abandoned one rather than rendering an empty summary as though
it were complete.

`INTERRUPTED` is new, and it is what makes a dropped call resumable rather
than indistinguishable from one still on the line. Progress is persisted as
the call goes — `last_screen` and `collected_details` on the session — so a
reconnect continues instead of restarting:

- the socket's first frame carries the screen and the collected values, so
  the browser lands where the call got to;
- `build_intake_prompt` receives the same progress and appends a resume
  block, so the agent acknowledges the drop in one sentence and carries on.

That block is reconstructed from the marker and the recorded values, **never
by replaying stored turns as a Strands message list**. An agent trusts its
own history, and these transcripts end on tool calls, which would be
re-executed with no model turn in between.

---

## 7. Failure behaviour, by design

| Failure | What happens |
|---|---|
| malformed inbound frame | `error` frame, call continues (`intake_client_frame_rejected`) |
| inbound audio that will not base64-decode | frame dropped, call continues |
| model timeout | Strands reconnects; a recoverable `error` frame says "one moment" |
| model error | `error` frame, `call_ended: failed`, session `INTERRUPTED` |
| socket dies | sender task returns, call torn down, session `INTERRUPTED` |
| progress write fails | logged, call continues — a lost marker costs a repeated question, raising would cost the call |
| transcript write fails | logged, call continues (`transcript_turn_write_failed`) |
| bus full | stream frames dropped, control frames kept |

On the browser side: a lost socket reconnects with exponential backoff and
jitter (five attempts, ~30s total), fetching a fresh ticket each time. A
`1008` close is treated as the session being refused, not a network problem,
and is not retried. A 15-second heartbeat with a 10-second pong deadline
closes a half-open socket the browser would otherwise mistake for a quiet
patient.

---

## 8. What is not here

- **One rough edge, observed and not explained.** After a long idle stretch
  mid-call, the agent was seen restarting its greeting rather than
  continuing. The likely cause is a model-connection restart
  (`bidi_connection_restart`), which rebuilds from `agent.messages` -- and
  that history still contains the opener. If so, the opener should be
  suppressed on a restart. Not confirmed, and worth reproducing before
  changing anything.
- **Voice itself is verified.** Against the real model: 292 audio chunks /
  764 KB of speech, real transcripts following the prompt in order, and
  `navigate_to_screen {'screen': 'confirm-details'}`. In the browser, with
  the microphone blocked entirely, the agent greets by the patient's real
  name and their physician's, states the emergency notice, and asks for
  confirmation -- see the delivery notes in `../../docs/INTEGRATION.md`.
- **Screen recording** stays out of scope. The `recording/*` endpoints remain
  in place and unused.

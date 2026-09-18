# Changelog

Running record of changes, newest first. Scope is what changed and why —
for the full contract see [API_REFERENCE.md](./API_REFERENCE.md), for the
reasoning behind the product decisions see `../../docs/INTEGRATION.md`.

---

## 2026-09-11 — The closing question can now be answered with "move my appointment"

The call's last question is "is there anything I can help you with", and the
one thing patients most often want at that point is a different appointment
time. Until now the call could hear that and do nothing with it: the agent's
only reschedule tools searched the `appointment_slots` collection for a slot
strictly *earlier* than the current one, offered it out loud, and never
touched the patient's screen — while the reschedule screen they were looking
at fetched a completely different list from `AvailabilityService` over REST.
Two sources, one of them invisible to the patient, and neither reachable from
the closing screen.

### What changed

- **`AppointmentService`** (new) holds the two operations that used to live
  inline in `app.api.v1.appointments`: which times are open for this session's
  doctor, and moving the appointment onto one of them. The REST routes the
  screen calls and the tools the agent calls now go through it, so the times
  offered aloud and the times shown on screen are the same list, re-validated
  the same way when one is chosen.
- **`offer_appointment_times` / `move_appointment`** (new, in
  `app.agents.tools.appointment`) replace `find_earlier_appointment_slots` and
  `reschedule_appointment` in the agent's toolset. The first moves the patient
  to `appointment-reschedule` and hands the model the same times it just put on
  screen; the second writes the chosen one, tells the browser to re-read the
  appointment, and puts the patient back on `thank-you` with it showing. Both
  refuse unless the patient is already on a closing screen — a screening
  abandoned halfway through for a calendar screen is worth nothing to their
  doctor, so a mid-intake request is answered at the end.
  `SchedulingService` keeps `cancel_appointment` and its slot-inventory
  reschedule; the patient-facing path is the availability one, which is what
  the screen has always used and which can offer a *later* time.
- **Two new client frames.** `client_reschedule_requested` is the button on the
  closing screen, and it performs nothing — it asks the agent to offer times, so
  tapping and saying it produce the same conversation. `client_appointment_
  rescheduled` reports a move the patient made themselves, after the REST write
  succeeded; the server reads the new time from the session rather than the
  browser and tells the agent it is already saved, so it confirms instead of
  writing it again.
- **Prompt steps 15-18** replace the old 15-17: ask, handle a reschedule if
  that is what they ask for, ask again, then the goodbye and `end_session`.
- **The force-end watchdog** now covers `appointment-reschedule` as well as
  `thank-you` (`CLOSING_SCREENS`). Nothing watched the reschedule branch, so a
  patient who walked away mid-list left the call open until the model timed
  out. It always uses the long window there, and both routes onto that screen
  clear `closing_started` — the eight-second goodbye window counting while
  someone reads a list of times is the same bug that used to hang up on
  patients who paused to think.
- **Protocol version 4**: `appointment_updated` (server), the two client frames
  above, and `appointment-reschedule` as a screen `navigate` may name. Additive
  — a version-3 client ignores the new frames and keeps working, with a stale
  appointment on the closing screen and no way to ask by tapping.

---

## 2026-09-11 — The call listens instead of reading a script

Four moments from real calls, each a different way the intake stopped being a
conversation. They shared three root causes, so they are fixed together rather
than patched one at a time.

### What patients actually got

- A patient who tore a muscle playing football was asked whether the tear
  **"comes and goes"**.
- A patient who tapped **"I am not sure"** when booking was told *"You've booked
  this appointment about I am not sure. Shall we go through some questions about
  that?"*
- A patient who said **"move next"** had that recorded as the reason for their
  visit. It satisfied the completeness gate, advanced the call, and reached
  their doctor.
- Nothing anywhere could say whether an answer was confirmed, guessed, inferred,
  or refused.

### Root cause 1 — every collected value was a bare string

`CallProgress.details` was `dict[screen][field] -> str`, and `SymptomAnswer.answer`
was a `str`. Five different situations stored identically: a plain answer, a
hedge, a decline, a "don't know", and the agent's own inference.

The only test of whether a string was an answer was `_is_answer`, which rejected
a value **if it contained a question mark** and nothing else. So "move next" was
an answer. And because `navigate_to_screen`'s completeness gate tested whether
the *key was present*, it was also a complete one.

- New `AnswerStatus` (`constants.py`): confirmed / uncertain / inferred /
  undisclosed / unknown, with `ANSWERED_STATUSES` as what the gates count —
  `inferred` deliberately excluded, since the agent working something out is
  not the same as having asked.
- `RecordedValue(text, status, source)` replaces the bare string.
  `as_storage()` still returns plain words, so the frontend prefill, the
  summarizer and the PDF are untouched; certainty travels beside it in the new
  `Session.collected_statuses`, and a session written before it existed reads
  as confirmed — which is how those values were treated at the time.
- `reject_as_answer` replaces `_is_answer` and refuses three things: a question,
  placeholder text, and a value that is *nothing but* a request to move on.
  Matched whole, never as a substring, so "I skip breakfast" is still an answer.
- The completeness gate now counts answered fields, not present keys. A decline
  **does** advance the call: blocking on one leaves the agent only one way
  forward, which is to press someone who has already said no.
- `record()` returns a `RecordOutcome` reporting what it refused and what it
  *replaced*, so a correction is acknowledged rather than silently overwritten —
  the model's own history still held the old value, which is how the call ended
  up contradicting the patient a few turns later.

### Root cause 2 — the screening had only one axis

`PRESCREENING_CATEGORY_BRIEFS` is keyed on the booking menu: seven chronic organ-system
concerns plus two catch-alls. A traumatic injury fits none of them, so it landed
on `not_sure`, whose brief — correctly for an unexplained symptom, absurdly for a
torn hamstring — asks whether the problem is constant or comes in episodes.

- New `PresentationType`: acute injury / new problem / ongoing condition /
  routine / undifferentiated, with its own briefs, chosen by the model from the
  conversation and passed to `start_prescreening`. It *leads* the category brief.
- `PRESENTATION_CAUTIONS` states what each presentation makes absurd, as
  prohibitions. A positive brief alone never stopped it, because the category
  brief genuinely asked for the pattern questions.
- `_PATTERN_QUESTION` refuses them outright for an acute injury — the same shape
  as the existing `_NUMERIC_SCALE` guard, and for the same reason: a prompt is a
  request. Contextual, not a banned-words list: "does it come and go" is still
  exactly right for a recurring symptom.
- Persisted as `Session.symptom_presentation`, so a reconnect does not revert.

### Root cause 3 — the tools could not see the conversation

`_brief()` was built from `plan.categories` and `plan.reference` and nothing
else, and `navigate_to_screen` described a screen purely from the static tables.
Neither ever mentioned what the patient had already said, so re-asking was the
default failure mode of a long call.

- `start_prescreening` now opens with everything already established, rendered
  with each value's certainty.
- `navigate_to_screen` says what is already answered on the screen it lands on.
- `_progress()` — the most repeated instruction in the system, re-injected on
  every screening turn — no longer *leads* with the question quota. It led with
  "at least 7 are required. Keep going", against a counterweight stated once in
  the prompt, and that is what produced the padding that reached for the pattern
  questions. The range is unchanged and still enforced.
- `record_symptom_answer` takes `corrects`, so a correction to an earlier answer
  lands on the question it belongs to instead of overwriting whatever is on
  screen.

### Also fixed

- **`not_sure` is no longer a spoken reason.** `VisitTypeCopy.spoken_topic`
  separates the button label from the phrase that goes in a sentence, and it is
  `None` for `not_sure` because there is nothing to confirm. Its `_booking_block`
  branch opens by asking, and explicitly offers the patient the option of not
  saying. Free text now outranks an empty `not_sure` selection.
- **`resolve_prescreening_category` is a spelling table again, not a classifier.**
  It matched aliases as substrings, so "chest infection" resolved to `heart` and
  anything containing "other" (*bothered*, *mother*) to `not_sure`. Whole-word
  only, and the symptom aliases are gone — which reason a story belongs to is the
  model's call.
- **The `thank-you` watchdog no longer ends the call mid-answer.** It armed on any
  agent audio on that screen, but that screen carries step 15's question as well
  as the goodbye, so it started counting when the agent finished *asking* and hung
  up on anyone who took more than eight seconds to think. Now gated on
  `closing_started`, with a longer idle window before it and cancellation on any
  patient activity.
- **A patient can retract an answer.** An emptied field was dropped at three
  separate layers, so a mis-heard value could not be taken back. It now clears the
  words *and* the option the agent ticked from them.
- **A patient who will not engage is no longer asked forever.** Both bounds counted
  answers, so a deflected question moved neither; `MAX_QUESTIONS_PUT_ON_SCREEN`
  caps questions asked.
- **The question bank no longer learns from refusals.** It is ranked by how often a
  question has been asked, so learning from a declined one taught the corpus to
  keep asking it.
- **Frontend matching** (`prefillMatching.ts`): the boolean scan took the first
  polarity cue anywhere in the sentence, so "I had a scan last month, nothing
  showed up" was recorded as *no recent tests*; negation was bounded at four
  tokens, so "I don't have any history of heart disease" ticked Heart disease;
  and splitting on " and " ran before the negation guard, so "no heart disease and
  diabetes" ticked Diabetes.
- The summarizer is given each value's certainty and told to distinguish
  *declined* from *could not remember* from *never asked* in `gaps`.

## 2026-09-10 — Completing the `wip-basecode-santhosh` merge

Merge commit `5079f4a` combined the scheduling/slot branch with the UI-integration
branch and resolved several conflicts by keeping one side whole. Where the two
sides touched the same file, that kept the **callers** from one branch and the
**definitions** from the other. The app did not start; the failures surfaced one
crash at a time, and three of them were silent.

### It did not import

- `SlotStatus` was used by four modules and defined by none.
- `main.py`'s lifespan created a task for `_sweep_loop`, which was never written
  (`SweeperService.run_once` is deliberately loop-free — the loop belongs to the
  caller). Rebuilt against `SweeperLockRepository`, so one replica runs a tick.
- Dropped imports: `SlotRepository` (`api/deps.py`), `SettingsDep` (`mock_booking`),
  `CalendarDelivery`/`resolve_appointment_timezone`/`resolve_clinic_zone`/
  `describe_document` (`session_service`).
- `SlotUnavailableError` was declared **twice**, so the second silently won and
  every 2-argument raise in `SchedulingService` was a `TypeError`. They are two
  different errors: the booking-time one (no slot id exists yet, identifies a
  doctor) is now `AppointmentTimeUnavailableError`.
- Model fields their own callers required, restored: `Session.appointment_timezone`,
  `.current_slot_id`, `.cancelled_at`, `.cancellation_reason`, `.document_summary`,
  `Doctor.timezone`, `SessionState.CANCELLED`.
- `GoogleCalendarClient.update_event_time` and `.delete_event`, both called by
  `SchedulingService`. `move_event` stays: it is the `CalendarDelivery` path's
  own method, not a duplicate to remove.

### Three regressions nothing would have reported

These type-checked, linted and ran. Only the behaviour was wrong.

- **The agent spoke raw UTC again.** `build_intake_prompt` had been reverted to
  `strftime` on the UTC value, re-introducing the exact bug
  `format_spoken_datetime` exists to prevent — a patient booked at 2:00 PM IST
  was told 8:30. The import was still there, unused.
- **`booking_visit_type` was never set** on a new session, so the call could not
  open by confirming what the patient booked, and every screening fell back to
  an open question.
- **`invocation_state["scheduling"]` was never populated**, so all three in-call
  scheduling tools would `KeyError` mid-call. The imports were still in
  `ws/intake.py`, unused.
- `scheduled_at` lost its `AwareDatetime` annotation on both inbound schemas,
  so a naive datetime was silently accepted and reinterpreted as UTC.

### Found by running it, not by the suite

Booking through the UI (`doctor_id`, no `physician`) created its slot with an
**empty physician**, because that call still passed `payload.physician` while
the session stores `doctor.name`. The slot's physician is the join key
`SchedulingService` matches on, so every such appointment held a slot no
reschedule could find. Now `doctor.name` on both sides.

### Removed

`SessionState.SAFETY_ESCALATED`, and its docstring pointing at the deleted
`app.agents.tools.safety`. Safety escalation was removed by product decision on
2026-09-08 (see that entry); the merge brought the state back with no writer,
no reader, and no tool. No stored session used it.

### Tests

`test_tool_contracts.py` had been truncated from 35 tests to 21 and had a test
for the deleted safety tool appended. Restored, with the four scheduling-tool
tests ported on top. Also repaired: `test_report_pdf`'s `_session()` helper
(signature lost two parameters its body still used), a `create_event` call
missing the now-required `tz`, an uploads test still using `?token=` instead of
the session cookie, and the two `_start_end_pair`/`_event_body` timezone tests
that pin the fix above. `contracts/intake-field-options.json` was regenerated.

**382 passing**, ruff + format + ty clean.

**Still open:** `tests/integration/test_intake_ws.py` fails intermittently
(~2-4 in 12 runs, a different test each time, always `CancelledError` out of
`receive_json`). Pre-existing, not merge damage — it reproduces at 2/12 against
the untouched pre-merge `ws/intake.py` from `e1f0de7` with an identical test
file. `_supervise` hard-cancels the sender task, possibly mid-`send_json`.

---

## 2026-09-10 — The agent names the option, instead of the browser guessing it

Protocol version **2 → 3**. Prefill previously travelled as the patient's own
words only, and the browser recovered which control they meant by matching
those words against its option labels with `String.includes`. That had two
failure modes, in opposite directions.

- **It read denials as answers.** "No diabetes", "tested negative for
  diabetes" and "no cancer in the family" all contained the label, so the card
  ticked. A denial rendered as an affirmation is the one fabrication that puts
  a condition nobody has in front of a physician.
- **It missed the words patients use.** "Hypertension", "my sugars", "I use an
  inhaler", "I quit ten years ago", "just socially" matched nothing. Every
  yes/no field was effectively dead: `matchBoolean` compared the whole string
  against a set of bare words, so the value the agent naturally writes for
  `no_known_allergies` — "no known allergies" — resolved to nothing at all.

The model understood those sentences; the pipeline threw that away at the tool
boundary and tried to reconstruct it downstream with a string function.

### What changed

- **`SCREEN_FIELD_OPTIONS`** (`app.core.constants`) — the closed option
  vocabulary per field, and `BOOLEAN_SCREEN_FIELDS` derived from it.
- **`navigate_to_screen`** now lists that screen's option ids inline in its
  reply, so they are in context when the model lands on the topic.
- **`record_intake_details(screen, details, selections)`** — a third flat
  string carrying option ids. Optional. `CallProgress.record_selections`
  allowlists every id exactly as `SCREEN_FIELDS` allowlists field names; a
  field whose ids are all unknown is dropped rather than sent empty.
- **`form_prefill` and `connected`** carry `selections` alongside `fields`, and
  `Session.collected_selections` persists them so a reconnect restores a ticked
  card rather than re-deriving it. Withheld from the summarizer, which reads
  `collected_details` because the patient's words are the record.
- **`contracts/intake-field-options.json`** — generated by
  `scripts/export_field_options.py`, asserted from both codebases so the
  vocabulary cannot drift silently.
- **Frontend `prefillMatching.ts`** rewritten as the fallback: token-boundary
  matching, a per-option alias table, and a negation guard with clause-break
  scoping. `matchBoolean` reads phrases rather than bare words.

Nothing added a network round trip or a second model — the ids ride the same
tool call and the same frame, so prefill still appears as the patient speaks.
`selections` being optional is what makes it safe to ask a speech-to-speech
model for structure mid-turn: where it declines, the frontend matches the words
and the behaviour is exactly what it was before.

---

## 2026-09-09 — The live call, and the report it produces

The patient app and the live agent were wired together end-to-end; the full
reasoning is in `../../docs/INTEGRATION.md` §6a-6c. What changed here, and why:

### The call

- **`app.api.ws.intake`** — one WebSocket per session, single-writer discipline
  via `UiEventBus`, resume from `INTERRUPTED` on a persisted `last_screen`.
- **Nova Sonic does not open a conversation and text alone does not drive
  generation.** Both cost a live call before they were understood: the route
  injects an opening system turn, and `BrowserInput` fills audio gaps with
  silence so a typed answer produces a spoken reply. Measured: 0 response
  chunks text-only, 94 with the silence gap.
- **`BIDI_MODEL_ID` must be a bare model id.**
  `InvokeModelWithBidirectionalStream` rejects an application-inference-profile
  ARN. Warned about at boot rather than discovered mid-call.
- **AWS credentials come from this service's own configuration**
  (`app.core.aws`), never boto3's ambient chain. `Settings` had not declared
  the fields, so `.env` values were dropped and a stale `ASIA…` key from the
  host's credentials file was used instead, producing a 403 mid-call.
- **Consent moved from the socket gate to the tool boundary.** The patient has
  to hear the greeting before being asked, so the socket opens first and every
  collection tool refuses until consent is on record.

### Navigation

- **`navigate_to_screen` enforces the order.** A forward jump past a screen is
  refused with the screen that has to come next; a move back to a covered
  screen stays allowed. It was advisory before, and the agent ran a topic ahead
  of what the patient was reading.
- **`record_intake_details` no longer catches up silently** — the skip is
  logged as `intake_navigation_skipped` and the reply tells the model to
  navigate first.

### The report

- **The summarizer is asked for the question-and-answer record.** It never was,
  so `findings` came back empty from a call with sixteen answered questions and
  the PDF's detail section was omitted entirely. Re-run on a stored call:
  0 findings → 16.
- **`Session.collected_details` goes in alongside the transcript** as
  corroboration, since live transcription drops words. The transcript still
  outranks it — it is the only place a later correction appears.
- **`report.category` is optional.** Required, it forced a routine checkup to
  be labelled `blood_pressure`; the PDF now prints `GENERAL INTAKE`.
- **`CalendarDelivery` picks the Google credential that can actually write.**
  Reports were rendered, uploaded, and linked to nobody: delivery used the
  service account whose key file is not installed, while the event had been
  created under the doctor's own OAuth grant. Now the doctor's grant is tried
  first, the service account second, and a `calendar_delivery_unavailable`
  warning is logged when neither works.

### Operability

- **`app.core.stream_noise`** filters awscrt's two post-completion teardown
  artefacts out of the log, by exception *and* originating module, so every
  finished call no longer ends in four library tracebacks.

---

## 2026-09-08 — Session identity end-to-end

Made the patient app able to talk to this service at all, and replaced the
URL-borne credential with a cookie. Verified in a browser against real
MongoDB Atlas, not only in tests.

### Security — the credential model changed

```
link ?token=… ──POST /sessions/{id}/attach──▶ HttpOnly cookie ──▶ every REST route
                                                    └──POST /ws-ticket──▶ single-use ticket ──▶ WS
```

| Change | Why |
|---|---|
| Tokens are **purpose-bound** (`intake` / `session` / `ws` signed into the payload) | The 48h link token could otherwise open the WebSocket directly, making the short-lived ticket pointless |
| `POST /sessions/{id}/attach` — the only route accepting `?token=` | A bearer credential for PHI should be spent once, not presented on every request from browser history, `Referer` headers and access logs |
| Session cookie is `HttpOnly` + `SameSite`, TTL 2h | Page JavaScript cannot read it, so an XSS on the patient app cannot exfiltrate the session |
| WebSocket auth is a **single-use ticket**, ~60s TTL, spent on connect | A browser cannot set headers on a WS handshake, so the credential must sit in the URL — a ticket makes that exposure worth almost nothing |
| New `ws_tickets` collection as a spend-once ledger | Signatures are stateless; without stored state a ticket replays until it expires |
| Every other patient route moved from `?token=` to the cookie | Uniform auth, and a cookie minted for one session is rejected on another |

New: `repositories/ws_ticket_repository.py`. Reworked: `core/security.py`
(scoped tokens), `api/deps.py` (`require_session_cookie`), `core/config.py`
(cookie/ticket settings), `core/exceptions.py` (`SessionAuthError`,
`WsTicketInvalidError`).

### Frontend could not reach the API at all

- **Added `CORSMiddleware`** with credentials, allow-listed from
  `ALLOWED_ORIGINS`, falling back to Vite's dev origins — never `*`, which is
  illegal alongside credentials and would work nowhere.
- **Fixed `build_intake_url`**: it emitted `/intake/{id}` while the patient app
  routes `/prescreen/:sessionId`. Every link this service minted 404'd in the
  browser.

### New endpoints

| Route | Purpose |
|---|---|
| `POST /sessions/{id}/attach?token=…` | Exchange link token for cookie; returns context; marks `STARTED` |
| `GET /sessions/{id}/context` | Patient + appointment detail (PHI; behind the cookie, not pollable) |
| `POST /sessions/{id}/ws-ticket` | Mint one single-use WebSocket ticket |

`GET /sessions/{id}` stays the PHI-free pollable status route. `attach` also
gives `STARTED` its first writer — `start_call()` existed but nothing called it.

### Safety escalation removed

Product decision (see `../../docs/INTEGRATION.md` §4 D4): assessing urgency is
triage, which this service is not licensed to perform. Deleted the
`escalate_safety_concern` tool, `SAFETY_ESCALATED` state, `SafetyEscalation`
model, its repository write, `escalate_for_safety`, the prompt's safety-gate
section, and the urgent-prefix Calendar path.

Replaced by a **standing emergency notice**: stated once at the start of every
call, to every patient, never in response to anything they say — a notice, not
triage. The tradeoff is recorded in the integration doc rather than dropped
silently.

### Tooling

- `make dev-link` (`scripts/dev_intake_link.py`) mints a real session and prints
  a working intake URL, so frontend work runs against a real session instead of
  a `crypto.randomUUID()` that names nothing.
- `docs/API_REFERENCE.md` — the full wire contract.

### Two bugs the new tests caught, in code written the same day

- WebSocket replay protection depended entirely on a unique index the test app
  never created — against a store not enforcing it, every ticket replayed freely
  with nothing failing visibly. Now an atomic upsert that holds either way.
- Two frontend screens seeded `useState` from the asynchronously-arriving
  session, so they stayed empty for the patient. Now derived and keyed.

### Patient app (`../../prescreening-agent-ui`)

Attach-then-cookie handshake with Zod validation at the boundary; `AppBoot`
branches on real session status (connecting / failed-per-error-kind / distinct
`declined`, `expired`, completed screens) instead of a 1600 ms timer; consent
posts to the server and blocks progress on the response; the hardcoded
`SAMPLE_APPOINTMENT` is gone from the schedule screen.

### Verified

Preflight 200 → attach 200 → token stripped from URL → consent 200 →
`in_progress` + timestamped consent in MongoDB. `document.cookie`,
`localStorage`, `sessionStorage` all empty. Refresh recovers from the cookie
alone. Session A's cookie is rejected on session B. Replayed ticket →
`intake_ws_rejected_replayed_ticket`; intake token used as a ticket →
`intake_ws_rejected_bad_ticket`.

Backend 111 tests, ruff + ty clean. Frontend 105 tests, ESLint + tsc clean.

### Known issues found while verifying

- A configured-but-missing `GOOGLE_SERVICE_ACCOUNT_FILE` **kills startup**
  rather than degrading to the documented no-op.
- Binding uvicorn to `127.0.0.1` is not enough on Windows — browsers resolve
  `localhost` to IPv6 and get `ERR_CONNECTION_REFUSED`. Use `--host 0.0.0.0`,
  as `make dev` already does.

### Not included here

Concurrent work on doctor registration, the booking API, Google OAuth and
availability (`api/v1/booking.py`, `api/v1/doctors.py`,
`integrations/google_oauth.py`, `services/availability_service.py` and their
tests) landed in this same working tree from another workstream. It is not part
of this entry. Combined suite: 142 passing.

Still open: live voice (WS event schema is pinned by nothing yet),
resume-where-you-left-off, agent-driven screen routing, and merging
`origin/wip-basecode-santhosh`, which already carries in-call
cancel/reschedule with slot management.

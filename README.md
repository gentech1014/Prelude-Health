# Prelude Health API

An AI agent that talks to a patient before their appointment, has a short
adaptive conversation about why they are coming in, and hands the physician a
structured summary to read before the consultation starts.

Built on the [Strands Agents SDK](https://strandsagents.com) with **Amazon Nova 2
Sonic** for the live voice call and **Claude Sonnet** on Bedrock for the written
summary.

---

## The problem

A physician typically opens a consultation with nothing but a one-line booking
reason. The first minutes go to reconstructing a story the patient already
knows — how long, how bad, what else, what changed. That time is spent
gathering, not treating.

This service moves the gathering to *before* the appointment. The patient gets a
link after booking, talks to an agent for a few minutes, and the physician finds
a structured summary waiting in the appointment context.

## The boundary

**The agent collects and organizes information. It does not practise medicine.**

It never diagnoses, never triages, never recommends or comments on treatment, and
never names a condition back to the patient as a conclusion. If asked what is
wrong or whether something is serious, it says plainly that it cannot advise and
that the doctor will go through it at the appointment.

This is enforced in three places, not one: the live agent's system prompt
(`agents/bidi/prompts.py`), the summarizer's instructions
(`agents/summarizer/prompts.py`), and the report schema itself, which has no
field capable of holding an assessment.

---

## Architecture

Two agents, split by latency budget — this is the central design decision.

```text
                  HOT PATH — the patient is waiting
                      budget: sub-second per turn

  +---------+  audio  +----------------------+  turns  +-----------+
  | Patient |<------->| Live agent           |-------->| MongoDB   |
  | browser |   WS    | BidiAgent            |  live   | session   |
  +---------+         | Nova 2 Sonic         |         | history   |
                      | + 7 tools            |         +-----+-----+
                      +----------------------+               |
                                                             | reads
   --- call ends: end_session -> COMPLETED, or               |
       escalate_safety_concern -> SAFETY_ESCALATED ---        |
                                                             v
                  COLD PATH — nobody is waiting        +--------------+
                      budget: seconds, retryable       | Summarizer   |
                                                       | Claude Sonnet|
                                                       +------+-------+
                                                              |
                                                     structured report
                                                     (findings + gaps)
```

| | Live agent | Summarization agent |
|---|---|---|
| Trigger | Patient opens the intake link | Session reaches `COMPLETED` |
| Interface | Bidirectional audio stream | Ordinary request/response |
| Latency budget | Sub-second per turn | ~4-5 seconds |
| On failure | Degrade audibly, keep the call alive | Land in `SUMMARY_FAILED`, keep the transcript |
| State | Long-lived connection | Stateless, one shot, fresh instance per call |
| Model | `amazon.nova-2-sonic-v1:0` | `global.anthropic.claude-sonnet-4-6` |

Nova 2 Sonic is speech-to-speech only, which is why the summarizer runs on a
separate text model rather than reusing the same one.

The two agents never talk to each other. **MongoDB is the handoff.**

---

## How a call flows

1. **Booking confirmed** — `POST /api/v1/webhooks/booking-confirmed` creates one
   session, keyed by `session_id`. Idempotent by `appointment_id`, so a retried
   webhook returns the existing session instead of creating a second one.
2. **Link delivered** — a signed, expiring token is minted and sent to the
   patient. The token binds to that one session; a `session_id` alone opens
   nothing.
3. **Consent** — `POST /api/v1/sessions/{id}/consent`. The live call is gated on
   an affirmative record; a decline ends the journey at `DECLINED`.
4. **The call** — `WS /ws/intake/{session_id}?token=...`. The agent greets the
   patient by name, then confirms the reason they booked under and asks whether
   that is still what the call should be about. From their answer it fixes the
   reason being screened, and writes up to ten questions for it on the spot —
   one at a time, each from the last answer — then asks
   about current medications, medication allergies, and what the patient
   wants from today's visit. It does not read anything back — the written
   report is the deliverable, not a spoken recap.
5. **Farewell, then hang up** — the agent says goodbye *out loud*, then calls
   `end_session`. **Or, at any point a safety answer is concerning** — the
   agent says, out loud, to seek emergency care right now, then calls
   `escalate_safety_concern` instead; the call ends there, and no further
   questions are asked.
6. **Summarization** — the session moves `COMPLETED` -> `SUMMARIZING` ->
   `SUMMARY_READY`, and the physician reads the report. A safety-escalated
   session skips this entirely and moves straight to `SAFETY_ESCALATED` — the
   transcript is too short by then for a summary to mean anything.
7. **Calendar delivery** *(if booked through the mock booking platform)* — the
   report link, and later the supporting-document and recording links if
   uploaded, are each appended to the doctor's Google Calendar event for
   that appointment as they become available. Best-effort: never blocks or
   fails the artifact it is delivering. A safety escalation instead prefixes
   the event **title** with an urgent warning and appends a plain-text
   description of what triggered it — see [Safety escalation](#safety-escalation).

An abandoned call is deliberately different from a completed one: if the socket
drops, whatever reached Mongo stands, the session stays `IN_PROGRESS`, and **no
report is generated** — so the physician view can tell the two apart rather than
showing an empty summary as though it were finished.

## Silent category routing

A pre-screening is organized around **the reason the patient booked under** —
the same seven options the booking screen offers: diabetes, blood pressure,
heart, lung, stomach, general checkup, and *I am not sure*.

The call opens by confirming it: *"You've booked this appointment about your
breathing. Shall we go through some questions about that, or is there something
else you're dealing with right now?"* If they confirm, that is what gets
screened. If they say it is something else, that is what gets screened instead.
If they raise a second thing alongside it, both do. **It never says the reason
aloud as a category and never asks the patient to choose one from a list.**

That confirmation is the fix for a real failure. The booked visit type used to
be flattened into a sentence and then discarded, the prompt told the agent the
booking was "often vague or wrong", and the screening vocabulary had five
clinical areas and nothing for a routine checkup. So a checkup or an unplaceable
complaint — 23% of bookings in this deployment — reached the screening with no
category, and the agent picked whichever seeded set sounded closest to
unexplained pain. Every one of those calls became an abdominal-pain interview.

### The questions are written during the call

There is no fixed list any more. `start_prescreening` returns a **coverage
brief** for that reason — what a screening for it needs to end up establishing —
and the agent writes each question itself, for the patient in front of it, from
that brief and from the answer it just heard. A follow-up the conversation
obviously calls for is now expressible; under the old design the agent could
only re-word one of eighteen canned lines.

What the model writes is checked before the patient can see it: nothing stacked
into two questions, nothing over the screen's length, no "rate it 1 to 10", and
nothing that asks what an earlier question in this call already asked — matched
on what a question asks rather than on how it was phrased, since re-wording
would otherwise be a free pass to repeat one.

The symptom screen is then driven one question at a time. The agent puts a
question on it, waits, records the answer in the patient's own words, and the
box clears for the next — so the patient reads exactly what they were just
asked, and never a form of six fields they have to work out for themselves. Both
the ten-question budget and the two-area limit are enforced in the tools, not
requested in the prompt.

### The bank learns

Questions still live in Mongo, but as a *reference corpus* rather than a script.
Two directions:

- **In**, at the start of a screening: the questions most often asked for this
  reason before are handed to the agent as material it may draw on, explicitly
  labelled as reference and not as a list to work through. Loaded into process
  memory **once at boot** — a database read mid-call is exactly the kind of
  blocking work that ruins a voice conversation.
- **Out**, once the call has ended and the patient has hung up: every question
  that was asked *and answered* is merged back in under that reason,
  deduplicated by normalized text with an `asked_count`. A reason nobody has
  booked yet starts empty and fills itself from real calls.

The reference set is cut by count, so hand-written seeds are only a cold-start
floor: as soon as real calls produce questions that work for a reason, those
outrank them.

## The seven tools
## The seven tools

| Tool | Called when | Does |
|---|---|---|
| `start_prescreening` | Once the patient has confirmed the booked reason, or named another; twice at most | Fixes the reason being screened and returns its coverage brief plus reference questions |
| `ask_symptom_question` | Immediately before speaking each screening question | Checks the question the agent just wrote, then puts it on the patient's screen |
| `record_symptom_answer` | As soon as the answer lands | Records it against the live question and clears the screen for the next |
| `request_document_upload` | After the questions are exhausted | Signals the browser to open a file picker — never touches the file |
| `end_session` | After the agent has said goodbye out loud | Flags the loop for graceful shutdown |
| `escalate_safety_concern` | After the agent has said the emergency-care line out loud | Flags the loop to stop *and* carries the trigger question + patient's answer out to the WebSocket handler |
| `cancel_appointment` | After the patient confirms they want to cancel | Frees the slot, cancels the session — see [Scheduling](#scheduling) |
| `find_earlier_appointment_slots` | Near the end of the call | Returns pre-formatted local-time offers for earlier slots |
| `reschedule_appointment` | Only after the patient agrees to move | Books the new slot, releases the old one |

Documents are handled entirely out-of-band. Nova Sonic is speech-to-speech and
accepts no image input on the live connection, so the tool only emits a
`tool_use_stream` event the frontend reacts to; the file goes straight to object
storage, keyed by `session_id`.

Once the upload completes, `SessionService.attach_document` best-effort
describes the file's **type only** — e.g. "This appears to be an X-ray
image." — using the same Claude model as summarization, this time with an
image/document content block (Bedrock's Converse API, confirmed live
against both a real PDF and a real image). The prompt is deliberately
narrow: modality only, never a finding or anything actually visible inside
the file — that line is enforced by instruction, not by any code-level
filter. The description and a link to the file appear together in the
report's **Attached Documents** section. An unsupported format or any
model failure both degrade to a fixed fallback string; this never blocks
the document link itself from being delivered.

## Safety escalation

Before any symptom-specific questions, the agent asks a short, fixed set of
red-flag questions — chest pain/breathing difficulty, thoughts of self-harm,
sudden severe/neurological symptoms, severe bleeding or allergic reaction. A
concerning answer, at any point in the call (not only during that fixed set),
stops the normal flow immediately: no report is generated, and the session
moves straight to `SAFETY_ESCALATED`.

Mechanically, `escalate_safety_concern` writes onto
`invocation_state["request_state"]` — the same dict the bidi loop already
checks for `end_session`'s `stop_event_loop` flag — so the WebSocket handler
can read the trigger question and the patient's response back out after
`agent.run()` returns, with no mid-call database write and no added latency.

Physician alerting reuses the only channel that's real today: Google Calendar.
The event **title** gets a `🚨 URGENT —` prefix (visible on the calendar grid
without opening the event) and the **description** gets the trigger question,
the patient's own words, and a note that no full report was generated.

**This is a first pass, not a clinically validated protocol.** The exact
red-flag questions and escalation wording need a real physician's review
before any real deployment — this change builds the mechanism, not clinical
sign-off. Detection is also LLM judgment only: there is no deterministic
keyword backstop, so a model that fails to recognize a red flag produces a
silent false negative.

## Scheduling

The live agent can, mid-call, cancel the appointment or move it to an earlier
slot someone else just freed — the same `SchedulingService` the patient-facing
`GET/POST /sessions/{id}/appointment...` routes call, so the phone call and the
frontend card can never drift apart.

**Slots are the source of truth**, not Google Calendar — a new `appointment_slots`
collection, one document per bookable half-hour. Every state change (`FREE` →
`HELD` → `BOOKED`, and back) is a single guarded `update_one`, never a
transaction: MongoDB already makes one document atomic with no transaction,
and `mongomock` — the library every test in this suite runs on — cannot open
a client session at all, so a transactional design would ship with zero test
coverage. See `app.repositories.slot_repository`'s module docstring for the
exact (and non-obvious) reason a `find_one_and_update` + `{"_id": 0}`
projection is unsafe here even though `SessionRepository` uses it everywhere
else.

**Availability is pulled, not pushed.** `find_earlier_appointment_slots` runs
one indexed query at the moment the agent considers the offer — not a
MongoDB change stream (unsupported by `mongomock` entirely, and provably no
fresher than a read taken at the same instant anyway). The accept step is a
second guarded write that fails cleanly if someone else won the race in the
meantime; the agent says "that one just went" rather than the call breaking.

**Nothing is destroyed by a crash.** A cancel or reschedule that dies
mid-sequence can only ever leave *lost inventory* (a slot nobody holds,
recoverable) — never two sessions believing they hold the same slot. A
lease-guarded background task (started in the FastAPI lifespan, no scheduler
dependency added) reclaims a slot an abandoned call left `BOOKED` forever,
tidies an expired reschedule hold, and finally gives the long-dormant
`SessionState.EXPIRED` a writer — the same root cause, one fix. A separate
`scheduling_events` collection is an append-only audit trail (who cancelled,
who won a REST-vs-voice-tool race, and when), deliberately not an array
embedded on the slot: slot storage hygiene and audit retention are different
lifetimes.

**A real, shipped timezone bug was fixed as part of this work.** The agent
used to speak the raw UTC hour as if it were the patient's local time — every
test fixture happened to sit at UTC+0, which is why nothing caught it. Every
appointment time now carries a resolved IANA zone (`Doctor.timezone`, else
`CLINIC_TIMEZONE`), stamped once at booking, converted at exactly four render
sites (the spoken prompt, the physician PDF, the Calendar event, and any
slot offered mid-call) — never re-derived, never guessed.

Seed a slot grid with `make seed-slots` (after `make seed-doctors`); trigger
a reconciliation pass by hand with `make sweep-slots`.

---

## Booking screen and doctor self-registration

A responsive booking surface at `/book` in the frontend, backed by the
`/api/v1/booking/*` read endpoints. Everything it renders is live: the visit
types are this service's own `BookingVisitType` vocabulary (the same seven the
live agent screens for and the question bank is keyed by), the providers are
the `doctors` collection, and the times are computed per doctor. Submitting
still goes through `POST /mock-booking/appointments`, which owns the
simulated scheduling platform — so booking on this screen is what creates
the pre-screening session and its intake link, exactly as a real platform's
webhook would.

**Visit types are a booking vocabulary, not a clinical one.**
`BookingVisitType` is a deliberate superset of `SymptomCategory`: the five
conditions map 1:1 onto a category, and two more -- `general_checkup` and
`not_sure` -- map to nothing. They exist because a condition list cannot
answer "I want a routine checkup" or "something is wrong and I do not know
what", and a patient who cannot find themselves in the list picks the closest
wrong option, which is worse than no pre-scoping at all.

They are **not** new `SymptomCategory` members, on purpose. That enum is the
clinical vocabulary: `QuestionBankService.preload` refuses to start the server
if any member lacks a seeded question set, and the live agent silently routes
to one of exactly those five. A booking-screen convenience must not change
either. An unscoped visit type books with no category and offers every
provider (there is no condition to match specialties against); the agent then
infers the category from the patient's own words during the call, which is
what it does regardless of what was picked at booking.

**How a slot is decided** (`app.services.availability_service`). Clinic
opening hours generate the candidates; nothing outside `CLINIC_OPEN_HOUR` /
`CLINIC_CLOSE_HOUR` / `CLINIC_OPEN_WEEKDAYS` is ever offered. Those
candidates are then filtered by what is already taken:

- the doctor's real Google Calendar free/busy, when they have connected
  their own account (`AvailabilitySource.CALENDAR`);
- every appointment already booked through this service, always — which is
  what prevents double-booking a doctor who has *not* connected Google
  (`AvailabilitySource.CLINIC_HOURS`).

Each day is labelled with which of the two it was, and the booking UI says
so: an unverified clinic-hours slot is never presented as calendar-confirmed.
The slot is re-checked server-side at submit time and answers `409` if
someone took it first, so a stale page cannot double-book.

**Provider portraits.** `Doctor.photo_url` is served on the provider card and
is optional — absent means the UI falls back to initials rather than showing a
broken image. A doctor who registers through the Google flow gets their own
account picture captured from the OpenID `picture` claim, so there is nothing
to upload; `make seed-doctors` seeds stock Unsplash portraits as demo
placeholders, which must be replaced with real clinician photos (or blanked)
before patients see them.

**Doctor self-registration** (`app.api.v1.doctors`). A doctor opens the
booking screen's settings menu, enters their profile, and is redirected to
Google's consent screen for read/write access to their own calendar. The
callback stores the refresh token on their `doctors` document and points
`google_calendar_id` at the granted account. From then on their availability
is read from their real calendar and their appointments are written to it as
them — replacing the manual "share your calendar with the service account"
step `GOOGLE_SERVICE_ACCOUNT_FILE` requires. The `state` is single-use (a
TTL'd `doctor_registrations` document), so a callback URL cannot be replayed,
and the refresh token never appears in any API response or redirect URL.

Needs `GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET`; without them
the registration route answers `503` and the UI reports the feature as
unavailable rather than starting a flow that cannot finish. Service-account
delivery is unaffected either way.

> **Note on the "Scheduling" section above:** it describes a slot-inventory
> design (`appointment_slots`, `SchedulingService`, `Doctor.timezone`,
> `make seed-slots`) that is **not implemented anywhere in this tree** — no
> such module, collection, or make target exists. Availability is computed
> on read as described here, against one clinic-wide `CLINIC_TIMEZONE`.
> Reconcile the two before treating either as settled.

---

## Data model

Two collections.

**`session_chat_history`** — one document per appointment, keyed by `session_id`
(never by patient name: the same patient returns, and a name is not a safe key).
Patient identity is a field inside the document.

```json
{
  "session_id": "sess_8f2c1a",
  "appointment_id": "appt_4471",
  "patient": {
    "name": "Asha Rao",
    "patient_id": "pt_2290",
    "date_of_birth": "1990-05-14",
    "sex": "female"
  },
  "physician": "Dr. Mehta",
  "appointment_datetime": "2026-09-03T09:30:00Z",
  "booking_reason": "Short of breath on stairs",
  "status": "summary_ready",
  "consent": { "given": true, "recorded_at": "2026-09-01T09:11:00Z" },
  "turns": [
    { "role": "assistant", "text": "Hi Asha, this is a short pre-visit call...", "ts": "..." },
    { "role": "user", "text": "I've been out of breath climbing stairs.", "ts": "..." }
  ],
  "document_uploaded": true,
  "document_ref": "documents/sess_8f2c1a/1a2b3c4d",
  "document_summary": "This appears to be an X-ray image.",
  "report": {
    "chief_concern": "Shortness of breath on exertion",
    "category": "lung",
    "findings": [ { "question": "...", "answer": "...", "flagged": false } ],
    "medications": [ { "name": "Albuterol", "dose": null, "frequency": "as needed", "reason": null } ],
    "allergies": [ { "allergen": "Penicillin", "reaction": "Rash" } ],
    "patient_goal": "Understand what's causing the breathlessness",
    "gaps": ["Smoking history not confirmed"]
  },
  "safety_escalation": null,
  "summary_error": null,
  "video_ref": "s3://.../sess_8f2c1a.webm",
  "calendar_event_id": "evt_abc123"
}
```

**`prescreening_question_bank`** — one document per question, keyed
`category::normalized-text`, accumulating across calls.

```json
{
  "_id": "lung::how long have you had this",
  "category": "lung",
  "key": "how long have you had this",
  "text": "How long have you had this?",
  "asked_count": 14,
  "source": "seed",
  "first_asked_at": "2026-09-01T09:12:04Z",
  "last_asked_at": "2026-09-09T16:31:55Z"
}
```

Replaces `questions_bank`, which held one document per category with a plain
list of strings — a shape with nowhere to record how often a question had
earned its place. Run `make seed-db` once to populate the new collection; it
inserts only what is missing, so it is safe against a live environment.

**`doctors`** — one document per physician, mapping to their Google Calendar. Seeded
by `scripts/seed_doctors.py`; the doctor must share that calendar with the
service account's email (Editor access) before events can be created on it.

```json
{ "name": "Dr. Mehta", "google_calendar_id": "dr-mehta@example.com" }
```

### Why `gaps` exists

A silent absence and an asked-but-declined answer are different clinical
signals. Anything the patient could not answer, did not know, declined, or was
never asked lands in `gaps` — never as a guess. **A visible gap is more useful to
a physician than an invented answer.**

The same rule governs `medications`, `allergies`, and `patient_goal`: an empty
`medications`/`allergies` list means the patient said they have none — if they
were never asked or declined to answer, that goes in `gaps` instead, not a
silently empty list that reads as a confirmed negative.

## Session state machine

Fourteen states. The happy-path demo never visits `DECLINED`, `EXPIRED`,
`SUMMARY_FAILED`, `SAFETY_ESCALATED`, or `CANCELLED`, but all five are
implemented rather than stubbed, so a real session that hits one degrades
honestly.
Fourteen states. The happy-path demo never visits `DECLINED`, `EXPIRED`,
`SUMMARY_FAILED`, `SAFETY_ESCALATED`, or `CANCELLED`, but all five are
implemented rather than stubbed, so a real session that hits one degrades
honestly.

```text
booking_created -> ai_link_ready -> notification_sent -> started -> in_progress
    -> completed -> summarizing -> summary_ready -> video_ready

off-ramps:  declined          (consent refused)
            expired           (link window closed unopened, or the sweeper
                               reclaimed an abandoned call's slot)
            expired           (link window closed unopened, or the sweeper
                               reclaimed an abandoned call's slot)
            summary_failed    (summarization produced nothing)
            safety_escalated  (safety gate stopped the call early)
            cancelled         (patient cancelled, by voice or the REST button)
            cancelled         (patient cancelled, by voice or the REST button)
```

`summarizing` and `summary_failed` exist so the physician view can distinguish
"being generated" from "failed" instead of rendering a blank report as complete.
When a summary fails, `summary_error` records why. `safety_escalated` and
`cancelled` are both reached directly from `in_progress` — neither ever passes
through `completed`/`summarizing`, since summarization is deliberately skipped
for both (see [Safety escalation](#safety-escalation) and
[Scheduling](#scheduling)). `expired` finally has a writer too, as of the
scheduling feature's sweeper — previously defined but never written by any
code path.
When a summary fails, `summary_error` records why. `safety_escalated` and
`cancelled` are both reached directly from `in_progress` — neither ever passes
through `completed`/`summarizing`, since summarization is deliberately skipped
for both (see [Safety escalation](#safety-escalation) and
[Scheduling](#scheduling)). `expired` finally has a writer too, as of the
scheduling feature's sweeper — previously defined but never written by any
code path.

---

## API

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/api/v1/booking/visit-types` | none | The 7 `BookingVisitType` options (5 conditions + general checkup + not sure) |
| `GET` | `/api/v1/booking/providers?category=…` | none | Bookable doctors, filtered by visit type |
| `GET` | `/api/v1/booking/providers/{doctor_id}/availability` | none | Bookable slots per day (calendar free/busy, else clinic hours) |
| `POST` | `/api/v1/mock-booking/appointments` | none (internal mock) | Stands in for a real scheduling platform: re-checks the slot, creates the Calendar event, then the session |
| `POST` | `/api/v1/doctors/registration` | none | Start doctor self-registration; returns the Google consent URL |
| `GET` | `/api/v1/doctors/google/callback` | OAuth `state` (single-use) | Google's redirect target; stores the doctor's calendar grant |
| `POST` | `/api/v1/webhooks/booking-confirmed` | `X-Signature` (HMAC) | Booking platform -> create session, send link |
| `GET` | `/api/v1/sessions/{session_id}` | intake token | Session status for the patient page |
| `POST` | `/api/v1/sessions/{session_id}/consent` | intake token | Record consent; gates the live call |
| `GET` | `/api/v1/sessions/{session_id}/appointment` | intake token | The appointment card: doctor, time, duration, reason, status |
| `POST` | `/api/v1/sessions/{session_id}/appointment/cancel` | intake token | Cancel the appointment, freeing its slot — same `SchedulingService` the voice tool calls |
| `GET` | `/api/v1/sessions/{session_id}/appointment/earlier-slots` | intake token | Earlier slots for this doctor, mirroring what the voice agent can offer |
| `POST` | `/api/v1/sessions/{session_id}/appointment/reschedule` | intake token | Move to an earlier slot; `409` if it was just taken |
| `POST` | `/api/v1/sessions/{session_id}/documents/upload-url` | intake token | Presigned URL for a supporting document |
| `POST` | `/api/v1/sessions/{session_id}/documents/complete` | intake token | Attach the document; best-effort type description, then calendar delivery |
| `POST` | `/api/v1/sessions/{session_id}/recording/upload-url` | intake token | Presigned URL for the screen recording |
| `POST` | `/api/v1/sessions/{session_id}/recording/complete` | intake token | Attach the recording; triggers calendar delivery |
| `GET` | `/api/v1/sessions/{session_id}/report` | `X-API-Key` | The structured physician report (the one PHI-bearing route) |
| `WS` | `/ws/intake/{session_id}?token=...` | intake token | The live intake call |
| `GET` | `/healthz` | none | Liveness probe (deliberately does not touch Mongo) |

---

## Prerequisites

- **Python 3.12+** — a hard requirement of Nova Sonic's experimental AWS SDK
  dependency, not a preference.
- **A running MongoDB.**
- **An AWS account with Bedrock model access granted** for both Nova 2 Sonic and
  Claude Sonnet, in a region that hosts Nova 2 Sonic:
  `us-east-1`, `us-west-2`, `eu-north-1`, `ap-northeast-1`.
  **Nova 2 Sonic supports no cross-region inference**, so this choice is binding
  and is validated at startup.
- **IAM permissions:**
  - `bedrock:InvokeModelWithBidirectionalStream` — the live agent
  - `bedrock:InvokeModel` and `bedrock:InvokeModelWithResponseStream` — the summarizer

## Quick start

```bash
make install
```

```bash
cp .env.example .env
```

Generate the required signing secret and put it in `.env`:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Then load the question bank, doctor -> calendar mapping, and a working-hours
slot grid, and start the server:

```bash
make seed-db
make seed-doctors
make seed-slots
```

```bash
make dev
```

## Configuration

Environment-driven (12-factor). AWS credentials resolve through the standard
boto3 chain — env vars, `~/.aws/config`, or an instance role.

| Variable | Default | Notes |
|---|---|---|
| `INTAKE_LINK_SECRET` | **required, min 32 chars** | HMAC key for patient links. No default on purpose — see below. |
| `BOOKING_WEBHOOK_SECRET` | **required, min 32 chars** | HMAC key verifying the inbound booking webhook. Same reasoning. |
| `PHYSICIAN_API_KEY` | **required, min 32 chars** | `X-API-Key` guarding the one PHI-bearing route, `GET .../report`. |
| `AWS_REGION` | `us-east-1` | Must be one of the four Nova Sonic regions; validated at startup. |
| `MONGO_URI` | `mongodb://localhost:27017` | |
| `MONGO_DB_NAME` | `prescreening` | |
| `BIDI_MODEL_ID` | `amazon.nova-2-sonic-v1:0` | Also accepts a full application-inference-profile ARN; its region must match `AWS_REGION`. |
| `BIDI_VOICE` | `matthew` | Also `tiffany`, `kiara`, etc.; see the Nova Sonic voice list. |
| `BIDI_ENDPOINTING_SENSITIVITY` | `MEDIUM` | `HIGH` / `MEDIUM` / `LOW`. Nova Sonic **v2 only**. |
| `SUMMARY_MODEL_ID` | `global.anthropic.claude-sonnet-4-6` | Also accepts a full application-inference-profile ARN. |
| `SUMMARY_MODEL_REGION` | *(empty)* | Region to invoke `SUMMARY_MODEL_ID` in, when it's an ARN created in a different region than `AWS_REGION`. Falls back to `AWS_REGION` when blank. |
| `INTAKE_LINK_TTL_SECONDS` | `172800` | 48h — covers the pre-appointment window. |
| `PATIENT_APP_BASE_URL` | *(empty)* | Base of the link sent to patients. |
| `CLINIC_TIMEZONE` | `UTC` (set to `Asia/Kolkata` in `.env`) | IANA zone the clinic's opening hours are expressed in. Bookable slots are generated in it and served with an offset. There is no per-doctor override in the code today. |
| `CLINIC_OPEN_HOUR` / `CLINIC_CLOSE_HOUR` | `9` / `17` | Opening hours, in `CLINIC_TIMEZONE`. Nothing outside them is ever bookable. |
| `CLINIC_SLOT_MINUTES` | `30` | Length of one bookable slot. |
| `CLINIC_OPEN_WEEKDAYS` | `0,1,2,3,4` | `datetime.weekday()` values the clinic is open on (Monday=0). |
| `BOOKING_HORIZON_DAYS` | `14` | How far ahead the booking screen may offer slots. |
| `BOOKING_LEAD_TIME_MINUTES` | `60` | Minimum notice before a slot may be booked. |
| `GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET` | *(empty)* | OAuth client for doctor self-registration. Blank -> `POST /doctors/registration` answers `503`. |
| `GOOGLE_OAUTH_REDIRECT_URI` | `http://localhost:8000/api/v1/doctors/google/callback` | Must be registered verbatim on the OAuth client in Google Cloud Console. |
| `DOCTOR_REGISTRATION_TTL_SECONDS` | `900` | How long a pending registration (and its single-use OAuth `state`) survives. |
| `BOOKING_APP_BASE_URL` | `http://localhost:5173` | Where the OAuth callback redirects the doctor back to (`<base>/book`). |
| `SLOT_DURATION_MINUTES` | `30` | Length of one bookable slot. |
| `RESCHEDULE_MIN_LEAD_MINUTES` | `60` | Never offer a reschedule slot starting sooner than this from now. |
| `RESCHEDULE_OFFER_LIMIT` | `3` | Max earlier slots the agent may mention in one call. |
| `SLOT_HOLD_TTL_SECONDS` | `90` | How long a slot stays `HELD` mid-reschedule before it is bookable again. |
| `ABANDONED_SLOT_RECLAIM_MINUTES` | `15` | A `BOOKED` slot whose call has gone quiet this long is reclaimed by the sweeper. |
| `SLOT_RETENTION_DAYS` | `400` | TTL grace period before a past `FREE`/`CANCELLED` slot is purged. |
| `SCHEDULING_EVENT_RETENTION_DAYS` | `365` | TTL for the audit trail — a separate lifetime from slot retention on purpose. |
| `SWEEP_INTERVAL_SECONDS` | `60` | How often the lifespan sweeper task ticks. |
| `STORAGE_BUCKET_NAME` | *(empty)* | S3 bucket for documents/recordings. Must exist ahead of time, **and must have a CORS rule** — see below. |
| `STORAGE_ACCESS_KEY` / `STORAGE_SECRET_KEY` / `STORAGE_ENDPOINT_URL` | *(empty)* | Leave blank to reuse the AWS credentials above via boto3's default chain. |
| `STORAGE_KMS_KEY_ID` | *(empty)* | KMS key ID/ARN for SSE-KMS on uploads. Blank -> SSE-S3 (`AES256`) instead; still encrypted either way. |
| `ALLOWED_ORIGINS` | *(empty)* | Comma-separated browser Origins allowed to open `/ws/intake/{id}`. Blank -> check skipped (permissive). |
| `GOOGLE_SERVICE_ACCOUNT_FILE` | *(empty)* | Path to a Calendar-scoped service-account JSON key. Blank -> Calendar delivery no-ops. |
| `SECRETS_MANAGER_SECRET_ID` | *(empty)* | Only read when `APP_ENV=production` — see [Security](#security) below. |

### The upload bucket needs CORS

Patients upload documents straight to S3 with a presigned PUT, so the *browser*
talks to the bucket and the bucket has to allow it. A bucket with no CORS
configuration rejects the preflight with `CORSResponse: CORS is not enabled for
this bucket`, which reaches the console as the far less obvious "No
'Access-Control-Allow-Origin' header is present". Apply [`s3-cors.json`](s3-cors.json)
once per bucket, with the origins the patient app is actually served from:

```bash
aws s3api put-bucket-cors --bucket "$STORAGE_BUCKET_NAME" --cors-configuration file://s3-cors.json
```
| `LOG_LEVEL` / `LOG_JSON` | `INFO` / `true` | JSON in deployment, console locally. |

`INTAKE_LINK_SECRET`, `BOOKING_WEBHOOK_SECRET`, and `PHYSICIAN_API_KEY` all have
**no default and a minimum length** for the same reason: an empty secret still
produces a signature or key that "matches," so the thing it is meant to gate
would be wide open. The app refuses to start without all three.

## Security

Every category of data this service handles is encrypted, in transit and at
rest, using transport-level and provider-managed mechanisms only — no
field-level/client-side crypto (it would cost Mongo its query performance and
buys nothing extra here).

**In transit:**
- Backend -> Bedrock, S3, MongoDB Atlas, Google Calendar: already TLS, enforced
  by each provider's own SDK/connection scheme (`mongodb+srv://` implies TLS;
  Atlas rejects plaintext outright). Nothing to configure.
- Browser <-> backend (REST + the live `/ws/intake/{id}` audio/transcript
  WebSocket): this service owns this hop. `Caddyfile` + `docker-compose.yml`
  are prepared (Caddy terminates TLS with automatic ACME certs, and
  `reverse_proxy` proxies WebSocket upgrades natively) but **not yet wired to
  a real domain** — there is no deployment target as of this writing. Fill in
  the real domain in `Caddyfile` and run `docker compose up -d --build` once
  one exists. The Dockerfile's `uvicorn` already runs with
  `--proxy-headers --forwarded-allow-ips=*` so it trusts the proxy's
  `X-Forwarded-Proto`/`X-Forwarded-For` once fronted.
- `ALLOWED_ORIGINS` (see Configuration) restricts which browser Origins can
  open the intake WebSocket at all, once the patient-app frontend's real
  origin is known.
- TLS handshake cost is paid once per WebSocket connection (this is one
  long-lived call, not per audio frame), so none of this adds latency to the
  live call.

**At rest:**
- MongoDB Atlas encrypts everything at rest by default (AES-256, every tier)
  — no code or config needed. `MongoConnection.connect()` warns (does not
  block startup) if `APP_ENV=production` and `MONGO_URI` isn't
  `mongodb+srv://`, since that would mean TLS-in-transit isn't guaranteed.
  Optional: enable Atlas's customer-managed-key (KMS-backed) encryption on
  M10+ tiers via the Atlas console (Project Settings -> Advanced) — a
  disk-level, query-transparent setting, not something this app configures.
- Uploaded documents and the generated report PDF (S3): `ObjectStorageClient.upload`
  requests server-side encryption explicitly — SSE-KMS when `STORAGE_KMS_KEY_ID`
  is set, else SSE-S3. The presigned-PUT path patients would use
  (`generate_upload_url`) does not bake in the same params yet, since no
  upload frontend exists to guarantee it sends matching headers — enable S3
  **bucket default encryption** (a one-time console/`put_bucket_encryption`
  step) to cover that path too, with zero client-side contract required.

**Secrets:** `src/app/core/secrets_loader.py` is inert until `APP_ENV=production`
and `SECRETS_MANAGER_SECRET_ID` are both set — local dev keeps using `.env`
unchanged. When active, it fetches one JSON secret blob from AWS Secrets
Manager at process boot (before `Settings` is constructed) and populates
`os.environ`, including materializing a `GOOGLE_SERVICE_ACCOUNT_JSON` value to
a temp file for `GOOGLE_SERVICE_ACCOUNT_FILE`. Costs one API call at cold
start only. Where the deploy target supports an attached IAM role (ECS task
role, EC2 instance profile), prefer that over static
`STORAGE_ACCESS_KEY`/`STORAGE_SECRET_KEY` in the secret blob —
`ObjectStorageClient` already falls through to boto3's default credential
chain when those are blank.

**Logging:** `notification_service.py` no longer logs the patient's phone
number or the token-bearing intake URL verbatim (only presence/absence).
`core/logging.py` also masks a small denylist of field names
(`intake_url`, `token`, `contact_phone`, `mongo_uri`, and the secret settings)
as defense-in-depth against a future accidental repeat.

**Calendar payload:** report/document/recording links appended to the
doctor's Calendar event no longer repeat the patient's name next to each
link — the name still appears once, in the event's `summary` at creation,
which is where a doctor actually needs it.

## Project layout

```text
src/app/
  main.py              app factory + lifespan (Mongo, indexes, question-bank preload, calendar client)
  core/                settings, logging, exceptions, constants, link/webhook signing, prod secrets bootstrap
  api/
    deps.py            dependency providers, incl. intake-token + physician-API-key guards
    v1/                REST: mock-booking, webhooks, sessions, reports, uploads
    ws/                intake WebSocket + BidiInput/BidiOutput channel adapters
  agents/
    bidi/              live agent: construction, system prompt, Mongo transcript writer
    summarizer/        post-call agent + its instructions
    tools/             the seven live-call tools
  models/              domain models (Session, Doctor, ConversationTurn, PreScreeningReport)
  schemas/             wire DTOs, kept separate from domain models
  repositories/        Mongo data access (sessions, doctors, question bank, slots, audit trail, sweeper lease)
  services/            orchestration (session lifecycle, question bank, notifications, video, scheduling, sweeper)
  db/                  Motor client lifecycle
  integrations/        booking platform, object storage (S3), Google Calendar
tests/
  unit/                fast, no I/O (moto for S3, disabled-path Calendar tests)
  integration/         API + DB boundaries against mongomock-motor
scripts/
  seed_question_bank.py
  seed_doctors.py
  seed_slots.py       working-hours slot grid for every seeded doctor
  sweep_slots.py      one manual reconciliation pass -- see `make sweep-slots`
```

## Development

```bash
make lint
```

```bash
make format
```

```bash
make typecheck
```

```bash
make test
```

All four gates are expected to pass at all times: **ruff format, ruff lint, ty,
pytest** (currently 156 passing).
pytest** (currently 156 passing).

---

## Sharp edges

Hard-won specifics about this stack. Every one was verified against the
**installed** package, not the published docs — the
`strands.experimental.bidi` surface is experimental and the docs contradict it
in places.

**Tools receive context through `tool_context`, not `invocation_state`.**
Strands injects exactly two special parameters: `tool_context` and `agent`. Any
other parameter is treated as **model-supplied input** and appears in the tool's
public JSON schema. A tool written as `def end_session(request_state: dict)` —
which is what the Strands docs show — asks the *model* to invent that dict, then
mutates the invention, so the call never ends. Use `@tool(context=True)` and read
`tool_context.invocation_state`.

**`BidiAgent.run()` calls `start()` and stops the agent itself.** Pass
`invocation_state` to `run()`; do not call `start()` or `stop()` around it, or
you open the model connection twice.

**FastAPI dependencies in a WebSocket route must not be typed `Request`.**
FastAPI cannot satisfy a `Request` dependency there, and the handler raises
`TypeError` before its body runs — every call fails. Use `HTTPConnection`, the
common base of `Request` and `WebSocket`.

**`return` is illegal inside an `except*` block** (PEP 654). `run()` drives I/O
in a TaskGroup, so a disconnect arrives wrapped in an `ExceptionGroup` and a
plain `except WebSocketDisconnect` never fires — but you cannot `return` out of
the `except*` handler either. Carry the outcome out in a flag.

**The Mongo client sets `tz_aware=True` deliberately.** BSON stores datetimes
without an offset, so the default driver returns them naive — and comparing one
to `datetime.now(UTC)` raises `TypeError` rather than returning a wrong answer.

**Never rehydrate the transcript as a Strands message list.** An agent trusts its
own history: hand it a stored history whose last entry is a tool call and it
re-executes that tool with no model turn in between. These transcripts contain
exactly such entries. `render_transcript_as_text` flattens to a plain string.

**A tool's docstring is its model-facing description.** Editing one changes agent
behaviour; it is not documentation.

**`structlog` needs `format_exc_info` explicitly.** Without it, `log.exception()`
under the JSON renderer emits the literal `"exc_info": true` and discards the
traceback — every production stack trace silently lost.

**A provider's own error text can crash the logger that reports it.**
Verified live: Bedrock's `ValidationException` for a rejected image included
Unicode box-drawing characters, and `log.exception(...)`'s console renderer
then raised `UnicodeEncodeError` on a Windows terminal's cp1252 stdout —
turning a caught, handled error into an unhandled one. `describe_document`
logs `str(exc).encode("ascii", "backslashreplace").decode("ascii")` via
`log.error(...)` instead of relying on `log.exception()`'s automatic
traceback rendering, specifically so a best-effort path's own logging can
never be what breaks its "never raises" contract. The same risk exists
wherever else a bare `except Exception: log.exception(...)` wraps a call to
an external provider (e.g. `SessionService.complete_call`'s summarization
failure path) — this codebase has not audited every such site for it.

**Only final transcript events are persisted.** The stream also emits
incremental previews; storing every delta produces a fragmented, unusable
transcript. Filter on `is_final`, and prefer `current_transcript` over the raw
delta (it is `None` on the first delta, so keep the fallback).

**Pin `strands-agents` exactly.** The bidi API has changed shape between
releases. Re-verify anything load-bearing before bumping it.

**boto3 and the Google API client are both synchronous.** Every call in
`integrations/storage.py` and `integrations/google_calendar.py` is wrapped in
`asyncio.to_thread` for exactly this reason — a blocking network call on the
main event loop would stall every other session's live audio, not just the
one making the call.

**Calendar delivery only appends; it never replaces the description.**
`GoogleCalendarClient.append_to_event_description` reads the current
description and adds a line, so it is safe to call up to three times — once
each for the report, the supporting document, and the recording, in any
order or with any subset missing — without one call clobbering another's
line.

**BSON has no date-only type.** A bare `datetime.date` field (e.g. a patient's
`date_of_birth`) raises `bson.errors.InvalidDocument` the moment pymongo tries
to encode it — caught by the mocked test suite, not just live Mongo, since
`mongomock-motor` calls the real `bson` encoder. Store it as a UTC-midnight
`datetime` instead; `PatientRef.date_of_birth` does this, while the API-facing
schemas (`BookingConfirmedWebhook`, `ScheduleAppointmentRequest`) still accept
a plain `date` — `SessionService.create_from_booking` converts once, at the
boundary.

**A guarded `find_one_and_update` can lie under `mongomock` — verified by
execution.** When its filter guards on a field the same update also mutates
(e.g. `status`), and the post-update value falls outside the filter's
original matching set, `mongomock`'s `_find_and_modify` re-runs the
*original* filter against the *already-mutated* document to build the
`AFTER` image — which no longer matches — and returns `None` even though the
write genuinely landed. Real MongoDB has no such bug; only the mock does,
and it fails in the dangerous direction (reports "lost the race" when it
actually won). Every CAS write in `SlotRepository` and the new guarded
`SessionRepository` methods (`_guarded_set` and friends) uses `update_one` +
`result.matched_count` instead — never this codebase's own `_set()` idiom,
which is safe only because its filter (`session_id` alone) is never a field
any caller also mutates.

**Multi-document transactions are not just inadvisable here, they are
untestable.** `mongomock.MongoClient.start_session()` raises
`NotImplementedError` unconditionally, and every DB-backed test in this
suite builds its database from `AsyncMongoMockClient`. The cancel/reschedule
saga (see [Scheduling](#scheduling)) is deliberately built from ordered,
single-document guarded writes instead — MongoDB already makes one
`update_one` atomic with no transaction, which is the only atomicity the
feature actually needs.

---

## Status

**Implemented and covered by tests** — the full backend, end to end:

- **The live call** over Nova 2 Sonic — greeting, consent, an upfront safety
  gate (see [Safety escalation](#safety-escalation)), an appointment
  confirm/cancel check, silent category routing with the boot-time
  question-bank preload (capped at ten category questions, no spoken
  read-back), medications, allergies, patient goal/expectation, a
  live reschedule offer (see [Scheduling](#scheduling)), and optional
  document upload.
- **Post-call summarization** into a structured report (`chief_concern`,
  `clinical_summary`, `findings`, `medications`, `allergies`, `patient_goal`,
  `gaps`) with an XML-escaping fix so patient/LLM text can never crash PDF
  rendering.
- **Document-type description**: an uploaded document gets a best-effort,
  type-only description (never a finding) from the same Claude model,
  fed as a Bedrock image/document content block — the report's new
  **Attached Documents** section pairs that description with the file
  link. Verified live against a real PDF and a real image, including the
  unsupported-format and provider-rejection fallback paths.
- **In-call cancel/reschedule** (see [Scheduling](#scheduling)) — slot-backed
  availability, a crash-safe guarded-write saga, a lease-guarded sweeper for
  abandoned holds, an append-only audit trail, and a real, verified timezone
  fix (the agent used to speak raw UTC as if it were local time).
- **Booking + delivery**: the mock booking platform (Calendar event creation
  -> session creation -> slot creation), booking-webhook signature
  verification, consent gating with a guarded state-transition, signed
  intake links, REST auth (intake token on patient routes, `X-API-Key` on
  the report route), S3-backed presigned uploads for documents and
  recordings, and best-effort Google Calendar delivery of the report,
  recording, safety-escalation, and cancel/reschedule updates.
- **Security hardening**: WS Origin allowlist, S3 SSE-KMS/AES256, an AWS
  Secrets Manager bootstrap (production-only), structured-log PHI
  redaction, and Calendar payload minimization — see [Security](#security).

**Deliberately not built here** — frontends. The Booking UI, the patient's
in-call page, and any physician dashboard are out of scope for this service;
this is the backend they call.

**Known gaps worth knowing before a production deployment.**
- `PHYSICIAN_API_KEY` is one shared secret, not per-doctor accounts — adequate
  for this project's scope, not a real auth system.
- `SessionState.STARTED` is still defined but nothing writes it — no
  page-load hook exists yet. `EXPIRED` now does have a writer (the
  scheduling feature's sweeper, see [Scheduling](#scheduling)), but only for
  a session stuck before consent past its appointment time or the intake
  link's TTL, or one whose live call was abandoned mid-way — a session
  that opened the link but never did either is still invisible, since
  nothing marks `STARTED` for it to notice.
- `SessionState.STARTED` is still defined but nothing writes it — no
  page-load hook exists yet. `EXPIRED` now does have a writer (the
  scheduling feature's sweeper, see [Scheduling](#scheduling)), but only for
  a session stuck before consent past its appointment time or the intake
  link's TTL, or one whose live call was abandoned mid-way — a session
  that opened the link but never did either is still invisible, since
  nothing marks `STARTED` for it to notice.
- Calendar delivery assumes an exact string match between `Session.physician`
  and a `doctors` document's `name` — there is no doctor ID, so a typo at
  booking time silently skips delivery rather than erroring.
- No rate limiting on any route.
- TLS termination (`Caddyfile`/`docker-compose.yml`) is prepared but not wired
  to a real domain — there is no deployment target yet. See [Security](#security).
- The Secrets Manager bootstrap (`core/secrets_loader.py`) is inert until a
  production deployment sets `APP_ENV=production` + `SECRETS_MANAGER_SECRET_ID`.
- The safety gate's red-flag questions (see [Safety escalation](#safety-escalation))
  are a first-pass draft, not clinically validated, and detection is LLM
  judgment only — no keyword-based backstop exists.
- Cancelling an appointment is LLM-triggered and immediately frees a real
  slot someone else can take — the same class of risk as the safety gate,
  mitigated only by the prompt requiring explicit spoken confirmation first,
  not a hard guarantee.
- `scripts/seed_slots.py`'s working hours (09:00–17:00, every doctor, no
  per-day variation) are a placeholder grid, not a real scheduling policy.
- The abandoned-slot sweep keys off `updated_at`, which only advances on a
  final transcript turn — a patient who goes quiet but stays connected (the
  medications step explicitly sends them to fetch a bottle) looks identical
  to a dead process. Accepted tradeoff, not a real liveness signal.
- Slot/audit TTL retention is real in Atlas but has no faithful test
  coverage: `mongomock`'s TTL emulation ignores the partial filter and uses
  a naive clock internally, so a test asserting the *behavior* (not just the
  index declaration) would pass or fail for reasons unrelated to production.

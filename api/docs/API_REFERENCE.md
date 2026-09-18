# Backend API Reference — Prelude Health

The wire contract between `prelude-health-api` (FastAPI) and any client:
the patient UI, the booking UI, and the physician surface. Verified against the
code at `src/app/api/` as of 2026-09-09.

- **The live call's wire protocol** (the WebSocket, in full): [`LIVE_CALL_PROTOCOL.md`](./LIVE_CALL_PROTOCOL.md)
- **System design view** (why the pieces fit together): [`architecture.md`](./architecture.md)
- **Operational view** (env vars, seeding, sharp edges): [`../README.md`](../README.md)
- **Frontend view**: `../../prelude-health-ui/docs/FRONTEND_ARCHITECTURE.md`
- **Integration status and open questions**: `../../docs/INTEGRATION.md`

---

## 1. What this is

One FastAPI process exposing three surfaces, each with its own auth scheme and
its own consumer. There are **no user accounts anywhere** — every caller is
authenticated by a signed token or a shared key.

| Surface | Consumer | Auth | Carries PHI? |
|---|---|---|---|
| Booking | Booking UI (not built) | none (mock) / HMAC (webhook) | patient identity only |
| Patient intake | Patient mobile UI | `HttpOnly` session cookie; a single-use ticket for the WS | yes, in transit |
| Physician | Physician dashboard (not built) | `X-API-Key` header | yes |

Base URL shape (local dev):

```
REST  http://localhost:8000/api/v1/...
WS    ws://localhost:8000/ws/intake/{session_id}?ticket=...
Ops   http://localhost:8000/healthz
```

Note the WebSocket route is **not** under `/api/v1` — it is mounted at the app
root. A frontend `VITE_API_BASE_URL` of `http://localhost:8000/api/v1` cannot be
reused verbatim to build the socket URL.

---

## 2. Authentication

### 2.1 Three purpose-bound credentials

All three are HMAC-SHA256 over the same shape (`src/app/core/security.py`),
separated by a `purpose` field that is **part of the signed payload**. That
binding is what stops them being interchangeable: without it, a 48-hour
intake token lifted from a URL would open the WebSocket directly and the
short-lived ticket would be pointless.

```
<purpose>.<expires_at>[.<nonce>].<signature>
signature = HMAC-SHA256(INTAKE_LINK_SECRET, "<purpose>:<session_id>:<expires_at>:<nonce>")
```

| Purpose | Where it lives | TTL | Used by |
|---|---|---|---|
| `intake` | inside the emailed/SMS link, as `?token=` | 48 h (`INTAKE_LINK_TTL_SECONDS`) | `POST /sessions/{id}/attach` **and nothing else** |
| `session` | an `HttpOnly`, `SameSite` cookie set at attach | 2 h (`SESSION_COOKIE_TTL_SECONDS`) | every other patient route |
| `ws` | the WebSocket query string, as `?ticket=` | 60 s (`WS_TICKET_TTL_SECONDS`), single use | the WS handshake only |

The flow:

```
link ?token=…  ──POST /sessions/{id}/attach──▶  HttpOnly cookie  ──▶  every REST route
                                                       │
                                                       └──POST /ws-ticket──▶ single-use ticket ──▶ WS
```

The patient app reads the token once, exchanges it, and strips it from the
URL — so it never lingers in the address bar, browser history, or a
`Referer` header. The credential that replaces it is one page JavaScript
cannot read, so an XSS on the patient app cannot lift it.

A missing or invalid credential returns `401` on REST; the WS route closes
with code `1008` **before** the protocol upgrade, so the browser sees a
failed handshake rather than a connected-then-closed socket.

Cross-origin cookies need both `allow_credentials` on the server and
`withCredentials` on the client, and a wildcard `Access-Control-Allow-Origin`
is illegal alongside credentials — which is why an unset `ALLOWED_ORIGINS`
falls back to Vite's dev origins rather than `*`.

### 2.2 Physician API key

`GET /api/v1/sessions/{id}/report` requires header `X-API-Key: <PHYSICIAN_API_KEY>`,
compared with `hmac.compare_digest`. This is one shared secret for all
physicians, not per-doctor auth. **It must never be embedded in a browser
bundle** — anything prefixed `VITE_` is public.

### 2.3 Booking webhook signature

`POST /api/v1/webhooks/booking-confirmed` requires header `X-Signature` =
`HMAC-SHA256(raw_request_body_bytes, BOOKING_WEBHOOK_SECRET)`. Verified against
the **raw bytes** before the JSON is parsed. Server-to-server only.

---

## 3. REST endpoints

### 3.1 `POST /api/v1/mock-booking/appointments`

Stands in for a real scheduling platform. Creates the Google Calendar event,
then the session, then returns the patient's intake link. Auth: none.

Request (`ScheduleAppointmentRequest`):

```json
{
  "patient_name": "Asha Rao",
  "patient_id": "pt_2290",
  "date_of_birth": "1990-05-14",
  "sex": "female",
  "physician": "Dr. Mehta",
  "scheduled_at": "2026-09-12T09:30:00Z",
  "booking_reason": "Short of breath on stairs",
  "contact_phone": "+15550100",
  "contact_email": "asha@example.com"
}
```

`sex` ∈ `male | female | other`. `date_of_birth` is a plain ISO date on the
wire; the service converts it to a UTC-midnight `datetime` at the boundary
(BSON has no date-only type).

Two optional fields the booking UI uses instead of `physician`:

| Field | Purpose |
|---|---|
| `doctor_id` | Stable id from `GET /booking/providers`. Preferred over `physician`; the name is then read from the doctor's record rather than trusted from the client. One of the two is required. |
| `visit_type` | What the patient picked on the booking screen (a `BookingVisitType`). Preferred over `symptom_category`: it also covers the unscoped options, and the server derives the clinical category rather than trusting the client to map it. Recorded into `booking_reason` as *"Patient selected visit type: …"* — the patient's own selection, never a clinical finding. |
| `symptom_category` | The clinical category to pre-scope to. Ignored when `visit_type` is given. |
| `patient_id` | Optional. Most patients cannot recall an MRN, so omitting it is normal — the server mints a `provisional-…` id and flags it in the response. |

Response `201` (`ScheduleAppointmentResponse`):

```json
{
  "session_id": "…",
  "appointment_id": "…",
  "intake_url": "https://patient-app/prescreen/{session_id}?token=…",
  "calendar_event_id": "…",
  "physician": "Dr. Mehta",
  "scheduled_at": "2026-09-12T09:30:00Z",
  "calendar_synced": true
}
```

`patient_id` echoes the identifier the session was created under, and
`patient_id_is_provisional` says whether the server minted it — surface that
rather than presenting a generated id as clinic-issued. `notification_message`
is the exact SMS body sent to the patient, intake link included, so a booking
UI can show what they received instead of re-composing it.

`calendar_synced` is `false` when the booking is recorded here but no Google
Calendar event was created (no doctor grant and no service account, or the
API call failed) — surface that rather than implying a sync that did not
happen.

`404` if neither `doctor_id` nor `physician` matches a document in the
`doctors` collection (`physician` matching is an **exact string comparison on
the name**).

`409` if the requested `scheduled_at` is not one of that doctor's open slots.
The slot is re-checked server-side at submit time, so a client holding a
stale availability snapshot cannot double-book. Recover by re-fetching
availability, not by retrying.

### 3.1a Booking screen reads

Auth: none — nothing here is PHI, and it is what a public scheduling page
needs. No response carries a doctor's stored Google credentials.

**`GET /api/v1/booking/visit-types`** → `200`

```json
[
  {
    "visit_type": "general_checkup",
    "label": "General checkup",
    "description": "A routine visit with no specific problem",
    "symptom_category": null
  },
  {
    "visit_type": "heart",
    "label": "Heart",
    "description": "Chest discomfort, palpitations, and breathlessness",
    "symptom_category": "heart"
  }
]
```

`visit_type` ∈ the seven `BookingVisitType` values: the five conditions
(`diabetes | blood_pressure | heart | lung | stomach`), plus `general_checkup`
and `not_sure`. `symptom_category` is the clinical category the visit type
pre-scopes the call to, and is **null for the two unscoped options** — that is
not a gap, the agent infers a category from the patient's own words during the
call either way. Label and description are served, not derived client-side.
The unscoped options are returned first, deliberately.

**`GET /api/v1/booking/providers?visit_type=heart`** → `200`

```json
[
  {
    "doctor_id": "dr-mehta",
    "name": "Dr. Mehta",
    "credential": "MD",
    "categories": ["heart", "blood_pressure"],
    "modality": "in_person_and_virtual",
    "calendar_connected": true,
    "photo_url": "https://images.unsplash.com/photo-…?auto=format&fit=facearea&w=256&h=256"
  }
]
```

`visit_type` is optional; omitted — or set to an unscoped option — every
doctor is returned. A doctor with an
empty or absent `categories` is unrestricted and returned for every visit
type — which is what keeps doctors seeded before this field shipped bookable.
`modality` ∈ `in_person | virtual | in_person_and_virtual`. `photo_url` is
`null` when the doctor has no portrait on file — render initials, not a
placeholder image.

**`GET /api/v1/booking/providers/{doctor_id}/availability?from=&days=`** → `200`

```json
{
  "doctor_id": "dr-mehta",
  "timezone": "Asia/Kolkata",
  "calendar_connected": false,
  "days": [
    {
      "day": "2026-09-14",
      "weekday_label": "Mon",
      "day_label": "14",
      "source": "clinic_hours",
      "slots": [
        { "start": "2026-09-14T09:00:00+05:30", "end": "…", "label": "9:00 AM" }
      ]
    }
  ]
}
```

`from` defaults to today and `days` to `BOOKING_HORIZON_DAYS`. Days with no
open slots are still returned, with `slots: []` — do not skip them, or the
date strip misaligns.

`source` per day is the contract that matters:

| Value | Meaning |
|---|---|
| `calendar` | Checked against the doctor's own Google Calendar free/busy. |
| `clinic_hours` | Clinic opening hours minus appointments booked through this service. The doctor has not connected Google, or their calendar was unreachable. |

A `clinic_hours` day **must not** be presented as calendar-confirmed. `start`
is what `POST /mock-booking/appointments` must echo back verbatim; `label` is
pre-rendered in `timezone` so every client shows a slot at the same time
regardless of the browser's own zone. `404` if `doctor_id` is unknown.

### 3.1b Doctor self-registration (Google OAuth)

**`POST /api/v1/doctors/registration`** → `201`

```json
{ "name": "Dr. Priya Raman", "credential": "MD", "categories": ["heart"], "modality": "virtual" }
```

Responds `{ "authorization_url": "https://accounts.google.com/o/oauth2/v2/auth?…" }`.
Navigate the browser to it — a full navigation, not a popup or iframe.
`503` when the deployment has no `GOOGLE_OAUTH_CLIENT_ID` /
`GOOGLE_OAUTH_CLIENT_SECRET`; report the feature as unavailable rather than
starting a flow that cannot complete.

**`GET /api/v1/doctors/google/callback?state=&code=`** → `307`

Google's redirect target, not for direct use. Redirects to
`<BOOKING_APP_BASE_URL>/book?doctor_registration=<outcome>` where outcome is
`connected` (with `&doctor=<name>`), `declined` (the doctor cancelled at
Google — not an error), or `failed`. The `state` is single-use, so a replayed
callback URL yields `failed`. The refresh token is written to Mongo and never
appears in a response body or a redirect URL.

### 3.2 `POST /api/v1/webhooks/booking-confirmed`

Same payload as above plus `appointment_id`. Auth: `X-Signature`.
Returns `202` with a `SessionStatusResponse`. Idempotent by `appointment_id`
(enforced by a unique Mongo index), so a retried webhook returns the existing
session instead of creating a second one.

### 3.3 `GET /api/v1/sessions/{session_id}`

Auth: intake token. Response (`SessionStatusResponse`):

```json
{
  "session_id": "…",
  "status": "notification_sent",
  "document_uploaded": false,
  "has_report": false,
  "has_video": false
}
```

`404` if the session does not exist. **This response deliberately carries no
PHI** — no patient name, no appointment time, no physician. See
`../../docs/INTEGRATION.md` §Open questions, Q2.

### 3.4 `POST /api/v1/sessions/{session_id}/consent`

Auth: session cookie. Body: `{ "given": true }`. Returns `SessionContextResponse` (see §3.4a) so the app can render the next screen from one response.

Guarded transition: only permitted from `notification_sent` or `started`
(`_CONSENTABLE_STATES`). `given=true` → `in_progress`; `given=false` →
`declined`. Calling it from any other state raises a domain error.

**The WS route refuses to connect unless an affirmative consent record exists.**

### 3.4a The session context response

`attach`, `GET /sessions/{id}/context` and `POST .../consent` all return the
same `SessionContextResponse`. **It carries PHI** and is gated behind the
`HttpOnly` session cookie, never the intake token.

Beyond the patient and appointment blocks it also carries the deployment's
own identity, so the patient app holds no copy of any of it:

| Field | Source | Why it is served rather than hardcoded |
|---|---|---|
| `assistant.name` / `.role` | `ASSISTANT_NAME` / `ASSISTANT_ROLE` | The UI names the assistant from this; a frontend constant would disagree with the deployment the moment either changes. |
| `clinic.name` / `.location` | `CLINIC_NAME` / `CLINIC_LOCATION` | Null means the UI names no facility rather than inventing one. |
| `clinic.timezone` | `CLINIC_TIMEZONE` | Appointment times render in the clinic's zone, not the browser's. |
| `appointment.duration_minutes` | `CLINIC_SLOT_MINUTES` | So the UI never renders an end time it made up. |
| `appointment.physician_credential` | the doctor's own record | Null means show no subtitle, not a plausible job title. |
| `call_progress.screen` / `.details` | `last_screen` / `collected_details` | The resume marker and prefill. Null screen means no call has started. |

### 3.4b The patient's own appointment

Both routes are cookie-gated and scoped to the session's own doctor, so the
patient app never resolves a `doctor_id` itself or reaches an endpoint that
can enumerate providers.

```
GET  /api/v1/sessions/{id}/appointment/availability[?from=YYYY-MM-DD&days=N]
  -> ProviderAvailabilityResponse   (same shape as the booking screen's)

POST /api/v1/sessions/{id}/appointment/reschedule   { "start": "2026-09-24T09:00:00+05:30" }
  -> SessionContextResponse
```

`start` must be echoed verbatim from an availability response, and is
**re-validated server-side** — the patient's list is a snapshot, and someone
else may have taken the slot while they were reading it. That returns `409`
(`SlotUnavailableError`) and the UI must re-fetch rather than retry.

`404` when the session's free-text `physician` matches no doctor on file;
`409` from a state with no live appointment (`declined`, `expired`). The
Calendar event is moved best-effort *after* the session is written: the
stored time is what the agent's greeting, the intake message and the report
all read from, so a Calendar hiccup costs the doctor a stale entry, not the
patient's booking.

> §9.1 of `../../docs/INTEGRATION.md` notes that
> `origin/wip-basecode-santhosh` has its own, richer reschedule
> implementation. These two routes live in their own module
> (`app/api/v1/appointments.py`) to keep that merge small, but they still
> have to be reconciled.

### 3.5 Uploads — documents and recordings

Identical shape for both artifacts. Auth: session cookie on all four routes.
The file bytes **never pass through this API**.

```
POST /api/v1/sessions/{id}/documents/upload-url   { "content_type": "application/pdf" }
  -> { "upload_url": "https://s3…", "key": "documents/{id}/{uuid}" }

PUT  <upload_url>            (browser -> S3 directly, raw bytes)

POST /api/v1/sessions/{id}/documents/complete     { "storage_key": "documents/{id}/{uuid}" }
  -> SessionStatusResponse
```

Recording equivalents: `/recording/upload-url` and `/recording/complete`
(key prefix `recordings/`). `recording/complete` also moves the session to
`video_ready`.

Both `complete` calls best-effort append a presigned link to the doctor's
Calendar event. Calendar failures never fail the request.

> S3 must have CORS configured to accept a browser `PUT` from the UI's origin,
> and the presigned PUT does not currently bake in server-side-encryption
> headers — enable bucket default encryption. See `../README.md` § Security.

### 3.6 `GET /api/v1/sessions/{session_id}/report`

Auth: `X-API-Key`. Returns the `PreScreeningReport` object, or `null` with a
`200` when the report is not ready yet. `404` only when the session itself does
not exist — that distinction is deliberate.

```json
{
  "chief_concern": "Shortness of breath on exertion",
  "category": "lung",
  "clinical_summary": "3–6 sentences…",
  "findings": [{ "question": "…", "answer": "…", "flagged": false }],
  "medications": [{ "name": "Albuterol", "dose": null, "frequency": "as needed", "reason": null }],
  "allergies": [{ "allergen": "Penicillin", "reaction": "Rash" }],
  "patient_goal": "…",
  "gaps": ["Smoking history not confirmed"]
}
```

An empty `medications`/`allergies` list means the patient confirmed *none*. If
they were never asked or declined, that lands in `gaps` instead. The schema has
**no field capable of holding a diagnosis or triage decision** — by design.

### 3.7 `GET /healthz`

Liveness only, no auth, deliberately does not touch Mongo.

---

## 4. WebSocket: the live intake call

```
WS /ws/intake/{session_id}?ticket=<single_use_ws_ticket>
```

**Full protocol reference:
[`LIVE_CALL_PROTOCOL.md`](./LIVE_CALL_PROTOCOL.md).** This section is the
summary; that document is the contract.

### 4.1 Handshake gates (all applied *before* `accept()`)

In order, each closing with code `1008` and no upgrade:

1. `Origin` header not in `ALLOWED_ORIGINS` — skipped entirely when that setting is blank.
2. Invalid, expired, or wrong-purpose ticket.
3. Ticket already spent (single use, claimed against the `ws_tickets` ledger).
4. Unknown `session_id`.
5. No consent record, or `consent.given == false`.
6. Session state outside `{in_progress, interrupted}` — consent alone is not a
   licence, since a summarized session keeps its consent record forever.

Because the rejection happens pre-upgrade, the browser cannot distinguish
*which* gate failed — that is intentional, and it means the UI must call
`GET /sessions/{id}` first to render a meaningful reason.

### 4.2 Message protocol

**The browser does not speak the `strands.experimental.bidi` event schema.**
Both directions are translated against `src/app/schemas/intake_channel.py`,
which is this service's own contract and carries a `protocol_version`.

Client → server: `client_audio` (base64 PCM16 mono @ 16 kHz, ≤64 KB per
frame), `client_text`, `client_form_update`, `client_screen_ack`,
`client_control`, `client_ping`. Everything is validated against a
discriminated union; an invalid frame is answered with an `error` and the
call continues.

Server → client: `connected`, `agent_ready`, `agent_audio`, `transcript`,
`agent_speaking`, `interrupted`, `navigate`, `form_prefill`,
`upload_requested`, `call_ended`, `error`, `pong`.

Deliberately **not** forwarded: `tool_use_stream`, `tool_result`,
`bidi_usage`, and the raw `bidi_error` payload — they carry tool names,
partially-streamed tool arguments and exception class names. The upload
prompt, navigation and prefill reach the browser from the tools themselves
instead, as complete instructions.

### 4.3 How the call ends — three distinct outcomes

| Outcome | Trigger | Session lands in | Report? |
|---|---|---|---|
| Completed | agent says goodbye out loud, then calls `end_session` | `completed` → `summarizing` → `summary_ready` / `summary_failed` | yes |
| Interrupted | patient pressed End, or the socket dropped | `interrupted` | **no** |
| Failed | the model or the socket errored; an `error` frame is sent first | `interrupted` | **no** |

An interrupted call is **resumable**: `last_screen` and `collected_details`
are persisted as the call goes, the next socket's `connected` frame carries
them, and the rebuilt agent gets a resume block. It is no longer
indistinguishable from an in-flight call — the physician view can tell an
abandoned call from a finished one, and must not render an empty report for
either.

---

## 5. Session state machine

Twelve states in `SessionState` (`src/app/core/constants.py`):

```
booking_created → ai_link_ready → notification_sent → started → in_progress
    → completed → summarizing → summary_ready → video_ready

           in_progress ⇄ interrupted        (a dropped call, and its rejoin)

off-ramps:
  declined          consent refused
  expired           link TTL elapsed (DEFINED BUT NEVER WRITTEN — no sweep exists)
  summary_failed    summarization errored; `summary_error` records why
```

`started` is written by `POST /sessions/{id}/attach` — opening the link is
what it means.

`interrupted` is what a dropped or patient-ended call produces, and it is
written only from `in_progress`/`interrupted` so a teardown arriving after
`complete_call` cannot undo a finished, summarized session. Accepting a new
socket writes `in_progress` again, so a session reads as in progress only
while there is actually a call on it.

`safety_escalated` was removed with the safety gate (D4).

---

## 6. What the frontend must not do

- Never call `GET .../report` from the browser — it needs the physician key.
- Never persist the intake token in `localStorage`; it is a bearer credential
  for PHI (see the UI's `CLAUDE.md`, Security).
- Never treat `SessionStatusResponse` as the source of appointment/patient data
  — it does not contain any.
- Never render a report or summary as clinician-verified; the schema is
  LLM-generated and `gaps` are load-bearing.

---

## 7. Known gaps in this API

1. `ALLOWED_ORIGINS` is blank by default, which makes the WebSocket's Origin
   check permissive. `CORSMiddleware` falls back to Vite's dev origins rather
   than `*`, so REST is not permissive — but the socket is until this is set.
2. No rate limiting on any route.
3. `expired` is still unreachable — no TTL sweep exists, so an interrupted
   call stays resumable indefinitely rather than aging out.
4. The report route returns `200 null` for "not ready", which a strict Zod
   schema on the client must model as nullable rather than as an error.
5. AWS credentials come from this service's own configuration only --
   boto3's shared credentials file, SSO cache and instance profile are
   deliberately not consulted. A deployment with none configured gets a
   `503` from the Bedrock and storage paths rather than borrowing the
   host's identity. See `src/app/core/aws.py`.
6. `BIDI_MODEL_ID` must be a bare model id.
   `InvokeModelWithBidirectionalStream` rejects an
   application-inference-profile ARN, unlike the text models. A boot
   warning (`bidi_model_id_is_an_inference_profile`) catches it.

Closed since the previous revision: CORS is installed with credentials and an
allow-list; typed answers now reach the transcript as `user` turns
(`client_text` / `client_form_update`); appointment availability and
reschedule endpoints exist; `started` is written at attach.

---

## 8. Contract changes decided 2026-09-08, and where each landed

Full rationale in [`../../docs/INTEGRATION.md`](../../docs/INTEGRATION.md) §4;
delivery notes in §6 and §6a.

| # | Change | Decision | Status |
|---|---|---|---|
| 1 | `CORSMiddleware` added; `ALLOWED_ORIGINS` set for real | B1, B2 | done (middleware); `ALLOWED_ORIGINS` still blank by default |
| 2 | Token moves out of the query string: exchanged once at boot for an `HttpOnly` cookie; the WS gets a **single-use, ~60 s ticket** instead of the 48 h token | D1 | done |
| 3 | Session binding, bounded reuse, and revocation added to the intake token | D1 | **open** — the token is purpose-bound and TTL'd, but not device-bound or revocable |
| 4 | New `interrupted` state + reconnect path; abandoned calls get a TTL instead of sitting in `in_progress` forever | D1 | state and reconnect done; **no TTL sweep** |
| 5 | Session endpoint gains patient + appointment context | D2 | done — plus clinic and assistant identity, see §3.4a |
| 6 | Text input accepted on the WS as a first-class `user` turn, so typed answers land in the same transcript as spoken ones | D0 | done — `client_text` and `client_form_update` |
| 7 | **Safety escalation removed** | D4 | done |
| 8 | `PreScreeningReport` extended: family history, social history, hospital stays, recent providers, recent tests, follow-up notes — plus summariser instructions, PDF rendering, and live-agent prompt steps for each | D5 | done |
| 9 | A topic/step signal emitted over the WS so the UI can follow agent-driven navigation | D6 | done — an explicit `navigate_to_screen` tool and a `navigate` frame, not inference |
| 10 | Appointment slot availability + reschedule endpoints, with Calendar write-back | D7 | done, see §3.4b — needs reconciling with the other branch |
| 11 | Doctors become real records with a **stable doctor id**, plus doctor registration, doctor authentication, and **per-doctor Google OAuth** | D8 | doctor id and per-doctor OAuth done; **doctor authentication open** |
| 12 | Recording routes stay in place but unused | D9 | as decided |

**Not planned, still required for the HIPAA posture D2 assumes:** BAAs with AWS
and MongoDB Atlas, audit logging of every PHI access, enforced end-to-end TLS
(the `Caddyfile` is prepared but wired to no domain), and a data-retention
policy.

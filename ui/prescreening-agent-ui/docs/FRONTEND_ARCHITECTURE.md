# Frontend Architecture — Prelude Health UI

What `prelude-health-ui` actually is, how it is built, and exactly where it
stops. Verified against `src/` and against the running backend as of
2026-09-09.

- **Enforced product/clinical/engineering rules**: [`../CLAUDE.md`](../CLAUDE.md)
- **Setup, scripts, theming, UI states, the flow**: [`../README.md`](../README.md)
- **Backend contract**: `../../prelude-health-api/docs/API_REFERENCE.md`
- **The live call's wire protocol**: `../../prelude-health-api/docs/LIVE_CALL_PROTOCOL.md`
- **Integration status**: `../../docs/INTEGRATION.md`

---

## 1. What this is

A **mobile-only, portrait-only** React SPA. A patient opens a link a day or two
before their appointment, has a voice conversation with an intake assistant, and
what they say becomes a physician-ready summary.

It is a live client, not a prototype. Concretely, what happens now: the patient
lands on `/prescreen/{id}?token=…`; the token is exchanged once for an
`HttpOnly` cookie and stripped from the URL; they tap **Start**, which arms the
microphone and audio playback inside that gesture; they consent, which the
server records and which is what allows the WebSocket to open at all; from then
on the **agent decides which screen they are on**, their speech streams up as
PCM16, the assistant's speech streams back and plays, both sides are captioned
live, and each answer appears in its screen's own field where they can correct
a mishearing.

Voice is verified end to end. With the microphone blocked entirely, the
assistant still greets the patient by name and works through its script: the
backend fills gaps in the audio stream so a patient who declines the
microphone is not left in silence. See
`../../prelude-health-api/docs/LIVE_CALL_PROTOCOL.md` §4.

### The two inversions that shape everything here

**The conversation owns navigation.** There is no wizard. The eleven screens are
a _display mapping_ from "what the agent is asking about" to "which screen to
show", and the only code that navigates between them is a `useEffect` reacting
to a server frame. No page has a Next button; every page test asserts it has
none.

**Nothing in the UI starts, advances, or delays the call.** It opens itself
when the session lands, the assistant moves the patient between screens, and
a `navigate` frame is acted on the moment it arrives. There is one exception,
about the opening screen only: the browser reaches `confirm-details` itself if
the assistant has not within twelve seconds, because the control the patient
gives consent with lives there and nowhere else.

An earlier version also held `welcome` until the assistant went quiet, so a
greeting could not be cut off mid-sentence. That hold could deadlock — the
assistant asking for consent kept talking, the hold kept renewing, and the
patient never reached the screen holding the control it was asking them to
use. Being unable to consent is worse than a screen that changes while
someone is still speaking, and _when_ to navigate belongs to the
conversation, not to the browser.

**The screens are not a data model.** They are an editing surface over one
conversation. Every value shown is something the assistant heard, and every
edit is sent back as the patient's own turn — so the transcript on the server
stays the single record the report is built from, whether an answer was spoken
or typed.

---

## 2. Stack

| Concern      | Choice                       | Notes                                               |
| ------------ | ---------------------------- | --------------------------------------------------- |
| Framework    | React 19 + TypeScript strict |                                                     |
| Build        | Vite 5                       | `@/` path alias → `src/`                            |
| Routing      | react-router-dom 6           | `createBrowserRouter`                               |
| Server state | TanStack Query 5             | provider mounted; the call is a socket, not a query |
| HTTP         | axios (single instance)      | `src/services/apiClient.ts`, `withCredentials`      |
| Validation   | zod 3                        | every REST response **and every socket frame**      |
| Animation    | framer-motion 13             | page transitions, section reveals                   |
| Audio        | Web Audio + AudioWorklet     | no library; see [§5](#5-the-audio-pipeline)         |
| Icons        | lucide-react                 | the only icon set; no emoji anywhere                |
| Styling      | plain CSS + design tokens    | `src/app/theme.css`, no framework                   |
| Tests        | Vitest + RTL                 | 31 files, 184 tests                                 |

No dependency was added for the live call. The socket is `WebSocket`, the audio
is Web Audio, the resampling is twelve lines in a worklet, and base64 is
`atob`/`btoa` — each of which would otherwise have been a package.

---

## 3. Layering

```
pages/                     route-level screens. Thin: layout + which fields to show.
  └── features/prescreening-session/
        CallScreenLayout        the shell all eleven in-call screens share
        IntakeCallProvider      hosts the call; the only thing that navigates
        useIntakeCallEngine     socket, audio, captions, reconnect, controls
        intakeProtocol          the wire contract, parsed
        usePrefill              binds a control to what the agent heard
        prefillMatching         pure: patient's words → option ids
        audio/                  micCapture, agentPlayback, pcm
  └── services/               typed API boundary; nothing else calls the network
  └── components/             design-system primitives, domain-free
```

The direction is one-way: `pages → features → services`. A page never builds an
API payload, and a design-system component never knows about a screen id.

### State ownership

| What                                                 | Where                                             | Why there                                                                                           |
| ---------------------------------------------------- | ------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| Session identity, patient/appointment/clinic context | `PrescreeningSessionProvider`                     | Mounted once per session; every step shares it.                                                     |
| Live call: socket, audio, captions, screen, prefill  | `useIntakeCallEngine` inside `IntakeCallProvider` | Nested _inside_ the session provider because it needs consent state to know whether it may connect. |
| A single control's current value                     | the `usePrefill` hook at its call site            | As close to the consumer as it gets.                                                                |

Audio frames never enter React state. They arrive around thirty times a second
and go straight to the playback queue held in a ref. Only what a screen actually
renders is state.

### The one deliberate abstraction

`CallScreenLayout` exists because eleven screens had byte-identical shells. It
carries the header, the heading, the children, the document-upload prompt and
the live captions. The upload prompt is the reason it _has_ to be shared: the
agent can open it from whatever screen the call is on, so it cannot live on the
one screen that expects it.

---

## 4. The eleven screens, and what each binds

Each page is now a list of `usePrefill` bindings plus its own controls. The
field names are **the backend's** — they must match `SCREEN_FIELDS` in
`app/core/constants.py`, which is the allowlist that stops a hallucinated field
reaching a patient's screen.

| #   | Route                    | Fields bound                                                                                          |
| --- | ------------------------ | ----------------------------------------------------------------------------------------------------- |
| —   | index                    | `AppBoot` — branches on real session status; no timer                                                 |
| 1   | `welcome`                | none. Where the assistant introduces itself.                                                          |
| 2   | `confirm-details`        | `full_name`, `date_of_birth`, `phone_number` + the consent gate                                       |
| 3   | `patient-concerns`       | `concerns` (multi-select), `concern_details`                                                          |
| 4   | `symptom-story`          | none — one assistant-chosen question at a time (see below)                                            |
| 5   | `medication`             | `medications` + a photo of the box                                                                    |
| 6   | `allergies`              | `no_known_allergies` (bool), `allergies` (pair list)                                                  |
| 7   | `medical-history`        | `conditions`, `condition_details`, `no_hospital_stays`, `hospital_stays`                              |
| 8   | `recent-care`            | `saw_other_provider`, `provider_who/_when/_reason`, `had_recent_tests`, `test_details`                |
| 9   | `family-social-history`  | `family_conditions`, `family_details`, `tobacco_use`, `alcohol_use`, `occupation`, `living_situation` |
| 10  | `thank-you`              | `anything_else` + the recap of what the call covered                                                  |
| —   | `appointment-schedule`   | a read-back, reachable from the closing recap; not a step                                             |
| —   | `appointment-reschedule` | a branch off `thank-you`; real availability                                                           |

`symptom-story` is the one screen with no fields of its own. Which questions
belong on it is not knowable when the screen is built: the assistant infers the
symptom area (or two, for a chronic condition plus a new complaint) from what
the patient has already said, draws the questions from the clinic's own bank,
and chooses each next one from the answers so far. So the screen holds exactly
one live question at a time plus the answers behind it, and the server sends
that whole state as a `symptom_state` snapshot.

The live question card is keyed by question id, which is what empties the answer
box between questions — clearing it in an effect would race an answer the
patient is still finishing. Answered entries stay editable, because this is the
only place a patient can fix a mishearing: the assistant never reads answers
back.

`thank-you`'s recap is derived from the screens the call actually reached. A
fixed list of ticks would tell the patient their doctor has information nobody
collected.

`appointment-reschedule` is the one screen the server can send the patient to
that is not a step of the flow — hence `INTAKE_SCREENS` (the flow plus that
branch) being what the socket's screen enum is built from, rather than
`PRESCREENING_FLOW_STEPS`. The assistant takes them there when they answer the
closing question by asking to move their appointment, reads out the same times
the screen shows, and brings them back to `thank-you` once one is chosen. The
patient may instead tap a time and confirm it here: that goes over REST as it
always has, and `reportAppointmentRescheduled` then tells the assistant it is
already saved so it confirms rather than offering times that are no longer the
question. While a call is live the closing screen's own reschedule button asks
the assistant (`requestReschedule`) rather than navigating — the screen follows
the conversation, and jumping there before anything has been offered would put
the patient in front of a list nobody was talking about. Off a call, it
navigates directly, which is the only way there.

### Prefill: two rules, both clinical

- **The server leads until the patient touches a control.** Prefill arrives
  asynchronously and can arrive twice, so a control seeded once from it with
  `useState` would sit stale.
- **The patient wins from then on.** Showing the value is only worth doing if
  they can correct it.

`prefillMatching.ts` is deliberately conservative and has its own test file. It
matches an option only on a real label match, never falls back to an `other`
catch-all, and returns `null` for a yes/no it cannot read — because defaulting
an uncertain answer to _No_ would tell a physician the patient denied something
they never denied. It also refuses to split `penicillin, ibuprofen` into an
allergen and a reaction.

---

## 5. The audio pipeline

PCM16 mono at 16 kHz in both directions. The backend publishes that in the
socket's first frame rather than letting the client assume it.

**Capture** (`audio/micCapture.ts`) resamples from the hardware rate **inside
the AudioWorklet**, reading the worklet's own `sampleRate`. The tempting
alternative — `new AudioContext({ sampleRate: 16000 })` — is a request browsers
may quietly ignore, and a 48 kHz context whose frames are labelled 16 kHz sends
speech to the model at a third of its real speed: inaudible in code review,
unintelligible in the patient's ear. The worklet module is loaded from a Blob
URL rather than `public/`, so it cannot go missing independently of the bundle.
The worklet output is routed to a zero-gain sink, because some browsers stop
pulling from a worklet with nothing downstream, and routing it to the speakers
would echo the patient back at themselves.

**Playback** (`audio/agentPlayback.ts`) schedules each chunk against a running
clock. `start()` with no argument fires immediately, so playing chunks on
arrival makes the assistant sound like several people at once. Buffers are
created at the _frame's_ rate and left for the graph to resample — forcing them
to the context rate changes the pitch of the voice. Output is fanned to both the
speakers and a `MediaStreamAudioDestinationNode`, which is what lets the one
shared `AudioWaveform` visualize what the patient is actually hearing.

On an `interrupted` frame the queue is flushed. Without that, the patient hears
the rest of a sentence the model has already abandoned, several seconds after
they spoke over it.

**The queue depth is also the clock two other things run on**, because the
model finishes a turn seconds before the patient hears the end of it:

- the screen change, which waits so the next topic does not appear while the
  previous one is still being spoken;
- the caption, which waits for the same reason. A caption shown on arrival is
  the caption of the *next* thing the assistant will say, printed over the
  words the patient is still listening to — the greeting being overwritten by
  the consent request halfway through was reported from a real call. A new
  assistant turn is held until the audio queued ahead of it has played, keeps
  growing while it waits, and is dropped outright on an interruption, because
  audio that was flushed was never heard. Nothing is held while playback is
  blocked: a suspended `AudioContext` never advances its clock, so the queue
  only grows, and the patient who cannot hear is the one who needs the
  captions most. The patient's own captions are never held.

The caption line itself renders whole (`VoiceTranscriptPanel`), with the CSS
entrance animation on each new turn and nothing per character. A
character-by-character typewriter sat here briefly and had to go: Nova emits
every utterance twice — the words it planned, then the words it spoke — and
the two differ by a comma often enough that the line blanked and typed itself
out a second time mid-greeting, which is what a patient reported. It also kept
the words out of the DOM for as long as it took to type them, which for a long
turn is seconds, and a caption is what a patient who cannot hear is relying
on.

Both are started as soon as the session lands, with no tap. That is what
autoplay policy pushes back on: an `AudioContext` created outside a user
gesture starts suspended. So the engine reports `needsAudioUnlock` and the
opening screen offers a single tap to the patients who need it, rather than
putting a button in front of everyone. The call connects either way, so
captions and typed answers work even if playback never unblocks.

---

## 6. Failure behaviour

Failure-first, per screen and per layer:

| Failure                             | Behaviour                                                                                                                                   |
| ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| No microphone, or permission denied | The call still connects. A banner says so, and every screen keeps its typed path — a patient who cannot or will not speak is never blocked. |
| No Web Audio at all                 | Same: captions and typed answers work.                                                                                                      |
| Socket drops                        | Jittered backoff, five attempts (~30s), a fresh single-use ticket each time.                                                                |
| `1008` close                        | Treated as the session being refused, not a network fault. Not retried.                                                                     |
| Half-open socket                    | 15s heartbeat, 10s pong deadline, then closed — otherwise indistinguishable from a quiet patient.                                           |
| Unrecognized socket frame           | Ignored. An older build against a newer server must not lose a working call.                                                                |
| Malformed REST response             | `ApiError('malformed')` at the service boundary, never an `undefined` three components deep.                                                |
| Consent post fails                  | The patient stays on the step with an explanation. The call genuinely cannot start.                                                         |
| Reschedule slot taken               | `409` → the list is re-read, not the request retried.                                                                                       |
| Upload fails after the bytes landed | Reported as a failure: nothing points at the file, so the physician would never see it.                                                     |

Errors carry no technical detail. `toApiError` normalises everything into a
`kind` screens branch on, with patient-facing copy.

---

## 7. Testing

31 files, 184 tests. Three layers, deliberately:

- **Pure logic** — `prefillMatching`, `intakeProtocol`, `bookingTime`,
  `intakeMessage`. Cheapest and most valuable: the matcher is the one place this
  app could invent a clinical answer, and its test file has a case for every
  "leave it alone" path.
- **Hook behaviour** — `usePrefill`, where the ownership rule is asserted
  directly rather than through a page.
- **Screens** — each page renders inside the real router and the real session
  provider, with the live-call context layered over it (`renderPageInSession`'s
  `call` option) so a test can say "the agent heard this" and assert what
  appears.

Fixtures are obviously synthetic and the clinic timezone is pinned to UTC —
appointment times render in the clinic's zone, so a floating one would make
assertions depend on where the suite ran.

Two bugs in this integration were found by driving the running stack, not by
tests, and both are recorded in `../../docs/INTEGRATION.md` §6a. The lesson is
in the test suite's shape: page tests cannot catch a lifecycle bug in a
provider that jsdom never mounts twice.

---

## 8. What is not done

1. **Interruption is unverified.** Flushing playback on `interrupted` cannot
   be exercised without a real microphone to talk over the assistant with, so
   that path is reasoned about rather than observed.
2. **No audio-level test coverage.** jsdom has no Web Audio, so the capture and
   playback modules are exercised only by their real failure path (returning a
   typed error and degrading to typed answers). Verifying them properly needs a
   real browser.
3. **`expired` is still unreachable** — the backend has no TTL sweep, so an
   interrupted call stays resumable indefinitely.
4. **The booking and doctor-registration surfaces** (D8) are not built. `/book`
   exists as its own responsive surface; doctor registration does not.
5. **Screen recording** stays out of scope by decision (D9).

---

## 9. What should be preserved

- **One waveform component.** `AudioWaveform` is the only place bars are drawn.
  It now takes a real `MediaStream` and follows whoever holds the turn.
- **The screen vocabulary is shared, not parallel.** `PRESCREENING_FLOW_STEPS`
  and the backend's `IntakeScreen` are the same strings. Renaming one side
  silently desynchronizes the call from what the patient sees, and the Zod
  schema on `navigate` is what turns that into a caught error rather than a
  navigation to nowhere.
- **Nothing invented.** Null means "not known" everywhere: no clinician job
  title, no clinic address, no appointment duration, no assistant name that the
  server did not supply. The appointment card omits a row rather than filling
  it.
- **The typed path is a real input channel**, not a fallback of last resort. It
  reaches the same transcript a spoken answer does.

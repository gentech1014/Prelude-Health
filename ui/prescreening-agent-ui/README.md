# Prelude Health UI

A **mobile-only** patient pre-visit screening application. Patients join a conversational,
AI-assisted call before their appointment; the agent gathers relevant clinical
context and produces a concise, physician-ready pre-visit summary.

> The call is live and agent-driven: the patient's microphone streams to the
> backend over one WebSocket, the assistant's speech plays back, both sides are
> transcribed on screen, and **the agent decides which screen the patient is
> on** — nothing in this app advances the flow. See
> [Routing & Session Flow](#routing--session-flow).

## Product Principles

- **Physician-first** — every feature reduces GP context switching and prepares
  the physician for the encounter.
- **Patient-first** — calm, human, simple, and trustworthy experience.
- **Conversation over forms** — natural conversation and targeted follow-ups
  instead of long static questionnaires.
- **Mobile-only** — the application is portrait mobile only; no desktop product
  experience is built.

See [`CLAUDE.md`](./CLAUDE.md) for the full set of enforced product, clinical,
UX, and engineering rules governing this codebase.

## Stack

- React 19 + TypeScript (strict mode)
- Vite
- React Router (`react-router-dom`)
- TanStack Query (`@tanstack/react-query`)
- Zod
- Axios (centralized in [`src/services/apiClient.ts`](./src/services/apiClient.ts))
- Vitest + React Testing Library
- ESLint (flat config) + Prettier
- Plain CSS with design tokens as custom properties (no CSS framework) — see
  [`src/app/theme.css`](./src/app/theme.css)
- [lucide-react](https://lucide.dev) — the only icon set used anywhere in the
  UI; no emoji

Further dependencies are added only as functionality actually requires them,
following the project's YAGNI-first engineering guidelines.

## Project Structure

```text
src/
  app/              # App shell, providers, routing entry
  assets/           # Static assets (images, fonts, icons)
  components/       # Shared, reusable UI components
  features/         # Feature/domain-specific code (e.g. pre-visit screening flow)
  hooks/            # Shared React hooks
  lib/              # Small shared utilities/config
  pages/            # Route-level screens
  services/         # API/client integrations
  types/            # Shared TypeScript types
tests/              # Test suites and fixtures
docs/               # Project documentation
```

Folders are organized around features as complexity grows; no abstractions are
added ahead of actual need.

## Development

Requires Node.js >= 20 and npm.

```bash
npm install
npm run dev
```

Available scripts:

| Script                 | Purpose                                               |
| ---------------------- | ----------------------------------------------------- |
| `npm run dev`          | Start the Vite dev server                             |
| `npm run build`        | Type-check (project references) and build             |
| `npm run preview`      | Preview the production build locally                  |
| `npm run test`         | Run the Vitest suite once                             |
| `npm run test:watch`   | Run Vitest in watch mode                              |
| `npm run lint`         | Run ESLint                                            |
| `npm run typecheck`    | Run the TypeScript compiler with no emit              |
| `npm run format`       | Format the codebase with Prettier                     |
| `npm run format:check` | Check formatting without writing                      |
| `npm run check`        | `lint` + `typecheck` + `test` — run before any commit |

## Environment Variables

Copy `.env.example` to `.env.local` and adjust as needed:

```bash
cp .env.example .env.local
```

| Variable            | Purpose                                                                                                                                                                                                      |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `VITE_API_BASE_URL` | Base URL of the backend REST API, including the `/api/v1` suffix (used by [`src/services/apiClient.ts`](./src/services/apiClient.ts))                                                                        |
| `VITE_WS_BASE_URL`  | Origin of the intake WebSocket. Kept separate on purpose: the backend mounts `/ws/intake/{id}` at the app root, **outside** `/api/v1`, so the socket URL cannot be built by concatenating onto the REST base |

Everything else the app displays — the clinic's name, the assistant's name,
the appointment length, the clinician's credential — comes from the session
context response, not from configuration here. A hardcoded clinic or assistant
name in this app is a value that silently disagrees with the deployment behind
it.

`.env*` files are gitignored. Never commit secrets; server-only secrets must
never be exposed to the client bundle (anything prefixed `VITE_` is public).

## Theming

All color/spacing/radius/shadow/typography values are CSS custom properties
defined once in [`src/app/theme.css`](./src/app/theme.css) — light theme in
`:root`, dark overrides in `:root[data-theme='dark']`. Components must consume
the semantic tokens (`--color-primary`, `--color-surface`, `--color-error`,
`--font-sans`, `--fw-semibold`, …), never the raw scale tokens
(`--emerald-700`, …) or a literal hex value/font-weight number. Update a value
once in `theme.css` and every component picks it up automatically in both
themes.

Typeface is **DM Sans** app-wide (`--font-sans`, loaded via the Google Fonts
`<link>` in [`index.html`](./index.html)), with a system fallback stack and
weight tokens `--fw-regular` / `--fw-medium` / `--fw-semibold` / `--fw-bold`.

Theme mode (`light` / `dark` / `system`) is managed by
[`ThemeProvider`](./src/app/ThemeProvider.tsx) (React Context — justified here
since both the `<html data-theme>` attribute and the toggle button need one
shared source of truth) and read via [`useTheme`](./src/hooks/useTheme.ts).
[`ThemeToggle`](./src/components/ThemeToggle.tsx) cycles the three modes. The
chosen mode persists in `localStorage`, and an inline script in
[`index.html`](./index.html) applies it before first paint to avoid a flash of
the wrong theme.

## UI States

Every product screen's loading/empty/error/feedback states are built from a
small, reusable set in [`src/components/states/`](./src/components/states):
`EmptyState`, `InlineBanner`, `LoadingState`, `Skeleton`/`SkeletonCard`,
`StatusBadge`, `ConfirmDialog`, and the `ToastProvider`/`useToast` system.
Every one of them carries an icon (never color alone) for its meaning. See
[`StatesGallery`](./src/features/theme-preview/StatesGallery.tsx) for how each
of the product's 16 named states (loading, skeleton, empty, error, partial
error, no results, permission denied, unauthorized/session expired, offline,
success, saving, unsaved changes, confirmation, processing, completed,
cancelled) maps onto these components — build new screens on top of them
rather than inventing new one-off state UI.

## Architecture

Current app shell (see [`src/app/App.tsx`](./src/app/App.tsx)):

```text
ErrorBoundary
  ThemeProvider              # resolves + applies light/dark/system theme
    QueryClientProvider (TanStack Query)
      MobileOnlyGate         # desktop/tablet viewports see a mobile-only message
        ToastProvider        # transient action feedback (success/saving/etc.)
          RouterProvider     # React Router, routes defined in src/app/router.tsx
```

Product screens live under `src/pages/` (route-level) and `src/features/`
(domain logic). The call works through eleven screens, in this order:

```text
welcome → confirm-details → patient-concerns → symptom-story
  → medication → allergies → medical-history → recent-care
  → family-social-history → thank-you
```

**That order is the display mapping, not a wizard.** There is no Start
button and no Next button: the call opens as soon as the session lands, the
assistant introduces itself on `welcome`, and every transition after that
is automatic. See [Routing & Session Flow](#routing--session-flow).

`appointment-schedule` is a real screen but not a step — reading the
appointment aloud before the patient has said why they are calling delays
the only part of the conversation that matters. It is reachable from the
closing recap, and it is where a reschedule starts.

The screen ids are shared with the backend verbatim: they are the values of
its `IntakeScreen` enum, and renaming one here without renaming it there
silently desynchronizes the call from what the patient can see.

[`ThemePreview`](./src/pages/ThemePreview.tsx) is a separate style-guide
screen at `/style-guide` (palette swatches, a dashboard-style mobile mockup,
and the UI-states gallery) — not a screen in the product flow.

## Routing & Session Flow

The entire call lives under one dynamic route, `/prescreen/:sessionId`, not
one top-level route per screen. [`router.tsx`](./src/app/router.tsx) nests
every step (`welcome`, `confirm-details`, `appointment-schedule`, …) as a
child of that route; [`PrescreeningSessionProvider`](./src/features/prescreening-session/PrescreeningSessionProvider.tsx)
is the layout element for it, giving every step access to the session's
`sessionId` and its patient/appointment context via
[`usePrescreeningSession`](./src/features/prescreening-session/usePrescreeningSession.ts).
The ordered step list in
[`prescreeningFlowSteps.ts`](./src/features/prescreening-session/prescreeningFlowSteps.ts)
remains the only place `/prescreen/:sessionId/...` is assembled — but it is now
the _display mapping_ the agent's screen ids resolve against, not an order
anything walks through.

The real entry point is a link of the form `/prescreen/:sessionId?token=...`.
`PrescreeningSessionProvider` reads that one-time token once, exchanges it for
an `HttpOnly` session cookie via
[`prescreeningSessionService.ts`](./src/services/prescreeningSessionService.ts),
and immediately strips it from the URL — it is never persisted or left in the
address bar, and the credential that replaces it is one page JavaScript cannot
read. Bare `/` explains that a link is required; it does **not** mint a session
id, which an earlier version did and which produced a session that existed
nowhere.

### Starting, and the one tap that can still appear

The call opens on its own. A patient who has opened their pre-visit screening link
has already said they want to begin, so `AppBoot` holds a loading screen
until the assistant is actually on the line — and then stands aside.
Which screen comes first is the server's answer, applied by
`IntakeCallProvider`: normally `welcome`, but a resumed call picks up
wherever it was cut off.

What that costs is autoplay: an `AudioContext` created outside a user
gesture starts suspended on most browsers. Rather than put a button in
front of everyone, the engine reports `needsAudioUnlock` and the opening
screen offers one tap only to the patients who need it. The call connects
and runs either way — captions and typed answers work even if playback
never unblocks.

### The agent owns the flow

[`IntakeCallProvider`](./src/features/prescreening-session/IntakeCallProvider.tsx)
hosts the live call and is the only thing that navigates between steps. The
server sends a `navigate` frame, this navigates there, and
[`useIntakeCallEngine`](./src/features/prescreening-session/useIntakeCallEngine.ts)
acknowledges it. Navigation is one-directional by design: nothing in the UI
advances the flow, so the screen cannot get ahead of the conversation or fall
behind it. There are no back/next controls, and each page asserts that it has
none.

Two narrow exceptions, both about the opening screen and both documented
where they happen: leaving `welcome` waits for the assistant to stop
talking, so its introduction is not cut off mid-sentence; and the browser
will reach `confirm-details` itself if the assistant has not, because the
control the patient gives consent with lives there and nowhere else.

The engine owns everything imperative about the call:

| Concern                                     | Where                                                                                                                                                |
| ------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| Wire contract (Zod-parsed, both directions) | [`intakeProtocol.ts`](./src/features/prescreening-session/intakeProtocol.ts)                                                                         |
| Socket, reconnect, heartbeat, controls      | [`useIntakeCallEngine.ts`](./src/features/prescreening-session/useIntakeCallEngine.ts)                                                               |
| Mic capture → PCM16 @ 16 kHz                | [`audio/micCapture.ts`](./src/features/prescreening-session/audio/micCapture.ts)                                                                     |
| Agent playback + its `MediaStream`          | [`audio/agentPlayback.ts`](./src/features/prescreening-session/audio/agentPlayback.ts)                                                               |
| Prefill from what the agent heard           | [`usePrefill.ts`](./src/features/prescreening-session/usePrefill.ts), [`prefillMatching.ts`](./src/features/prescreening-session/prefillMatching.ts) |
| The shell every in-call screen shares       | [`CallScreenLayout.tsx`](./src/features/prescreening-session/CallScreenLayout.tsx)                                                                   |

Audio never touches React state — frames arrive around thirty times a second
and go straight to the playback queue in a ref. Only what a screen renders
lives in state.

Two rules govern prefill, and both matter clinically. **The server leads until
the patient touches a control**, because prefill arrives asynchronously and can
arrive twice. **The patient wins from then on**, because the whole point of
showing the value is that they can correct a mishearing. Nothing here is a
source of clinical truth: every edit is sent back as the patient’s own turn in
the same conversation, and the transcript on the server is what the report is
built from. An unmatched value is simply left unselected —
`prefillMatching.ts` returns null rather than rounding a near-miss up to the
closest checkbox.

### Resuming a dropped call

A lost socket reconnects with jittered backoff (five attempts, ~30s), fetching
a fresh single-use ticket each time. A `1008` close means the session was
refused, not that the network failed, and is not retried. The backend keeps a
resume marker, so the first frame of the new socket says which screen the call
had reached and what it had already collected — the patient lands there rather
than back at the first question, and the assistant acknowledges the drop in one
sentence instead of starting over.

## Booking Surface (`/book`)

Appointment booking is a **separate product surface** from the pre-screening
call, and the only responsive desktop screen in the app.
[`BookAppointment`](./src/pages/BookAppointment.tsx) composes the feature
components in [`src/features/booking/`](./src/features/booking) into the
three-stage flow the design specifies: visit type and provider, date and
time, then confirm.

Everything on it is live, via
[`bookingService.ts`](./src/services/bookingService.ts) (Zod-validated at the
boundary like every other service here):

- the visit types come from `GET /booking/visit-types` — the backend's own
  vocabulary, not a list hardcoded in the frontend. Seven options: the five
  clinical categories, plus a general checkup and "I am not sure", which carry
  a null `symptomCategory` and match every provider;
- the providers come from `GET /booking/providers?category=…`;
- the times come from `GET /booking/providers/{id}/availability`, computed
  against the doctor's Google Calendar when they have connected it and
  against clinic hours otherwise. The screen states which, so an unverified
  slot never looks calendar-confirmed;
- booking posts to `POST /mock-booking/appointments`, which creates the
  pre-screening session. A `409` means the slot was taken while the page was
  open: availability is refetched and the patient re-picks, rather than the
  submit being retried.

Provider cards deliberately show no ratings or review counts — this system
holds no such data, and fabricating a clinical credential is not acceptable
even as placeholder UI.

**Patient ID is optional.** Most patients cannot recall an MRN, and blocking a
booking on one is worse than issuing an identifier the clinic reconciles
later. Left blank, the server mints a visibly `provisional-` prefixed id and
the confirmation says so, so a generated id is never mistaken for a
clinic-issued one.

### The intake message preview

The confirmation shows a phone rendering the SMS the patient just received
([`IntakeMessagePreview`](./src/features/booking/IntakeMessagePreview.tsx)).
Booking ends on this screen but the pre-screening call starts on the
patient's own phone, and without seeing the message it is not obvious that
anything reaches them at all. The body is the server's own
`notification_message`, not a re-typed approximation, so a change to the
clinic's wording changes this too — and the link is live, so the hand-off can
be walked end to end from here.

The device is drawn in CSS rather than shipped as an image, so it inherits
the booking palette and stays crisp at any zoom.

> The intake link is a session credential, and this is the **only** place the
> booking UI renders it — inside the message preview, as the patient's own
> copy. A test pins that: the token must appear in exactly one element, and
> that element must be inside the preview.

### Palette

This surface is **monochrome, and deliberately not the product's emerald
tokens** — booking is a separate surface from the pre-screening call and
should not read as the same screen. White ground, hairline rules, and one
near-black ink for every selected and primary state. The whole palette is
~20 CSS custom properties scoped to `.booking-shell` at the top of
[`booking.css`](./src/features/booking/booking.css), with an inverted dark
block beneath it, so re-tinting the entire screen is a change to that one
block and touches nothing else in the app.

Two deliberate exceptions: validation errors keep the global `--color-error`
token, and error/warning banners and toasts keep their tones — flattening a
red field message or a "that time was just taken" warning into the
monochrome would cost meaning a patient must not miss. Purely informational
notices use `tone="neutral"` instead of `info` so they stay on-palette.

`.booking-shell` also paints an opaque background, because
[`AppBackgroundScenery`](./src/components/AppBackgroundScenery.tsx) is
mounted app-wide behind every route and its green hills would otherwise show
through.

### Provider portraits

Photos come from the API as `photoUrl` on each provider — they are live data
(seeded Unsplash URLs, or the doctor's own Google account picture captured at
registration), never a map hardcoded in the frontend.
[`ProviderAvatar`](./src/features/booking/ProviderAvatar.tsx) renders the
image and falls back to initials both when `photoUrl` is null _and_ on
`onError`, so a doctor with no photo or a dead image host still produces a
readable card rather than a broken-image icon. Portraits render grayscale and
go full-colour on hover/selection, so differently-lit stock photography does
not drag six colour casts into a monochrome screen.

> The seeded photos are **stock portraits standing in for real provider
> photography** — placeholders for a demo, not the likeness of the named
> doctor. Replace them with each clinician's own photo (or leave the field
> blank, which falls back to initials) before this is shown to patients.

**Mobile-only gating moved.** [`MobileOnlyGate`](./src/components/MobileOnlyGate.tsx)
used to wrap the whole router in `App.tsx`, which would have shown "continue
on your phone" on the booking screen too. It now wraps only the
`/prescreen/:sessionId` route — the patient's own phone experience — leaving
booking responsive across desktop, tablet, and mobile.

**Doctor registration.** The top-bar settings menu opens
[`DoctorRegistrationDialog`](./src/features/booking/DoctorRegistrationDialog.tsx),
which collects the doctor's profile and then navigates to Google's consent
screen for calendar access. The dialog never sees a token or an authorization
code — the API's callback completes the grant and redirects back to
`/book?doctor_registration=…`, whose outcome the page reports once and then
strips from the URL. When the backend has no OAuth client configured it
answers `503` and the dialog says the feature is unavailable.

## API Configuration

HTTP calls go through the single Axios instance in
[`src/services/apiClient.ts`](./src/services/apiClient.ts), configured from
`VITE_API_BASE_URL`. Do not instantiate Axios or call `fetch` ad hoc elsewhere;
add new endpoints as functions in `src/services/` and consume them through
TanStack Query hooks. All external responses must be validated with Zod at the
service boundary before use.

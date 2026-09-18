import { z } from 'zod';
import {
  INTAKE_SCREENS,
  type IntakeScreenName,
  type PrescreeningFlowStep,
} from '@/features/prescreening-session/prescreeningFlowSteps';

/**
 * The intake WebSocket's wire contract, mirroring the backend's
 * `app/schemas/intake_channel.py`. Parsed, never trusted: this socket
 * carries the whole call, and an unrecognized frame must fail at the
 * boundary rather than surface as an undefined three components deep.
 *
 * Deliberately not the raw agent-runtime event vocabulary — the backend
 * translates first, so tool names, streamed tool arguments and token usage
 * never reach the browser.
 */
export const INTAKE_PROTOCOL_VERSION = 4;

/** A screen the agent can send the patient to. Same strings as the route segments. */
const intakeScreenSchema = z.enum(INTAKE_SCREENS);

const prefillSchema = z.record(z.string(), z.record(z.string(), z.string()));

/**
 * Which on-screen option the agent picked, keyed by screen then by field,
 * as comma-joined option ids the server has already validated.
 *
 * Separate from `prefill` because the two say different things. `prefill`
 * is what the patient said, which is what they read back and correct;
 * this is which control that means, which is what ticks. A field missing
 * here is one the agent left alone, and the screen falls back to matching
 * the words — so a server that never sends this behaves like protocol 2.
 */
const selectionsSchema = z.record(z.string(), z.record(z.string(), z.string()));

/**
 * The symptom screen's whole state, sent as a snapshot rather than a delta.
 *
 * That screen has no fixed fields: the assistant picks each question from
 * the clinic's question bank based on what the patient has already said,
 * shows one at a time, and moves the answer onto the record behind it. A
 * snapshot is what makes a reconnect land mid-conversation instead of
 * back at the first question.
 */
const symptomIntakeSchema = z.object({
  current: z.object({ question_id: z.string(), text: z.string() }).nullable(),
  answers: z.array(
    z.object({
      question_id: z.string(),
      question: z.string(),
      answer: z.string(),
      /**
       * How well the assistant actually knows this answer.
       *
       * Optional, defaulting to `confirmed`, so an older server that
       * never sends it behaves exactly as before. It exists because a
       * declined question and an answered one used to render
       * identically — the patient saw "Preferred not to say" sitting in
       * an answer box with no indication it was a boundary they had set
       * rather than a fact they had given.
       */
      status: z
        .enum(['confirmed', 'uncertain', 'inferred', 'undisclosed', 'unknown'])
        .default('confirmed'),
    }),
  ),
  answered_count: z.number(),
});

const connectedSchema = z.object({
  type: z.literal('connected'),
  protocol_version: z.number(),
  screen: intakeScreenSchema,
  prefill: prefillSchema,
  selections: selectionsSchema.default({}),
  symptoms: symptomIntakeSchema,
  audio: z.object({
    sample_rate: z.number(),
    channels: z.number(),
    format: z.string(),
  }),
});

const serverEventSchema = z.discriminatedUnion('type', [
  connectedSchema,
  z.object({ type: z.literal('agent_ready') }),
  z.object({ type: z.literal('agent_audio'), audio: z.string(), sample_rate: z.number() }),
  z.object({
    type: z.literal('transcript'),
    role: z.enum(['agent', 'patient']),
    text: z.string(),
    is_final: z.boolean(),
  }),
  z.object({ type: z.literal('agent_speaking'), speaking: z.boolean() }),
  z.object({ type: z.literal('interrupted') }),
  z.object({ type: z.literal('navigate'), screen: intakeScreenSchema, sequence: z.number() }),
  z.object({
    type: z.literal('form_prefill'),
    screen: intakeScreenSchema,
    fields: z.record(z.string(), z.string()),
    selections: z.record(z.string(), z.string()).default({}),
  }),
  symptomIntakeSchema.extend({ type: z.literal('symptom_state') }),
  z.object({ type: z.literal('upload_requested') }),
  /**
   * The appointment behind this session has moved, so the copy of it the
   * app fetched over REST is stale.
   *
   * Carries no appointment on purpose: the session context route is the
   * authority on when the patient is expected, and a second copy on this
   * wire is a second thing that can be wrong.
   */
  z.object({ type: z.literal('appointment_updated') }),
  z.object({
    type: z.literal('call_ended'),
    reason: z.enum(['completed', 'interrupted', 'failed']),
  }),
  z.object({
    type: z.literal('error'),
    code: z.string(),
    message: z.string(),
    recoverable: z.boolean(),
  }),
  z.object({ type: z.literal('pong') }),
]);

export type IntakeServerEvent = z.infer<typeof serverEventSchema>;
export type IntakeConnectedEvent = z.infer<typeof connectedSchema>;
export type CallEndReason = 'completed' | 'interrupted' | 'failed';

/** Values the agent heard, keyed by screen then by field. */
export type IntakePrefill = Readonly<Record<string, Readonly<Record<string, string>>>>;

/** Option ids the agent picked, keyed by screen then by field. */
export type IntakeSelections = Readonly<Record<string, Readonly<Record<string, string>>>>;

/** The symptom screen's state as the server last described it. */
export type IntakeSymptomState = z.infer<typeof symptomIntakeSchema>;

/**
 * Parses one inbound frame, returning null for anything unrecognized.
 *
 * Null rather than throwing: a frame this build does not know about (an
 * older browser against a newer server) must not tear down a call that is
 * otherwise working.
 */
export function parseIntakeServerEvent(raw: unknown): IntakeServerEvent | null {
  const parsed = serverEventSchema.safeParse(raw);
  return parsed.success ? parsed.data : null;
}

export type IntakeControlAction = 'mute' | 'unmute' | 'hold' | 'resume' | 'hangup';

/**
 * Frames the browser sends. Built only through these constructors so the
 * set of things this app can say to the server is enumerable from one file.
 */
export const intakeClientEvent = {
  audio: (audio: string) => ({ type: 'client_audio' as const, audio }),
  text: (text: string) => ({ type: 'client_text' as const, text }),
  formUpdate: (screen: PrescreeningFlowStep, field: string, value: string) => ({
    type: 'client_form_update' as const,
    screen,
    field,
    value,
  }),
  /**
   * An answer the patient typed on the symptom screen — either to the live
   * question or as a correction to one already answered. Addressed by
   * question id because that screen has no fixed fields.
   */
  symptomAnswer: (questionId: string, value: string) => ({
    type: 'client_symptom_answer' as const,
    question_id: questionId,
    value,
  }),
  screenAck: (screen: IntakeScreenName) => ({
    type: 'client_screen_ack' as const,
    screen,
  }),
  /**
   * The patient asked for a different appointment time by tapping rather
   * than saying so. Decides nothing: the assistant still offers the times,
   * so a tap and a spoken request produce the same conversation.
   */
  rescheduleRequested: () => ({ type: 'client_reschedule_requested' as const }),
  /**
   * The patient picked a new time on screen and it is already saved.
   *
   * Sent after the reschedule request has succeeded, so the assistant
   * acknowledges the new time instead of trying to write it again. The
   * server re-reads the appointment rather than believing a time from
   * here, which is why this carries none.
   */
  appointmentRescheduled: () => ({ type: 'client_appointment_rescheduled' as const }),
  /**
   * Tells the server consent has just been recorded, so the agent can move
   * past it. A hint about when to look, not the answer: the server re-reads
   * the session and believes that.
   */
  consentRecorded: () => ({ type: 'client_consent_recorded' as const }),
  /**
   * A supporting document finished uploading and the write succeeded.
   *
   * The upload goes to storage over REST, which the socket cannot see, so
   * without this the assistant never learned a file had arrived — it had
   * been told not to wait and not to ask, and the patient had no idea they
   * were expected to announce it. The assistant asks what the report is.
   */
  documentUploaded: (fileName: string) => ({
    type: 'client_document_uploaded' as const,
    file_name: fileName,
  }),
  /**
   * A supporting document was offered and did not make it. The more
   * important half: a failure was otherwise silent, so the assistant moved
   * on while the patient was still looking at an error.
   */
  documentUploadFailed: () => ({ type: 'client_document_upload_failed' as const }),
  control: (action: IntakeControlAction) => ({ type: 'client_control' as const, action }),
  ping: () => ({ type: 'client_ping' as const }),
};

export type IntakeClientEvent = ReturnType<
  (typeof intakeClientEvent)[keyof typeof intakeClientEvent]
>;

/** Origin of the intake socket — mounted at the app root, outside `/api/v1`. */
const WS_BASE_URL = import.meta.env.VITE_WS_BASE_URL ?? 'ws://localhost:8000';

/**
 * The socket URL for one connection.
 *
 * The ticket is single-use and lives about a minute, which is why it is
 * tolerable in a query string at all: a browser cannot set headers on a
 * WebSocket handshake, so the credential has to travel where access logs
 * and proxy traces can see it. Request a fresh one immediately before
 * every connect, including every reconnect.
 */
export function buildIntakeSocketUrl(sessionId: string, ticket: string): string {
  return `${WS_BASE_URL}/ws/intake/${encodeURIComponent(sessionId)}?ticket=${encodeURIComponent(ticket)}`;
}

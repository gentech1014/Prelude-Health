import { z } from 'zod';
import {
  PRESCREENING_FLOW_STEPS,
  type PrescreeningFlowStep,
} from '@/features/prescreening-session/prescreeningFlowSteps';
import { apiClient, ApiError, toApiError } from '@/services/apiClient';

/**
 * Mirrors the backend's `SessionState`. Parsed rather than trusted: an
 * unrecognized state must fail at the boundary, not render as a blank screen
 * three components deep.
 */
export const SESSION_STATUSES = [
  'booking_created',
  'ai_link_ready',
  'notification_sent',
  'started',
  'in_progress',
  'interrupted',
  'declined',
  'completed',
  'summarizing',
  'summary_ready',
  'summary_failed',
  'video_ready',
  'expired',
] as const;

const sessionStatusSchema = z.enum(SESSION_STATUSES);
export type SessionStatus = z.infer<typeof sessionStatusSchema>;

const sessionStatusResponseSchema = z.object({
  session_id: z.string(),
  status: sessionStatusSchema,
  document_uploaded: z.boolean(),
  has_report: z.boolean(),
  has_video: z.boolean(),
});

const sessionContextResponseSchema = z.object({
  session_id: z.string(),
  status: sessionStatusSchema,
  consent_given: z.boolean(),
  document_uploaded: z.boolean(),
  has_report: z.boolean(),
  patient: z.object({
    name: z.string(),
    date_of_birth: z.string(),
    sex: z.enum(['male', 'female', 'other']),
    contact_phone: z.string().nullable(),
  }),
  appointment: z.object({
    appointment_id: z.string(),
    physician: z.string(),
    physician_credential: z.string().nullable(),
    scheduled_at: z.string(),
    duration_minutes: z.number(),
    booking_reason: z.string().nullable(),
  }),
  assistant: z.object({ name: z.string(), role: z.string() }),
  clinic: z.object({
    name: z.string().nullable(),
    location: z.string().nullable(),
    timezone: z.string(),
  }),
  call_progress: z.object({
    screen: z.enum(PRESCREENING_FLOW_STEPS).nullable(),
    details: z.record(z.string(), z.record(z.string(), z.string())),
  }),
});

const wsTicketResponseSchema = z.object({
  ticket: z.string(),
  expires_in_seconds: z.number(),
});

/**
 * The session's patient and appointment detail, in the app's own shape.
 *
 * Also carries the deployment's own clinic and assistant identity. The app
 * holds no copy of either: a hardcoded clinic name or assistant name is a
 * value that silently disagrees with the deployment behind it.
 */
export interface PrescreeningSessionContext {
  sessionId: string;
  status: SessionStatus;
  consentGiven: boolean;
  documentUploaded: boolean;
  patient: {
    name: string;
    dateOfBirth: Date;
    sex: 'male' | 'female' | 'other';
    /** What the booking gave. Null means show an empty field, never a guess. */
    contactPhone: string | null;
  };
  appointment: {
    appointmentId: string;
    physician: string;
    /** Post-nominal from the doctor's record. Null means show no subtitle, not a guess. */
    physicianCredential: string | null;
    scheduledAt: Date;
    durationMinutes: number;
    bookingReason: string | null;
  };
  assistant: { name: string; role: string };
  clinic: { name: string | null; location: string | null; timezone: string };
  /** How far a previous call got, for resume. Screen is null before the first call. */
  callProgress: {
    screen: PrescreeningFlowStep | null;
    details: Readonly<Record<string, Readonly<Record<string, string>>>>;
  };
}

export interface PrescreeningSessionStatus {
  sessionId: string;
  status: SessionStatus;
  documentUploaded: boolean;
  hasReport: boolean;
}

// Mapped to a view model rather than passed through, per CLAUDE.md: the UI
// should not be coupled to the backend's snake_case wire shape.
function toContext(raw: z.infer<typeof sessionContextResponseSchema>): PrescreeningSessionContext {
  return {
    sessionId: raw.session_id,
    status: raw.status,
    consentGiven: raw.consent_given,
    documentUploaded: raw.document_uploaded,
    patient: {
      name: raw.patient.name,
      dateOfBirth: new Date(raw.patient.date_of_birth),
      sex: raw.patient.sex,
      contactPhone: raw.patient.contact_phone,
    },
    appointment: {
      appointmentId: raw.appointment.appointment_id,
      physician: raw.appointment.physician,
      physicianCredential: raw.appointment.physician_credential,
      scheduledAt: new Date(raw.appointment.scheduled_at),
      durationMinutes: raw.appointment.duration_minutes,
      bookingReason: raw.appointment.booking_reason,
    },
    assistant: raw.assistant,
    clinic: raw.clinic,
    callProgress: {
      screen: raw.call_progress.screen,
      details: raw.call_progress.details,
    },
  };
}

/**
 * Validates and maps a session-context payload from any endpoint that
 * returns one. Exported for the appointment routes, which also answer with
 * the full context so the caller can render from one response.
 */
export function parsePrescreeningSessionContext(payload: unknown): PrescreeningSessionContext {
  const parsed = sessionContextResponseSchema.safeParse(payload);
  if (!parsed.success) {
    throw new ApiError('malformed', 'We received an unexpected response. Try again shortly.');
  }
  return toContext(parsed.data);
}

async function request<T>(
  schema: z.ZodType<T>,
  call: () => Promise<{ data: unknown }>,
): Promise<T> {
  let payload: unknown;
  try {
    payload = (await call()).data;
  } catch (cause) {
    throw toApiError(cause);
  }

  const parsed = schema.safeParse(payload);
  if (!parsed.success) {
    throw new ApiError('malformed', 'We received an unexpected response. Try again shortly.');
  }
  return parsed.data;
}

/**
 * Exchanges the entry link's one-time token for an HttpOnly session cookie
 * and returns the session's context. The only call that ever sends the token;
 * every request after it authenticates with the cookie the backend set here,
 * which page JavaScript cannot read.
 */
export async function attachPrescreeningSession(
  sessionId: string,
  token: string,
): Promise<PrescreeningSessionContext> {
  const raw = await request(sessionContextResponseSchema, () =>
    apiClient.post(`/sessions/${sessionId}/attach`, null, { params: { token } }),
  );
  return toContext(raw);
}

/** Re-reads the session's context using the cookie already held. */
export async function fetchPrescreeningSessionContext(
  sessionId: string,
): Promise<PrescreeningSessionContext> {
  const raw = await request(sessionContextResponseSchema, () =>
    apiClient.get(`/sessions/${sessionId}/context`),
  );
  return toContext(raw);
}

/** Polls lifecycle state only — carries no patient detail, so it is safe to call repeatedly. */
export async function fetchPrescreeningSessionStatus(
  sessionId: string,
): Promise<PrescreeningSessionStatus> {
  const raw = await request(sessionStatusResponseSchema, () =>
    apiClient.get(`/sessions/${sessionId}`),
  );
  return {
    sessionId: raw.session_id,
    status: raw.status,
    documentUploaded: raw.document_uploaded,
    hasReport: raw.has_report,
  };
}

/** Records the patient's consent decision. An affirmative one gates the live call. */
export async function submitConsent(
  sessionId: string,
  given: boolean,
): Promise<PrescreeningSessionContext> {
  const raw = await request(sessionContextResponseSchema, () =>
    apiClient.post(`/sessions/${sessionId}/consent`, { given }),
  );
  return toContext(raw);
}

/**
 * Mints a single-use, seconds-long ticket for one WebSocket connection.
 * Request it immediately before connecting — it expires in about a minute
 * and is spent the moment the socket opens.
 */
export async function requestWsTicket(sessionId: string): Promise<string> {
  const raw = await request(wsTicketResponseSchema, () =>
    apiClient.post(`/sessions/${sessionId}/ws-ticket`),
  );
  return raw.ticket;
}

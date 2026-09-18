import { z } from 'zod';
import { apiClient, ApiError, toApiError } from '@/services/apiClient';
import {
  parsePrescreeningSessionContext,
  type PrescreeningSessionContext,
} from '@/services/prescreeningSessionService';

/**
 * Open times with this session's own doctor, and moving the appointment to
 * one of them.
 *
 * Both routes are scoped to the session and behind its cookie, so the
 * patient app never resolves a `doctor_id` itself or reaches an endpoint
 * that can enumerate providers.
 */

const slotSchema = z.object({
  start: z.string(),
  end: z.string(),
  label: z.string(),
});

const availabilityResponseSchema = z.object({
  doctor_id: z.string(),
  timezone: z.string(),
  calendar_connected: z.boolean(),
  days: z.array(
    z.object({
      day: z.string(),
      weekday_label: z.string(),
      day_label: z.string(),
      source: z.enum(['calendar', 'clinic_hours']),
      slots: z.array(slotSchema),
    }),
  ),
});

/** One bookable start time. `start` is echoed back verbatim when confirming. */
export interface AppointmentSlot {
  /** The start time is already unique per doctor, so it needs no separate id. */
  id: string;
  /** ISO timestamp, offset-aware, exactly as the server sent it. */
  start: string;
  timeRange: string;
}

/**
 * How a day's slots were established.
 *
 * Carried through to the UI so a slot derived from clinic hours alone is
 * never presented as though the doctor's own calendar had confirmed it.
 */
export type AvailabilitySource = 'calendar' | 'clinic_hours';

/** One calendar day and its open times, in the clinic's own timezone. */
export interface AppointmentDay {
  /** `YYYY-MM-DD`, in the clinic's timezone -- the stable key for this day. */
  day: string;
  weekdayLabel: string;
  dayLabel: string;
  slots: readonly AppointmentSlot[];
}

export interface AppointmentAvailability {
  timezone: string;
  /** True when the doctor's own calendar was checked, not just clinic hours. */
  isCalendarConnected: boolean;
  /**
   * Grouped by day, not flattened: picking a date before a time is what
   * keeps this list short enough to scroll on a phone. See
   * `AppointmentDaySlotPicker`.
   */
  days: readonly AppointmentDay[];
}

/** Open times with this session's doctor, day by day over the reschedule horizon. */
export async function fetchAppointmentAvailability(
  sessionId: string,
): Promise<AppointmentAvailability> {
  let payload: unknown;
  try {
    payload = (await apiClient.get(`/sessions/${sessionId}/appointment/availability`)).data;
  } catch (cause) {
    throw toApiError(cause);
  }

  const parsed = availabilityResponseSchema.safeParse(payload);
  if (!parsed.success) {
    throw new ApiError('malformed', 'We received an unexpected response. Try again shortly.');
  }

  const timeFormatter = new Intl.DateTimeFormat('en-US', {
    hour: 'numeric',
    minute: '2-digit',
    timeZone: parsed.data.timezone,
  });

  const days = parsed.data.days.map((day) => ({
    day: day.day,
    weekdayLabel: day.weekday_label,
    dayLabel: day.day_label,
    slots: day.slots.map((slot) => ({
      id: slot.start,
      start: slot.start,
      timeRange: `${timeFormatter.format(new Date(slot.start))} – ${timeFormatter.format(new Date(slot.end))}`,
    })),
  }));

  return {
    timezone: parsed.data.timezone,
    isCalendarConnected: parsed.data.calendar_connected,
    days,
  };
}

/**
 * Moves the appointment, returning the session's updated context.
 *
 * The slot is re-validated server-side, so a 409 here means someone else
 * took it while the patient was reading the list — the caller re-fetches
 * rather than retrying the same request.
 */
export async function rescheduleAppointment(
  sessionId: string,
  slot: AppointmentSlot,
): Promise<PrescreeningSessionContext> {
  let payload: unknown;
  try {
    payload = (
      await apiClient.post(`/sessions/${sessionId}/appointment/reschedule`, {
        start: slot.start,
      })
    ).data;
  } catch (cause) {
    throw toApiError(cause);
  }
  return parsePrescreeningSessionContext(payload);
}

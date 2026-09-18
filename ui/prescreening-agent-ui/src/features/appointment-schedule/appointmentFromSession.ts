import type { AppointmentSummary } from '@/features/appointment-schedule/AppointmentDetailsCard';
import type { PrescreeningSessionContext } from '@/services/prescreeningSessionService';

/**
 * Maps a session's booking record onto the card's view model.
 *
 * Every value comes from the server. The clinician's credential, the
 * appointment length and the facility name are all served on the session
 * context precisely so this function has nothing to invent — an earlier
 * version filled in a job title, a duration and a clinic address that
 * nobody had stated, which is exactly the kind of plausible-looking
 * fabrication a patient cannot tell apart from a real record.
 *
 * Anything the backend does not know is left null, and the card omits that
 * row rather than rendering a placeholder.
 */
export function appointmentFromSession(
  session: PrescreeningSessionContext,
  timeZone?: string,
): AppointmentSummary {
  const { scheduledAt, physician, physicianCredential, durationMinutes, bookingReason } =
    session.appointment;
  const endsAt = new Date(scheduledAt.getTime() + durationMinutes * 60_000);

  // Formatted in the clinic's own zone, not the browser's: a patient
  // travelling, or on a phone with the wrong zone, must still be told the
  // time their clinic means.
  const zone = timeZone ?? session.clinic.timezone;
  const format = (options: Intl.DateTimeFormatOptions): Intl.DateTimeFormat =>
    new Intl.DateTimeFormat('en-US', { ...options, timeZone: zone });

  return {
    weekday: format({ weekday: 'short' }).format(scheduledAt),
    day: format({ month: 'short', day: 'numeric' }).format(scheduledAt),
    year: format({ year: 'numeric' }).format(scheduledAt),
    clinician: physician,
    clinicianCredential: physicianCredential,
    timeRange: `${format({ hour: 'numeric', minute: '2-digit' }).format(scheduledAt)} – ${format({
      hour: 'numeric',
      minute: '2-digit',
    }).format(endsAt)}`,
    duration: `${durationMinutes} minutes`,
    visitReason: bookingReason,
    location: session.clinic.name,
    locationDetail: session.clinic.location,
  };
}

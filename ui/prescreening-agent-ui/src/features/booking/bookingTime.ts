/**
 * Formats a slot's start time in the clinic's own timezone.
 *
 * The picker's slot labels are rendered server-side in the clinic's
 * timezone, so formatting anything else in the browser's timezone makes the
 * same appointment read as two different times — pick "10:00 AM", then see
 * "3:30 PM" on the confirmation. The zone is named in the output so the time
 * is never ambiguous to a patient in a different one.
 */
export function formatAppointmentLabel(startIso: string, timeZone: string | null): string {
  const label = format(startIso, timeZone);
  return timeZone === null ? label : `${label} (${timeZone})`;
}

function format(startIso: string, timeZone: string | null): string {
  const options: Intl.DateTimeFormatOptions = {
    weekday: 'long',
    month: 'long',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  };

  try {
    return new Intl.DateTimeFormat(
      'en-US',
      timeZone === null ? options : { ...options, timeZone },
    ).format(new Date(startIso));
  } catch {
    // An unrecognized IANA zone from the API must not blank out the
    // appointment the patient is about to book.
    return new Intl.DateTimeFormat('en-US', options).format(new Date(startIso));
  }
}

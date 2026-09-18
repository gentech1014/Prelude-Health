import type { PrescreeningSessionContext } from '@/services/prescreeningSessionService';

/** Fixed id so every page test's URL is deterministic — never a real session. */
export const TEST_SESSION_ID = 'test-session';

/**
 * Obviously synthetic patient, per the project's sensitive-test-data rule:
 * no realistic identity, and a deterministic date so formatted output is
 * stable across runs and time zones.
 *
 * The clinic timezone is pinned to UTC for the same reason — appointment
 * times are rendered in the clinic's zone, so a floating one would make
 * every assertion depend on where the test ran.
 */
export function buildTestSession(
  overrides: Partial<PrescreeningSessionContext> = {},
): PrescreeningSessionContext {
  return {
    sessionId: TEST_SESSION_ID,
    status: 'started',
    consentGiven: false,
    documentUploaded: false,
    patient: {
      name: 'Test Patient',
      dateOfBirth: new Date('1990-01-01T00:00:00Z'),
      sex: 'other',
      contactPhone: '+1 555 0100',
    },
    appointment: {
      appointmentId: 'appt_test',
      physician: 'Dr. Test',
      physicianCredential: 'MD',
      scheduledAt: new Date('2026-09-23T14:30:00Z'),
      durationMinutes: 30,
      bookingReason: 'Routine check-up',
    },
    assistant: { name: 'Test Assistant', role: 'Pre-visit assistant' },
    clinic: { name: 'Test Clinic', location: 'Test Site', timezone: 'UTC' },
    callProgress: { screen: null, details: {} },
    ...overrides,
  };
}

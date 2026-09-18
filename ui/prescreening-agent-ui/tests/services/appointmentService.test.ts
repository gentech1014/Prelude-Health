import { beforeEach, describe, expect, it, vi } from 'vitest';
import { apiClient } from '@/services/apiClient';
import { fetchAppointmentAvailability, rescheduleAppointment } from '@/services/appointmentService';

// Deterministic, obviously synthetic, and in a fixed zone: slot labels are
// rendered in the clinic's timezone, so a floating one would make these
// assertions depend on where the suite happens to run.
const AVAILABILITY_RESPONSE = {
  doctor_id: 'dr-test',
  timezone: 'UTC',
  calendar_connected: true,
  days: [
    {
      day: '2026-09-24',
      weekday_label: 'Thu',
      day_label: '24',
      source: 'calendar',
      slots: [
        {
          start: '2026-09-24T09:00:00+00:00',
          end: '2026-09-24T09:30:00+00:00',
          label: '9:00 AM',
        },
      ],
    },
    {
      day: '2026-09-25',
      weekday_label: 'Fri',
      day_label: '25',
      source: 'calendar',
      slots: [],
    },
  ],
};

describe('fetchAppointmentAvailability', () => {
  beforeEach(() => vi.restoreAllMocks());

  it('keeps the server days grouped, so the picker can offer a date before a time', async () => {
    vi.spyOn(apiClient, 'get').mockResolvedValue({ data: AVAILABILITY_RESPONSE });

    const availability = await fetchAppointmentAvailability('test-session');

    expect(availability.timezone).toBe('UTC');
    expect(availability.isCalendarConnected).toBe(true);
    expect(availability.days).toHaveLength(2);
    expect(availability.days[0]).toMatchObject({ day: '2026-09-24', weekdayLabel: 'Thu', dayLabel: '24' });
    expect(availability.days[0]?.slots).toEqual([
      { id: '2026-09-24T09:00:00+00:00', start: '2026-09-24T09:00:00+00:00', timeRange: '9:00 AM – 9:30 AM' },
    ]);
    // The second day is genuinely open with nothing left, not omitted.
    expect(availability.days[1]?.slots).toEqual([]);
  });

  it('reports whether the doctor’s own calendar was actually checked', async () => {
    // A clinic-hours slot must never reach the UI looking like a
    // calendar-confirmed one — the screen says so explicitly.
    vi.spyOn(apiClient, 'get').mockResolvedValue({
      data: { ...AVAILABILITY_RESPONSE, calendar_connected: false },
    });

    const availability = await fetchAppointmentAvailability('test-session');

    expect(availability.isCalendarConnected).toBe(false);
  });

  it('rejects a malformed response at the boundary', async () => {
    vi.spyOn(apiClient, 'get').mockResolvedValue({ data: { days: 'not-an-array' } });

    await expect(fetchAppointmentAvailability('test-session')).rejects.toMatchObject({
      kind: 'malformed',
    });
  });
});

describe('rescheduleAppointment', () => {
  beforeEach(() => vi.restoreAllMocks());

  it('sends the slot start verbatim, so the server re-validates what was shown', async () => {
    const post = vi.spyOn(apiClient, 'post').mockRejectedValue(new Error('stop here'));

    await expect(
      rescheduleAppointment('test-session', {
        id: '2026-09-24T09:00:00+00:00',
        start: '2026-09-24T09:00:00+00:00',
        timeRange: '9:00 AM – 9:30 AM',
      }),
    ).rejects.toBeTruthy();

    expect(post).toHaveBeenCalledWith('/sessions/test-session/appointment/reschedule', {
      start: '2026-09-24T09:00:00+00:00',
    });
  });
});

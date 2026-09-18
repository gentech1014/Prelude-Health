import { describe, expect, it } from 'vitest';
import { formatAppointmentLabel } from '@/features/booking/bookingTime';

describe('formatAppointmentLabel', () => {
  it('renders the time in the clinic timezone, not the browser one', () => {
    // 10:00 UTC must not read as 3:30 PM just because the browser sits in IST.
    expect(formatAppointmentLabel('2026-09-09T10:00:00+00:00', 'UTC')).toBe(
      'Wednesday, September 9 at 10:00 AM (UTC)',
    );
  });

  it('names the zone so a patient elsewhere is not misled', () => {
    expect(formatAppointmentLabel('2026-09-09T10:00:00+00:00', 'Asia/Kolkata')).toBe(
      'Wednesday, September 9 at 3:30 PM (Asia/Kolkata)',
    );
  });

  it('still renders the appointment when the zone is unusable', () => {
    expect(formatAppointmentLabel('2026-09-09T10:00:00+00:00', 'Not/AZone')).toContain(
      'September 9',
    );
  });

  it('falls back to the browser zone when the clinic zone is unknown', () => {
    expect(formatAppointmentLabel('2026-09-09T10:00:00+00:00', null)).toContain('September 9');
  });
});

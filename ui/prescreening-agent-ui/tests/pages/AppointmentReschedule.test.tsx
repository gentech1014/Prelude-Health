import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '@/services/apiClient';
import type { IntakeCallValue } from '@/features/prescreening-session/intakeCallContext';
import { AppointmentReschedule } from '@/pages/AppointmentReschedule';
import {
  fetchAppointmentAvailability,
  rescheduleAppointment,
  type AppointmentAvailability,
} from '@/services/appointmentService';
import { buildTestSession } from '../sessionFixture';
import { renderPageInSession } from '../testUtils';

vi.mock('@/services/appointmentService', () => ({
  fetchAppointmentAvailability: vi.fn(),
  rescheduleAppointment: vi.fn(),
}));

// Two separate days, one slot each: exercises the date-then-time picker
// as a patient actually uses it, not two times on the same date.
const DAYS = [
  {
    day: '2026-09-24',
    weekdayLabel: 'Thu',
    dayLabel: '24',
    slots: [
      {
        id: '2026-09-24T09:00:00+00:00',
        start: '2026-09-24T09:00:00+00:00',
        timeRange: '9:00 AM – 9:30 AM',
      },
    ],
  },
  {
    day: '2026-09-25',
    weekdayLabel: 'Fri',
    dayLabel: '25',
    slots: [
      {
        id: '2026-09-25T13:30:00+00:00',
        start: '2026-09-25T13:30:00+00:00',
        timeRange: '1:30 PM – 2:00 PM',
      },
    ],
  },
];

function availability(overrides: Partial<AppointmentAvailability> = {}): AppointmentAvailability {
  return { timezone: 'UTC', isCalendarConnected: true, days: DAYS, ...overrides };
}

function renderAppointmentReschedule(
  call?: Partial<IntakeCallValue>,
): ReturnType<typeof renderPageInSession> {
  return renderPageInSession(<AppointmentReschedule />, {
    step: 'appointment-reschedule',
    destinationRoutes: { 'thank-you': <div>Thank you screen</div> },
    ...(call ? { call } : {}),
  });
}

describe('AppointmentReschedule', () => {
  beforeEach(() => {
    vi.mocked(fetchAppointmentAvailability).mockReset();
    vi.mocked(rescheduleAppointment).mockReset();
  });

  it('shows a loading state while fetching open times', () => {
    vi.mocked(fetchAppointmentAvailability).mockReturnValue(new Promise(() => {}));

    renderAppointmentReschedule();

    expect(screen.getByText(/looking for open times/i)).toBeInTheDocument();
  });

  it('picks the first open date automatically, and switches times when another date is picked', async () => {
    const user = userEvent.setup();
    vi.mocked(fetchAppointmentAvailability).mockResolvedValue(availability());

    renderAppointmentReschedule();

    // The first day with anything open is shown immediately -- no tap
    // needed to see a time at all.
    expect(await screen.findByText('9:00 AM – 9:30 AM')).toBeInTheDocument();
    // The second day's time is not on screen until that date is picked:
    // this is the whole point of the picker over one long flat list.
    expect(screen.queryByText('1:30 PM – 2:00 PM')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /friday, september 25/i }));

    expect(await screen.findByText('1:30 PM – 2:00 PM')).toBeInTheDocument();
    expect(screen.queryByText('9:00 AM – 9:30 AM')).not.toBeInTheDocument();
  });

  it('says when a time has not been checked against the doctor’s own calendar', async () => {
    // A clinic-hours slot is weaker than a calendar-confirmed one, and the
    // patient is told so rather than shown both identically.
    vi.mocked(fetchAppointmentAvailability).mockResolvedValue(
      availability({ isCalendarConnected: false }),
    );

    renderAppointmentReschedule();

    expect(await screen.findByText(/come from clinic hours/i)).toBeInTheDocument();
  });

  it('tells the patient when nothing is open', async () => {
    vi.mocked(fetchAppointmentAvailability).mockResolvedValue(availability({ days: [] }));

    renderAppointmentReschedule();

    expect(await screen.findByText(/no open times right now/i)).toBeInTheDocument();
  });

  it('keeps the patient here and says the appointment is unchanged when loading fails', async () => {
    vi.mocked(fetchAppointmentAvailability).mockRejectedValue(new ApiError('server', 'nope'));

    renderAppointmentReschedule();

    expect(await screen.findByText(/could not load open times/i)).toBeInTheDocument();
    expect(screen.getByText(/appointment is unchanged/i)).toBeInTheDocument();
  });

  it('disables Confirm until a time is picked, then moves the appointment', async () => {
    const user = userEvent.setup();
    vi.mocked(fetchAppointmentAvailability).mockResolvedValue(availability());
    vi.mocked(rescheduleAppointment).mockResolvedValue(
      buildTestSession({ appointment: { ...buildTestSession().appointment } }),
    );

    renderAppointmentReschedule();

    const confirmButton = await screen.findByRole('button', { name: /confirm new time/i });
    expect(confirmButton).toBeDisabled();

    await user.click(screen.getByText('9:00 AM – 9:30 AM'));
    expect(confirmButton).toBeEnabled();

    await user.click(confirmButton);

    // The slot's own start time, verbatim: the server re-validates exactly
    // what the patient was shown rather than a value the client rebuilt.
    expect(vi.mocked(rescheduleAppointment)).toHaveBeenCalledWith(
      'test-session',
      expect.objectContaining({ start: '2026-09-24T09:00:00+00:00' }),
    );
    // `waitFor` around the whole assertion, not `findByText`: the step
    // transition keeps the outgoing screen mounted while the incoming one
    // arrives, so a node found once can still be detached a tick later.
    await waitFor(() => expect(screen.getByText('Thank you screen')).toBeInTheDocument(), {
      timeout: 5_000,
    });
  });

  it('tells the assistant the new time is saved, so it confirms rather than writing it again', async () => {
    const user = userEvent.setup();
    const reportAppointmentRescheduled = vi.fn();
    vi.mocked(fetchAppointmentAvailability).mockResolvedValue(availability());
    vi.mocked(rescheduleAppointment).mockResolvedValue(buildTestSession());

    renderAppointmentReschedule({ reportAppointmentRescheduled });

    await user.click(await screen.findByText('9:00 AM – 9:30 AM'));
    await user.click(screen.getByRole('button', { name: /confirm new time/i }));

    await waitFor(() => expect(reportAppointmentRescheduled).toHaveBeenCalledTimes(1));
  });

  it('says nothing to the assistant when the time was taken and nothing was saved', async () => {
    // The assistant would otherwise confirm a move that never happened,
    // on a screen still showing the patient their original appointment.
    const user = userEvent.setup();
    const reportAppointmentRescheduled = vi.fn();
    vi.mocked(fetchAppointmentAvailability).mockResolvedValue(availability());
    vi.mocked(rescheduleAppointment).mockRejectedValue(
      new ApiError('conflict', 'That time is no longer available.'),
    );

    renderAppointmentReschedule({ reportAppointmentRescheduled });

    await user.click(await screen.findByText('9:00 AM – 9:30 AM'));
    await user.click(screen.getByRole('button', { name: /confirm new time/i }));

    expect(await screen.findByText(/no longer available/i)).toBeInTheDocument();
    expect(reportAppointmentRescheduled).not.toHaveBeenCalled();
  });

  it('re-reads the list when the slot was taken while the patient was reading it', async () => {
    const user = userEvent.setup();
    vi.mocked(fetchAppointmentAvailability).mockResolvedValue(availability());
    vi.mocked(rescheduleAppointment).mockRejectedValue(
      new ApiError('conflict', 'That time is no longer available.'),
    );

    renderAppointmentReschedule();

    await user.click(await screen.findByText('9:00 AM – 9:30 AM'));
    await user.click(screen.getByRole('button', { name: /confirm new time/i }));

    expect(await screen.findByText(/no longer available/i)).toBeInTheDocument();
    // Re-fetched rather than the same payload retried: the list was stale.
    expect(vi.mocked(fetchAppointmentAvailability)).toHaveBeenCalledTimes(2);
  });

  it('has no manual back/next controls', async () => {
    vi.mocked(fetchAppointmentAvailability).mockResolvedValue(availability());

    renderAppointmentReschedule();
    await screen.findByText('9:00 AM – 9:30 AM');

    expect(screen.queryByRole('button', { name: 'Back' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Next' })).not.toBeInTheDocument();
  });
});

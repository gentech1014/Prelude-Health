import { screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { AppointmentSchedule } from '@/pages/AppointmentSchedule';
import { fetchPrescreeningSessionContext } from '@/services/prescreeningSessionService';
import { buildTestSession } from '../sessionFixture';
import { renderPageInSession } from '../testUtils';

describe('AppointmentSchedule', () => {
  it('reads back the appointment from the session, not a fixture', async () => {
    renderPageInSession(<AppointmentSchedule />, { step: 'appointment-schedule' });

    expect(screen.getByRole('heading', { name: /your appointment/i })).toBeInTheDocument();
    expect(await screen.findByText('Dr. Test')).toBeInTheDocument();
    // 30 minutes from the clinic's configured slot size, rendered in the
    // clinic's own timezone rather than the browser's.
    expect(screen.getByText('2:30 PM – 3:00 PM')).toBeInTheDocument();
    expect(screen.getByText('30 minutes')).toBeInTheDocument();
    expect(screen.getByText('Test Clinic')).toBeInTheDocument();
  });

  it('omits the clinician subtitle rather than inventing a job title', async () => {
    vi.mocked(fetchPrescreeningSessionContext).mockResolvedValueOnce(
      buildTestSession({
        appointment: { ...buildTestSession().appointment, physicianCredential: null },
      }),
    );

    renderPageInSession(<AppointmentSchedule />, { step: 'appointment-schedule' });

    expect(await screen.findByText('Dr. Test')).toBeInTheDocument();
    expect(screen.queryByText('MD')).not.toBeInTheDocument();
  });

  it('omits the location row entirely when the deployment names no clinic', async () => {
    vi.mocked(fetchPrescreeningSessionContext).mockResolvedValueOnce(
      buildTestSession({ clinic: { name: null, location: null, timezone: 'UTC' } }),
    );

    renderPageInSession(<AppointmentSchedule />, { step: 'appointment-schedule' });

    expect(await screen.findByText('Dr. Test')).toBeInTheDocument();
    expect(screen.queryByText('Test Clinic')).not.toBeInTheDocument();
  });

  it('shows the assistant badge with the name the deployment gave it', async () => {
    const { container } = renderPageInSession(<AppointmentSchedule />, {
      step: 'appointment-schedule',
    });

    await screen.findByText('Dr. Test');
    expect(container.querySelector('.agent-profile-badge__name')).toHaveTextContent(
      'Test Assistant',
    );
    expect(container.querySelector('.agent-profile-badge__avatar')).toBeInTheDocument();
  });

  it('has no manual back/next controls', () => {
    renderPageInSession(<AppointmentSchedule />, { step: 'appointment-schedule' });

    expect(screen.queryByRole('button', { name: 'Back' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Next' })).not.toBeInTheDocument();
  });
});

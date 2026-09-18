import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, within, type RenderResult } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ThemeProvider } from '@/app/ThemeProvider';
import { ToastProvider } from '@/components/states';
import { BookAppointment } from '@/pages/BookAppointment';
import { ApiError } from '@/services/apiClient';
import {
  bookAppointment,
  fetchProviderAvailability,
  fetchProviders,
  fetchVisitTypes,
  startDoctorRegistration,
} from '@/services/bookingService';

vi.mock('@/services/bookingService', () => ({
  fetchVisitTypes: vi.fn(),
  fetchProviders: vi.fn(),
  fetchProviderAvailability: vi.fn(),
  bookAppointment: vi.fn(),
  startDoctorRegistration: vi.fn(),
}));

const VISIT_TYPES = [
  {
    visitType: 'general_checkup' as const,
    label: 'General checkup',
    description: 'A routine visit with no specific problem',
    symptomCategory: null,
  },
  {
    visitType: 'not_sure' as const,
    label: 'I am not sure',
    description: 'Something feels wrong and you cannot place it',
    symptomCategory: null,
  },
  {
    visitType: 'heart' as const,
    label: 'Heart',
    description: 'Chest discomfort and palpitations',
    symptomCategory: 'heart' as const,
  },
  {
    visitType: 'lung' as const,
    label: 'Lung and breathing',
    description: 'Cough and wheezing',
    symptomCategory: 'lung' as const,
  },
];

const PROVIDERS = [
  {
    doctorId: 'dr-one',
    name: 'Dr. Ada One',
    credential: 'MD',
    categories: ['heart' as const],
    modality: 'in_person_and_virtual' as const,
    calendarConnected: true,
    photoUrl: 'https://images.unsplash.test/dr-one?w=256',
  },
  {
    doctorId: 'dr-two',
    name: 'Dr. Bo Two',
    credential: null,
    categories: ['heart' as const],
    modality: 'virtual' as const,
    calendarConnected: false,
    photoUrl: null,
  },
];

const AVAILABILITY = {
  doctorId: 'dr-one',
  timezone: 'UTC',
  calendarConnected: true,
  days: [
    {
      day: '2026-09-14',
      weekdayLabel: 'Mon',
      dayLabel: '14',
      source: 'calendar' as const,
      slots: [
        { start: '2026-09-14T09:00:00+00:00', label: '9:00 AM' },
        { start: '2026-09-14T09:30:00+00:00', label: '9:30 AM' },
      ],
    },
    {
      day: '2026-09-19',
      weekdayLabel: 'Sat',
      dayLabel: '19',
      source: 'calendar' as const,
      slots: [],
    },
  ],
};

function renderBookAppointment(initialUrl = '/book'): RenderResult {
  // A fresh client per test: a shared one would leak one test's cached
  // providers into the next and make failures order-dependent.
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });

  return render(
    <MemoryRouter initialEntries={[initialUrl]}>
      <ThemeProvider>
        <QueryClientProvider client={queryClient}>
          <ToastProvider>
            <BookAppointment />
          </ToastProvider>
        </QueryClientProvider>
      </ThemeProvider>
    </MemoryRouter>,
  );
}

/**
 * A user with no inter-keystroke delay. The confirm step types into five
 * fields, and userEvent's default per-key delay pushed those cases right up
 * against the 5s test timeout -- they passed alone and failed intermittently
 * in a full parallel run.
 */
function setupUser(): ReturnType<typeof userEvent.setup> {
  return userEvent.setup({ delay: null });
}

/** Walks the flow up to a chosen slot, which most cases need as a starting point. */
async function selectVisitTypeProviderAndSlot(
  user: ReturnType<typeof userEvent.setup>,
): Promise<void> {
  await user.click(await screen.findByRole('radio', { name: /heart/i }));
  await user.click(await screen.findByRole('radio', { name: /ada one/i }));
  await user.click(await screen.findByRole('button', { name: /monday, september 14/i }));
  await user.click(await screen.findByRole('button', { name: '9:00 AM' }));
}

describe('BookAppointment', () => {
  beforeEach(() => {
    vi.mocked(fetchVisitTypes).mockReset().mockResolvedValue(VISIT_TYPES);
    vi.mocked(fetchProviders).mockReset().mockResolvedValue(PROVIDERS);
    vi.mocked(fetchProviderAvailability).mockReset().mockResolvedValue(AVAILABILITY);
    vi.mocked(bookAppointment).mockReset();
    vi.mocked(startDoctorRegistration).mockReset();
  });

  it('renders the visit types served by the API, not a hardcoded list', async () => {
    renderBookAppointment();

    expect(await screen.findByRole('radio', { name: /heart/i })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: /lung and breathing/i })).toBeInTheDocument();
  });

  it('asks for a visit type before offering providers or times', async () => {
    renderBookAppointment();

    expect(
      await screen.findByText(/select an appointment type first to see who is available/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/choose a provider to see their open times/i)).toBeInTheDocument();
    expect(fetchProviders).not.toHaveBeenCalled();
  });

  it('loads providers for the chosen visit type', async () => {
    const user = setupUser();
    renderBookAppointment();

    await user.click(await screen.findByRole('radio', { name: /heart/i }));

    expect(await screen.findByRole('radio', { name: /ada one/i })).toBeInTheDocument();
    expect(fetchProviders).toHaveBeenCalledWith('heart');
  });

  it('shows a provider portrait when one is on file, and initials when not', async () => {
    const user = setupUser();
    const { container } = renderBookAppointment();

    await user.click(await screen.findByRole('radio', { name: /heart/i }));
    await screen.findByRole('radio', { name: /ada one/i });

    const portrait = container.querySelector('img[src^="https://images.unsplash.test"]');
    expect(portrait).not.toBeNull();
    // Dr. Bo Two has no photo, so their card must still read cleanly.
    expect(screen.getByText('BT')).toBeInTheDocument();
  });

  it('falls back to initials when the portrait fails to load', async () => {
    const user = setupUser();
    const { container } = renderBookAppointment();

    await user.click(await screen.findByRole('radio', { name: /heart/i }));
    await screen.findByRole('radio', { name: /ada one/i });

    const portrait = container.querySelector<HTMLImageElement>(
      'img[src^="https://images.unsplash.test"]',
    );
    expect(portrait).not.toBeNull();
    fireEvent.error(portrait as HTMLImageElement);

    // A dead image host must never leave a broken-image icon on the card.
    expect(screen.getByText('AO')).toBeInTheDocument();
    expect(container.querySelector('img[src^="https://images.unsplash.test"]')).toBeNull();
  });

  it('offers a route for a routine checkup and for not knowing what is wrong', async () => {
    renderBookAppointment();

    // The five condition cards cannot answer either intent, so a patient
    // with neither would otherwise have to pick a wrong one.
    expect(await screen.findByRole('radio', { name: /general checkup/i })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: /i am not sure/i })).toBeInTheDocument();
  });

  it('shows every provider for an unscoped visit type', async () => {
    const user = setupUser();
    renderBookAppointment();

    await user.click(await screen.findByRole('radio', { name: /general checkup/i }));

    // No condition to match specialties against, so nobody is filtered out.
    expect(fetchProviders).toHaveBeenCalledWith('general_checkup');
    expect(await screen.findByRole('radio', { name: /ada one/i })).toBeInTheDocument();
  });

  it('books an unscoped visit without inventing a clinical category', async () => {
    const user = setupUser();
    vi.mocked(bookAppointment).mockResolvedValue({
      sessionId: 'sess_2',
      appointmentId: 'appt_2',
      intakeUrl: 'https://example.test/prescreen/sess_2?token=redacted',
      physician: 'Dr. Ada One',
      scheduledAt: '2026-09-14T09:00:00+00:00',
      patientId: 'pt_0001',
      patientIdIsProvisional: false,
      notificationMessage:
        'Hi Test, your appointment with Dr. Ada One is confirmed for Monday, September 14 at 9:00 AM. ' +
        'Before your visit, please complete a short pre-screening call: ' +
        'https://example.test/prescreen/sess_1?token=redacted',
      calendarSynced: true,
    });
    renderBookAppointment();

    await user.click(await screen.findByRole('radio', { name: /i am not sure/i }));
    await user.click(await screen.findByRole('radio', { name: /ada one/i }));
    await user.click(await screen.findByRole('button', { name: /monday, september 14/i }));
    await user.click(await screen.findByRole('button', { name: '9:00 AM' }));
    await user.click(screen.getByRole('button', { name: /continue/i }));

    await user.type(screen.getByLabelText(/patient full name/i), 'Test Patient');
    await user.type(screen.getByLabelText(/patient id/i), 'pt_0002');
    await user.type(screen.getByLabelText(/date of birth/i), '1990-01-01');
    await user.type(screen.getByLabelText(/mobile number/i), '+10000000000');
    await user.click(screen.getByRole('button', { name: /^book appointment$/i }));

    // The visit type travels as-is; the server maps it to a category (or to
    // none), so the client never guesses a clinical one.
    expect(vi.mocked(bookAppointment).mock.calls[0]?.[0]).toEqual(
      expect.objectContaining({ visitType: 'not_sure' }),
    );
  });

  it('says when a provider has not connected their calendar', async () => {
    const user = setupUser();
    vi.mocked(fetchProviderAvailability).mockResolvedValue({
      ...AVAILABILITY,
      doctorId: 'dr-two',
      calendarConnected: false,
    });
    renderBookAppointment();

    await user.click(await screen.findByRole('radio', { name: /heart/i }));
    await user.click(await screen.findByRole('radio', { name: /bo two/i }));

    // An unverified slot must never look like a calendar-confirmed one.
    expect(await screen.findByText(/has not connected their calendar/i)).toBeInTheDocument();
  });

  it('disables a date with no remaining times', async () => {
    const user = setupUser();
    renderBookAppointment();

    await user.click(await screen.findByRole('radio', { name: /heart/i }));
    await user.click(await screen.findByRole('radio', { name: /ada one/i }));

    expect(
      await screen.findByRole('button', { name: /saturday, september 19.*no times available/i }),
    ).toBeDisabled();
  });

  it('keeps Continue disabled until a type, provider and time are all chosen', async () => {
    const user = setupUser();
    renderBookAppointment();

    const continueButton = await screen.findByRole('button', { name: /continue/i });
    expect(continueButton).toBeDisabled();

    await selectVisitTypeProviderAndSlot(user);

    expect(continueButton).toBeEnabled();
  });

  it('clears the provider and slot when the visit type changes', async () => {
    const user = setupUser();
    renderBookAppointment();

    await selectVisitTypeProviderAndSlot(user);
    await user.click(screen.getByRole('radio', { name: /lung and breathing/i }));

    expect(screen.getByRole('button', { name: /continue/i })).toBeDisabled();
    expect(screen.getByText(/choose a provider to see their open times/i)).toBeInTheDocument();
  });

  it('books the appointment with the patient details from the confirm step', async () => {
    const user = setupUser();
    vi.mocked(bookAppointment).mockResolvedValue({
      sessionId: 'sess_1',
      appointmentId: 'appt_1',
      intakeUrl: 'https://example.test/prescreen/sess_1?token=redacted',
      physician: 'Dr. Ada One',
      scheduledAt: '2026-09-14T09:00:00+00:00',
      patientId: 'pt_0001',
      patientIdIsProvisional: false,
      notificationMessage:
        'Hi Test, your appointment with Dr. Ada One is confirmed for Monday, September 14 at 9:00 AM. ' +
        'Before your visit, please complete a short pre-screening call: ' +
        'https://example.test/prescreen/sess_1?token=redacted',
      calendarSynced: true,
    });
    renderBookAppointment();

    await selectVisitTypeProviderAndSlot(user);
    await user.click(screen.getByRole('button', { name: /continue/i }));

    await user.type(screen.getByLabelText(/patient full name/i), 'Test Patient');
    await user.type(screen.getByLabelText(/patient id/i), 'pt_0001');
    await user.type(screen.getByLabelText(/date of birth/i), '1990-01-01');
    await user.type(screen.getByLabelText(/mobile number/i), '+10000000000');
    await user.click(screen.getByRole('button', { name: /^book appointment$/i }));

    // Asserted on the first argument only: react-query passes its own
    // mutation context as a second argument, which is not part of the contract.
    expect(vi.mocked(bookAppointment).mock.calls[0]?.[0]).toEqual(
      expect.objectContaining({
        doctorId: 'dr-one',
        visitType: 'heart',
        slotStart: '2026-09-14T09:00:00+00:00',
        patientName: 'Test Patient',
        patientId: 'pt_0001',
        contactPhone: '+10000000000',
      }),
    );
    expect(await screen.findByRole('heading', { name: /appointment booked/i })).toBeInTheDocument();
    // Shown in the clinic's timezone, matching the slot label that was picked.
    expect(screen.getByText(/9:00 AM \(UTC\)/)).toBeInTheDocument();
  });

  it('books without a patient ID, which most patients cannot recall', async () => {
    const user = setupUser();
    vi.mocked(bookAppointment).mockResolvedValue({
      sessionId: 'sess_3',
      appointmentId: 'appt_3',
      intakeUrl: 'https://example.test/prescreen/sess_3?token=redacted',
      physician: 'Dr. Ada One',
      scheduledAt: '2026-09-14T09:00:00+00:00',
      patientId: 'provisional-abc1234567',
      patientIdIsProvisional: true,
      notificationMessage:
        'Hi Test, your appointment is confirmed: https://example.test/prescreen/sess_3?token=redacted',
      calendarSynced: true,
    });
    renderBookAppointment();

    await selectVisitTypeProviderAndSlot(user);
    await user.click(screen.getByRole('button', { name: /continue/i }));

    // Deliberately leaves Patient ID blank.
    await user.type(screen.getByLabelText(/patient full name/i), 'Test Patient');
    await user.type(screen.getByLabelText(/date of birth/i), '1990-01-01');
    await user.type(screen.getByLabelText(/mobile number/i), '+10000000000');
    await user.click(screen.getByRole('button', { name: /^book appointment$/i }));

    expect(vi.mocked(bookAppointment).mock.calls[0]?.[0]).toEqual(
      expect.objectContaining({ patientId: null }),
    );
    // A generated id is never presented as though the clinic had issued it.
    expect(await screen.findByText(/provisional-abc1234567 \(provisional\)/)).toBeInTheDocument();
    expect(screen.getByText(/a provisional one was issued/i)).toBeInTheDocument();
  });

  it('shows the patient the message they were sent, with a live link', async () => {
    const user = setupUser();
    vi.mocked(bookAppointment).mockResolvedValue({
      sessionId: 'sess_4',
      appointmentId: 'appt_4',
      intakeUrl: 'https://example.test/prescreen/sess_4?token=redacted',
      physician: 'Dr. Ada One',
      scheduledAt: '2026-09-14T09:00:00+00:00',
      patientId: 'pt_0001',
      patientIdIsProvisional: false,
      notificationMessage:
        'Hi Test, your appointment with Dr. Ada One is confirmed. Complete your pre-screening ' +
        'call: https://example.test/prescreen/sess_4?token=redacted',
      calendarSynced: true,
    });
    renderBookAppointment();

    await selectVisitTypeProviderAndSlot(user);
    await user.click(screen.getByRole('button', { name: /continue/i }));
    await user.type(screen.getByLabelText(/patient full name/i), 'Test Patient');
    await user.type(screen.getByLabelText(/patient id/i), 'pt_0001');
    await user.type(screen.getByLabelText(/date of birth/i), '1990-01-01');
    await user.type(screen.getByLabelText(/mobile number/i), '+10000000000');
    await user.click(screen.getByRole('button', { name: /^book appointment$/i }));

    // The server's own wording, not a re-typed approximation of it.
    expect(await screen.findByText(/complete your pre-screening call/i)).toBeInTheDocument();

    const link = screen.getByRole('link', {
      name: 'https://example.test/prescreen/sess_4?token=redacted',
    });
    expect(link).toHaveAttribute('href', 'https://example.test/prescreen/sess_4?token=redacted');
    expect(link).toHaveAttribute('rel', expect.stringContaining('noopener'));
  });

  it('refuses to submit until the required patient details are valid', async () => {
    const user = setupUser();
    renderBookAppointment();

    await selectVisitTypeProviderAndSlot(user);
    await user.click(screen.getByRole('button', { name: /continue/i }));
    await user.click(screen.getByRole('button', { name: /^book appointment$/i }));

    expect(await screen.findByText(/enter the patient full name/i)).toBeInTheDocument();
    expect(bookAppointment).not.toHaveBeenCalled();
  });

  it('sends the patient back to pick another slot when the time was just taken', async () => {
    const user = setupUser();
    vi.mocked(bookAppointment).mockRejectedValue(
      new ApiError('conflict', 'This step was already completed.'),
    );
    renderBookAppointment();

    await selectVisitTypeProviderAndSlot(user);
    await user.click(screen.getByRole('button', { name: /continue/i }));
    await user.type(screen.getByLabelText(/patient full name/i), 'Test Patient');
    await user.type(screen.getByLabelText(/patient id/i), 'pt_0001');
    await user.type(screen.getByLabelText(/date of birth/i), '1990-01-01');
    await user.type(screen.getByLabelText(/mobile number/i), '+10000000000');
    await user.click(screen.getByRole('button', { name: /^book appointment$/i }));

    expect(await screen.findByText(/that time was just taken/i)).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: /continue/i })).toBeDisabled();
  });

  it('shows the intake link only inside the message preview, never as page text', async () => {
    const user = setupUser();
    vi.mocked(bookAppointment).mockResolvedValue({
      sessionId: 'sess_1',
      appointmentId: 'appt_1',
      intakeUrl: 'https://example.test/prescreen/sess_1?token=super-secret-token',
      physician: 'Dr. Ada One',
      scheduledAt: '2026-09-14T09:00:00+00:00',
      patientId: 'pt_0001',
      patientIdIsProvisional: false,
      notificationMessage:
        'Hi Test, your appointment with Dr. Ada One is confirmed. Complete your pre-screening call: ' +
        'https://example.test/prescreen/sess_1?token=super-secret-token',
      calendarSynced: false,
    });
    const { container } = renderBookAppointment();

    await selectVisitTypeProviderAndSlot(user);
    await user.click(screen.getByRole('button', { name: /continue/i }));
    await user.type(screen.getByLabelText(/patient full name/i), 'Test Patient');
    await user.type(screen.getByLabelText(/patient id/i), 'pt_0001');
    await user.type(screen.getByLabelText(/date of birth/i), '1990-01-01');
    await user.type(screen.getByLabelText(/mobile number/i), '+10000000000');
    await user.click(screen.getByRole('button', { name: /^book appointment$/i }));

    expect(await screen.findByRole('heading', { name: /appointment booked/i })).toBeInTheDocument();

    // The link is a session credential, so it appears in exactly one place:
    // inside the phone preview, as the message body the patient was sent.
    const withToken = [...container.querySelectorAll('*')].filter(
      (element) =>
        element.children.length === 0 &&
        element.textContent?.includes('super-secret-token') === true,
    );
    expect(withToken).toHaveLength(1);
    expect(withToken[0]?.closest('.intake-preview')).not.toBeNull();

    // An unsynced calendar is stated, not implied to have worked.
    expect(screen.getByText(/not connected/i)).toBeInTheDocument();
  });

  it('opens doctor registration from the settings menu and hands off to Google', async () => {
    const user = setupUser();
    vi.mocked(startDoctorRegistration).mockResolvedValue('https://accounts.google.test/consent');
    const assign = vi.fn();
    vi.spyOn(window, 'location', 'get').mockReturnValue({ ...window.location, assign });
    renderBookAppointment();

    await user.click(screen.getByRole('button', { name: /settings/i }));
    await user.click(screen.getByRole('menuitem', { name: /register as doctor/i }));

    const dialog = screen.getByRole('dialog', { name: /register as doctor/i });
    await user.type(within(dialog).getByLabelText(/name shown to patients/i), 'Dr. Three');
    await user.click(within(dialog).getByRole('button', { name: /continue with google/i }));

    expect(startDoctorRegistration).toHaveBeenCalledWith(
      expect.objectContaining({ name: 'Dr. Three' }),
    );
    expect(assign).toHaveBeenCalledWith('https://accounts.google.test/consent');
  });

  it('reports the outcome the OAuth callback redirected back with', async () => {
    renderBookAppointment('/book?doctor_registration=connected&doctor=Dr.%20Three');

    expect(
      await screen.findByText(/dr\. three is now bookable with their own calendar/i),
    ).toBeInTheDocument();
  });
});

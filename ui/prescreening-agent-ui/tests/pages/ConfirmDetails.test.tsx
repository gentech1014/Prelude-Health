import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ConfirmDetails } from '@/pages/ConfirmDetails';
import { ApiError } from '@/services/apiClient';
import { submitConsent } from '@/services/prescreeningSessionService';
import { buildTestSession } from '../sessionFixture';
import { renderPageInSession } from '../testUtils';

describe('ConfirmDetails', () => {
  // Reset, not clear: an implementation set by one case would otherwise
  // still be installed for every case after it.
  beforeEach(() => {
    vi.mocked(submitConsent).mockReset();
    vi.mocked(submitConsent).mockResolvedValue(
      buildTestSession({ consentGiven: true, status: 'in_progress' }),
    );
  });

  it('asks the patient to confirm identity details', () => {
    renderPageInSession(<ConfirmDetails />, { step: 'confirm-details' });

    expect(screen.getByRole('heading', { name: /confirm a few details/i })).toBeInTheDocument();
  });

  it('names the clinic from the session rather than a value baked into the app', async () => {
    renderPageInSession(<ConfirmDetails />, { step: 'confirm-details' });

    expect(await screen.findByText(/test clinic/i)).toBeInTheDocument();
  });

  it('pre-fills the identity fields from the booking record', async () => {
    renderPageInSession(<ConfirmDetails />, { step: 'confirm-details' });

    // Exact labels, not a loose regex: each row also carries an "Edit <field>"
    // button, so /full name/i matches the input and its pencil alike.
    // The session arrives asynchronously; the fields fill in once it lands.
    await waitFor(() => {
      expect(screen.getByLabelText('Full name')).toHaveValue('Test Patient');
    });
    expect(screen.getByLabelText('Date of birth')).toHaveValue('01/01/1990');
    // The number the clinic already holds — the link was sent to it, so
    // asking the patient to type it again is asking twice.
    expect(screen.getByLabelText('Phone number')).toHaveValue('+1 555 0100');
  });

  it('fills a field from what the assistant heard, not just the booking record', async () => {
    renderPageInSession(<ConfirmDetails />, {
      step: 'confirm-details',
      call: { prefill: { 'confirm-details': { phone_number: '+44 7700 900100' } } },
    });

    // What the assistant heard wins over the booked number.
    expect(await screen.findByLabelText('Phone number')).toHaveValue('+44 7700 900100');
  });

  it('records consent on the server before the call can start', async () => {
    const user = userEvent.setup();
    renderPageInSession(<ConfirmDetails />, { step: 'confirm-details' });

    const consentCheckbox = screen.getByRole('checkbox', { name: /yes, i give my consent/i });
    expect(consentCheckbox).not.toBeChecked();
    // Nothing is submittable until the patient actively ticks it.
    expect(screen.getByRole('button', { name: 'Continue' })).toBeDisabled();

    await user.click(consentCheckbox);
    await user.click(screen.getByRole('button', { name: 'Continue' }));

    expect(vi.mocked(submitConsent)).toHaveBeenCalledWith('test-session', true);
  });

  it('records a decline as a decision, not as simply not proceeding', async () => {
    const user = userEvent.setup();
    vi.mocked(submitConsent).mockResolvedValue(buildTestSession({ status: 'declined' }));
    renderPageInSession(<ConfirmDetails />, { step: 'confirm-details' });

    await user.click(screen.getByRole('button', { name: 'Decline' }));

    expect(vi.mocked(submitConsent)).toHaveBeenCalledWith('test-session', false);
  });

  it('keeps the patient on this step and explains when consent cannot be recorded', async () => {
    const user = userEvent.setup();
    vi.mocked(submitConsent).mockRejectedValueOnce(new ApiError('offline', 'You are offline.'));
    renderPageInSession(<ConfirmDetails />, { step: 'confirm-details' });

    await user.click(screen.getByRole('checkbox', { name: /yes, i give my consent/i }));
    await user.click(screen.getByRole('button', { name: 'Continue' }));

    expect(await screen.findByText('You are offline.')).toBeInTheDocument();
    // Still here: the call genuinely cannot start, so advancing would strand them.
    expect(screen.getByRole('heading', { name: /confirm a few details/i })).toBeInTheDocument();
  });

  it('has no manual back/next controls', () => {
    renderPageInSession(<ConfirmDetails />, { step: 'confirm-details' });

    expect(screen.queryByRole('button', { name: 'Back' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Next' })).not.toBeInTheDocument();
  });

  it('shows the assistant’s live caption, named from the session', async () => {
    const { container } = renderPageInSession(<ConfirmDetails />, {
      step: 'confirm-details',
      call: {
        activeCaption: {
          role: 'agent',
          text: 'Can you confirm your date of birth?',
          isFinal: false,
          turnId: 'turn-1',
        },
      },
    });

    await waitFor(() => {
      expect(container.querySelector('.voice-transcript-panel__speaker')).toHaveTextContent(
        'Test Assistant',
      );
    });
    expect(screen.getByText(/confirm your date of birth/i)).toBeInTheDocument();
  });

  it('shows what the patient is being heard to say, labelled as theirs', () => {
    renderPageInSession(<ConfirmDetails />, {
      step: 'confirm-details',
      call: {
        activeCaption: {
          role: 'patient',
          text: 'Fourth of March',
          isFinal: true,
          turnId: 'turn-2',
        },
      },
    });

    expect(screen.getByText('Fourth of March')).toBeInTheDocument();
    expect(screen.getByText('You')).toBeInTheDocument();
  });
});

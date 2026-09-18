import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { Medication } from '@/pages/Medication';
import { renderPageInSession } from '../testUtils';

describe('Medication', () => {
  it('asks about regular medications, including over-the-counter ones', () => {
    renderPageInSession(<Medication />, { step: 'medication' });

    expect(screen.getByRole('heading', { name: 'Medication' })).toBeInTheDocument();
    expect(screen.getByText(/over-the-counter and supplements/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/medications, dose, and how often/i)).toBeInTheDocument();
  });

  it('fills the list from what the assistant heard', () => {
    renderPageInSession(<Medication />, {
      step: 'medication',
      call: {
        prefill: { medication: { medications: 'Metformin 500mg, twice a day' } },
      },
    });

    expect(screen.getByLabelText(/medications, dose, and how often/i)).toHaveValue(
      'Metformin 500mg, twice a day',
    );
  });

  it('reports a typed correction so it reaches the same conversation', async () => {
    const user = userEvent.setup();
    const reportFieldEdit = vi.fn();
    renderPageInSession(<Medication />, { step: 'medication', call: { reportFieldEdit } });

    await user.type(screen.getByLabelText(/medications, dose, and how often/i), 'Aspirin');
    expect(reportFieldEdit).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: /send this answer/i }));

    expect(reportFieldEdit).toHaveBeenCalledWith('medication', 'medications', 'Aspirin');
  });

  it('has no Proceed button — the conversation advances the call, not the screen', () => {
    // A button here would let the patient move on while the assistant was
    // still asking, which is the desync the flow exists to prevent.
    renderPageInSession(<Medication />, { step: 'medication' });

    expect(screen.queryByRole('button', { name: /proceed/i })).not.toBeInTheDocument();
  });

  it('offers a photo of the box as the alternative to typing it out', () => {
    renderPageInSession(<Medication />, { step: 'medication' });

    expect(screen.getByRole('button', { name: /add a photo of the box/i })).toBeInTheDocument();
  });

  it('has no manual back/next controls', () => {
    renderPageInSession(<Medication />, { step: 'medication' });

    expect(screen.queryByRole('button', { name: 'Back' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Next' })).not.toBeInTheDocument();
  });
});

import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { MedicalHistory } from '@/pages/MedicalHistory';
import { renderPageInSession } from '../testUtils';

describe('MedicalHistory', () => {
  it('asks about ongoing conditions and hospital stays', () => {
    renderPageInSession(<MedicalHistory />, { step: 'medical-history' });

    expect(screen.getByRole('heading', { name: /^medical history$/i })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /ongoing conditions/i })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /hospital stays/i })).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: 'Diabetes' })).not.toBeChecked();
    expect(screen.getByLabelText('Reason for stay')).toHaveValue('');
  });

  it('supports selecting more than one ongoing condition', async () => {
    const user = userEvent.setup();
    renderPageInSession(<MedicalHistory />, { step: 'medical-history' });

    await user.click(screen.getByRole('checkbox', { name: 'Diabetes' }));
    await user.click(screen.getByRole('checkbox', { name: 'Heart disease' }));

    expect(screen.getByRole('checkbox', { name: 'Diabetes' })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: 'Heart disease' })).toBeChecked();
  });

  it('adds and removes hospital stay entries', async () => {
    const user = userEvent.setup();
    renderPageInSession(<MedicalHistory />, { step: 'medical-history' });

    await user.click(screen.getByRole('button', { name: /add another hospital stay/i }));
    expect(screen.getAllByLabelText('Reason for stay')).toHaveLength(2);

    await user.click(screen.getByRole('button', { name: 'Remove hospital stay 2' }));
    expect(screen.getAllByLabelText('Reason for stay')).toHaveLength(1);
  });

  it('hides the hospital stay list once "no relevant hospital stays" is checked', async () => {
    const user = userEvent.setup();
    renderPageInSession(<MedicalHistory />, { step: 'medical-history' });

    await user.click(screen.getByRole('checkbox', { name: /relevant hospital stays/i }));

    expect(screen.queryByLabelText('Reason for stay')).not.toBeInTheDocument();
  });

  it('has no manual back/next controls', () => {
    renderPageInSession(<MedicalHistory />, { step: 'medical-history' });

    expect(screen.queryByRole('button', { name: 'Back' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Next' })).not.toBeInTheDocument();
  });
});

import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { FamilySocialHistory } from '@/pages/FamilySocialHistory';
import { renderPageInSession } from '../testUtils';

describe('FamilySocialHistory', () => {
  it('asks about family history and lifestyle, nothing pre-selected', () => {
    renderPageInSession(<FamilySocialHistory />, { step: 'family-social-history' });

    expect(screen.getByRole('heading', { name: /family and social history/i })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /^family history$/i })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /^social history$/i })).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: 'Diabetes' })).not.toBeChecked();
    expect(screen.getByLabelText('Occupation')).toHaveValue('');
  });

  it('ticks the family conditions the assistant heard, and fills the social fields', () => {
    renderPageInSession(<FamilySocialHistory />, {
      step: 'family-social-history',
      call: {
        prefill: {
          'family-social-history': {
            family_conditions: 'diabetes, stroke',
            tobacco_use: 'former',
            occupation: 'retired',
          },
        },
      },
    });

    expect(screen.getByRole('checkbox', { name: 'Diabetes' })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: 'Stroke' })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: 'Cancer' })).not.toBeChecked();
    expect(screen.getByRole('button', { name: 'Former' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByLabelText('Occupation')).toHaveValue('retired');
  });

  it('leaves a question unanswered when the patient would rather not say', () => {
    // An unanswered field must not read as a denial: the physician sees
    // the gap in the report, not an assumed "never".
    renderPageInSession(<FamilySocialHistory />, {
      step: 'family-social-history',
      call: {
        prefill: {
          'family-social-history': { alcohol_use: 'would rather not say' },
        },
      },
    });

    for (const button of screen.getAllByRole('button', { name: 'Never' })) {
      expect(button).toHaveAttribute('aria-pressed', 'false');
    }
  });

  it('supports selecting more than one family history condition', async () => {
    const user = userEvent.setup();
    renderPageInSession(<FamilySocialHistory />, { step: 'family-social-history' });

    await user.click(screen.getByRole('checkbox', { name: 'Diabetes' }));
    await user.click(screen.getByRole('checkbox', { name: 'Cancer' }));

    expect(screen.getByRole('checkbox', { name: 'Diabetes' })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: 'Cancer' })).toBeChecked();
  });

  it('lets the patient pick a tobacco and alcohol use option', async () => {
    const user = userEvent.setup();
    renderPageInSession(<FamilySocialHistory />, { step: 'family-social-history' });

    await user.click(screen.getByRole('button', { name: 'Former' }));
    await user.click(screen.getByRole('button', { name: 'Occasional' }));

    expect(screen.getByRole('button', { name: 'Former' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'Occasional' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    // "Never" appears in both the tobacco and alcohol groups — neither was picked.
    for (const button of screen.getAllByRole('button', { name: 'Never' })) {
      expect(button).toHaveAttribute('aria-pressed', 'false');
    }
  });

  it('has no manual back/next controls', () => {
    renderPageInSession(<FamilySocialHistory />, { step: 'family-social-history' });

    expect(screen.queryByRole('button', { name: 'Back' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Next' })).not.toBeInTheDocument();
  });
});

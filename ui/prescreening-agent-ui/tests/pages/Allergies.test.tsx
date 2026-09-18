import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { Allergies } from '@/pages/Allergies';
import { renderPageInSession } from '../testUtils';

describe('Allergies', () => {
  it('asks about allergies and starts with one empty entry', () => {
    renderPageInSession(<Allergies />, { step: 'allergies' });

    expect(screen.getByRole('heading', { name: /^allergies$/i })).toBeInTheDocument();
    expect(screen.getByLabelText('Allergen')).toHaveValue('');
    expect(screen.getByLabelText('Reaction')).toHaveValue('');
    // Solo entry — nothing to remove yet.
    expect(screen.queryByRole('button', { name: /remove allergy/i })).not.toBeInTheDocument();
  });

  it('fills the allergen and its reaction from what the assistant heard', () => {
    renderPageInSession(<Allergies />, {
      step: 'allergies',
      call: { prefill: { allergies: { allergies: 'penicillin: comes up in a rash' } } },
    });

    expect(screen.getByLabelText('Allergen')).toHaveValue('penicillin');
    expect(screen.getByLabelText('Reaction')).toHaveValue('comes up in a rash');
  });

  it('keeps two allergens apart rather than merging them into one entry', () => {
    renderPageInSession(<Allergies />, {
      step: 'allergies',
      call: {
        prefill: {
          allergies: { allergies: 'penicillin: rash; peanuts: swelling' },
        },
      },
    });

    expect(screen.getAllByLabelText('Allergen').map((input) => input)).toHaveLength(2);
    expect(screen.getAllByLabelText('Reaction')[1]).toHaveValue('swelling');
  });

  it('marks "no known allergies" when the assistant heard exactly that', () => {
    // A clinically meaningful answer, and distinguishable from an empty
    // list nobody got round to filling in.
    renderPageInSession(<Allergies />, {
      step: 'allergies',
      call: { prefill: { allergies: { no_known_allergies: 'yes' } } },
    });

    expect(screen.getByRole('checkbox', { name: /known allergies/i })).toBeChecked();
    expect(screen.queryByLabelText('Allergen')).not.toBeInTheDocument();
  });

  it('leaves the opt-out untouched when the answer was neither yes nor no', () => {
    renderPageInSession(<Allergies />, {
      step: 'allergies',
      call: { prefill: { allergies: { no_known_allergies: 'not sure' } } },
    });

    expect(screen.getByRole('checkbox', { name: /known allergies/i })).not.toBeChecked();
  });

  it('adds and removes allergy entries, reporting the list back', async () => {
    const user = userEvent.setup();
    const reportFieldEdit = vi.fn();
    renderPageInSession(<Allergies />, { step: 'allergies', call: { reportFieldEdit } });

    await user.click(screen.getByRole('button', { name: /add another allergy/i }));
    expect(screen.getAllByLabelText('Allergen')).toHaveLength(2);

    await user.click(screen.getByRole('button', { name: 'Remove allergy 2' }));
    expect(screen.getAllByLabelText('Allergen')).toHaveLength(1);
    expect(reportFieldEdit).toHaveBeenCalled();
  });

  it('hides the entry list once "no known allergies" is checked', async () => {
    const user = userEvent.setup();
    renderPageInSession(<Allergies />, { step: 'allergies' });

    await user.click(screen.getByRole('checkbox', { name: /known allergies/i }));

    expect(screen.queryByLabelText('Allergen')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /add another allergy/i })).not.toBeInTheDocument();
  });

  it('has no manual back/next controls', () => {
    renderPageInSession(<Allergies />, { step: 'allergies' });

    expect(screen.queryByRole('button', { name: 'Back' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Next' })).not.toBeInTheDocument();
  });
});

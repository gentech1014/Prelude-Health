import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { PatientConcerns } from '@/pages/PatientConcerns';
import { renderPageInSession } from '../testUtils';

describe('PatientConcerns', () => {
  it('asks what brings the patient in and lists selectable concerns', () => {
    renderPageInSession(<PatientConcerns />, { step: 'patient-concerns' });

    expect(screen.getByRole('heading', { name: /what brings you in today/i })).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: 'Pain or discomfort' })).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: 'Routine check-up' })).toBeInTheDocument();
  });

  it('nothing is selected until the patient or the assistant says something', () => {
    // An earlier version pre-ticked a concern to stand in for the voice
    // turn, which put an answer on the record the patient never gave.
    renderPageInSession(<PatientConcerns />, { step: 'patient-concerns' });

    for (const checkbox of screen.getAllByRole('checkbox')) {
      expect(checkbox).not.toBeChecked();
    }
  });

  it('ticks the card matching what the assistant heard, in the patient’s own words', () => {
    renderPageInSession(<PatientConcerns />, {
      step: 'patient-concerns',
      call: {
        prefill: {
          'patient-concerns': { concerns: 'pain or discomfort', concern_details: 'left knee' },
        },
      },
    });

    expect(screen.getByRole('checkbox', { name: 'Pain or discomfort' })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: 'Routine check-up' })).not.toBeChecked();
  });

  it('does not guess a card for something it could not match', () => {
    // Guessing which checkbox a patient meant is exactly the invention the
    // clinical rules forbid; the transcript already has what they said.
    renderPageInSession(<PatientConcerns />, {
      step: 'patient-concerns',
      call: { prefill: { 'patient-concerns': { concerns: 'my ears have been ringing' } } },
    });

    for (const checkbox of screen.getAllByRole('checkbox')) {
      expect(checkbox).not.toBeChecked();
    }
  });

  it('reports a correction back into the conversation', async () => {
    const user = userEvent.setup();
    const reportFieldEdit = vi.fn();
    renderPageInSession(<PatientConcerns />, {
      step: 'patient-concerns',
      call: { reportFieldEdit },
    });

    await user.click(screen.getByRole('checkbox', { name: 'Routine check-up' }));

    // Labels, not ids: the value lands in the patient's own transcript.
    expect(reportFieldEdit).toHaveBeenCalledWith(
      'patient-concerns',
      'concerns',
      'Routine check-up',
    );
  });

  it('supports selecting more than one concern', async () => {
    const user = userEvent.setup();
    renderPageInSession(<PatientConcerns />, { step: 'patient-concerns' });

    await user.click(screen.getByRole('checkbox', { name: 'Pain or discomfort' }));
    await user.click(screen.getByRole('checkbox', { name: 'Injury follow-up' }));

    expect(screen.getByRole('checkbox', { name: 'Pain or discomfort' })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: 'Injury follow-up' })).toBeChecked();
  });

  it('has no manual back/next controls', () => {
    renderPageInSession(<PatientConcerns />, { step: 'patient-concerns' });

    expect(screen.queryByRole('button', { name: 'Back' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Next' })).not.toBeInTheDocument();
  });
});

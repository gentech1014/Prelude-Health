import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { RecentCare } from '@/pages/RecentCare';
import { renderPageInSession } from '../testUtils';

describe('RecentCare', () => {
  it('asks about recent care and tests, unanswered by default', () => {
    renderPageInSession(<RecentCare />, { step: 'recent-care' });

    expect(screen.getByRole('heading', { name: /recent care and tests/i })).toBeInTheDocument();
    expect(screen.getAllByText(/seen anyone else about this recently/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/tests or results recently/i).length).toBeGreaterThan(0);
    expect(screen.queryByLabelText('Who did you see?')).not.toBeInTheDocument();
  });

  it('reveals provider details only once "seen anyone else" is answered yes', async () => {
    const user = userEvent.setup();
    renderPageInSession(<RecentCare />, { step: 'recent-care' });

    const [yesButton] = screen.getAllByRole('button', { name: 'Yes' });
    await user.click(yesButton!);

    expect(screen.getByLabelText('Who did you see?')).toBeInTheDocument();
    expect(screen.getByLabelText('When?')).toBeInTheDocument();
  });

  it('answers the yes/no question from what the assistant heard, and fills the detail', () => {
    renderPageInSession(<RecentCare />, {
      step: 'recent-care',
      call: {
        prefill: {
          'recent-care': {
            saw_other_provider: 'yes',
            provider_who: 'the urgent care clinic',
            provider_when: 'last Tuesday',
          },
        },
      },
    });

    expect(screen.getByLabelText('Who did you see?')).toHaveValue('the urgent care clinic');
    expect(screen.getByLabelText('When?')).toHaveValue('last Tuesday');
  });

  it('shows no upload control until the assistant actually asks for a document', () => {
    // The assistant opens it from wherever the call has got to, so it is
    // not tied to this screen and must not appear unprompted.
    renderPageInSession(<RecentCare />, { step: 'recent-care' });

    expect(
      screen.queryByRole('button', { name: /upload a report or take a photo/i }),
    ).not.toBeInTheDocument();
  });

  it('offers the upload once the assistant has asked, and can be dismissed', async () => {
    const user = userEvent.setup();
    renderPageInSession(<RecentCare />, {
      step: 'recent-care',
      call: { isUploadRequested: true },
    });

    expect(
      screen.getByRole('button', { name: /upload a report or take a photo/i }),
    ).toBeInTheDocument();
    // Dismissable because the assistant does not wait for the file either.
    expect(
      screen.getByRole('button', { name: /dismiss the document request/i }),
    ).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /dismiss the document request/i }));
  });

  it('has no manual back/next controls', () => {
    renderPageInSession(<RecentCare />, { step: 'recent-care' });

    expect(screen.queryByRole('button', { name: 'Back' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Next' })).not.toBeInTheDocument();
  });
});

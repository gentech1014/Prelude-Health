import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { ThankYou } from '@/pages/ThankYou';
import { renderPageInSession } from '../testUtils';

function renderThankYou(
  options: Parameters<typeof renderPageInSession>[1] = {},
): ReturnType<typeof renderPageInSession> {
  return renderPageInSession(<ThankYou />, {
    step: 'thank-you',
    destinationRoutes: { 'appointment-reschedule': <div>Reschedule screen</div> },
    ...options,
  });
}

describe('ThankYou', () => {
  it('recaps the appointment from the session', async () => {
    renderThankYou();

    expect(screen.getByRole('heading', { name: /thank you/i })).toBeInTheDocument();
    expect(await screen.findByText('Dr. Test')).toBeInTheDocument();
    expect(screen.getByText('2:30 PM – 3:00 PM')).toBeInTheDocument();
  });

  it('ticks only the topics the call actually reached', async () => {
    // A recap that claims full coverage tells the patient their doctor has
    // information nobody collected.
    renderThankYou({
      call: {
        visitedScreens: ['confirm-details', 'patient-concerns', 'medication'],
      },
    });

    await screen.findByText('Dr. Test');
    expect(screen.getAllByText('Not covered').length).toBeGreaterThan(0);
    expect(screen.getByText('Medications reviewed')).toBeInTheDocument();
  });

  it('marks a topic as not covered when the call never navigated to it', async () => {
    renderThankYou({ call: { visitedScreens: ['confirm-details'] } });

    await screen.findByText('Dr. Test');
    // Seven of the eight recap rows were never reached.
    expect(screen.getAllByText('Not covered')).toHaveLength(7);
  });

  it('asks whether there is anything they would like help with, and reports it', async () => {
    const user = userEvent.setup();
    const reportFieldEdit = vi.fn();
    renderThankYou({ call: { reportFieldEdit } });

    const field = screen.getByLabelText(/anything you would like help with/i);
    await user.type(field, 'I forgot to mention my back');
    expect(reportFieldEdit).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: /send this answer/i }));

    expect(reportFieldEdit).toHaveBeenCalledWith(
      'thank-you',
      'anything_else',
      'I forgot to mention my back',
    );
  });

  it('fills that field from what the assistant heard at the end of the call', async () => {
    renderThankYou({
      call: { prefill: { 'thank-you': { anything_else: 'my knee also clicks' } } },
    });

    expect(await screen.findByLabelText(/anything you would like help with/i)).toHaveValue(
      'my knee also clicks',
    );
  });

  it('offers to reschedule the appointment shown in the recap', async () => {
    const user = userEvent.setup();
    renderThankYou();

    await user.click(await screen.findByRole('button', { name: /change time/i }));

    expect(await screen.findByText('Reschedule screen')).toBeInTheDocument();
  });

  it('asks the assistant for a new time while the call is live, instead of jumping the screen', async () => {
    // During a call the assistant owns the screen: it fetches the times
    // and moves the patient once it has them. Navigating from here as
    // well would land them on the list before anything was offered.
    const user = userEvent.setup();
    const requestReschedule = vi.fn();
    renderThankYou({ call: { status: 'live', requestReschedule } });

    await user.click(await screen.findByRole('button', { name: /change time/i }));

    expect(requestReschedule).toHaveBeenCalledTimes(1);
    expect(screen.queryByText('Reschedule screen')).not.toBeInTheDocument();
  });

  it('never labels either appointment control as a bare yes or no', async () => {
    // The reported bug. The assistant's closing question is "is there
    // anything I can help with", and the answer to it was being read
    // against a card whose buttons said Yes and No -- where "No" meant
    // reschedule. A patient declining help was offered a different
    // appointment, and the Yes button did nothing whatsoever.
    renderThankYou({ call: { status: 'live' } });

    await screen.findByText('Dr. Test');
    expect(screen.queryByRole('button', { name: /^yes$/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^no$/i })).not.toBeInTheDocument();
  });

  it('tells the assistant the time is being kept, rather than doing nothing', async () => {
    // Confirming reaches the assistant as the patient's own turn, exactly
    // as saying it aloud would, so it closes the call instead of stalling.
    const user = userEvent.setup();
    const sendTypedAnswer = vi.fn();
    const requestReschedule = vi.fn();
    renderThankYou({ call: { status: 'live', sendTypedAnswer, requestReschedule } });

    await user.click(await screen.findByRole('button', { name: /keep this time/i }));

    expect(sendTypedAnswer).toHaveBeenCalledTimes(1);
    expect(sendTypedAnswer.mock.calls[0]?.[0]).toMatch(/keep/i);
    expect(requestReschedule).not.toHaveBeenCalled();
  });

  it('still offers the same end-call confirmation as the rest of the call', async () => {
    const user = userEvent.setup();
    // Only meaningful while a call is live — the controls are inert
    // otherwise, so this asserts the live case.
    renderThankYou({ call: { status: 'live' } });

    await user.click(screen.getByRole('button', { name: /end call/i }));

    expect(screen.getByRole('alertdialog')).toBeInTheDocument();
  });

  it('tells the patient their answers are kept if they end early', async () => {
    const user = userEvent.setup();
    renderThankYou({ call: { status: 'live' } });

    await user.click(screen.getByRole('button', { name: /end call/i }));

    // The session is resumable, so the old "nothing will be saved" copy
    // would have been a lie about their own data.
    expect(screen.getByText(/picks up where you left off/i)).toBeInTheDocument();
  });

  it('has no manual back/next controls', () => {
    renderThankYou();

    expect(screen.queryByRole('button', { name: 'Back' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Next' })).not.toBeInTheDocument();
  });
});

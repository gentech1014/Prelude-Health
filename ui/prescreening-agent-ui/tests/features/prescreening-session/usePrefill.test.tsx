import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState, type JSX, type ReactNode } from 'react';
import { describe, expect, it, vi } from 'vitest';
import {
  IntakeCallContext,
  type IntakeCallValue,
} from '@/features/prescreening-session/intakeCallContext';
import { usePrefilledText } from '@/features/prescreening-session/usePrefill';
import type {
  IntakePrefill,
  IntakeSelections,
} from '@/features/prescreening-session/intakeProtocol';

/**
 * The ownership rule, which is the whole reason prefill is safe to show:
 * the server leads until the patient touches a control, and the patient
 * wins from then on. Getting it backwards either strands a stale value on
 * screen or overwrites a correction the patient just made.
 */

function callValue(
  prefill: IntakePrefill,
  reportFieldEdit = vi.fn(),
  selections: IntakeSelections = {},
): IntakeCallValue {
  return {
    status: 'live',
    problem: null,
    endReason: null,
    micStream: null,
    agentStream: null,
    isAgentSpeaking: false,
    isMuted: false,
    isOnHold: false,
    callDurationSeconds: 0,
    activeCaption: null,
    turns: [],
    screen: 'symptom-story',
    visitedScreens: [],
    prefill,
    selections,
    symptomIntake: { current: null, answers: [] },
    isUploadRequested: false,
    needsAudioUnlock: false,
    unlockAudio: vi.fn(),
    start: () => Promise.resolve(),
    hangUp: vi.fn(),
    retry: () => Promise.resolve(),
    setMuted: vi.fn(),
    setOnHold: vi.fn(),
    sendTypedAnswer: vi.fn(),
    reportFieldEdit,
    reportSymptomAnswer: vi.fn(),
    notifyConsentRecorded: vi.fn(),
    requestReschedule: vi.fn(),
    reportAppointmentRescheduled: vi.fn(),
    dismissUploadRequest: vi.fn(),
    reportDocumentUploaded: vi.fn(),
    reportDocumentUploadFailed: vi.fn(),
  };
}

function Field(): JSX.Element {
  const onset = usePrefilledText('symptom-story', 'onset');
  return (
    <label>
      Onset
      <input value={onset.value} onChange={(event) => onset.onChange(event.target.value)} />
      <button type="button" onClick={onset.onSubmit}>
        Send
      </button>
    </label>
  );
}

/** Lets a test deliver a second prefill the way a later tool call would. */
function Harness({
  initial,
  children,
}: {
  initial: IntakeCallValue;
  children: ReactNode;
}): JSX.Element {
  const [value, setValue] = useState(initial);
  return (
    <IntakeCallContext.Provider value={value}>
      <button
        type="button"
        onClick={() => setValue(callValue({ 'symptom-story': { onset: 'six weeks' } }))}
      >
        Deliver later prefill
      </button>
      {children}
    </IntakeCallContext.Provider>
  );
}

describe('usePrefilledText', () => {
  it('shows what the assistant heard before the patient has touched it', () => {
    render(
      <IntakeCallContext.Provider value={callValue({ 'symptom-story': { onset: 'two weeks' } })}>
        <Field />
      </IntakeCallContext.Provider>,
    );

    expect(screen.getByLabelText('Onset')).toHaveValue('two weeks');
  });

  it('follows a later prefill while the patient has not typed anything', async () => {
    const user = userEvent.setup();
    render(
      <Harness initial={callValue({ 'symptom-story': { onset: 'two weeks' } })}>
        <Field />
      </Harness>,
    );

    await user.click(screen.getByRole('button', { name: /deliver later prefill/i }));

    expect(screen.getByLabelText('Onset')).toHaveValue('six weeks');
  });

  it('keeps the patient’s correction when the assistant records that field again', async () => {
    const user = userEvent.setup();
    render(
      <Harness initial={callValue({ 'symptom-story': { onset: 'two weeks' } })}>
        <Field />
      </Harness>,
    );

    await user.clear(screen.getByLabelText('Onset'));
    await user.type(screen.getByLabelText('Onset'), 'three days');
    await user.click(screen.getByRole('button', { name: /deliver later prefill/i }));

    // Showing the value is only worth doing if the patient can fix it.
    expect(screen.getByLabelText('Onset')).toHaveValue('three days');
  });

  it('does not report while the patient is still typing', async () => {
    const user = userEvent.setup();
    const reportFieldEdit = vi.fn();
    render(
      <IntakeCallContext.Provider value={callValue({}, reportFieldEdit)}>
        <Field />
      </IntakeCallContext.Provider>,
    );

    // A pause mid-sentence must never be read as a finished answer -- see
    // `PrefilledText.onSubmit`'s docstring for why this stopped being a
    // debounced auto-send.
    await act(async () => {
      await user.type(screen.getByLabelText('Onset'), 'ab');
    });

    expect(reportFieldEdit).not.toHaveBeenCalled();
  });

  it('reports the typed value once the patient explicitly submits it', async () => {
    const user = userEvent.setup();
    const reportFieldEdit = vi.fn();
    render(
      <IntakeCallContext.Provider value={callValue({}, reportFieldEdit)}>
        <Field />
      </IntakeCallContext.Provider>,
    );

    await act(async () => {
      await user.type(screen.getByLabelText('Onset'), 'ab');
    });
    await user.click(screen.getByRole('button', { name: 'Send' }));

    expect(reportFieldEdit).toHaveBeenLastCalledWith('symptom-story', 'onset', 'ab');
  });

  it('does not report an unedited, merely-confirmed prefilled value', async () => {
    const user = userEvent.setup();
    const reportFieldEdit = vi.fn();
    render(
      <IntakeCallContext.Provider
        value={callValue({ 'symptom-story': { onset: 'two weeks' } }, reportFieldEdit)}
      >
        <Field />
      </IntakeCallContext.Provider>,
    );

    // Send is reachable even before the patient has touched the field --
    // clicking it without an edit must not resend the agent's own value
    // back as though the patient had corrected it.
    await user.click(screen.getByRole('button', { name: 'Send' }));

    expect(reportFieldEdit).not.toHaveBeenCalled();
  });
});

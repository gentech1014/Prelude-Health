import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { Welcome } from '@/pages/Welcome';
import { renderPageInSession } from '../testUtils';

function renderWelcome(
  options: Parameters<typeof renderPageInSession>[1] = {},
): ReturnType<typeof renderPageInSession> {
  return renderPageInSession(<Welcome />, {
    step: 'welcome',
    destinationRoutes: { 'confirm-details': <div>Confirm details screen</div> },
    ...options,
  });
}

describe('Welcome', () => {
  it('introduces the assistant by the name the deployment gave it', async () => {
    renderWelcome();

    expect(
      await screen.findByRole('heading', { name: /hi, i am test assistant/i }),
    ).toBeInTheDocument();
    expect(screen.getByText(/secure & private/i)).toBeInTheDocument();
    expect(screen.getByText(/few minutes/i)).toBeInTheDocument();
  });

  it('has no button to start the call — it is already running', async () => {
    // A patient who opened their prescreening link has already said they
    // want to begin. Asking them to say it again earns nothing.
    renderWelcome();

    await screen.findByRole('heading', { name: /hi, i am test assistant/i });
    expect(screen.queryByRole('button', { name: /start|rejoin|begin/i })).not.toBeInTheDocument();
  });

  it('shows what the assistant is saying, and whose turn it is', async () => {
    renderWelcome({
      call: {
        status: 'live',
        isAgentSpeaking: true,
        activeCaption: {
          role: 'agent',
          text: 'Hello Test Patient.',
          isFinal: false,
          turnId: 'turn-1',
        },
      },
    });

    expect(await screen.findByText('Hello Test Patient.')).toBeInTheDocument();
    expect(screen.getAllByText('Speaking…').length).toBeGreaterThan(0);
  });

  it('offers a tap only when the browser is blocking playback', async () => {
    // Autoplay policy, not our design: an AudioContext created outside a
    // user gesture starts suspended. Everyone else gets no button at all.
    renderWelcome({ call: { status: 'live', needsAudioUnlock: true } });

    expect(
      await screen.findByRole('button', { name: /tap to hear your assistant/i }),
    ).toBeInTheDocument();
  });

  it('unlocks playback from inside the tap', async () => {
    const user = userEvent.setup();
    const unlockAudio = vi.fn();
    renderWelcome({ call: { status: 'live', needsAudioUnlock: true, unlockAudio } });

    await user.click(await screen.findByRole('button', { name: /tap to hear/i }));

    expect(unlockAudio).toHaveBeenCalled();
  });

  it('tells the patient plainly when the microphone is unavailable', async () => {
    // jsdom has no Web Audio, so starting genuinely fails here — which is
    // the path a patient on a locked-down browser actually takes. They are
    // told, and not blocked: every screen keeps its typed path.
    renderWelcome();

    expect(await screen.findByText(/microphone|cannot capture audio/i)).toBeInTheDocument();
  });

  it('shows no in-call controls before the call exists', () => {
    // The control bar belongs to a live call; Welcome is the tap that
    // starts one, so hanging up here would mean nothing.
    renderWelcome();

    expect(screen.queryByRole('button', { name: /end call/i })).not.toBeInTheDocument();
  });

  it('has no manual back/next controls — advancing is not a timer or a tap', () => {
    renderWelcome();

    expect(screen.queryByRole('button', { name: 'Back' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Next' })).not.toBeInTheDocument();
  });
});

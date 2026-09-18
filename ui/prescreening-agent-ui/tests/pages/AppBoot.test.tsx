import { screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AppBoot } from '@/pages/AppBoot';
import { ApiError } from '@/services/apiClient';
import { fetchPrescreeningSessionContext } from '@/services/prescreeningSessionService';
import { buildTestSession } from '../sessionFixture';
import { renderPageInSession } from '../testUtils';

const fetchContextMock = vi.mocked(fetchPrescreeningSessionContext);

describe('AppBoot', () => {
  beforeEach(() => {
    fetchContextMock.mockReset();
    fetchContextMock.mockResolvedValue(buildTestSession());
  });

  it('shows a loading status while the session handshake is in flight', () => {
    renderPageInSession(<AppBoot />);

    expect(screen.getByRole('status')).toHaveTextContent(/pre-visit screening|assistant/i);
  });

  it('holds the loader until the assistant is actually on the line', async () => {
    // A real session is not enough. Handing off as soon as the handshake
    // lands would show the patient an introduction with nobody there to
    // give it, then silence.
    renderPageInSession(<AppBoot />, { destinationRoutes: { welcome: <div>Welcome screen</div> } });

    await waitFor(() => expect(screen.getByRole('status')).toBeInTheDocument());
    expect(screen.queryByText('Welcome screen')).not.toBeInTheDocument();
  });

  it('never picks the first screen itself', async () => {
    // Which screen the call is on is the server's answer, delivered on the
    // socket and applied by IntakeCallProvider. Sending everyone to
    // `welcome` from here raced that on every connect, and was wrong
    // outright on a resumed call that had already passed it.
    renderPageInSession(<AppBoot />, {
      destinationRoutes: { welcome: <div>Welcome screen</div> },
      call: { status: 'live' },
    });

    await waitFor(() => expect(screen.getByRole('status')).toBeInTheDocument());
    expect(screen.queryByText('Welcome screen')).not.toBeInTheDocument();
  });

  it('lets a dropped call be rejoined rather than treating it as finished', async () => {
    // An interrupted session keeps a resume marker on the server, so
    // re-entering the link must wait for the reconnect, not close the door.
    fetchContextMock.mockResolvedValue(buildTestSession({ status: 'interrupted' }));
    renderPageInSession(<AppBoot />, { call: { status: 'live' } });

    await waitFor(() => expect(screen.getByRole('status')).toBeInTheDocument());
    expect(screen.queryByText(/already completed/i)).not.toBeInTheDocument();
  });

  it('explains a call that could not be reached, rather than spinning forever', async () => {
    renderPageInSession(<AppBoot />, {
      call: {
        status: 'failed',
        problem: { kind: 'connection', message: 'No signal here.', recoverable: true },
      },
    });

    expect(await screen.findByText(/could not reach your assistant/i)).toBeInTheDocument();
    expect(screen.getByText('No signal here.')).toBeInTheDocument();
  });

  it('explains an invalid link instead of stranding the patient on a loader', async () => {
    fetchContextMock.mockRejectedValue(new ApiError('unauthorized', 'nope'));
    renderPageInSession(<AppBoot />, { destinationRoutes: { welcome: <div>Welcome screen</div> } });

    expect(await screen.findByText(/this link is no longer valid/i)).toBeInTheDocument();
    expect(screen.queryByText('Welcome screen')).not.toBeInTheDocument();
  });

  it('offers a retry when the patient is offline', async () => {
    fetchContextMock.mockRejectedValue(new ApiError('offline', 'nope'));
    renderPageInSession(<AppBoot />);

    expect(await screen.findByText(/you appear to be offline/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /try again/i })).toBeInTheDocument();
  });

  it('does not restart the call for a session that already declined', async () => {
    // A declined session is finished. Re-entering the link must explain that,
    // not drop the patient back into a consent flow they already refused.
    fetchContextMock.mockResolvedValue(buildTestSession({ status: 'declined' }));
    renderPageInSession(<AppBoot />, { destinationRoutes: { welcome: <div>Welcome screen</div> } });

    expect(await screen.findByText(/pre-visit screening declined/i)).toBeInTheDocument();
    expect(screen.queryByText('Welcome screen')).not.toBeInTheDocument();
  });

  it('does not restart the call for a session that already completed', async () => {
    fetchContextMock.mockResolvedValue(buildTestSession({ status: 'summary_ready' }));
    renderPageInSession(<AppBoot />, { destinationRoutes: { welcome: <div>Welcome screen</div> } });

    expect(await screen.findByText(/already completed/i)).toBeInTheDocument();
    expect(screen.queryByText('Welcome screen')).not.toBeInTheDocument();
  });

  it('explains an expired link in its own words', async () => {
    fetchContextMock.mockResolvedValue(buildTestSession({ status: 'expired' }));
    renderPageInSession(<AppBoot />);

    expect(await screen.findByText(/this link has expired/i)).toBeInTheDocument();
  });
});

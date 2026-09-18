import { render, screen } from '@testing-library/react';
import type { JSX } from 'react';
import { MemoryRouter, Route, Routes, useSearchParams } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { PrescreeningSessionProvider } from '@/features/prescreening-session/PrescreeningSessionProvider';
import { usePrescreeningSession } from '@/features/prescreening-session/usePrescreeningSession';
import {
  attachPrescreeningSession,
  fetchPrescreeningSessionContext,
} from '@/services/prescreeningSessionService';
import { renderPageInSession } from '../../testUtils';

const attachMock = vi.mocked(attachPrescreeningSession);
const fetchContextMock = vi.mocked(fetchPrescreeningSessionContext);

/** Surfaces context/URL state as text so assertions don't reach into router internals. */
function SessionProbe(): JSX.Element {
  const { sessionId, phase, session } = usePrescreeningSession();
  const [searchParams] = useSearchParams();
  return (
    <div>
      <span>session:{sessionId}</span>
      <span>token:{searchParams.get('token') ?? 'none'}</span>
      <span>phase:{phase}</span>
      <span>patient:{session?.patient.name ?? 'none'}</span>
    </div>
  );
}

describe('PrescreeningSessionProvider', () => {
  beforeEach(() => {
    attachMock.mockClear();
    fetchContextMock.mockClear();
  });

  it('exposes the sessionId from the route param to every step beneath it', () => {
    renderPageInSession(<SessionProbe />, { step: 'welcome' });

    expect(screen.getByText(/session:test-session/)).toBeInTheDocument();
  });

  it('consumes a one-time entry token and strips it from the URL', async () => {
    renderPageInSession(<SessionProbe />, { step: 'welcome', search: '?token=secret-token' });

    expect(await screen.findByText('token:none')).toBeInTheDocument();
  });

  it('leaves the URL untouched when there is no token to consume', () => {
    renderPageInSession(<SessionProbe />, { step: 'welcome' });

    expect(screen.getByText('token:none')).toBeInTheDocument();
  });

  it('exchanges the entry token for a session rather than holding on to it', async () => {
    renderPageInSession(<SessionProbe />, { step: 'welcome', search: '?token=secret-token' });

    expect(await screen.findByText('phase:ready')).toBeInTheDocument();
    expect(attachMock).toHaveBeenCalledWith('test-session', 'secret-token');
  });

  it('never writes the entry token to browser storage', async () => {
    // The token is a bearer credential for PHI. It is traded for an HttpOnly
    // cookie precisely so nothing readable by page scripts ever holds it.
    renderPageInSession(<SessionProbe />, { step: 'welcome', search: '?token=secret-token' });
    await screen.findByText('phase:ready');

    const readAll = (store: Storage): string =>
      Array.from(
        { length: store.length },
        (_, index) => store.getItem(store.key(index) ?? '') ?? '',
      ).join('|');
    expect(readAll(localStorage)).not.toContain('secret-token');
    expect(readAll(sessionStorage)).not.toContain('secret-token');
  });

  it('re-reads an existing session on entry without a token, so a refresh recovers', async () => {
    renderPageInSession(<SessionProbe />, { step: 'welcome' });

    expect(await screen.findByText('phase:ready')).toBeInTheDocument();
    expect(fetchContextMock).toHaveBeenCalledWith('test-session');
    expect(attachMock).not.toHaveBeenCalled();
  });

  it('reports a failed handshake instead of exposing a half-built session', async () => {
    fetchContextMock.mockRejectedValueOnce(new Error('network down'));
    renderPageInSession(<SessionProbe />, { step: 'welcome' });

    expect(await screen.findByText('phase:failed')).toBeInTheDocument();
    expect(screen.getByText('patient:none')).toBeInTheDocument();
  });
});

describe('PrescreeningSessionProvider (missing route param)', () => {
  it('throws when mounted at a route with no :sessionId param, guarding against router misconfiguration', () => {
    expect(() =>
      render(
        <MemoryRouter initialEntries={['/prescreen']}>
          <Routes>
            <Route path="/prescreen" element={<PrescreeningSessionProvider />} />
          </Routes>
        </MemoryRouter>,
      ),
    ).toThrow();
  });
});

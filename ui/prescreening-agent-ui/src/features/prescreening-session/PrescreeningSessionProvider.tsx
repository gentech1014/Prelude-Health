import { AnimatePresence } from 'framer-motion';
import { useCallback, useEffect, useMemo, useRef, useState, type JSX } from 'react';
import { Outlet, useLocation, useParams, useSearchParams } from 'react-router-dom';
import { IntakeCallProvider } from '@/features/prescreening-session/IntakeCallProvider';
import {
  PrescreeningSessionContext,
  type PrescreeningSessionPhase,
} from '@/features/prescreening-session/prescreeningSessionContext';
import { toApiError, type ApiError } from '@/services/apiClient';
import {
  attachPrescreeningSession,
  fetchPrescreeningSessionContext,
  type PrescreeningSessionContext as SessionContext,
} from '@/services/prescreeningSessionService';

/**
 * Layout route for `/prescreen/:sessionId` — the one place every step of the
 * call shares a session identity. Mounted once per session (see RootLayout,
 * which keys its own transition by sessionId rather than the full path so
 * step-to-step navigation never remounts this provider) and owns its own
 * step-transition animation for exactly that reason.
 *
 * Also the one place the entry link's token is ever read. It is exchanged
 * for an HttpOnly session cookie and stripped from the URL immediately, so
 * it never lingers in the address bar, browser history, or a Referer header
 * — and the credential that replaces it is one page JavaScript cannot read.
 *
 * The live call is hosted inside it, not above it: the call needs the
 * session's consent state to know whether it may connect at all.
 */
export function PrescreeningSessionProvider(): JSX.Element {
  const { sessionId } = useParams<{ sessionId: string }>();
  const location = useLocation();
  const [searchParams, setSearchParams] = useSearchParams();
  const hasHandshakeStarted = useRef(false);

  const [phase, setPhase] = useState<PrescreeningSessionPhase>('connecting');
  const [session, setSession] = useState<SessionContext | null>(null);
  const [error, setError] = useState<ApiError | null>(null);

  const applySession = useCallback((next: SessionContext): void => {
    setSession(next);
    setPhase('ready');
    setError(null);
  }, []);

  // Without a token this re-reads an existing cookie session, which is what a
  // refresh mid-call does — the token is long gone from the URL by then.
  const establish = useCallback(
    async (id: string, token: string | null): Promise<void> => {
      setPhase('connecting');
      setError(null);
      try {
        applySession(
          token === null
            ? await fetchPrescreeningSessionContext(id)
            : await attachPrescreeningSession(id, token),
        );
      } catch (cause) {
        setError(toApiError(cause));
        setPhase('failed');
      }
    },
    [applySession],
  );

  useEffect(() => {
    if (hasHandshakeStarted.current || !sessionId) return;
    hasHandshakeStarted.current = true;

    const token = searchParams.get('token');
    void establish(sessionId, token);

    if (token !== null) {
      setSearchParams(
        (current) => {
          current.delete('token');
          return current;
        },
        { replace: true },
      );
    }
  }, [establish, searchParams, setSearchParams, sessionId]);

  const refresh = useCallback(async (): Promise<void> => {
    if (sessionId) await establish(sessionId, null);
  }, [establish, sessionId]);

  const value = useMemo(
    () => ({ sessionId: sessionId ?? '', phase, session, error, refresh, applySession }),
    [sessionId, phase, session, error, refresh, applySession],
  );

  if (!sessionId) {
    throw new Error('PrescreeningSessionProvider rendered without a :sessionId route param');
  }

  return (
    <PrescreeningSessionContext.Provider value={value}>
      <IntakeCallProvider>
        <AnimatePresence mode="wait" initial={false}>
          <Outlet key={location.pathname} />
        </AnimatePresence>
      </IntakeCallProvider>
    </PrescreeningSessionContext.Provider>
  );
}

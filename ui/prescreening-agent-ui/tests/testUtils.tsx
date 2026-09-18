import { render, type RenderResult } from '@testing-library/react';
import { useMemo, type JSX, type ReactElement, type ReactNode } from 'react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { ThemeProvider } from '@/app/ThemeProvider';
import { ToastProvider } from '@/components/states';
import {
  IntakeCallContext,
  type IntakeCallValue,
} from '@/features/prescreening-session/intakeCallContext';
import { PrescreeningSessionProvider } from '@/features/prescreening-session/PrescreeningSessionProvider';
import { useIntakeCall } from '@/features/prescreening-session/useIntakeCall';
import { TEST_SESSION_ID } from './sessionFixture';

interface RenderPageInSessionOptions {
  /** The route segment the page under test is mounted at, e.g. 'appointment-schedule'. Omit for an index route (e.g. AppBoot). */
  step?: string;
  /** Extra sibling routes standing in for navigation destinations, keyed by their path segment. */
  destinationRoutes?: Record<string, ReactNode>;
  /** Appended after the session id in the initial URL, e.g. '?token=abc'. */
  search?: string;
  /** location.state for the initial entry — stands in for state passed by a preceding navigate() call. */
  state?: unknown;
  /**
   * Overrides on the live-call context — the screen the agent has reached,
   * what it has heard, whether it is speaking. Layered over the real
   * engine rather than replacing it, so anything a test does not name keeps
   * its genuine idle value.
   */
  call?: Partial<IntakeCallValue>;
}

interface CallOverrideProps {
  overrides: Partial<IntakeCallValue>;
  children: ReactNode;
}

// A test helper module, never part of a Fast Refresh boundary.
/* eslint-disable-next-line react-refresh/only-export-components */
function CallOverride({ overrides, children }: CallOverrideProps): JSX.Element {
  const actual = useIntakeCall();
  const value = useMemo(() => ({ ...actual, ...overrides }), [actual, overrides]);
  return <IntakeCallContext.Provider value={value}>{children}</IntakeCallContext.Provider>;
}

/**
 * Renders a prescreening page the same way the real router does — nested
 * under /prescreen/:sessionId so the session and live-call contexts
 * resolve — with optional stub destination routes standing in for the
 * neighboring screens a test navigates to.
 */
export function renderPageInSession(
  page: ReactElement,
  options: RenderPageInSessionOptions = {},
): RenderResult {
  const { step, destinationRoutes = {}, search = '', state, call } = options;
  const entryPath = step
    ? `/prescreen/${TEST_SESSION_ID}/${step}`
    : `/prescreen/${TEST_SESSION_ID}`;
  const entry = { pathname: `${entryPath}`, search, state };
  const element = call ? <CallOverride overrides={call}>{page}</CallOverride> : page;

  return render(
    <MemoryRouter initialEntries={[entry]}>
      <ThemeProvider>
        <ToastProvider>
          <Routes>
            <Route path="/prescreen/:sessionId" element={<PrescreeningSessionProvider />}>
              {step ? <Route path={step} element={element} /> : <Route index element={element} />}
              {Object.entries(destinationRoutes).map(([path, destination]) => (
                <Route key={path} path={path} element={destination} />
              ))}
            </Route>
          </Routes>
        </ToastProvider>
      </ThemeProvider>
    </MemoryRouter>,
  );
}

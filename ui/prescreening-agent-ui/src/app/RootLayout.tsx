import { AnimatePresence, MotionConfig } from 'framer-motion';
import type { JSX } from 'react';
import { Outlet, useLocation, useParams } from 'react-router-dom';
import { AppBackgroundScenery } from '@/components/AppBackgroundScenery';

/**
 * Hosts every top-level route so AnimatePresence can see one swap out before
 * the next mounts. Keyed by sessionId (not the full path) once inside a
 * prescreening session so step-to-step navigation doesn't remount
 * PrescreeningSessionProvider underneath it — that provider animates its own
 * step transitions instead. Falls back to the pathname for the few routes
 * outside a session (the entry redirect, the style guide).
 */
export function RootLayout(): JSX.Element {
  const location = useLocation();
  const { sessionId } = useParams<{ sessionId?: string }>();
  const transitionKey = sessionId ?? location.pathname;

  // The booking appointment screen is a distinct desktop/mobile surface with
  // its own dedicated backdrop and rail. Hide the shared mountain illustration
  // completely on /book per user request.
  const isBookingScreen = location.pathname.startsWith('/book');

  return (
    <MotionConfig reducedMotion="user">
      {!isBookingScreen && <AppBackgroundScenery />}
      <AnimatePresence mode="wait" initial={false}>
        <Outlet key={transitionKey} />
      </AnimatePresence>
    </MotionConfig>
  );
}

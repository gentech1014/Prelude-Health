import { useEffect, useState, type JSX, type ReactNode } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { IntakeCallContext } from '@/features/prescreening-session/intakeCallContext';
import { buildPrescreeningStepPath } from '@/features/prescreening-session/prescreeningFlowSteps';
import { useIntakeCallEngine } from '@/features/prescreening-session/useIntakeCallEngine';
import { usePrescreeningSession } from '@/features/prescreening-session/usePrescreeningSession';

interface IntakeCallProviderProps {
  children: ReactNode;
}

/**
 * How long to wait for the assistant to say anything at all before the app
 * puts the consent control in front of the patient itself.
 *
 * A dead-model fallback and nothing more. Leaving `welcome` is otherwise
 * the *server's* move: it emits it at the end of the greeting turn, after
 * every chunk of that audio, and this component just follows.
 *
 * It used to be a 12s timer armed whenever the assistant went quiet, which
 * is what kept breaking the greeting. The assistant is reported quiet the
 * moment it finishes *generating*, seconds before the patient has heard
 * the words, and a five-sentence introduction outlasts twelve seconds
 * easily — so the timer fired mid-greeting and moved the patient to the
 * consent screen with the introduction still playing. Armed only while
 * nothing has been spoken at all, it can no longer race that.
 */
const SILENT_ASSISTANT_FALLBACK_MS = 30_000;

/**
 * Hosts the live call. The server decides which screen the patient is on;
 * this follows it.
 *
 * Deliberately one-directional: the server says which screen, this
 * navigates there, and the engine acks it. Nothing in the UI advances the
 * flow and nothing in it delays one, so the screen cannot get ahead of the
 * conversation or behind it.
 *
 * One narrow exception, documented where it happens: an assistant that
 * never speaks at all must not strand the patient on a screen with no
 * controls. Everything else is the conversation's decision.
 */
export function IntakeCallProvider({ children }: IntakeCallProviderProps): JSX.Element {
  const navigate = useNavigate();
  const location = useLocation();
  const { sessionId, session, refresh } = usePrescreeningSession();

  // Starts as soon as the session is real, not once consent exists: the
  // patient has to hear the greeting before being asked for consent, so
  // the call opens first and the agent is restricted server-side until
  // consent is recorded.
  //
  // `refresh` re-reads the session when the assistant moves the
  // appointment mid-call: the closing screen shows that appointment, and
  // the copy fetched at handshake is stale the moment it changes.
  const call = useIntakeCallEngine({
    sessionId,
    canStart: session !== null,
    onAppointmentChanged: () => void refresh(),
  });

  const { screen, isAgentSpeaking } = call;

  // The greeting has to have actually happened before anything is allowed
  // to move past it. Nova can take longer than the floor below just to
  // wake up, and a floor armed against that silence skipped the
  // introduction entirely — the patient landed on the consent screen
  // having been told nothing.
  const [hasAgentSpoken, setHasAgentSpoken] = useState(false);
  useEffect(() => {
    if (isAgentSpeaking) setHasAgentSpoken(true);
  }, [isAgentSpeaking]);

  const hasConsented = session?.consentGiven ?? false;
  const isOnWelcome = location.pathname.endsWith('/welcome');

  // The screen follows the server, with nothing in between.
  //
  // An earlier version held the opening screen until the assistant went
  // quiet, so a greeting could not be cut off mid-sentence. It could also
  // deadlock: the assistant asking for consent kept talking, the hold kept
  // renewing, and the patient never reached the screen holding the control
  // it was asking them to use. Not being able to consent is worse than a
  // screen that changes while someone is still speaking, and *when* to
  // navigate is the conversation's call to make, not this component's.
  useEffect(() => {
    if (call.status === 'ended' || call.status === 'failed') return;
    if (screen === null) return;

    // Guarded on where the patient actually is, and nothing else. An extra
    // guard on the path last routed to used to sit alongside this, and it
    // made a desync permanent: once the route drifted from the call's
    // screen — a back gesture, a restored history entry — the target was
    // still the one already routed, so this returned and the patient stayed
    // put while the assistant carried on. It only recovered when the call
    // reached a *different* screen, which on `symptom-story` is seven to
    // ten questions later. Every screen the call can name is a real route,
    // so re-navigating converges rather than looping.
    const target = buildPrescreeningStepPath(sessionId, screen);
    if (location.pathname === target) return;
    navigate(target, { replace: true });
  }, [location.pathname, navigate, screen, sessionId, call.status]);

  // The exception: an assistant that never speaks must not strand them.
  //
  // Disarmed the instant it says anything, because from then on the server
  // owns this move and will make it at the end of the greeting turn.
  // Racing that is exactly what used to cut the introduction short.
  useEffect(() => {
    if (hasConsented || !isOnWelcome || hasAgentSpoken) return;

    const timer = window.setTimeout(() => {
      navigate(buildPrescreeningStepPath(sessionId, 'confirm-details'), { replace: true });
    }, SILENT_ASSISTANT_FALLBACK_MS);
    return () => window.clearTimeout(timer);
  }, [hasAgentSpoken, hasConsented, isOnWelcome, navigate, sessionId]);

  // Handle call completion by moving the patient to the final end-call screen
  useEffect(() => {
    if (call.status === 'ended') {
      navigate(buildPrescreeningStepPath(sessionId, 'end-call'), { replace: true });
    }
  }, [call.status, navigate, sessionId]);

  return <IntakeCallContext.Provider value={call}>{children}</IntakeCallContext.Provider>;
}

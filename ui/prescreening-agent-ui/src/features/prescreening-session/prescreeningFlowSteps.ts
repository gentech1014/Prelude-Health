// Single source of truth for step order — every page asks this array
// "what's next/previous" instead of hardcoding a neighbor's literal path.
export const PRESCREENING_FLOW_STEPS = [
  'welcome',
  'confirm-details',
  'appointment-schedule',
  'patient-concerns',
  'symptom-story',
  'medication',
  'allergies',
  'medical-history',
  'recent-care',
  'family-social-history',
  'thank-you',
] as const;

export type PrescreeningFlowStep = (typeof PRESCREENING_FLOW_STEPS)[number];

export type PrescreeningBranchStep = 'appointment-reschedule' | 'end-call';

/**
 * Every screen the server may put the patient on over the intake socket.
 *
 * The ordered flow plus the one branch off its last step: the assistant
 * takes the patient to `appointment-reschedule` when they ask to move
 * their appointment on the closing screen, and brings them back once it
 * is moved. `end-call` is deliberately absent -- the browser goes there
 * on its own once the call is over, and the server never names it.
 */
export const INTAKE_SCREENS = [...PRESCREENING_FLOW_STEPS, 'appointment-reschedule'] as const;

export type IntakeScreenName = (typeof INTAKE_SCREENS)[number];

/** Builds the session-relative path for a step — the only place `/prescreen/:sessionId/...` is assembled. */
export function buildPrescreeningStepPath(
  sessionId: string,
  step: PrescreeningFlowStep | PrescreeningBranchStep,
): string {
  return `/prescreen/${sessionId}/${step}`;
}

/** The session's own root — re-entering it replays AppBoot's connecting screen before the first step. */
export function buildPrescreeningSessionRootPath(sessionId: string): string {
  return `/prescreen/${sessionId}`;
}

import {
  buildPrescreeningStepPath,
  type PrescreeningBranchStep,
  type PrescreeningFlowStep,
} from '@/features/prescreening-session/prescreeningFlowSteps';
import { usePrescreeningSession } from '@/features/prescreening-session/usePrescreeningSession';

interface StepNavigation {
  /** For branch transitions (reschedule, cancel) that need their own navigate() call with location.state. */
  buildStepPath: (step: PrescreeningFlowStep | PrescreeningBranchStep) => string;
}

/**
 * Builds session-relative step paths against the one ordered flow list
 * instead of each page hardcoding a neighbor's literal path.
 */
export function useStepNavigation(): StepNavigation {
  const { sessionId } = usePrescreeningSession();

  return {
    buildStepPath: (step) => buildPrescreeningStepPath(sessionId, step),
  };
}

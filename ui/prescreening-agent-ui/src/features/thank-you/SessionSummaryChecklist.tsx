import { motion } from 'framer-motion';
import { Check, Minus } from 'lucide-react';
import type { JSX } from 'react';
import type {
  IntakeScreenName,
  PrescreeningFlowStep,
} from '@/features/prescreening-session/prescreeningFlowSteps';
import '@/features/thank-you/SessionSummaryChecklist.css';
import { LIST_ITEM_VARIANTS, LIST_STAGGER_VARIANTS } from '@/lib/pageMotion';

/**
 * What each screen of the call covers, in the order the call works through
 * them.
 *
 * `appointment-schedule` is absent because the intake no longer walks
 * through it — a row that could only ever read "Not covered" tells the
 * patient something is missing when nothing is.
 */
const RECAP_ITEMS: readonly { screen: PrescreeningFlowStep; label: string }[] = [
  { screen: 'confirm-details', label: 'Your details confirmed' },
  { screen: 'patient-concerns', label: 'Reason for visit noted' },
  { screen: 'symptom-story', label: 'Symptoms reviewed' },
  { screen: 'medication', label: 'Medications reviewed' },
  { screen: 'allergies', label: 'Allergies reviewed' },
  { screen: 'medical-history', label: 'Medical history reviewed' },
  { screen: 'recent-care', label: 'Recent care and tests reviewed' },
  { screen: 'family-social-history', label: 'Family and social history reviewed' },
];

interface SessionSummaryChecklistProps {
  /**
   * The screens the call actually reached, from the server. Wider than
   * the rows below: the closing reschedule branch is a screen the call can
   * reach and not a topic it covers, so it simply matches nothing here.
   */
  visitedScreens: readonly IntakeScreenName[];
}

/**
 * What this call actually covered.
 *
 * Derived from the screens the conversation reached, never a fixed list of
 * ticks. A recap that claims every topic was covered when the call skipped
 * some tells the patient their doctor has information nobody collected —
 * and an unticked row is honest information, not a failure to hide.
 */
export function SessionSummaryChecklist({
  visitedScreens,
}: SessionSummaryChecklistProps): JSX.Element {
  return (
    <motion.div
      className="session-summary-checklist"
      variants={LIST_STAGGER_VARIANTS}
      initial="hidden"
      animate="visible"
    >
      {RECAP_ITEMS.map((item) => {
        const isCovered = visitedScreens.includes(item.screen);

        return (
          <motion.div
            variants={LIST_ITEM_VARIANTS}
            className={`session-summary-checklist__item${
              isCovered ? '' : ' session-summary-checklist__item--skipped'
            }`}
            key={item.screen}
          >
            {isCovered ? (
              <Check size={18} aria-hidden="true" className="session-summary-checklist__icon" />
            ) : (
              <Minus size={18} aria-hidden="true" className="session-summary-checklist__icon" />
            )}
            <span className="session-summary-checklist__label">{item.label}</span>
            {isCovered ? null : (
              <span className="session-summary-checklist__note">Not covered</span>
            )}
          </motion.div>
        );
      })}
    </motion.div>
  );
}

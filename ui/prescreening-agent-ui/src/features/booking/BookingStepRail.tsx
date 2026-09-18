import { Check } from 'lucide-react';
import type { JSX } from 'react';

/** The three stages of booking, in order. */
export const BOOKING_STEPS = [
  { id: 'visit-details', name: 'Visit details', hint: 'Choose a service and provider' },
  { id: 'date-time', name: 'Date and time', hint: 'Pick a convenient slot' },
  { id: 'confirm', name: 'Confirm', hint: 'Review and book' },
] as const;

export type BookingStepId = (typeof BOOKING_STEPS)[number]['id'];

interface BookingStepRailProps {
  activeStep: BookingStepId | 'done';
  /** `horizontal` is the compact mobile rendering; `vertical` is the desktop rail. */
  orientation?: 'vertical' | 'horizontal';
}

/**
 * Progress indicator for the booking flow. Derives complete/active/upcoming
 * from the active step rather than tracking its own state, so it can never
 * disagree with the panel beside it.
 */
export function BookingStepRail({
  activeStep,
  orientation = 'vertical',
}: BookingStepRailProps): JSX.Element {
  const activeIndex =
    activeStep === 'done'
      ? BOOKING_STEPS.length
      : BOOKING_STEPS.findIndex((step) => step.id === activeStep);

  return (
    <ol
      className={`booking-steps${orientation === 'horizontal' ? ' booking-steps--horizontal' : ''}`}
    >
      {BOOKING_STEPS.map((step, index) => {
        const isComplete = index < activeIndex;
        const isActive = index === activeIndex;
        const state = isComplete
          ? ' booking-step--complete'
          : isActive
            ? ' booking-step--active'
            : '';

        return (
          <li
            key={step.id}
            className={`booking-step${state}`}
            aria-current={isActive ? 'step' : undefined}
          >
            <span className="booking-step__marker">
              {isComplete ? <Check size={16} aria-hidden="true" /> : index + 1}
            </span>
            <span className="booking-step__label">
              <span className="booking-step__name">{step.name}</span>
              <span className="booking-step__hint">{step.hint}</span>
            </span>
          </li>
        );
      })}
    </ol>
  );
}

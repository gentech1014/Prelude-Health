import { useId, useState, type JSX } from 'react';
import './ConsentCard.css';

interface ConsentCardProps {
  hasConsented: boolean;
  isSubmitting: boolean;
  /** The facility named in the consent copy. Null means name none, never invent one. */
  clinicName: string | null;
  onDecision: (given: boolean) => void;
}

/**
 * Explicit opt-in before sharing health information — the checkbox
 * defaults unchecked and the patient must actively consent, never a
 * pre-ticked box.
 *
 * The decision is submitted to the server, not held here: the live call is
 * gated on the recorded consent, and declining is a real, recorded outcome
 * rather than simply not proceeding.
 */
export function ConsentCard({
  hasConsented,
  isSubmitting,
  clinicName,
  onDecision,
}: ConsentCardProps): JSX.Element {
  const checkboxId = useId();
  const [isTicked, setIsTicked] = useState(false);

  if (hasConsented) {
    return (
      <section className="consent-card consent-card--recorded">
        <p className="consent-card__recorded-message">
          Consent recorded. Your assistant is joining the call.
        </p>
      </section>
    );
  }

  return (
    <section className="consent-card">
      <div className="consent-card__body">
        <label htmlFor={checkboxId} className="consent-card__label">
          <input
            id={checkboxId}
            type="checkbox"
            checked={isTicked}
            onChange={(event) => setIsTicked(event.target.checked)}
            disabled={isSubmitting}
            className="consent-card__checkbox"
          />
          <span className="consent-card__label-text">Yes, I give my Consent.</span>
        </label>

        <p className="consent-card__copy">
          {clinicName
            ? `I agree to share my health information with ${clinicName} for care coordination, as per HIPAA privacy standards.`
            : 'I agree to share my health information with the care team for care coordination, as per HIPAA privacy standards.'}
        </p>

        <div className="consent-card__actions">
          <button
            type="button"
            className="btn-primary"
            onClick={() => onDecision(true)}
            disabled={!isTicked || isSubmitting}
          >
            {isSubmitting ? 'Recording…' : 'Continue'}
          </button>
          <button
            type="button"
            className="btn-secondary"
            onClick={() => onDecision(false)}
            disabled={isSubmitting}
          >
            Decline
          </button>
        </div>
      </div>
    </section>
  );
}

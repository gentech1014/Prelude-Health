import { Check } from 'lucide-react';
import type { JSX } from 'react';
import { visitTypeIcon } from '@/features/booking/visitTypePresentation';
import type { BookingVisitType, VisitType } from '@/services/bookingService';

interface VisitTypeCardsProps {
  visitTypes: readonly VisitType[];
  selectedVisitType: BookingVisitType | null;
  onSelect: (visitType: BookingVisitType) => void;
}

/**
 * Single-select card row for the visit type. A real radio group underneath,
 * so arrow keys move between options and screen readers announce the
 * selection — a div with a click handler would give neither.
 */
export function VisitTypeCards({
  visitTypes,
  selectedVisitType,
  onSelect,
}: VisitTypeCardsProps): JSX.Element {
  return (
    <div className="booking-option-grid" role="radiogroup" aria-label="Appointment type">
      {visitTypes.map((visitType) => {
        const isSelected = visitType.visitType === selectedVisitType;

        return (
          <label
            key={visitType.visitType}
            className={`booking-option${isSelected ? ' booking-option--selected' : ''}`}
          >
            <input
              type="radio"
              name="visit-type"
              className="booking-option__input"
              value={visitType.visitType}
              checked={isSelected}
              onChange={() => onSelect(visitType.visitType)}
            />
            <span className="booking-option__icon">{visitTypeIcon(visitType.visitType)}</span>
            <span className="booking-option__label">{visitType.label}</span>
            <span className="booking-option__description">{visitType.description}</span>
            {isSelected ? (
              <span className="booking-option__check" aria-hidden="true">
                <Check size={14} />
              </span>
            ) : null}
          </label>
        );
      })}
    </div>
  );
}

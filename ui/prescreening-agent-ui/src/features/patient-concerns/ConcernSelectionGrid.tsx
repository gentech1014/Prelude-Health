import { motion } from 'framer-motion';
import { Check } from 'lucide-react';
import type { JSX } from 'react';
import {
  PATIENT_CONCERN_OPTIONS,
  type PatientConcernId,
} from '@/features/patient-concerns/patientConcernOptions';
import '@/features/patient-concerns/ConcernSelectionGrid.css';
import { LIST_ITEM_VARIANTS, LIST_STAGGER_VARIANTS } from '@/lib/pageMotion';

interface ConcernSelectionGridProps {
  selectedIds: readonly PatientConcernId[];
  onToggle: (id: PatientConcernId) => void;
}

/**
 * Multi-select grid of reasons for the visit — the patient can pick more
 * than one, and the assistant ticks whichever matches what they said.
 *
 * Keeps its own card treatment rather than reusing MultiSelectCardGrid:
 * this is the screen's primary control and is styled accordingly, while
 * that one is the compact variant used further down the flow.
 */
export function ConcernSelectionGrid({
  selectedIds,
  onToggle,
}: ConcernSelectionGridProps): JSX.Element {
  return (
    <motion.div
      className="concern-selection-grid"
      variants={LIST_STAGGER_VARIANTS}
      initial="hidden"
      animate="visible"
    >
      {PATIENT_CONCERN_OPTIONS.map((option) => {
        const isSelected = selectedIds.includes(option.id);

        return (
          <motion.label
            key={option.id}
            variants={LIST_ITEM_VARIANTS}
            className={`concern-card${isSelected ? ' concern-card--selected' : ''}`}
          >
            <span className="concern-card__icon" aria-hidden="true">
              {option.icon}
            </span>
            <span className="concern-card__label">{option.label}</span>
            <span className="concern-card__checkbox">
              <input
                type="checkbox"
                className="concern-card__checkbox-input"
                checked={isSelected}
                onChange={() => onToggle(option.id)}
                aria-label={option.label}
              />
              {isSelected ? (
                <span className="concern-card__checkbox-icon" aria-hidden="true">
                  <Check size={14} />
                </span>
              ) : null}
            </span>
          </motion.label>
        );
      })}
    </motion.div>
  );
}

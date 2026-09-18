import { Check } from 'lucide-react';
import { motion } from 'framer-motion';
import type { JSX, ReactNode } from 'react';
import '@/components/MultiSelectCardGrid.css';
import { LIST_ITEM_VARIANTS, LIST_STAGGER_VARIANTS } from '@/lib/pageMotion';

export interface MultiSelectOption<Id extends string> {
  id: Id;
  label: string;
  icon: ReactNode;
}

interface MultiSelectCardGridProps<Id extends string> {
  options: readonly MultiSelectOption<Id>[];
  selectedIds: readonly Id[];
  onToggle: (id: Id) => void;
}

/**
 * Shared "pick one or more of these" grid — an icon, a label, and a checkbox
 * per card. Used for patient concerns, ongoing conditions, and any future
 * multi-select question; don't re-draw this card shape inside a feature.
 */
export function MultiSelectCardGrid<Id extends string>({
  options,
  selectedIds,
  onToggle,
}: MultiSelectCardGridProps<Id>): JSX.Element {
  return (
    <motion.div
      className="multi-select-card-grid"
      variants={LIST_STAGGER_VARIANTS}
      initial="hidden"
      animate="visible"
    >
      {options.map((option) => {
        const isSelected = selectedIds.includes(option.id);

        return (
          <motion.label
            key={option.id}
            variants={LIST_ITEM_VARIANTS}
            className={`multi-select-card${isSelected ? ' multi-select-card--selected' : ''}`}
          >
            <span className="multi-select-card__icon" aria-hidden="true">
              {option.icon}
            </span>
            <span className="multi-select-card__label">{option.label}</span>
            <span className="multi-select-card__checkbox">
              <input
                type="checkbox"
                className="multi-select-card__checkbox-input"
                checked={isSelected}
                onChange={() => onToggle(option.id)}
                aria-label={option.label}
              />
              {isSelected ? (
                <span className="multi-select-card__checkbox-icon" aria-hidden="true">
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

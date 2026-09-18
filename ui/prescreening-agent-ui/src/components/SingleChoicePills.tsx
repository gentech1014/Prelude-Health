import type { JSX } from 'react';
import '@/components/SingleChoicePills.css';

interface SingleChoicePillsProps {
  label: string;
  options: readonly string[];
  value: string | null;
  onChange: (value: string) => void;
}

/** Shared single-choice pill row — e.g. tobacco/alcohol use frequency. One label, N exclusive options. */
export function SingleChoicePills({
  label,
  options,
  value,
  onChange,
}: SingleChoicePillsProps): JSX.Element {
  return (
    <div className="single-choice-pills">
      <p className="single-choice-pills__label">{label}</p>
      <div className="single-choice-pills__options" role="group" aria-label={label}>
        {options.map((option) => (
          <button
            key={option}
            type="button"
            className={`single-choice-pills__option${value === option ? ' single-choice-pills__option--selected' : ''}`}
            aria-pressed={value === option}
            onClick={() => onChange(option)}
          >
            {option}
          </button>
        ))}
      </div>
    </div>
  );
}

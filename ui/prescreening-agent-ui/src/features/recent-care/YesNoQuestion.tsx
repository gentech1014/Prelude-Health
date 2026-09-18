import type { JSX, ReactNode } from 'react';
import '@/features/recent-care/YesNoQuestion.css';

interface YesNoQuestionProps {
  icon?: ReactNode;
  label: string;
  subtitle?: string;
  value: boolean | null;
  onChange: (value: boolean) => void;
  /** Shown below the options once answered "yes". */
  children?: ReactNode;
}

/** A yes/no question that reveals its follow-up content only once answered "yes". */
export function YesNoQuestion({
  icon,
  label,
  subtitle,
  value,
  onChange,
  children,
}: YesNoQuestionProps): JSX.Element {
  return (
    <div className="yes-no-question">
      <div className="yes-no-question__header">
        {icon && <div className="yes-no-question__icon">{icon}</div>}
        <div className="yes-no-question__text">
          <p className="yes-no-question__label">{label}</p>
          {subtitle && <p className="yes-no-question__subtitle">{subtitle}</p>}
        </div>
      </div>

      <div className="yes-no-question__options" role="group" aria-label={label}>
        <button
          type="button"
          className={`yes-no-question__option yes-no-question__option--yes${value === true ? ' yes-no-question__option--selected' : ''}`}
          aria-pressed={value === true}
          onClick={() => onChange(true)}
        >
          Yes
        </button>
        <button
          type="button"
          className={`yes-no-question__option yes-no-question__option--no${value === false ? ' yes-no-question__option--selected' : ''}`}
          aria-pressed={value === false}
          onClick={() => onChange(false)}
        >
          No
        </button>
      </div>

      {value === true ? children : null}
    </div>
  );
}

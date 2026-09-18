import { MessageSquare } from 'lucide-react';
import { useId, type JSX } from 'react';
import { AnswerInput } from '@/components/AnswerInput';
import '@/components/OptionalDetailsField.css';

const DEFAULT_MAX_LENGTH = 300;

interface OptionalDetailsFieldProps {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  /** Screen-reader label — not shown visually, since the card already sits right under its question. */
  label: string;
  helperText: string;
  maxLength?: number;
}

/**
 * Shared optional free-text elaboration card — one textarea, a helper line,
 * and a character counter/Send button. Used after concern/condition/history
 * questions; don't rebuild this shape inside a feature.
 */
export function OptionalDetailsField({
  value,
  onChange,
  onSubmit,
  label,
  helperText,
  maxLength = DEFAULT_MAX_LENGTH,
}: OptionalDetailsFieldProps): JSX.Element {
  const textareaId = useId();

  return (
    <div className="optional-details-field">
      <div className="optional-details-field__field">
        <span className="optional-details-field__icon" aria-hidden="true">
          <MessageSquare size={16} />
        </span>
        <div className="optional-details-field__control">
          <label
            htmlFor={textareaId}
            style={{
              position: 'absolute',
              width: 1,
              height: 1,
              overflow: 'hidden',
              clip: 'rect(0 0 0 0)',
              whiteSpace: 'nowrap',
            }}
          >
            {label}
          </label>
          <AnswerInput
            id={textareaId}
            value={value}
            onChange={onChange}
            onSubmit={onSubmit}
            maxLength={maxLength}
            placeholder="Tell me more (optional)"
            multiline
            rows={2}
          />
        </div>
      </div>
      <p className="optional-details-field__helper">{helperText}</p>
    </div>
  );
}

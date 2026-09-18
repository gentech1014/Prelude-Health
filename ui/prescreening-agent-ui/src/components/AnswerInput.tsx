import { CornerDownLeft } from 'lucide-react';
import type { ChangeEvent, JSX, KeyboardEvent } from 'react';
import '@/components/AnswerInput.css';

interface AnswerInputProps {
  id?: string;
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  placeholder?: string;
  /** Shown as a bottom-left counter. Omitted entirely when there is no limit. */
  maxLength?: number;
  /** Renders a `<textarea>` instead of a single-line `<input>`. */
  multiline?: boolean;
  rows?: number;
  autoComplete?: string;
  /** Screen-reader label, for a field with no visible `<label>`. */
  ariaLabel?: string;
  className?: string;
}

/**
 * One free-text answer field: the control, a character count bottom-left,
 * and a Send button bottom-right.
 *
 * Nothing here reports a value as the patient's answer until Send is
 * pressed, or Enter (Shift+Enter is left free for a newline when
 * `multiline`). Typing alone never submits — this replaces a debounced
 * auto-send that used to treat a pause for thought as a finished answer.
 */
export function AnswerInput({
  id,
  value,
  onChange,
  onSubmit,
  placeholder,
  maxLength,
  multiline = false,
  rows = 2,
  autoComplete,
  ariaLabel,
  className,
}: AnswerInputProps): JSX.Element {
  const canSubmit = value.trim().length > 0;

  const submit = (): void => {
    if (canSubmit) onSubmit();
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLInputElement | HTMLTextAreaElement>): void => {
    if (event.key !== 'Enter') return;
    if (multiline && event.shiftKey) return; // shift+enter inserts a newline instead
    event.preventDefault();
    submit();
  };

  const handleChange = (event: ChangeEvent<HTMLInputElement | HTMLTextAreaElement>): void => {
    onChange(event.target.value);
  };

  return (
    <div className={className ? `answer-input ${className}` : 'answer-input'}>
      <div className="answer-input__field-wrap">
        {multiline ? (
          <textarea
            id={id}
            className="answer-input__control"
            value={value}
            maxLength={maxLength}
            placeholder={placeholder}
            aria-label={ariaLabel}
            onChange={handleChange}
            onKeyDown={handleKeyDown}
            rows={rows}
          />
        ) : (
          <input
            id={id}
            type="text"
            className="answer-input__control"
            value={value}
            maxLength={maxLength}
            placeholder={placeholder}
            aria-label={ariaLabel}
            autoComplete={autoComplete}
            onChange={handleChange}
            onKeyDown={handleKeyDown}
          />
        )}
        {maxLength !== undefined && (
          <span className="answer-input__count">
            {value.length}/{maxLength}
          </span>
        )}
        <button
          type="button"
          className="answer-input__send"
          onClick={submit}
          disabled={!canSubmit}
          aria-label="Send this answer"
        >
          <CornerDownLeft size={15} aria-hidden="true" />
        </button>
      </div>
    </div>
  );
}

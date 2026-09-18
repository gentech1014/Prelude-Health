import { Calendar, CornerDownLeft, Phone, User } from 'lucide-react';
import { useId, type JSX, type KeyboardEvent } from 'react';
import type { PrefilledText } from '@/features/prescreening-session/usePrefill';
import { usePrefilledText } from '@/features/prescreening-session/usePrefill';
import { usePrescreeningSession } from '@/features/prescreening-session/usePrescreeningSession';
import '@/features/confirm-details/ConfirmDetailsForm.css';
import '@/components/AnswerInput.css';

const DATE_OF_BIRTH_FORMATTER = new Intl.DateTimeFormat('en-US', {
  month: '2-digit',
  day: '2-digit',
  year: 'numeric',
});

/** Enter submits, same as `AnswerInput` — these rows have no newline to protect. */
function handleEnterSubmits(
  event: KeyboardEvent<HTMLInputElement>,
  field: Pick<PrefilledText, 'value' | 'onSubmit'>,
): void {
  if (event.key !== 'Enter') return;
  event.preventDefault();
  if (field.value.trim().length > 0) field.onSubmit();
}

/**
 * Identity-confirmation fields, seeded from the session's booking record.
 *
 * Name and date of birth stay the booked values: they are what the patient
 * is being asked to check, and the server will not let the assistant write
 * over them. The phone number is seeded from the booking too — it is where
 * the intake link was sent — but unlike the other two it is the one field
 * the conversation is allowed to correct.
 *
 * Every field is editable, and every edit is sent back as the patient's
 * own turn in the conversation — so a correction typed here reaches the
 * transcript the physician's report is built from, exactly as a spoken one
 * would. Nothing is sent until the patient presses Send (or Enter): a
 * debounced auto-send used to treat a pause mid-correction as finished.
 */
export function ConfirmDetailsForm(): JSX.Element {
  const fullNameId = useId();
  const dobId = useId();
  const phoneInputId = useId();
  const { session } = usePrescreeningSession();

  const bookedName = session?.patient.name ?? '';
  const bookedDateOfBirth = session
    ? DATE_OF_BIRTH_FORMATTER.format(session.patient.dateOfBirth)
    : '';

  const bookedPhone = session?.patient.contactPhone ?? '';

  const fullName = usePrefilledText('confirm-details', 'full_name', bookedName);
  const dateOfBirth = usePrefilledText('confirm-details', 'date_of_birth', bookedDateOfBirth);
  const phoneNumber = usePrefilledText('confirm-details', 'phone_number', bookedPhone);

  return (
    <div className="confirm-details-form">
      {/* Full Name */}
      <div className="confirm-details-row">
        <div className="confirm-details-icon">
          <User size={22} strokeWidth={1.5} />
        </div>
        <div className="confirm-details-content">
          <label className="confirm-details-label" htmlFor={fullNameId}>
            Full name
          </label>
          <div className="confirm-details-input-wrap">
            <input
              id={fullNameId}
              type="text"
              className="confirm-details-input"
              value={fullName.value}
              onChange={(event) => fullName.onChange(event.target.value)}
              onKeyDown={(event) => handleEnterSubmits(event, fullName)}
              placeholder="Full name"
              autoComplete="name"
            />
            <button
              className="answer-input__send"
              type="button"
              onClick={fullName.onSubmit}
              disabled={fullName.value.trim().length === 0}
              aria-label="Send corrected full name"
            >
              <CornerDownLeft size={15} aria-hidden="true" />
            </button>
          </div>
        </div>
      </div>

      {/* Date of Birth */}
      <div className="confirm-details-row">
        <div className="confirm-details-icon">
          <Calendar size={22} strokeWidth={1.5} />
        </div>
        <div className="confirm-details-content">
          <label className="confirm-details-label" htmlFor={dobId}>
            Date of birth
          </label>
          <div className="confirm-details-input-wrap">
            <input
              id={dobId}
              type="text"
              inputMode="numeric"
              className="confirm-details-input"
              value={dateOfBirth.value}
              onChange={(event) => dateOfBirth.onChange(event.target.value)}
              onKeyDown={(event) => handleEnterSubmits(event, dateOfBirth)}
              placeholder="MM/DD/YYYY"
              autoComplete="bday"
            />
            <button
              className="answer-input__send"
              type="button"
              onClick={dateOfBirth.onSubmit}
              disabled={dateOfBirth.value.trim().length === 0}
              aria-label="Send corrected date of birth"
            >
              <CornerDownLeft size={15} aria-hidden="true" />
            </button>
          </div>
        </div>
      </div>

      {/* Phone Number */}
      <div className="confirm-details-row">
        <div className="confirm-details-icon">
          <Phone size={22} strokeWidth={1.5} />
        </div>
        <div className="confirm-details-content">
          <label className="confirm-details-label" htmlFor={phoneInputId}>
            Phone number
          </label>
          <div className="confirm-details-input-wrap">
            <input
              id={phoneInputId}
              type="tel"
              className="confirm-details-input"
              value={phoneNumber.value}
              onChange={(event) => phoneNumber.onChange(event.target.value)}
              onKeyDown={(event) => handleEnterSubmits(event, phoneNumber)}
              placeholder="Include your country code"
              autoComplete="tel"
            />
            <button
              className="answer-input__send"
              type="button"
              onClick={phoneNumber.onSubmit}
              disabled={phoneNumber.value.trim().length === 0}
              aria-label="Send corrected phone number"
            >
              <CornerDownLeft size={15} aria-hidden="true" />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

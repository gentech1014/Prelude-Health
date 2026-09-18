import { CalendarClock, CalendarPlus, CalendarSearch, Check, X } from 'lucide-react';
import { useEffect, useRef, useState, type FormEvent, type JSX } from 'react';
import { InlineBanner } from '@/components/states';
import { modalityLabel } from '@/features/booking/visitTypePresentation';
import { ApiError, type ApiErrorKind } from '@/services/apiClient';
import {
  startDoctorRegistration,
  type CareModality,
  type VisitCategory,
  type VisitType,
} from '@/services/bookingService';

const MODALITIES: readonly CareModality[] = ['in_person_and_virtual', 'in_person', 'virtual'];

/** What the doctor is being asked to grant, in plain language. */
const GRANTED_SCOPES = [
  {
    icon: <CalendarSearch size={15} />,
    text: 'Read your calendar to calculate your real open slots',
  },
  { icon: <CalendarPlus size={15} />, text: 'Create appointments booked through this service' },
  {
    icon: <CalendarClock size={15} />,
    text: 'Attach pre-screening intake summaries to confirmed events',
  },
] as const;

const ERROR_MESSAGE: Partial<Record<ApiErrorKind, string>> = {
  unavailable:
    'Google sign-in is not configured on this deployment yet. Contact your administrator.',
};

function GoogleIcon(): JSX.Element {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true" style={{ flexShrink: 0 }}>
      <path
        fill="#4285F4"
        d="M23.745 12.27c0-.7-.06-1.4-.19-2.07H12v4.51h6.6c-.29 1.52-1.14 2.82-2.4 3.68v3.05h3.88c2.27-2.09 3.66-5.17 3.66-9.17z"
      />
      <path
        fill="#34A853"
        d="M12 24c3.24 0 5.95-1.08 7.93-2.91l-3.88-3.05c-1.08.72-2.45 1.16-4.05 1.16-3.12 0-5.77-2.1-6.72-4.93H1.25v3.15C3.26 21.36 7.33 24 12 24z"
      />
      <path
        fill="#FBBC05"
        d="M5.28 14.27c-.25-.72-.38-1.49-.38-2.27s.13-1.55.38-2.27V6.58H1.25C.45 8.18 0 9.99 0 12s.45 3.82 1.25 5.42l4.03-3.15z"
      />
      <path
        fill="#EA4335"
        d="M12 4.75c1.77 0 3.35.61 4.6 1.8l3.42-3.42C17.95 1.19 15.24 0 12 0 7.33 0 3.26 2.64 1.25 6.58l4.03 3.15c.95-2.83 3.6-4.98 6.72-4.98z"
      />
    </svg>
  );
}

interface DoctorRegistrationDialogProps {
  visitTypes: readonly VisitType[];
  onClose: () => void;
}

/**
 * Collects a doctor's profile, then hands the browser to Google's consent
 * screen. Registration completes on the API's OAuth callback, which
 * redirects back to this page — so this dialog deliberately never sees a
 * token, an authorization code, or the doctor's password.
 */
export function DoctorRegistrationDialog({
  visitTypes,
  onClose,
}: DoctorRegistrationDialogProps): JSX.Element {
  const [name, setName] = useState('');
  const [credential, setCredential] = useState('');
  const [categories, setCategories] = useState<VisitCategory[]>([]);
  const [modality, setModality] = useState<CareModality>('in_person_and_virtual');
  const [isRedirecting, setIsRedirecting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const dialogRef = useRef<HTMLDivElement>(null);
  const nameRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    nameRef.current?.focus();

    function handleKeyDown(event: KeyboardEvent): void {
      if (event.key === 'Escape') onClose();
    }

    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  // A doctor declares clinical specialties; the unscoped booking options
  // (general checkup, not sure) map to no category and are never a specialty.
  const clinicalVisitTypes = visitTypes.flatMap((visitType) =>
    visitType.symptomCategory === null
      ? []
      : [{ ...visitType, symptomCategory: visitType.symptomCategory }],
  );

  function toggleCategory(category: VisitCategory): void {
    setCategories((current) =>
      current.includes(category)
        ? current.filter((entry) => entry !== category)
        : [...current, category],
    );
  }

  async function startConsent(): Promise<void> {
    if (name.trim().length < 2) {
      setError('Enter the name patients will see when booking.');
      return;
    }

    setIsRedirecting(true);
    setError(null);
    try {
      const authorizationUrl = await startDoctorRegistration({
        name: name.trim(),
        credential: credential.trim() === '' ? null : credential.trim(),
        categories,
        modality,
      });
      // A full navigation, not a popup: Google's consent screen refuses to
      // render inside an iframe, and a popup would be blocked as often as not.
      window.location.assign(authorizationUrl);
    } catch (cause) {
      const apiError = cause instanceof ApiError ? cause : null;
      setError(
        (apiError && ERROR_MESSAGE[apiError.kind]) ??
          apiError?.message ??
          'Registration could not be started. Try again shortly.',
      );
      setIsRedirecting(false);
    }
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    void startConsent();
  }

  return (
    <div
      className="booking-dialog-backdrop"
      onMouseDown={(event) => {
        if (!dialogRef.current?.contains(event.target as Node)) onClose();
      }}
    >
      <div
        className="booking-dialog"
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="doctor-registration-title"
      >
        <div className="booking-dialog__head">
          <div>
            <h2 className="booking-dialog__title" id="doctor-registration-title">
              Register as doctor
            </h2>
            <p className="booking-dialog__hint">
              Connect your Google account so patients can book against your real availability.
            </p>
          </div>
          <button
            type="button"
            className="booking-dialog__close"
            onClick={onClose}
            aria-label="Close"
          >
            <X size={18} aria-hidden="true" />
          </button>
        </div>

        <form className="booking-dialog__form" onSubmit={handleSubmit} noValidate>
          <div className="booking-field-grid booking-dialog__fields">
            <div className="booking-field">
              <label className="booking-field__label" htmlFor="doctor-name">
                Name shown to patients
              </label>
              <input
                id="doctor-name"
                ref={nameRef}
                className="booking-field__control"
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="Dr. Priya Raman"
              />
            </div>
            <div className="booking-field">
              <label className="booking-field__label" htmlFor="doctor-credential">
                Credential (optional)
              </label>
              <input
                id="doctor-credential"
                className="booking-field__control"
                value={credential}
                onChange={(event) => setCredential(event.target.value)}
                maxLength={24}
                placeholder="MD"
              />
            </div>

            <div className="booking-field booking-field--full">
              <label className="booking-field__label" htmlFor="doctor-modality">
                How you see patients
              </label>
              <select
                id="doctor-modality"
                className="booking-field__control"
                value={modality}
                onChange={(event) => setModality(event.target.value as CareModality)}
              >
                {MODALITIES.map((entry) => (
                  <option key={entry} value={entry}>
                    {modalityLabel(entry)}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div className="booking-dialog__section">
            <div className="booking-dialog__section-header">
              <span className="booking-field__label">Visit types you accept</span>
              <span className="booking-dialog__hint-inline">
                Leave all unselected to accept every visit type.
              </span>
            </div>
            <div className="booking-checkbox-row">
              {clinicalVisitTypes.map((visitType) => {
                const category = visitType.symptomCategory;
                const isChecked = categories.includes(category);

                return (
                  <label
                    key={category}
                    className={`booking-checkbox${isChecked ? ' booking-checkbox--checked' : ''}`}
                  >
                    <input
                      type="checkbox"
                      checked={isChecked}
                      onChange={() => toggleCategory(category)}
                    />
                    {isChecked ? <Check size={14} aria-hidden="true" /> : null}
                    {visitType.label}
                  </label>
                );
              })}
            </div>
          </div>

          <div className="booking-dialog__scopes">
            <ul className="booking-scope-list">
              {GRANTED_SCOPES.map((scope) => (
                <li className="booking-scope-list__item" key={scope.text}>
                  <span className="booking-scope-list__icon" aria-hidden="true">
                    {scope.icon}
                  </span>
                  <span>{scope.text}</span>
                </li>
              ))}
            </ul>
          </div>

          {error ? <InlineBanner icon={<X size={16} />} message={error} tone="error" /> : null}

          <div className="booking-dialog__actions">
            <button
              type="button"
              className="booking-dialog__btn-cancel"
              onClick={onClose}
              disabled={isRedirecting}
            >
              Cancel
            </button>
            <button type="submit" className="booking-dialog__btn-submit" disabled={isRedirecting}>
              <GoogleIcon />
              {isRedirecting ? 'Opening Google...' : 'Continue with Google'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

import { ArrowLeft, CalendarCheck } from 'lucide-react';
import { useState, type FormEvent, type JSX } from 'react';
import type { PatientSex } from '@/services/bookingService';

/** The patient identity a booking needs, as the form holds it. */
export interface PatientBookingDetails {
  patientName: string;
  patientId: string;
  dateOfBirth: string;
  sex: PatientSex;
  contactPhone: string;
  contactEmail: string;
  bookingReason: string;
}

const EMPTY_DETAILS: PatientBookingDetails = {
  patientName: '',
  patientId: '',
  dateOfBirth: '',
  sex: 'female',
  contactPhone: '',
  contactEmail: '',
  bookingReason: '',
};

type FieldErrors = Partial<Record<keyof PatientBookingDetails, string>>;

interface ConfirmBookingFormProps {
  visitTypeLabel: string;
  providerName: string;
  appointmentLabel: string;
  isSubmitting: boolean;
  onBack: () => void;
  onSubmit: (details: PatientBookingDetails) => void;
}

/**
 * Final booking step: the appointment being booked, then the patient
 * identity the pre-screening session is created against.
 *
 * Validation here is a UX control only — the API validates independently,
 * and a server-side rejection is surfaced by the page rather than swallowed
 * here. Nothing typed on this form is persisted client-side; it is held in
 * component state for the length of the submit and no longer.
 */
export function ConfirmBookingForm({
  visitTypeLabel,
  providerName,
  appointmentLabel,
  isSubmitting,
  onBack,
  onSubmit,
}: ConfirmBookingFormProps): JSX.Element {
  const [details, setDetails] = useState<PatientBookingDetails>(EMPTY_DETAILS);
  const [errors, setErrors] = useState<FieldErrors>({});

  function update<K extends keyof PatientBookingDetails>(
    field: K,
    value: PatientBookingDetails[K],
  ): void {
    setDetails((current) => ({ ...current, [field]: value }));
    setErrors((current) => ({ ...current, [field]: undefined }));
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();

    const nextErrors = validate(details);
    if (Object.keys(nextErrors).length > 0) {
      setErrors(nextErrors);
      return;
    }
    onSubmit(details);
  }

  return (
    <form className="booking-section" onSubmit={handleSubmit} noValidate>
      <div className="booking-summary">
        <div className="booking-summary__row">
          <span className="booking-summary__label">Appointment type</span>
          <span className="booking-summary__value">{visitTypeLabel}</span>
        </div>
        <div className="booking-summary__row">
          <span className="booking-summary__label">Provider</span>
          <span className="booking-summary__value">{providerName}</span>
        </div>
        <div className="booking-summary__row">
          <span className="booking-summary__label">Date and time</span>
          <span className="booking-summary__value">{appointmentLabel}</span>
        </div>
      </div>

      <h2 className="booking-section__heading">
        <span className="booking-section__index">3.</span> Confirm patient details
      </h2>
      <p className="booking-section__hint">
        These details identify the patient on the appointment and on the pre-screening call.
      </p>

      <div className="booking-field-grid">
        <TextField
          id="patient-name"
          label="Patient full name"
          value={details.patientName}
          error={errors.patientName}
          onChange={(value) => update('patientName', value)}
          autoComplete="name"
        />
        <TextField
          id="patient-id"
          label="Patient ID (optional)"
          value={details.patientId}
          error={errors.patientId}
          onChange={(value) => update('patientId', value)}
          hint="Leave blank if you do not know it."
        />
        <TextField
          id="patient-dob"
          label="Date of birth"
          type="date"
          value={details.dateOfBirth}
          error={errors.dateOfBirth}
          onChange={(value) => update('dateOfBirth', value)}
        />

        <div className="booking-field">
          <label className="booking-field__label" htmlFor="patient-sex">
            Sex
          </label>
          <select
            id="patient-sex"
            className="booking-field__control"
            value={details.sex}
            onChange={(event) => update('sex', event.target.value as PatientSex)}
          >
            <option value="female">Female</option>
            <option value="male">Male</option>
            <option value="other">Other</option>
          </select>
        </div>

        <TextField
          id="patient-phone"
          label="Mobile number"
          type="tel"
          value={details.contactPhone}
          error={errors.contactPhone}
          onChange={(value) => update('contactPhone', value)}
          autoComplete="tel"
          hint="Where the pre-screening call link is sent."
        />
        <TextField
          id="patient-email"
          label="Email (optional)"
          type="email"
          value={details.contactEmail}
          error={errors.contactEmail}
          onChange={(value) => update('contactEmail', value)}
          autoComplete="email"
        />

        <div className="booking-field booking-field--full">
          <label className="booking-field__label" htmlFor="booking-reason">
            Reason for the visit (optional)
          </label>
          <textarea
            id="booking-reason"
            className="booking-field__control"
            rows={3}
            maxLength={500}
            value={details.bookingReason}
            onChange={(event) => update('bookingReason', event.target.value)}
          />
        </div>
      </div>

      <div className="booking-actions">
        <button
          type="button"
          className="booking-secondary-button"
          onClick={onBack}
          disabled={isSubmitting}
        >
          <ArrowLeft size={16} aria-hidden="true" />
          Back
        </button>
        <button type="submit" className="booking-primary-button" disabled={isSubmitting}>
          <CalendarCheck size={18} aria-hidden="true" />
          {isSubmitting ? 'Booking appointment' : 'Book appointment'}
        </button>
      </div>
    </form>
  );
}

interface TextFieldProps {
  id: string;
  label: string;
  value: string;
  error?: string | undefined;
  onChange: (value: string) => void;
  type?: 'text' | 'date' | 'tel' | 'email';
  autoComplete?: string;
  hint?: string;
}

function TextField({
  id,
  label,
  value,
  error,
  onChange,
  type = 'text',
  autoComplete,
  hint,
}: TextFieldProps): JSX.Element {
  const describedBy = [error ? `${id}-error` : null, hint ? `${id}-hint` : null]
    .filter((entry) => entry !== null)
    .join(' ');

  return (
    <div className="booking-field">
      <label className="booking-field__label" htmlFor={id}>
        {label}
      </label>
      <input
        id={id}
        type={type}
        className="booking-field__control"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        autoComplete={autoComplete}
        aria-invalid={error !== undefined}
        aria-describedby={describedBy === '' ? undefined : describedBy}
      />
      {hint ? (
        <span id={`${id}-hint`} className="booking-section__hint">
          {hint}
        </span>
      ) : null}
      {error ? (
        <span id={`${id}-error`} className="booking-field__error">
          {error}
        </span>
      ) : null}
    </div>
  );
}

/** Field-level checks with actionable messages. Mirrors, never replaces, the API's own validation. */
function validate(details: PatientBookingDetails): FieldErrors {
  const errors: FieldErrors = {};

  if (details.patientName.trim().length < 2) {
    errors.patientName = 'Enter the patient full name.';
  }
  if (details.dateOfBirth === '') {
    errors.dateOfBirth = 'Enter the date of birth.';
  } else if (new Date(details.dateOfBirth) > new Date()) {
    errors.dateOfBirth = 'Date of birth cannot be in the future.';
  }
  if (details.contactPhone.trim().length < 6) {
    errors.contactPhone = 'Enter a mobile number that can receive the call link.';
  }
  if (details.contactEmail !== '' && !details.contactEmail.includes('@')) {
    errors.contactEmail = 'Enter a valid email address, or leave it blank.';
  }

  return errors;
}

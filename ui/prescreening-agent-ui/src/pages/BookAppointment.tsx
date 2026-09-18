import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertCircle,
  ArrowRight,
  CalendarCheck,
  CalendarClock,
  Check,
  CircleUserRound,
  ClipboardCopy,
  Info,
  ShieldCheck,
  Users,
} from 'lucide-react';
import { useEffect, useMemo, useState, type JSX } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { EmptyState, InlineBanner, LoadingState } from '@/components/states';
import { AppointmentDateTimePicker } from '@/features/booking/AppointmentDateTimePicker';
import { BookingStepRail, type BookingStepId } from '@/features/booking/BookingStepRail';
import { BookingTopBar } from '@/features/booking/BookingTopBar';
import { IntakeMessagePreview } from '@/features/booking/IntakeMessagePreview';
import { formatAppointmentLabel } from '@/features/booking/bookingTime';
import {
  ConfirmBookingForm,
  type PatientBookingDetails,
} from '@/features/booking/ConfirmBookingForm';
import { DoctorRegistrationDialog } from '@/features/booking/DoctorRegistrationDialog';
import { ProviderCards } from '@/features/booking/ProviderCards';
import { VisitTypeCards } from '@/features/booking/VisitTypeCards';
import '@/features/booking/booking.css';
import { useToast } from '@/hooks/useToast';
import { ApiError } from '@/services/apiClient';
import {
  bookAppointment,
  fetchProviderAvailability,
  fetchProviders,
  fetchVisitTypes,
  type BookingConfirmation,
  type BookingVisitType,
} from '@/services/bookingService';

const TRUST_MARKERS = [
  { icon: <ShieldCheck size={18} aria-hidden="true" />, label: 'Secure and private' },
  { icon: <Users size={18} aria-hidden="true" />, label: 'Verified providers' },
  { icon: <CalendarClock size={18} aria-hidden="true" />, label: 'Real availability' },
] as const;

/**
 * Appointment booking — a standalone surface, not part of the mobile-only
 * pre-screening call. Everything on it is live: the visit types are the
 * service's own symptom categories, the providers are the doctors on file,
 * and the slots are computed against real calendars.
 *
 * Booking here is what creates the pre-screening session, so a confirmed
 * booking hands back the patient intake link rather than ending the flow.
 */
export function BookAppointment(): JSX.Element {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { showToast } = useToast();
  const queryClient = useQueryClient();

  const [visitType, setVisitType] = useState<BookingVisitType | null>(null);
  const [doctorId, setDoctorId] = useState<string | null>(null);
  const [selectedDay, setSelectedDay] = useState<string | null>(null);
  const [slotStart, setSlotStart] = useState<string | null>(null);
  const [isConfirming, setIsConfirming] = useState(false);
  const [isRegistrationOpen, setIsRegistrationOpen] = useState(false);
  const [confirmation, setConfirmation] = useState<BookingConfirmation | null>(null);

  const visitTypesQuery = useQuery({ queryKey: ['visit-types'], queryFn: fetchVisitTypes });

  const providersQuery = useQuery({
    queryKey: ['providers', visitType],
    queryFn: () => fetchProviders(visitType as BookingVisitType),
    enabled: visitType !== null,
  });

  const availabilityQuery = useQuery({
    queryKey: ['availability', doctorId],
    queryFn: () => fetchProviderAvailability(doctorId as string),
    enabled: doctorId !== null,
    // Availability goes stale the moment anyone else books, so it is not
    // cached as long as the app's other reads.
    staleTime: 15_000,
  });

  const booking = useMutation({
    mutationFn: bookAppointment,
    onSuccess: (result) => {
      setConfirmation(result);
      showToast({ message: 'Appointment booked.', tone: 'neutral' });
    },
    onError: (cause: unknown) => {
      const apiError = cause instanceof ApiError ? cause : null;
      if (apiError?.kind === 'conflict') {
        // Someone took the slot while this page was open: the only safe
        // recovery is fresh availability, so send the patient back to pick again.
        setSlotStart(null);
        setIsConfirming(false);
        void queryClient.invalidateQueries({ queryKey: ['availability', doctorId] });
        showToast({ message: 'That time was just taken. Choose another slot.', tone: 'warning' });
        return;
      }
      showToast({
        message: apiError?.message ?? 'The appointment could not be booked. Try again shortly.',
        tone: 'error',
      });
    },
  });

  // The OAuth callback redirects back here with its outcome. Report it once,
  // then strip it from the URL so a refresh does not repeat the message.
  const registrationOutcome = searchParams.get('doctor_registration');
  useEffect(() => {
    if (registrationOutcome === null) return;

    const doctorName = searchParams.get('doctor');
    if (registrationOutcome === 'connected') {
      showToast({
        message: doctorName
          ? `${doctorName} is now bookable with their own calendar.`
          : 'Your Google calendar is connected.',
        tone: 'success',
      });
      void queryClient.invalidateQueries({ queryKey: ['providers'] });
    } else if (registrationOutcome === 'declined') {
      showToast({
        message: 'Google access was not granted, so nothing was saved.',
        tone: 'neutral',
      });
    } else {
      showToast({ message: 'Registration did not complete. Start again.', tone: 'error' });
    }

    setSearchParams(
      (params) => {
        params.delete('doctor_registration');
        params.delete('doctor');
        return params;
      },
      { replace: true },
    );
  }, [registrationOutcome, searchParams, setSearchParams, showToast, queryClient]);

  const visitTypes = visitTypesQuery.data ?? [];
  const providers = providersQuery.data ?? [];
  const availability = availabilityQuery.data ?? null;

  const selectedVisitType = visitTypes.find((entry) => entry.visitType === visitType) ?? null;
  const selectedProvider = providers.find((entry) => entry.doctorId === doctorId) ?? null;

  const canContinue = visitType !== null && doctorId !== null && slotStart !== null;
  const activeStep: BookingStepId | 'done' =
    confirmation !== null
      ? 'done'
      : isConfirming
        ? 'confirm'
        : visitType !== null && doctorId !== null
          ? 'date-time'
          : 'visit-details';

  const clinicTimezone = availability?.timezone ?? null;
  const appointmentLabel = useMemo(
    () => (slotStart === null ? '' : formatAppointmentLabel(slotStart, clinicTimezone)),
    [slotStart, clinicTimezone],
  );

  function selectVisitType(next: BookingVisitType): void {
    setVisitType(next);
    // A provider chosen for the previous visit type may not accept this one,
    // so the downstream selections are cleared rather than left dangling.
    setDoctorId(null);
    setSelectedDay(null);
    setSlotStart(null);
  }

  function selectProvider(next: string): void {
    setDoctorId(next);
    setSelectedDay(null);
    setSlotStart(null);
  }

  function submitBooking(details: PatientBookingDetails): void {
    if (visitType === null || doctorId === null || slotStart === null) return;

    booking.mutate({
      doctorId,
      visitType,
      slotStart,
      patientName: details.patientName.trim(),
      patientId: details.patientId.trim() === '' ? null : details.patientId.trim(),
      dateOfBirth: details.dateOfBirth,
      sex: details.sex,
      contactPhone: details.contactPhone.trim() === '' ? null : details.contactPhone.trim(),
      contactEmail: details.contactEmail.trim() === '' ? null : details.contactEmail.trim(),
      bookingReason: details.bookingReason.trim() === '' ? null : details.bookingReason.trim(),
    });
  }

  function startOver(): void {
    setConfirmation(null);
    setIsConfirming(false);
    setVisitType(null);
    setDoctorId(null);
    setSelectedDay(null);
    setSlotStart(null);
  }

  return (
    <div className="booking-shell">
      <BookingTopBar
        onRegisterAsDoctor={() => setIsRegistrationOpen(true)}
        onViewReports={() => navigate('/reports')}
      />

      <div className="booking-body">
        <aside className="booking-rail">
          <div>
            <h1 className="booking-rail__title">Book an appointment</h1>
            <p className="booking-rail__subtitle">
              Choose the care you need and a time that works. A short pre-screening call is prepared
              for you afterwards.
            </p>
          </div>

          <BookingStepRail activeStep={activeStep} />

          <div className="booking-rail__trust">
            {TRUST_MARKERS.map((marker) => (
              <div className="booking-trust-marker" key={marker.label}>
                <span className="booking-trust-marker__icon">{marker.icon}</span>
                {marker.label}
              </div>
            ))}
          </div>
        </aside>

        <main className="booking-panel">
          <div className="booking-panel__mobile-head">
            <div>
              <h1 className="booking-panel__mobile-title">Book an appointment</h1>
              <p className="booking-panel__mobile-subtitle">
                Choose the care you need, at a time that works.
              </p>
            </div>
            <BookingStepRail activeStep={activeStep} orientation="horizontal" />
          </div>

          {confirmation !== null ? (
            <BookingConfirmed
              confirmation={confirmation}
              timezone={clinicTimezone}
              onStartOver={startOver}
            />
          ) : isConfirming && selectedVisitType && selectedProvider ? (
            <ConfirmBookingForm
              visitTypeLabel={selectedVisitType.label}
              providerName={selectedProvider.name}
              appointmentLabel={appointmentLabel}
              isSubmitting={booking.isPending}
              onBack={() => setIsConfirming(false)}
              onSubmit={submitBooking}
            />
          ) : (
            <>
              <section className="booking-section" aria-labelledby="visit-type-heading">
                <div>
                  <h2 className="booking-section__heading" id="visit-type-heading">
                    <span className="booking-section__index">1.</span> What type of appointment do
                    you need?
                  </h2>
                  <p className="booking-section__hint">
                    Select a service to see available providers.
                  </p>
                </div>

                {visitTypesQuery.isPending ? (
                  <LoadingState label="Loading appointment types" />
                ) : visitTypesQuery.isError ? (
                  <InlineBanner
                    icon={<AlertCircle size={16} />}
                    message="Appointment types could not be loaded."
                    tone="error"
                    action={{ label: 'Retry', onClick: () => void visitTypesQuery.refetch() }}
                  />
                ) : (
                  <VisitTypeCards
                    visitTypes={visitTypes}
                    selectedVisitType={visitType}
                    onSelect={selectVisitType}
                  />
                )}
              </section>

              <section className="booking-section" aria-labelledby="provider-heading">
                <div>
                  <h2 className="booking-section__heading" id="provider-heading">
                    <span className="booking-section__index">2.</span> Choose a provider
                  </h2>
                  <p className="booking-section__hint">Pick a provider that fits your needs.</p>
                </div>

                {visitType === null ? (
                  <p className="booking-section__hint">
                    Select an appointment type first to see who is available.
                  </p>
                ) : providersQuery.isPending ? (
                  <LoadingState label="Loading providers" />
                ) : providersQuery.isError ? (
                  <InlineBanner
                    icon={<AlertCircle size={16} />}
                    message="Providers could not be loaded."
                    tone="error"
                    action={{ label: 'Retry', onClick: () => void providersQuery.refetch() }}
                  />
                ) : providers.length === 0 ? (
                  <EmptyState
                    icon={<CircleUserRound size={24} aria-hidden="true" />}
                    title="No providers for this appointment type"
                    description="Choose a different type, or contact the clinic to be booked manually."
                  />
                ) : (
                  <ProviderCards
                    providers={providers}
                    selectedDoctorId={doctorId}
                    onSelect={selectProvider}
                  />
                )}
              </section>

              <section className="booking-section" aria-labelledby="date-time-heading">
                <div className="booking-section__head">
                  <h2 className="booking-section__heading" id="date-time-heading">
                    <span className="booking-section__index">3.</span> Select a date and time
                  </h2>
                  {availability !== null ? (
                    <span className="booking-section__aside">
                      Times shown in {availability.timezone}
                    </span>
                  ) : null}
                </div>

                {doctorId === null ? (
                  <p className="booking-section__hint">
                    Choose a provider to see their open times.
                  </p>
                ) : availabilityQuery.isPending ? (
                  <LoadingState label="Checking availability" />
                ) : availabilityQuery.isError ? (
                  <InlineBanner
                    icon={<AlertCircle size={16} />}
                    message="Availability could not be loaded."
                    tone="error"
                    action={{ label: 'Retry', onClick: () => void availabilityQuery.refetch() }}
                  />
                ) : availability === null ? null : (
                  <>
                    {availability.calendarConnected ? null : (
                      <InlineBanner
                        icon={<Info size={16} />}
                        message="This provider has not connected their calendar, so these times are the clinic's opening hours and are confirmed by the practice."
                        tone="neutral"
                      />
                    )}
                    <AppointmentDateTimePicker
                      days={availability.days}
                      selectedDay={selectedDay}
                      selectedSlotStart={slotStart}
                      onSelectDay={(day) => {
                        setSelectedDay(day);
                        setSlotStart(null);
                      }}
                      onSelectSlot={setSlotStart}
                    />
                  </>
                )}
              </section>

              <div className="booking-footer">
                <span className="booking-footer__icon" aria-hidden="true">
                  <CalendarCheck size={20} />
                </span>
                <span className="booking-footer__copy">
                  <span className="booking-footer__title">
                    {canContinue && selectedProvider
                      ? `${appointmentLabel} with ${selectedProvider.name}`
                      : 'Your appointment details are confirmed on the next step.'}
                  </span>
                  <span className="booking-footer__hint">
                    You can review and change everything before booking.
                  </span>
                </span>
                <button
                  type="button"
                  className="booking-primary-button"
                  disabled={!canContinue}
                  onClick={() => setIsConfirming(true)}
                >
                  Continue
                  <ArrowRight size={18} aria-hidden="true" />
                </button>
              </div>
            </>
          )}
        </main>
      </div>

      {isRegistrationOpen ? (
        <DoctorRegistrationDialog
          visitTypes={visitTypes}
          onClose={() => setIsRegistrationOpen(false)}
        />
      ) : null}
    </div>
  );
}

interface BookingConfirmedProps {
  confirmation: BookingConfirmation;
  timezone: string | null;
  onStartOver: () => void;
}

/**
 * Post-booking state. Surfaces the intake link because booking is what
 * creates the pre-screening session — and says plainly whether the doctor's
 * calendar was actually updated rather than implying a sync that may not
 * have happened.
 */
function BookingConfirmed({
  confirmation,
  timezone,
  onStartOver,
}: BookingConfirmedProps): JSX.Element {
  const when =
    confirmation.scheduledAt === null
      ? null
      : formatAppointmentLabel(confirmation.scheduledAt, timezone);

  const [copied, setCopied] = useState(false);

  const handleCopy = (): void => {
    void navigator.clipboard.writeText(confirmation.sessionId).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  return (
    <div className="booking-result-layout">
      <div className="booking-result">
        <span className="booking-result__mark" aria-hidden="true">
          <CalendarCheck size={24} />
        </span>
        <div>
          <h2 className="booking-result__title">Appointment booked</h2>
          <p className="booking-result__hint">
            {when === null
              ? `Booked with ${confirmation.physician}.`
              : `${when} with ${confirmation.physician}.`}{' '}
            The pre-screening call link has been sent to the patient.
          </p>
        </div>

        <div className="booking-summary">
          <div className="booking-summary__row">
            <span className="booking-summary__label">Appointment reference</span>
            <span className="booking-summary__value" style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              {confirmation.sessionId}
              <button
                type="button"
                onClick={handleCopy}
                aria-label="Copy appointment reference"
                title={copied ? 'Copied!' : 'Copy to clipboard'}
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  width: '24px',
                  height: '24px',
                  border: 'none',
                  borderRadius: '6px',
                  background: copied ? 'var(--color-primary-subtle, #EAF4EB)' : 'var(--color-surface-hover, #f5f5f5)',
                  color: copied ? 'var(--color-primary, #215949)' : 'var(--color-text-secondary)',
                  cursor: 'pointer',
                  flexShrink: 0,
                  transition: 'background 0.2s ease, color 0.2s ease',
                }}
              >
                {copied
                  ? <Check size={13} strokeWidth={2.5} aria-hidden="true" />
                  : <ClipboardCopy size={13} aria-hidden="true" />}
              </button>
            </span>
          </div>
          <div className="booking-summary__row">
            <span className="booking-summary__label">Patient ID</span>
            <span className="booking-summary__value">
              {confirmation.patientId}
              {confirmation.patientIdIsProvisional ? ' (provisional)' : ''}
            </span>
          </div>
          <div className="booking-summary__row">
            <span className="booking-summary__label">Doctor calendar</span>
            <span className="booking-summary__value">
              {confirmation.calendarSynced ? 'Event created' : 'Not connected'}
            </span>
          </div>
        </div>

        {confirmation.patientIdIsProvisional ? (
          <InlineBanner
            icon={<Info size={16} />}
            message="No patient ID was given, so a provisional one was issued. Reconcile it with the clinic record."
            tone="neutral"
          />
        ) : null}

        <button type="button" className="booking-secondary-button" onClick={onStartOver}>
          Book another appointment
        </button>
      </div>

      <IntakeMessagePreview
        message={confirmation.notificationMessage}
        intakeUrl={confirmation.intakeUrl}
      />
    </div>
  );
}

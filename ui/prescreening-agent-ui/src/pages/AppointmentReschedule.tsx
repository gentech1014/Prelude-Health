import { CalendarX2 } from 'lucide-react';
import { useCallback, useEffect, useState, type JSX } from 'react';
import { useNavigate } from 'react-router-dom';
import { PageSection } from '@/components/PageSection';
import { EmptyState, InlineBanner, LoadingState } from '@/components/states';
import { AppointmentDaySlotPicker } from '@/features/appointment-reschedule/AppointmentDaySlotPicker';
import { CallScreenLayout } from '@/features/prescreening-session/CallScreenLayout';
import { buildPrescreeningStepPath } from '@/features/prescreening-session/prescreeningFlowSteps';
import { useIntakeCall } from '@/features/prescreening-session/useIntakeCall';
import { usePrescreeningSession } from '@/features/prescreening-session/usePrescreeningSession';
import { useToast } from '@/hooks/useToast';
import { toApiError } from '@/services/apiClient';
import {
  fetchAppointmentAvailability,
  rescheduleAppointment,
  type AppointmentAvailability,
  type AppointmentSlot,
} from '@/services/appointmentService';

type LoadStatus = 'loading' | 'loaded' | 'error';

/**
 * Reached from the closing screen: the same clinician's real open times,
 * read from their own calendar where they have connected one and from
 * clinic hours where they have not.
 *
 * The chosen slot is re-validated server-side on confirm. A slot taken by
 * someone else while the patient was reading the list comes back as a
 * conflict, and the list is re-fetched rather than the request retried.
 *
 * During a live call this is one half of a conversation: the assistant
 * offers the same times out loud, and a time tapped here is reported back
 * to it so it confirms the new one rather than going on offering times
 * that are no longer the question.
 */
export function AppointmentReschedule(): JSX.Element {
  const navigate = useNavigate();
  const { showToast } = useToast();
  const { sessionId, session, applySession } = usePrescreeningSession();
  const { reportAppointmentRescheduled } = useIntakeCall();
  const thankYouPath = buildPrescreeningStepPath(sessionId, 'thank-you');

  const [status, setStatus] = useState<LoadStatus>('loading');
  const [availability, setAvailability] = useState<AppointmentAvailability | null>(null);
  const [selectedDay, setSelectedDay] = useState<string | null>(null);
  const [selectedSlotId, setSelectedSlotId] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (): Promise<void> => {
    setStatus('loading');
    try {
      const loaded = await fetchAppointmentAvailability(sessionId);
      setAvailability(loaded);
      // The first day with anything open, so the patient sees times
      // immediately rather than having to tap a date first every time.
      setSelectedDay(loaded.days.find((day) => day.slots.length > 0)?.day ?? null);
      setSelectedSlotId(null);
      setStatus('loaded');
    } catch {
      setStatus('error');
    }
  }, [sessionId]);

  useEffect(() => {
    void load();
  }, [load]);

  const days = availability?.days ?? [];
  const hasAnyOpenSlot = days.some((day) => day.slots.length > 0);
  const selectedSlot =
    days.find((day) => day.day === selectedDay)?.slots.find((slot) => slot.id === selectedSlotId) ??
    null;
  const clinician = session?.appointment.physician ?? 'your clinician';

  const handleConfirm = useCallback(
    async (slot: AppointmentSlot): Promise<void> => {
      setIsSubmitting(true);
      setError(null);
      try {
        applySession(await rescheduleAppointment(sessionId, slot));
        showToast({ message: 'Your appointment was moved.', tone: 'success' });
        // After the write, never before: the assistant is told the new time
        // is already saved, and saying so before it is would have it
        // confirming a move that could still fail on a conflict.
        reportAppointmentRescheduled();
        navigate(thankYouPath, { replace: true });
      } catch (cause) {
        const apiError = toApiError(cause);
        setError(apiError.message);
        setSelectedSlotId(null);
        // A conflict means the list is stale, not that the request was
        // wrong: re-reading is the only thing that can fix it.
        if (apiError.kind === 'conflict') await load();
      } finally {
        setIsSubmitting(false);
      }
    },
    [
      applySession,
      load,
      navigate,
      reportAppointmentRescheduled,
      sessionId,
      showToast,
      thankYouPath,
    ],
  );

  return (
    <CallScreenLayout title="Reschedule appointment" subtitle={`Open times with ${clinician}.`}>
      {status === 'loading' ? (
        <PageSection>
          <LoadingState label="Looking for open times…" />
        </PageSection>
      ) : null}

      {status === 'error' ? (
        <PageSection>
          <EmptyState
            icon={<CalendarX2 size={24} aria-hidden="true" />}
            title="We could not load open times"
            description="Your appointment is unchanged. Try again, or contact the clinic to move it."
            tone="error"
            primaryAction={{ label: 'Try again', onClick: () => void load() }}
            secondaryAction={{
              label: 'Back to summary',
              onClick: () => navigate(thankYouPath),
            }}
          />
        </PageSection>
      ) : null}

      {status === 'loaded' && !hasAnyOpenSlot ? (
        <PageSection>
          <EmptyState
            icon={<CalendarX2 size={24} aria-hidden="true" />}
            title="No open times right now"
            description={`There is nothing open with ${clinician} in the next couple of weeks. Contact the clinic to find a time.`}
            tone="neutral"
            primaryAction={{
              label: 'Back to summary',
              onClick: () => navigate(thankYouPath),
            }}
          />
        </PageSection>
      ) : null}

      {status === 'loaded' && hasAnyOpenSlot ? (
        <>
          {availability !== null && !availability.isCalendarConnected ? (
            <PageSection>
              {/* Said plainly: a clinic-hours slot has not been checked
                  against the doctor's own calendar, and presenting it as
                  though it had would be a promise nobody made. */}
              <InlineBanner
                tone="info"
                message="These times come from clinic hours. The clinic will confirm the new time with your doctor."
              />
            </PageSection>
          ) : null}

          <PageSection>
            <AppointmentDaySlotPicker
              days={days}
              selectedDay={selectedDay}
              selectedSlotId={selectedSlotId}
              onSelectDay={(day) => {
                setSelectedDay(day);
                setSelectedSlotId(null);
              }}
              onSelectSlot={setSelectedSlotId}
            />
          </PageSection>

          {error ? (
            <PageSection>
              <InlineBanner tone="error" message={error} />
            </PageSection>
          ) : null}

          <PageSection>
            <button
              type="button"
              onClick={() => selectedSlot && void handleConfirm(selectedSlot)}
              disabled={!selectedSlot || isSubmitting}
              style={{
                width: '100%',
                padding: 'var(--space-3)',
                borderRadius: 'var(--radius-md)',
                border: 'none',
                background:
                  !selectedSlot || isSubmitting
                    ? 'var(--color-surface-alt)'
                    : 'var(--color-primary-active)',
                color:
                  !selectedSlot || isSubmitting
                    ? 'var(--color-text-muted)'
                    : 'var(--color-on-primary)',
                fontWeight: 'var(--fw-semibold)',
              }}
            >
              {isSubmitting ? 'Moving your appointment…' : 'Confirm new time'}
            </button>
          </PageSection>
        </>
      ) : null}
    </CallScreenLayout>
  );
}

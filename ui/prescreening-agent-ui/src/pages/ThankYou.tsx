import { CalendarClock, Check } from 'lucide-react';
import type { JSX } from 'react';
import { useNavigate } from 'react-router-dom';
import { OptionalDetailsField } from '@/components/OptionalDetailsField';
import { PageSection } from '@/components/PageSection';
import { AppointmentDetailsCard } from '@/features/appointment-schedule/AppointmentDetailsCard';
import { appointmentFromSession } from '@/features/appointment-schedule/appointmentFromSession';
import { CallScreenLayout } from '@/features/prescreening-session/CallScreenLayout';
import { useIntakeCall } from '@/features/prescreening-session/useIntakeCall';
import { usePrefilledText } from '@/features/prescreening-session/usePrefill';
import { usePrescreeningSession } from '@/features/prescreening-session/usePrescreeningSession';
import { useStepNavigation } from '@/features/prescreening-session/useStepNavigation';
import { AnimatedSuccessTick } from '@/features/thank-you/AnimatedSuccessTick';
import { SessionSummaryChecklist } from '@/features/thank-you/SessionSummaryChecklist';

/**
 * The closing screen: the appointment again, what the call actually
 * covered, and one last opening — whether there is anything they would
 * like help with.
 *
 * The tick is deliberately not celebratory about the *outcome* — nothing
 * here has been reviewed by a clinician, and the copy says only that the
 * prescreening itself is done. This screen never hangs up on its own: the
 * call ends through the assistant saying goodbye and calling `end_session`,
 * the shared call controls, or — if neither happens in time — a backend
 * watchdog that force-ends it a few seconds after the goodbye is spoken
 * (`THANK_YOU_FORCE_END_SECONDS` in `app.api.ws.channels`).
 */
export function ThankYou(): JSX.Element {
  const navigate = useNavigate();
  const { buildStepPath } = useStepNavigation();
  const { session } = usePrescreeningSession();
  const { status, visitedScreens, requestReschedule, sendTypedAnswer } = useIntakeCall();
  const anythingElse = usePrefilledText('thank-you', 'anything_else');

  // While the call is live the assistant owns the screen: tapping asks it
  // for times, and it moves the patient once it has them. Navigating here
  // as well would put them on the list a moment before anyone is offering
  // anything. Off a call, this button is the only way there.
  const isCallLive = status === 'live' || status === 'reconnecting';
  const openReschedule = (): void => {
    if (isCallLive) {
      requestReschedule();
      return;
    }
    navigate(buildStepPath('appointment-reschedule'));
  };

  // Keeping the time was a control that did nothing at all, next to one
  // labelled "No" that rescheduled — so the patient answering the
  // assistant's closing question with "no" was looking at a button where
  // no meant "change it". Both now say what they do, and confirming
  // reaches the assistant as the patient's own turn, exactly as saying it
  // aloud would.
  const keepAppointment = (): void => {
    if (!isCallLive) return;
    sendTypedAnswer('That appointment time is fine, I would like to keep it.');
  };

  return (
    <CallScreenLayout
      title="Thank you"
      subtitle="Your prescreening is complete. Here is what your doctor will see."
    >
      <PageSection>
        <div style={{ display: 'grid', placeItems: 'center' }}>
          <AnimatedSuccessTick />
        </div>
      </PageSection>

      {session ? (
        <PageSection>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
            {/* Section title */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-1)' }}>
              <h2
                style={{
                  margin: 0,
                  fontSize: '1.1rem',
                  fontWeight: 'var(--fw-bold)',
                  color: 'var(--color-text)',
                  letterSpacing: '-0.01em',
                }}
              >
                Appointment schedule
              </h2>
              <p
                style={{
                  margin: 0,
                  fontSize: '0.9rem',
                  color: 'var(--color-text-secondary)',
                }}
              >
                This is when you are booked in. Change it only if you need to.
              </p>
            </div>

            <AppointmentDetailsCard appointment={appointmentFromSession(session)} />

            <div style={{ display: 'flex', gap: 'var(--space-3)' }}>
              <button
                type="button"
                onClick={keepAppointment}
                disabled={!isCallLive}
                style={{
                  flex: 1,
                  display: 'flex',
                  alignItems: 'center',
                  gap: 'var(--space-3)',
                  padding: 'var(--space-3)',
                  borderRadius: 'var(--radius-lg)',
                  border: 'none',
                  background: 'var(--color-primary)',
                  color: 'white',
                  cursor: isCallLive ? 'pointer' : 'not-allowed',
                  opacity: isCallLive ? 1 : 0.5,
                  textAlign: 'left',
                }}
              >
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    width: '32px',
                    height: '32px',
                    borderRadius: '50%',
                    background: 'white',
                    color: 'var(--color-primary)',
                    flexShrink: 0,
                  }}
                >
                  <Check size={20} strokeWidth={3} />
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
                  <span style={{ fontSize: '0.95rem', fontWeight: 'var(--fw-semibold)' }}>
                    Keep this time
                  </span>
                  <span style={{ fontSize: '0.8rem', opacity: 0.9 }}>no change needed</span>
                </div>
              </button>

              <button
                type="button"
                onClick={openReschedule}
                style={{
                  flex: 1,
                  display: 'flex',
                  alignItems: 'center',
                  gap: 'var(--space-3)',
                  padding: 'var(--space-3)',
                  borderRadius: 'var(--radius-lg)',
                  border: '1px solid var(--color-primary-border)',
                  background: 'var(--color-surface)',
                  color: 'var(--color-primary)',
                  cursor: 'pointer',
                  textAlign: 'left',
                }}
              >
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    width: '32px',
                    height: '32px',
                    borderRadius: 'var(--radius-md)',
                    background: 'var(--color-surface-hover)',
                    color: 'var(--color-primary)',
                    flexShrink: 0,
                  }}
                >
                  <CalendarClock size={20} />
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
                  <span style={{ fontSize: '0.95rem', fontWeight: 'var(--fw-semibold)' }}>
                    Change time
                  </span>
                  <span style={{ fontSize: '0.8rem', color: 'var(--color-text-secondary)' }}>
                    see other slots
                  </span>
                </div>
              </button>
            </div>
          </div>
        </PageSection>
      ) : null}

      <PageSection>
        {/* Derived from the screens the call actually reached, not a fixed
            list: a recap that claims coverage the call never got to would
            tell the patient their doctor has information nobody collected. */}
        <SessionSummaryChecklist visitedScreens={visitedScreens} />
      </PageSection>

      <PageSection>
        <OptionalDetailsField
          value={anythingElse.value}
          onChange={anythingElse.onChange}
          onSubmit={anythingElse.onSubmit}
          label="Is there anything you would like help with? (optional)"
          helperText="Say it, or type it here. It goes with the rest of your answers."
        />
      </PageSection>
    </CallScreenLayout>
  );
}

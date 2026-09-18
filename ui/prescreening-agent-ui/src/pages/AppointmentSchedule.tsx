import type { JSX } from 'react';
import { PageSection } from '@/components/PageSection';
import { LoadingState } from '@/components/states';
import { AppointmentDetailsCard } from '@/features/appointment-schedule/AppointmentDetailsCard';
import { appointmentFromSession } from '@/features/appointment-schedule/appointmentFromSession';
import { CallScreenLayout } from '@/features/prescreening-session/CallScreenLayout';
import { usePrescreeningSession } from '@/features/prescreening-session/usePrescreeningSession';

/**
 * The appointment, read back while the assistant says it out loud.
 *
 * Informational only — nothing is asked here, and the assistant moves on
 * after one short turn. Rescheduling, if the patient needs it, is offered
 * at the end of the call rather than in the middle of intake.
 */
export function AppointmentSchedule(): JSX.Element {
  const { session } = usePrescreeningSession();

  return (
    <CallScreenLayout title="Your appointment">
      <PageSection>
        {session ? (
          <AppointmentDetailsCard appointment={appointmentFromSession(session)} />
        ) : (
          <LoadingState label="Loading your appointment…" />
        )}
      </PageSection>
    </CallScreenLayout>
  );
}

import { useCallback, useState, type JSX } from 'react';
import { PageSection } from '@/components/PageSection';
import { InlineBanner } from '@/components/states';
import { CallScreenLayout } from '@/features/prescreening-session/CallScreenLayout';
import { ConfirmDetailsForm } from '@/features/confirm-details/ConfirmDetailsForm';
import { ConsentCard } from '@/features/confirm-details/ConsentCard';
import { useIntakeCall } from '@/features/prescreening-session/useIntakeCall';
import { usePrescreeningSession } from '@/features/prescreening-session/usePrescreeningSession';
import { toApiError } from '@/services/apiClient';
import { submitConsent } from '@/services/prescreeningSessionService';

/**
 * Where the patient confirms who they are, and the one gate the call
 * cannot start without.
 *
 * The assistant is already on the call by the time this renders, and is
 * waiting here: until consent is recorded it can greet and ask, and every
 * tool that would collect anything refuses. Recording consent is what
 * releases it, which is why this posts to the server and blocks on the
 * response rather than trusting a locally-ticked box.
 */
export function ConfirmDetails(): JSX.Element {
  const { sessionId, session, applySession } = usePrescreeningSession();
  const { notifyConsentRecorded } = useIntakeCall();
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const hasConsented = session?.consentGiven ?? false;

  const handleConsent = useCallback(
    async (given: boolean): Promise<void> => {
      setIsSubmitting(true);
      setError(null);
      try {
        applySession(await submitConsent(sessionId, given));
        // The assistant is waiting on the consent screen and nothing else
        // will release it. It verifies this against the session itself, so
        // saying so here cannot unlock anything that was not recorded.
        if (given) notifyConsentRecorded();
      } catch (cause) {
        // Kept on this step rather than advanced with an explanation: the
        // call genuinely cannot start, so moving on would strand them.
        setError(toApiError(cause).message);
      } finally {
        setIsSubmitting(false);
      }
    },
    [applySession, notifyConsentRecorded, sessionId],
  );

  return (
    <CallScreenLayout
      title="Confirm a few details"
      subtitle="Check these are right. You can correct anything here or tell the assistant."
    >
      <PageSection>
        {/* Keyed on the session so the fields re-initialize from the booking
            record once the handshake lands, rather than staying empty. */}
        <ConfirmDetailsForm key={session?.sessionId ?? 'pending'} />
      </PageSection>

      <PageSection>
        <ConsentCard
          hasConsented={hasConsented}
          isSubmitting={isSubmitting}
          clinicName={session?.clinic.name ?? null}
          onDecision={(given) => void handleConsent(given)}
        />
      </PageSection>

      {error ? (
        <PageSection>
          <InlineBanner tone="error" message={error} />
        </PageSection>
      ) : null}
    </CallScreenLayout>
  );
}

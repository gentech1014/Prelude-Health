import { Clock } from 'lucide-react';
import type { JSX } from 'react';
import { CallSessionControls } from '@/components/CallSessionControls';
import { Header } from '@/components/Header';
import { PageSection } from '@/components/PageSection';
import { PageTransition } from '@/components/PageTransition';
import { InlineBanner } from '@/components/states';
import { AssistantAvatar } from '@/features/welcome/AssistantAvatar';
import { CALL_CONTROL_BAR_CLEARANCE } from '@/features/welcome/CallControlBar';
import { HealthJourneyScenery } from '@/features/welcome/HealthJourneyScenery';
import '@/features/welcome/Welcome.css';
import { VoiceTranscriptPanel } from '@/features/confirm-details/VoiceTranscriptPanel';
import { useIntakeCall } from '@/features/prescreening-session/useIntakeCall';
import { usePrescreeningSession } from '@/features/prescreening-session/usePrescreeningSession';

/**
 * The screen the patient meets first, while the assistant introduces
 * itself.
 *
 * Nothing here starts the call and nothing here advances it. The call is
 * already running by the time this renders — `IntakeCallProvider` opened
 * it the moment the session landed — and the assistant moves the patient
 * on by navigating, once it has finished speaking. So this screen has no
 * controls at all, only what the assistant is saying.
 */
export function Welcome(): JSX.Element {
  const { session } = usePrescreeningSession();
  const { problem, needsAudioUnlock, unlockAudio } = useIntakeCall();

  return (
    <div className="welcome-page">
      <Header />

      <PageTransition
        className="welcome-page__content"
        style={{ padding: `var(--space-5) var(--space-4) ${CALL_CONTROL_BAR_CLEARANCE}` }}
      >
        <PageSection>
          <AssistantAvatar />
        </PageSection>

        <PageSection>
          <div>
            <h1 className="welcome-page__heading">
              {session ? `Hi, I am ${session.assistant.name}` : 'Getting ready'}
            </h1>
            <p className="welcome-page__role">{session?.assistant.role ?? ''}</p>
          </div>
        </PageSection>

        <PageSection>
          <div className="welcome-page__info-card">
            <span className="welcome-page__info-icon" aria-hidden="true">
              <Clock size={18} aria-hidden="true" />
            </span>
            <div>
              <p className="welcome-page__info-title">This takes a few minutes.</p>
              <p className="welcome-page__info-body">
                Speak naturally. Questions follow your answers, and you can type any answer instead.
              </p>
            </div>
          </div>
        </PageSection>

        {problem ? (
          <PageSection>
            <InlineBanner
              tone={problem.recoverable ? 'warning' : 'error'}
              message={problem.message}
            />
          </PageSection>
        ) : null}

        <PageSection>
          {/* The greeting is the whole point of this screen, so the
              captions belong here and not only on the in-call layout. */}
          <VoiceTranscriptPanel />
        </PageSection>

        <PageSection>
          <HealthJourneyScenery
            needsAudioUnlock={needsAudioUnlock}
            onUnlockAudio={unlockAudio}
          />
        </PageSection>
      </PageTransition>
      <CallSessionControls />
    </div>
  );
}

import type { JSX, ReactNode } from 'react';
import { AgentProfileBadge } from '@/components/AgentProfileBadge';
import { CallSessionControls } from '@/components/CallSessionControls';
import { Header } from '@/components/Header';
import { PageSection } from '@/components/PageSection';
import { PageTransition } from '@/components/PageTransition';
import { CALL_CONTROL_BAR_CLEARANCE } from '@/features/welcome/CallControlBar';
import { VoiceTranscriptPanel } from '@/features/confirm-details/VoiceTranscriptPanel';
import { DocumentUploadPrompt } from '@/features/prescreening-session/DocumentUploadPrompt';
import { usePrescreeningSession } from '@/features/prescreening-session/usePrescreeningSession';

interface CallScreenLayoutProps {
  title: string;
  subtitle?: string;
  children: ReactNode;
  /**
   * Set to true only on the screen where the agent may ask the patient to
   * upload a document (the RecentCare / recent-care screen). Defaults to
   * false so the upload prompt never leaks onto unrelated screens.
   */
  showUploadPrompt?: boolean;
}

const PAGE: React.CSSProperties = {
  position: 'relative',
  display: 'flex',
  flexDirection: 'column',
  flex: 1,
  minHeight: '100%',
};

/**
 * The shell every in-call screen shares: the status bar, the heading, the
 * screen's own controls, the live captions, and the call controls.
 *
 * Extracted because eleven screens had identical copies of it, which meant
 * eleven places to forget the transcript panel or the upload prompt — and
 * the upload prompt in particular can be opened by the agent on any screen,
 * so it cannot live on just the one that expects it.
 */
export function CallScreenLayout({
  title,
  subtitle,
  children,
  showUploadPrompt = false,
}: CallScreenLayoutProps): JSX.Element {
  const { session } = usePrescreeningSession();

  return (
    <div style={PAGE}>
      <Header />

      <PageTransition
        style={{
          flex: 1,
          overflowY: 'auto',
          display: 'flex',
          flexDirection: 'column',
          gap: 'var(--space-5)',
          padding: `var(--space-2) var(--space-4) ${CALL_CONTROL_BAR_CLEARANCE}`,
        }}
      >
        <PageSection>
          <div>
            <h1
              style={{
                margin: 0,
                fontSize: 'var(--fs-h2)',
                fontWeight: 'var(--fw-bold)',
                color: 'var(--color-text)',
              }}
            >
              {title}
            </h1>
            {subtitle ? (
              <p
                style={{
                  margin: 'var(--space-1) 0 0',
                  fontSize: 'var(--fs-body)',
                  color: 'var(--color-text-secondary)',
                }}
              >
                {subtitle}
              </p>
            ) : null}
          </div>
        </PageSection>

        {children}

        {showUploadPrompt && (
          <PageSection>
            <DocumentUploadPrompt />
          </PageSection>
        )}

        <PageSection>
          <VoiceTranscriptPanel />
        </PageSection>
      </PageTransition>

      {/* Outside PageTransition — position: fixed needs no animated ancestor between it and the viewport. */}
      {session ? <AgentProfileBadge name={session.assistant.name} /> : null}

      <CallSessionControls />
    </div>
  );
}

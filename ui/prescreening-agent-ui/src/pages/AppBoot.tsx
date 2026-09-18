import { CalendarX2, LinkIcon, ShieldOff, WifiOff } from 'lucide-react';
import type { JSX, ReactNode } from 'react';
import { ModelAwakeningLoader } from '@/components/ModelAwakeningLoader';
import { EmptyState } from '@/components/states';
import { useIntakeCall } from '@/features/prescreening-session/useIntakeCall';
import { usePrescreeningSession } from '@/features/prescreening-session/usePrescreeningSession';
import type { ApiErrorKind } from '@/services/apiClient';
import type { SessionStatus } from '@/services/prescreeningSessionService';

/**
 * A session in one of these states has finished; re-entering it must not
 * restart the call.
 *
 * `interrupted` is deliberately absent: a dropped call is expected to be
 * rejoined, and the backend keeps a resume marker precisely so it can be.
 */
const CLOSED_STATUSES: ReadonlySet<SessionStatus> = new Set([
  'declined',
  'expired',
  'completed',
  'summarizing',
  'summary_ready',
  'summary_failed',
  'video_ready',
]);

const CLOSED_COPY: Partial<Record<SessionStatus, { title: string; description: string }>> = {
  declined: {
    title: 'Pre-visit screening declined',
    description:
      'You chose not to share your health information. Your appointment is unaffected, and your care team will go through everything with you in person.',
  },
  expired: {
    title: 'This link has expired',
    description:
      'Pre-visit screening links are valid for a limited time. Contact the clinic for a new one.',
  },
};

const CLOSED_FALLBACK = {
  title: 'Pre-visit screening already completed',
  description:
    'You have already finished this pre-visit screening. There is nothing further to do here.',
};

const FAILURE_COPY: Record<ApiErrorKind, { icon: ReactNode; title: string; description: string }> =
  {
    unauthorized: {
      icon: <ShieldOff size={26} aria-hidden="true" />,
      title: 'This link is no longer valid',
      description:
        'Open the most recent pre-visit screening link sent to you, or contact the clinic for a new one.',
    },
    'not-found': {
      icon: <LinkIcon size={26} aria-hidden="true" />,
      title: 'We could not find this pre-visit screening',
      description: 'Check that you opened the full link exactly as it was sent to you.',
    },
    offline: {
      icon: <WifiOff size={26} aria-hidden="true" />,
      title: 'You appear to be offline',
      description:
        'Reconnect to the internet and try again. Nothing you have entered has been lost.',
    },
    timeout: {
      icon: <WifiOff size={26} aria-hidden="true" />,
      title: 'Connecting is taking too long',
      description: 'Check your connection and try again.',
    },
    conflict: {
      icon: <CalendarX2 size={26} aria-hidden="true" />,
      title: 'This pre-visit screening has already moved on',
      description: 'Reload to pick up where things stand now.',
    },
    server: {
      icon: <CalendarX2 size={26} aria-hidden="true" />,
      title: 'We could not start your pre-visit screening',
      description: 'Something went wrong on our side. Try again shortly.',
    },
    unavailable: {
      icon: <CalendarX2 size={26} aria-hidden="true" />,
      title: 'Pre-visit screening is temporarily unavailable',
      description:
        'The service is not accepting calls right now. Try again shortly, or contact the clinic.',
    },
    malformed: {
      icon: <CalendarX2 size={26} aria-hidden="true" />,
      title: 'We could not start your pre-visit screening',
      description: 'Something went wrong on our side. Try again shortly.',
    },
  };

const CENTERED = {
  position: 'relative',
  display: 'flex',
  flex: 1,
  alignItems: 'center',
  justifyContent: 'center',
  minHeight: '100%',
} as const;

/**
 * Index route of `/prescreen/:sessionId` — the first screen shown whenever
 * that URL is entered or re-entered.
 *
 * Shows the loader until the assistant is on the line, then gets out of the
 * way: `IntakeCallProvider` routes to whichever screen the *server* says the
 * call is on, and this route is left behind the moment it does.
 *
 * It deliberately does not navigate itself. Sending everyone to `welcome`
 * raced that provider on every connect, and was simply wrong on a resumed
 * call — the server's screen is the one the conversation is actually on.
 */
export function AppBoot(): JSX.Element {
  const { phase, session, error, refresh } = usePrescreeningSession();
  const { status: callStatus, problem: callProblem } = useIntakeCall();

  const isOpen = session !== null && !CLOSED_STATUSES.has(session.status);
  const hasCallFailed = callStatus === 'failed';

  if (phase === 'failed') {
    const copy = FAILURE_COPY[error?.kind ?? 'server'];
    return (
      <div style={CENTERED}>
        <EmptyState
          icon={copy.icon}
          title={copy.title}
          description={copy.description}
          tone="error"
          primaryAction={{ label: 'Try again', onClick: () => void refresh() }}
        />
      </div>
    );
  }

  if (session !== null && !isOpen) {
    const copy = CLOSED_COPY[session.status] ?? CLOSED_FALLBACK;
    return (
      <div style={CENTERED}>
        <EmptyState
          icon={<CalendarX2 size={26} aria-hidden="true" />}
          title={copy.title}
          description={copy.description}
          tone="neutral"
        />
      </div>
    );
  }

  if (hasCallFailed) {
    return (
      <div style={CENTERED}>
        <EmptyState
          icon={<WifiOff size={26} aria-hidden="true" />}
          title="We could not reach your assistant"
          description={
            callProblem?.message ?? 'Something went wrong on our side. Try again shortly.'
          }
          tone="error"
          primaryAction={{ label: 'Try again', onClick: () => void refresh() }}
        />
      </div>
    );
  }

  return (
    <div style={CENTERED}>
      <ModelAwakeningLoader label={loaderLabel(callStatus)} />
    </div>
  );
}

/** What the loader says, so it never claims more progress than there is. */
function loaderLabel(callStatus: ReturnType<typeof useIntakeCall>['status']): string {
  switch (callStatus) {
    case 'idle':
      return 'Opening your pre-visit screening…';
    case 'starting':
      return 'Waking your assistant…';
    default:
      return 'Connecting to your assistant…';
  }
}

import { ShieldCheck } from 'lucide-react';
import { useContext, type JSX } from 'react';
import '@/components/Header.css';
import { StatusBadge } from '@/components/states';
import { ThemeToggle } from '@/components/ThemeToggle';
import { IntakeCallContext } from '@/features/prescreening-session/intakeCallContext';

/** What the status dot and label actually say, per call state. */
const CALL_STATUS_COPY = {
  idle: { label: 'Not started', tone: 'idle' },
  starting: { label: 'Starting…', tone: 'pending' },
  connecting: { label: 'Connecting…', tone: 'pending' },
  live: { label: 'Connected', tone: 'live' },
  reconnecting: { label: 'Reconnecting…', tone: 'pending' },
  ended: { label: 'Call finished', tone: 'idle' },
  failed: { label: 'Disconnected', tone: 'error' },
} as const;

/**
 * Shared top bar for every screen: live call status, privacy badge, theme
 * toggle.
 *
 * The status is the real connection state, not a decoration. Read through
 * the raw context rather than the hook because this bar also renders
 * outside a call (the booking surface, the style guide), where there is no
 * provider above it.
 */
export function Header(): JSX.Element {
  const call = useContext(IntakeCallContext);
  const status = call === undefined ? null : CALL_STATUS_COPY[call.status];

  return (
    <header className="header">
      <div className="header__status">
        {status ? (
          <>
            <span
              className={`header__status-dot header__status-dot--${status.tone}`}
              aria-hidden="true"
            />
            <span className="header__status-label">{status.label}</span>
          </>
        ) : (
          <>
            <span className="header__status-dot" aria-hidden="true" />
            <span className="header__status-label">Pre-visit</span>
          </>
        )}
      </div>
      <div className="header__actions">
        <StatusBadge
          icon={<ShieldCheck size={14} aria-hidden="true" />}
          label="Secure & Private"
          tone="success"
        />
        <ThemeToggle />
      </div>
    </header>
  );
}

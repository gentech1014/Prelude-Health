import { AlertTriangle, CheckCircle2, Info, XCircle } from 'lucide-react';
import type { JSX, ReactNode } from 'react';
import type { Tone } from '@/types/tone';
import './InlineBanner.css';

interface InlineBannerAction {
  label: string;
  onClick: () => void;
}

interface InlineBannerProps {
  /** Overrides the tone's own icon. Omit it and the tone decides. */
  icon?: ReactNode;
  message: string;
  tone?: Tone;
  action?: InlineBannerAction;
}

// Defaulted per tone so an error banner cannot end up wearing a success
// icon, which is the one mistake that makes a notice actively misleading.
const TONE_ICONS: Record<Tone, ReactNode> = {
  neutral: <Info size={18} aria-hidden="true" />,
  success: <CheckCircle2 size={18} aria-hidden="true" />,
  warning: <AlertTriangle size={18} aria-hidden="true" />,
  error: <XCircle size={18} aria-hidden="true" />,
  info: <Info size={18} aria-hidden="true" />,
};

const TONE_STYLES: Record<Tone, { bg: string; border: string; color: string }> = {
  neutral: {
    bg: 'var(--color-surface-alt)',
    border: 'var(--color-border)',
    color: 'var(--color-text)',
  },
  success: {
    bg: 'var(--color-success-bg)',
    border: 'var(--color-success-border)',
    color: 'var(--color-success)',
  },
  warning: {
    bg: 'var(--color-warning-bg)',
    border: 'var(--color-warning-border)',
    color: 'var(--color-warning)',
  },
  error: {
    bg: 'var(--color-error-bg)',
    border: 'var(--color-error-border)',
    color: 'var(--color-error)',
  },
  info: {
    bg: 'var(--color-info-bg)',
    border: 'var(--color-info-border)',
    color: 'var(--color-info)',
  },
};

/**
 * A small inset notice used when a section — not the whole screen — needs to
 * say something: one widget in a mostly-loaded page failed, or changes are unsaved.
 */
export function InlineBanner({
  icon,
  message,
  tone = 'info',
  action,
}: InlineBannerProps): JSX.Element {
  const styles = TONE_STYLES[tone];

  return (
    <div
      className="inline-banner"
      role={tone === 'error' || tone === 'warning' ? 'alert' : 'status'}
      style={{ background: styles.bg, border: `1px solid ${styles.border}` }}
    >
      <span className="inline-banner__icon" aria-hidden="true" style={{ color: styles.color }}>
        {icon ?? TONE_ICONS[tone]}
      </span>
      <span className="inline-banner__message">{message}</span>
      {action ? (
        <button
          type="button"
          className="inline-banner__action"
          onClick={action.onClick}
          style={{ color: styles.color }}
        >
          {action.label}
        </button>
      ) : null}
    </div>
  );
}

import type { JSX, ReactNode } from 'react';
import type { Tone } from '@/types/tone';
import './StatusBadge.css';

interface StatusBadgeProps {
  icon: ReactNode;
  label: string;
  tone?: Tone;
  spin?: boolean;
}

const TONE_STYLES: Record<Tone, { bg: string; color: string }> = {
  neutral: { bg: 'var(--color-surface-alt)', color: 'var(--color-text-secondary)' },
  success: { bg: 'var(--color-success-bg)', color: 'var(--color-success)' },
  warning: { bg: 'var(--color-warning-bg)', color: 'var(--color-warning)' },
  error: { bg: 'var(--color-error-bg)', color: 'var(--color-error)' },
  info: { bg: 'var(--color-info-bg)', color: 'var(--color-info)' },
};

/**
 * A small inline pill for a status shown alongside other content — e.g. a
 * background job row's "Processing" / "Completed" / "Cancelled" indicator.
 * Icon always carries the meaning; color is a reinforcement, never the only signal.
 */
export function StatusBadge({
  icon,
  label,
  tone = 'neutral',
  spin = false,
}: StatusBadgeProps): JSX.Element {
  const styles = TONE_STYLES[tone];

  return (
    <span className="status-badge" style={{ background: styles.bg, color: styles.color }}>
      <span
        className={`status-badge__icon${spin ? ' status-badge__icon--spin' : ''}`}
        aria-hidden="true"
      >
        {icon}
      </span>
      {label}
    </span>
  );
}

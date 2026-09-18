import type { JSX, ReactNode } from 'react';
import type { Tone } from '@/types/tone';
import './EmptyState.css';

interface EmptyStateAction {
  label: string;
  onClick: () => void;
}

interface EmptyStateProps {
  icon: ReactNode;
  title: string;
  description?: string;
  tone?: Tone;
  primaryAction?: EmptyStateAction;
  secondaryAction?: EmptyStateAction;
}

const TONE_ICON_COLOR: Record<Tone, string> = {
  neutral: 'var(--color-text-secondary)',
  success: 'var(--color-success)',
  warning: 'var(--color-warning)',
  error: 'var(--color-error)',
  info: 'var(--color-info)',
};

/**
 * One layout for every "nothing to show, here's why" screen: empty data,
 * no search results, permission denied, session expired, offline, or a hard
 * error — they differ only in icon, copy, tone, and available action(s).
 */
export function EmptyState({
  icon,
  title,
  description,
  tone = 'neutral',
  primaryAction,
  secondaryAction,
}: EmptyStateProps): JSX.Element {
  return (
    <div className="empty-state" role={tone === 'error' || tone === 'warning' ? 'alert' : 'status'}>
      <div className="empty-state__icon" aria-hidden="true" style={{ color: TONE_ICON_COLOR[tone] }}>
        {icon}
      </div>
      <div>
        <h2 className="empty-state__title">{title}</h2>
        {description ? <p className="empty-state__description">{description}</p> : null}
      </div>
      {primaryAction || secondaryAction ? (
        <div className="empty-state__actions">
          {secondaryAction ? (
            <button type="button" className="btn-secondary" onClick={secondaryAction.onClick}>
              {secondaryAction.label}
            </button>
          ) : null}
          {primaryAction ? (
            <button type="button" className="btn-primary" onClick={primaryAction.onClick}>
              {primaryAction.label}
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

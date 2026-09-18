import { Loader2 } from 'lucide-react';
import type { JSX } from 'react';
import './LoadingState.css';

interface LoadingStateProps {
  label: string;
  variant?: 'page' | 'inline';
}

/**
 * A spinner + label. Used as-is for both "Loading" (fetching a page/section)
 * and "Processing"/"Saving" (an action or background job is in flight) —
 * those states only differ by the label passed in, not the visual.
 */
export function LoadingState({ label, variant = 'page' }: LoadingStateProps): JSX.Element {
  const isPage = variant === 'page';

  return (
    <div className={`loading-state loading-state--${variant}`} role="status" aria-live="polite">
      <Loader2 className="loading-state__spinner" size={isPage ? 24 : 16} aria-hidden="true" />
      <span className={`loading-state__label--${variant}`}>{label}</span>
    </div>
  );
}

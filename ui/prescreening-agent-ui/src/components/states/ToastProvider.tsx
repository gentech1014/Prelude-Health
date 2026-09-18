import { AlertOctagon, AlertTriangle, Bell, CheckCircle2, Info, X } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState, type JSX, type ReactNode } from 'react';
import { ToastContext } from '@/lib/toastContext';
import type { Toast, ToastInput } from '@/types/toast';
import type { Tone } from '@/types/tone';
import './ToastProvider.css';

const DEFAULT_DURATION_MS = 4000;
// How long the exit (slide-out) animation runs before the toast is actually removed.
const EXIT_ANIMATION_MS = 220;

let toastIdCounter = 0;
// Avoids depending on crypto.randomUUID(), which not every test/browser environment implements.
function createToastId(): string {
  toastIdCounter += 1;
  return `toast-${toastIdCounter}-${Date.now()}`;
}

const DEFAULT_ICON: Record<Tone, ReactNode> = {
  neutral: <Bell size={18} aria-hidden="true" />,
  success: <CheckCircle2 size={18} aria-hidden="true" />,
  warning: <AlertTriangle size={18} aria-hidden="true" />,
  error: <AlertOctagon size={18} aria-hidden="true" />,
  info: <Info size={18} aria-hidden="true" />,
};

// One solid accent per tone — drives both the left strip and the countdown bar.
const TONE_STYLES: Record<Tone, { bg: string; border: string; accent: string }> = {
  neutral: {
    bg: 'var(--color-surface)',
    border: 'var(--color-border)',
    accent: 'var(--color-border-strong)',
  },
  success: {
    bg: 'var(--color-success-bg)',
    border: 'var(--color-success-border)',
    accent: 'var(--color-success)',
  },
  warning: {
    bg: 'var(--color-warning-bg)',
    border: 'var(--color-warning-border)',
    accent: 'var(--color-warning)',
  },
  error: {
    bg: 'var(--color-error-bg)',
    border: 'var(--color-error-border)',
    accent: 'var(--color-error)',
  },
  info: {
    bg: 'var(--color-info-bg)',
    border: 'var(--color-info-border)',
    accent: 'var(--color-info)',
  },
};

interface ToastProviderProps {
  children: ReactNode;
}

/** Transient action feedback (Success, Saving, Completed, Cancelled) as stacked top-right toasts. */
export function ToastProvider({ children }: ToastProviderProps): JSX.Element {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [leavingIds, setLeavingIds] = useState<ReadonlySet<string>>(new Set());
  const dismissTimers = useRef(new Map<string, ReturnType<typeof setTimeout>>());
  const removalTimers = useRef(new Map<string, ReturnType<typeof setTimeout>>());

  useEffect(
    () => () => {
      dismissTimers.current.forEach(clearTimeout);
      removalTimers.current.forEach(clearTimeout);
    },
    [],
  );

  const dismissToast = useCallback((id: string) => {
    const dismissTimer = dismissTimers.current.get(id);
    if (dismissTimer) {
      clearTimeout(dismissTimer);
      dismissTimers.current.delete(id);
    }

    setLeavingIds((current) => {
      if (current.has(id)) return current;
      const next = new Set(current);
      next.add(id);
      return next;
    });

    removalTimers.current.set(
      id,
      setTimeout(() => {
        setToasts((current) => current.filter((toast) => toast.id !== id));
        setLeavingIds((current) => {
          const next = new Set(current);
          next.delete(id);
          return next;
        });
        removalTimers.current.delete(id);
      }, EXIT_ANIMATION_MS),
    );
  }, []);

  const showToast = useCallback(
    (input: ToastInput): string => {
      const id = createToastId();
      const tone = input.tone ?? 'info';
      const autoDismissMs = input.durationMs ?? DEFAULT_DURATION_MS;
      const toast: Toast = {
        id,
        icon: input.icon ?? DEFAULT_ICON[tone],
        message: input.message,
        tone,
        autoDismissMs,
      };
      setToasts((current) => [...current, toast]);

      if (autoDismissMs > 0) {
        dismissTimers.current.set(
          id,
          setTimeout(() => dismissToast(id), autoDismissMs),
        );
      }
      return id;
    },
    [dismissToast],
  );

  const value = useMemo(() => ({ showToast, dismissToast }), [showToast, dismissToast]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="toast-stack" aria-live="polite" aria-atomic="false">
        {toasts.map((toast) => {
          const styles = TONE_STYLES[toast.tone];
          const isLeaving = leavingIds.has(toast.id);

          return (
            <div
              key={toast.id}
              role="status"
              className={`toast${isLeaving ? ' toast--leaving' : ''}`}
              style={{ background: styles.bg, border: `1px solid ${styles.border}` }}
            >
              {/* Left strip — the tone's accent color, the first thing you see. */}
              <span className="toast__strip" aria-hidden="true" style={{ background: styles.accent }} />

              <span className="toast__icon" aria-hidden="true" style={{ color: styles.accent }}>
                {toast.icon}
              </span>
              <span className="toast__message">{toast.message}</span>
              <button
                type="button"
                className="toast__dismiss"
                onClick={() => dismissToast(toast.id)}
                aria-label="Dismiss"
              >
                <X size={16} aria-hidden="true" />
              </button>

              {/* Bottom countdown bar — shrinks over the toast's auto-dismiss duration. */}
              {toast.autoDismissMs > 0 && !isLeaving ? (
                <span
                  className="toast__progress"
                  aria-hidden="true"
                  style={{ background: styles.accent, animationDuration: `${toast.autoDismissMs}ms` }}
                />
              ) : null}
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}

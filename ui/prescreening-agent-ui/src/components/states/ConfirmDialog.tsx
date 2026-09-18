import { AlertTriangle, HelpCircle } from 'lucide-react';
import { useEffect, useRef, useState, type JSX, type ReactNode } from 'react';
import './ConfirmDialog.css';

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  description: string;
  confirmLabel: string;
  cancelLabel?: string;
  tone?: 'danger' | 'neutral';
  /** Defaults to a tone-appropriate icon (AlertTriangle for danger, HelpCircle for neutral). */
  icon?: ReactNode;
  onConfirm: () => void;
  onCancel: () => void;
}

const DEFAULT_ICON = {
  danger: <AlertTriangle size={26} aria-hidden="true" />,
  neutral: <HelpCircle size={26} aria-hidden="true" />,
} as const;

// How long the exit (slide-down) animation runs before the dialog unmounts.
const EXIT_ANIMATION_MS = 280;

/**
 * Modal confirmation for a destructive/important action, or for warning about
 * unsaved changes before navigating away — same shape, different copy/tone/icon.
 */
export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  cancelLabel = 'Cancel',
  tone = 'neutral',
  icon,
  onConfirm,
  onCancel,
}: ConfirmDialogProps): JSX.Element | null {
  const confirmButtonRef = useRef<HTMLButtonElement>(null);
  const closeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Stays mounted through the exit animation instead of vanishing the instant `open` flips.
  const [isRendered, setIsRendered] = useState(open);
  const [isClosing, setIsClosing] = useState(false);

  useEffect(() => {
    if (open) {
      if (closeTimerRef.current) {
        clearTimeout(closeTimerRef.current);
        closeTimerRef.current = null;
      }
      setIsRendered(true);
      setIsClosing(false);
      return undefined;
    }

    if (!isRendered) return undefined;

    setIsClosing(true);
    closeTimerRef.current = setTimeout(() => {
      setIsRendered(false);
      setIsClosing(false);
      closeTimerRef.current = null;
    }, EXIT_ANIMATION_MS);

    return () => {
      if (closeTimerRef.current) {
        clearTimeout(closeTimerRef.current);
        closeTimerRef.current = null;
      }
    };
  }, [open, isRendered]);

  useEffect(() => {
    if (open) confirmButtonRef.current?.focus();
  }, [open]);

  useEffect(() => {
    if (!open) return undefined;
    const handleKeyDown = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') onCancel();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [open, onCancel]);

  if (!isRendered) return null;

  const accentColor = tone === 'danger' ? 'var(--color-error)' : 'var(--color-primary)';
  const accentBg = tone === 'danger' ? 'var(--color-error-bg)' : 'var(--color-primary-subtle)';

  return (
    <div
      role="presentation"
      onClick={onCancel}
      className={`confirm-dialog-overlay${isClosing ? ' confirm-dialog-overlay--closing' : ''}`}
    >
      <div
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-dialog-title"
        aria-describedby="confirm-dialog-description"
        onClick={(event) => event.stopPropagation()}
        className={`confirm-dialog-sheet${isClosing ? ' confirm-dialog-sheet--closing' : ''}`}
      >
        <div
          className="confirm-dialog__icon"
          aria-hidden="true"
          style={{ background: accentBg, color: accentColor }}
        >
          {icon ?? DEFAULT_ICON[tone]}
        </div>

        <h2 id="confirm-dialog-title" className="confirm-dialog__title">
          {title}
        </h2>
        <p id="confirm-dialog-description" className="confirm-dialog__description">
          {description}
        </p>

        <div className="confirm-dialog__actions">
          <button type="button" className="btn-secondary" onClick={onCancel}>
            {cancelLabel}
          </button>
          <button
            ref={confirmButtonRef}
            type="button"
            className="btn-primary"
            onClick={onConfirm}
            style={{ background: accentColor }}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

import {
  AlertOctagon,
  AlertTriangle,
  Ban,
  CheckCircle2,
  Inbox,
  Loader2,
  LogIn,
  SearchX,
  ShieldAlert,
  WifiOff,
} from 'lucide-react';
import { useState, type JSX } from 'react';
import {
  ConfirmDialog,
  EmptyState,
  InlineBanner,
  LoadingState,
  SkeletonCard,
  StatusBadge,
} from '@/components/states';
import { useToast } from '@/hooks/useToast';

type StateId =
  | 'loading'
  | 'skeleton'
  | 'empty'
  | 'error'
  | 'partial-error'
  | 'no-results'
  | 'permission-denied'
  | 'unauthorized'
  | 'offline'
  | 'success'
  | 'saving'
  | 'unsaved-changes'
  | 'confirmation'
  | 'processing'
  | 'completed'
  | 'cancelled';

const STATE_OPTIONS: { id: StateId; label: string; hint: string }[] = [
  { id: 'loading', label: 'Loading', hint: 'Data/page is being fetched' },
  { id: 'skeleton', label: 'Skeleton loading', hint: 'Preserve page structure while data loads' },
  { id: 'empty', label: 'Empty state', hint: 'Page works but has no data yet' },
  { id: 'error', label: 'Error state', hint: 'Something failed' },
  { id: 'partial-error', label: 'Partial error', hint: 'Some components loaded, others failed' },
  { id: 'no-results', label: 'No results', hint: 'Search/filter returned nothing' },
  { id: 'permission-denied', label: 'Permission denied', hint: "User doesn't have access" },
  {
    id: 'unauthorized',
    label: 'Unauthorized / session expired',
    hint: 'User needs to log in again',
  },
  { id: 'offline', label: 'Offline / network error', hint: 'Connection unavailable' },
  { id: 'success', label: 'Success state', hint: 'Action completed' },
  { id: 'saving', label: 'Saving', hint: 'Form/action is currently being processed' },
  { id: 'unsaved-changes', label: 'Unsaved changes', hint: 'User is about to leave with changes' },
  {
    id: 'confirmation',
    label: 'Confirmation',
    hint: 'Destructive/important action needs confirmation',
  },
  { id: 'processing', label: 'Processing', hint: 'AI/background operation is running' },
  { id: 'completed', label: 'Completed', hint: 'Background operation finished' },
  { id: 'cancelled', label: 'Cancelled', hint: 'Operation was stopped' },
];

const noop = (): void => {
  /* demo-only action */
};

/** Lets the picker below be picked once and reused instead of re-declared per case. */
const SPINNING_LOADER = (
  <Loader2 size={16} aria-hidden="true" style={{ animation: 'spin 0.8s linear infinite' }} />
);

/**
 * Interactive picker over every product UI state, backed by the same small
 * set of components (EmptyState / InlineBanner / LoadingState / Skeleton /
 * StatusBadge / ConfirmDialog / Toast) real screens will use.
 */
export function StatesGallery(): JSX.Element {
  const [selected, setSelected] = useState<StateId>('loading');
  const [activeDialog, setActiveDialog] = useState<'confirmation' | 'unsaved' | null>(null);
  const { showToast } = useToast();

  const current = STATE_OPTIONS.find((option) => option.id === selected) ?? STATE_OPTIONS[0];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
      <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        <span style={{ fontSize: '0.75rem', color: 'var(--color-text-secondary)' }}>
          Preview a UI state
        </span>
        <select
          value={selected}
          onChange={(event) => setSelected(event.target.value as StateId)}
          style={{
            padding: '0.55rem',
            borderRadius: 'var(--radius-md)',
            border: '1px solid var(--color-border)',
            background: 'var(--color-surface)',
            color: 'var(--color-text)',
          }}
        >
          {STATE_OPTIONS.map((option) => (
            <option key={option.id} value={option.id}>
              {option.label}
            </option>
          ))}
        </select>
      </label>

      <p style={{ margin: 0, fontSize: '0.78rem', color: 'var(--color-text-secondary)' }}>
        {current?.hint}
      </p>

      <div
        style={{
          borderRadius: 'var(--radius-lg)',
          border: '1px dashed var(--color-border)',
          background: 'var(--color-bg)',
          padding: 'var(--space-3)',
          minHeight: 140,
          display: 'flex',
          alignItems: 'center',
        }}
      >
        {renderState(selected, { setActiveDialog, showToast })}
      </div>

      <ConfirmDialog
        open={activeDialog === 'confirmation'}
        title="Cancel this appointment?"
        description="This can't be undone. The patient will be notified immediately."
        confirmLabel="Cancel"
        cancelLabel="Keep"
        tone="danger"
        onConfirm={() => setActiveDialog(null)}
        onCancel={() => setActiveDialog(null)}
      />
      <ConfirmDialog
        open={activeDialog === 'unsaved'}
        title="Unsaved changes"
        description="You have unsaved answers. Leave this screen without saving?"
        confirmLabel="Leave"
        cancelLabel="Stay"
        tone="neutral"
        onConfirm={() => setActiveDialog(null)}
        onCancel={() => setActiveDialog(null)}
      />
    </div>
  );
}

interface RenderContext {
  setActiveDialog: (dialog: 'confirmation' | 'unsaved' | null) => void;
  showToast: ReturnType<typeof useToast>['showToast'];
}

function renderState(id: StateId, ctx: RenderContext): JSX.Element {
  switch (id) {
    case 'loading':
      return <LoadingState label="Loading your appointment…" />;

    case 'skeleton':
      return (
        <div
          style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-2)', width: '100%' }}
        >
          <SkeletonCard />
          <SkeletonCard />
        </div>
      );

    case 'empty':
      return (
        <EmptyState
          icon={<Inbox size={24} aria-hidden="true" />}
          title="No visits yet"
          description="Completed prescreening calls will show up here."
        />
      );

    case 'error':
      return (
        <EmptyState
          icon={<AlertOctagon size={24} aria-hidden="true" />}
          tone="error"
          title="Something went wrong"
          description="We couldn't load this screen. Please try again."
          primaryAction={{ label: 'Retry', onClick: noop }}
        />
      );

    case 'partial-error':
      return (
        <div
          style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-2)', width: '100%' }}
        >
          <SkeletonCard />
          <InlineBanner
            icon={<AlertTriangle size={16} aria-hidden="true" />}
            tone="error"
            message="Recent test results failed to load."
            action={{ label: 'Retry', onClick: noop }}
          />
        </div>
      );

    case 'no-results':
      return (
        <EmptyState
          icon={<SearchX size={24} aria-hidden="true" />}
          title="No matches"
          description="Try a different search term or clear your filters."
          primaryAction={{ label: 'Clear filters', onClick: noop }}
        />
      );

    case 'permission-denied':
      return (
        <EmptyState
          icon={<ShieldAlert size={24} aria-hidden="true" />}
          tone="warning"
          title="You don't have access"
          description="Ask an administrator to grant you access to this record."
        />
      );

    case 'unauthorized':
      return (
        <EmptyState
          icon={<LogIn size={24} aria-hidden="true" />}
          tone="error"
          title="Session expired"
          description="For your security, please sign in again to continue."
          primaryAction={{ label: 'Sign in', onClick: noop }}
        />
      );

    case 'offline':
      return (
        <EmptyState
          icon={<WifiOff size={24} aria-hidden="true" />}
          tone="warning"
          title="You're offline"
          description="Check your connection. We'll keep trying automatically."
          primaryAction={{ label: 'Retry now', onClick: noop }}
        />
      );

    case 'success':
      return (
        <ActionPreview
          label="Confirm appointment"
          onClick={() =>
            ctx.showToast({
              icon: <CheckCircle2 size={16} aria-hidden="true" />,
              tone: 'success',
              message: 'Appointment confirmed.',
            })
          }
        />
      );

    case 'saving':
      return (
        <ActionPreview
          label="Save answers"
          onClick={() =>
            ctx.showToast({ icon: SPINNING_LOADER, tone: 'info', message: 'Saving your answers…' })
          }
        />
      );

    case 'unsaved-changes':
      return (
        <ActionPreview
          label="Try to leave this screen"
          onClick={() => ctx.setActiveDialog('unsaved')}
        />
      );

    case 'confirmation':
      return (
        <ActionPreview
          label="Cancel appointment"
          tone="danger"
          onClick={() => ctx.setActiveDialog('confirmation')}
        />
      );

    case 'processing':
      return <LoadingState variant="inline" label="Processing your responses…" />;

    case 'completed':
      return (
        <StatusBadge
          icon={<CheckCircle2 size={14} aria-hidden="true" />}
          tone="success"
          label="Completed"
        />
      );

    case 'cancelled':
      return (
        <StatusBadge icon={<Ban size={14} aria-hidden="true" />} tone="neutral" label="Cancelled" />
      );

    default:
      return <LoadingState label="Loading…" />;
  }
}

function ActionPreview({
  label,
  tone = 'primary',
  onClick,
}: {
  label: string;
  tone?: 'primary' | 'danger';
  onClick: () => void;
}): JSX.Element {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        padding: '0.6rem 1rem',
        borderRadius: 'var(--radius-md)',
        border: '1px solid transparent',
        background: tone === 'danger' ? 'var(--color-error)' : 'var(--color-primary)',
        color: 'var(--color-on-primary)',
      }}
    >
      {label}
    </button>
  );
}

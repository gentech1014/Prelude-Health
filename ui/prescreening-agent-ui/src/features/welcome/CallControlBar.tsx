import { Mic, MicOff, Pause, PhoneOff, Play } from 'lucide-react';
import type { JSX, ReactNode } from 'react';

interface CallControlBarProps {
  isMuted: boolean;
  isOnHold: boolean;
  /** False before the call connects and after it ends — nothing here does anything then. */
  isLive: boolean;
  onToggleMute: () => void;
  onToggleHold: () => void;
  /** Ending a call is significant — the bar only asks; the caller decides how to confirm. */
  onRequestEndCall: () => void;
}

// Exported so the page hosting this floating bar can reserve matching scroll
// padding — otherwise the last bit of content would sit hidden underneath it.
// Sized generously (not just this bar's own ~113px) to leave a clear empty
// band above it as well.
export const CALL_CONTROL_BAR_CLEARANCE = 'calc(220px + env(safe-area-inset-bottom, 0px))';

/**
 * In-call controls, floating above the page content (never docked to the
 * viewport edge) so it reads as an overlay, not part of the scrollable flow.
 *
 * Every control is driven by the call itself, not by local state: mute in
 * particular is a privacy control, and a button that only looks pressed
 * while audio keeps flowing is worse than no button.
 */
export function CallControlBar({
  isMuted,
  isOnHold,
  isLive,
  onToggleMute,
  onToggleHold,
  onRequestEndCall,
}: CallControlBarProps): JSX.Element {
  return (
    <div
      style={{
        position: 'fixed',
        left: 'var(--space-4)',
        right: 'var(--space-4)',
        bottom: 'calc(var(--space-4) + env(safe-area-inset-bottom, 0px))',
        zIndex: 40,
        display: 'flex',
        alignItems: 'flex-start',
        justifyContent: 'space-evenly',
        padding: 'var(--space-3) var(--space-3)',
        borderRadius: 'var(--radius-lg)',
        // Frosted, not opaque — the shared app background scenery shows through
        // instead of the bar reading as a disconnected white panel on top of it.
        background: 'color-mix(in srgb, var(--color-bg) 80%, transparent)',
        backdropFilter: 'blur(16px)',
        WebkitBackdropFilter: 'blur(16px)',
        boxShadow: 'var(--shadow-lg)',
      }}
    >
      <ControlButton
        label="Hold"
        pressed={isOnHold}
        disabled={!isLive}
        onClick={onToggleHold}
        icon={
          isOnHold ? <Play aria-hidden="true" size={22} /> : <Pause aria-hidden="true" size={22} />
        }
      />

      <ControlButton
        label="Mute"
        pressed={isMuted}
        disabled={!isLive}
        onClick={onToggleMute}
        icon={
          isMuted ? <MicOff aria-hidden="true" size={22} /> : <Mic aria-hidden="true" size={22} />
        }
      />

      <ControlButton
        label="End call"
        variant="danger"
        disabled={!isLive}
        onClick={onRequestEndCall}
        icon={<PhoneOff aria-hidden="true" size={24} />}
      />
    </div>
  );
}

interface ControlButtonProps {
  label: string;
  icon: ReactNode;
  onClick: () => void;
  pressed?: boolean;
  disabled?: boolean;
  variant?: 'default' | 'danger';
}

function ControlButton({
  label,
  icon,
  onClick,
  pressed,
  disabled = false,
  variant = 'default',
}: ControlButtonProps): JSX.Element {
  const isDanger = variant === 'danger';
  const size = isDanger ? 64 : 52;
  const isToggle = pressed !== undefined;

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        gap: 'var(--space-2)',
      }}
    >
      <button
        type="button"
        onClick={onClick}
        disabled={disabled}
        aria-label={label}
        aria-pressed={isToggle ? pressed : undefined}
        style={{
          width: size,
          height: size,
          flexShrink: 0,
          borderRadius: '50%',
          border: 'none',
          display: 'grid',
          placeContent: 'center',
          opacity: disabled ? 0.45 : 1,
          background: isDanger
            ? 'var(--color-error)'
            : pressed
              ? 'var(--color-primary-subtle)'
              : 'var(--color-surface-alt)',
          color: isDanger
            ? 'var(--color-on-primary)'
            : pressed
              ? 'var(--color-primary)'
              : 'var(--color-text)',
          boxShadow: isDanger ? 'var(--shadow-md)' : 'none',
        }}
      >
        {icon}
      </button>
      <span style={{ fontSize: '0.8rem', color: 'var(--color-text-secondary)' }}>{label}</span>
    </div>
  );
}

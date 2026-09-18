import { useState, type JSX } from 'react';
import { ConfirmDialog } from '@/components/states';
import { CallControlBar } from '@/features/welcome/CallControlBar';
import { useIntakeCall } from '@/features/prescreening-session/useIntakeCall';

/**
 * Drop into any screen during an active call: the floating controls plus the
 * end-call confirmation flow, so every screen hangs up the same way instead
 * of each page re-implementing its own copy of a safety-relevant flow.
 *
 * Ending does not navigate anywhere. The call's own `call_ended` frame is
 * what moves the patient on, so the screen and the session agree about
 * whether the call is over — a local redirect would have the UI say the
 * call ended before the server knew it had.
 */
export function CallSessionControls(): JSX.Element {
  const { status, isMuted, isOnHold, setMuted, setOnHold, hangUp } = useIntakeCall();
  const [isEndCallConfirmOpen, setIsEndCallConfirmOpen] = useState(false);

  const handleConfirmEndCall = (): void => {
    setIsEndCallConfirmOpen(false);
    hangUp();
  };

  return (
    <>
      <CallControlBar
        isMuted={isMuted}
        isOnHold={isOnHold}
        isLive={status === 'live'}
        onToggleMute={() => setMuted(!isMuted)}
        onToggleHold={() => setOnHold(!isOnHold)}
        onRequestEndCall={() => setIsEndCallConfirmOpen(true)}
      />

      <ConfirmDialog
        open={isEndCallConfirmOpen}
        title="End this call?"
        description="Your pre-visit screening is not finished. What you have shared so far is kept, and reopening your link picks up where you left off."
        confirmLabel="End call"
        cancelLabel="Stay on call"
        tone="danger"
        onConfirm={handleConfirmEndCall}
        onCancel={() => setIsEndCallConfirmOpen(false)}
      />
    </>
  );
}

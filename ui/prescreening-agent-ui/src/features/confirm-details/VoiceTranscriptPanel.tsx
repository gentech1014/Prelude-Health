import { useEffect, useRef, type JSX } from 'react';
import { AudioWaveform } from '@/components/AudioWaveform';
import { useIntakeCall } from '@/features/prescreening-session/useIntakeCall';
import { usePrescreeningSession } from '@/features/prescreening-session/usePrescreeningSession';
import '@/features/confirm-details/VoiceTranscriptPanel.css';

/**
 * The live caption for the call: one line, showing whichever of the
 * assistant or the patient is the current speaker, plus a waveform of
 * whichever of them is actually speaking.
 *
 * There is only ever one line on screen — the newer speaker replaces the
 * older one rather than the two stacking — because showing both at once
 * left a stale line from a finished turn sitting next to a live one.
 *
 * It is real transcription from the model, streamed in as it arrives — a
 * partial line is shown as it changes rather than held back, because a
 * caption that appears only after a sentence finishes is no use to someone
 * relying on it.
 *
 * The assistant's name and the waveform both come from elsewhere: the name
 * from the session context, the waveform from the one shared
 * `AudioWaveform` component. Neither is drawn here.
 */
export function VoiceTranscriptPanel(): JSX.Element {
  const { session } = usePrescreeningSession();
  const { status, activeCaption, isAgentSpeaking, micStream, agentStream, isMuted, isOnHold } =
    useIntakeCall();

  const assistantName = session?.assistant.name ?? '';
  const isLive = status === 'live';
  const isListening = isLive && !isAgentSpeaking && !isMuted && !isOnHold;
  const isPatientTurn = activeCaption?.role === 'patient';
  const speakerLabel = isPatientTurn ? 'You' : assistantName;
  const captionText = activeCaption?.text ?? statusCopy(status);
  // Keyed by turn, not just role: remounting the line on every new
  // utterance is what lets the CSS entrance animation replay, instead of
  // the previous speaker's words sitting there while new text overwrites
  // them in place.
  const captionKey = activeCaption?.turnId ?? `status-${status}`;

  // A caption is now the whole turn, which grows as it is spoken, so the
  // newest words have to be the ones in view — a box that scrolls only
  // when the patient drags it would leave them reading the opening of a
  // sentence that has already finished.
  const captionRef = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    if (captionRef.current) captionRef.current.scrollTop = captionRef.current.scrollHeight;
  }, [captionText]);

  return (
    <div className="voice-transcript-panel">
      <p
        key={`speaker-${captionKey}`}
        className={
          isPatientTurn
            ? 'voice-transcript-panel__speaker voice-transcript-panel__speaker--you'
            : 'voice-transcript-panel__speaker'
        }
      >
        {speakerLabel}
      </p>
      <p
        key={captionKey}
        ref={captionRef}
        className={
          isPatientTurn
            ? 'voice-transcript-panel__text voice-transcript-panel__text--you'
            : 'voice-transcript-panel__text'
        }
      >
        {captionText}
      </p>

      <div className="voice-transcript-panel__status-row">
        <div className="voice-transcript-panel__waveform-block">
          {/* One waveform, following whoever holds the turn: two side by
              side would make the patient watch the wrong one. */}
          {isAgentSpeaking ? (
            <AudioWaveform source="agent" stream={agentStream} size="lg" barCount={12} />
          ) : (
            <AudioWaveform
              source="user"
              stream={isListening ? micStream : null}
              active={isListening}
              size="lg"
              barCount={12}
            />
          )}
          <span className="voice-transcript-panel__listening">
            {turnCopy({ isLive, isAgentSpeaking, isMuted, isOnHold })}
          </span>
        </div>
      </div>
    </div>
  );
}

/** What to show in place of a caption before the assistant has said anything. */
function statusCopy(status: ReturnType<typeof useIntakeCall>['status']): string {
  switch (status) {
    case 'idle':
    case 'starting':
      return 'Starting your call…';
    case 'connecting':
      return 'Connecting to your assistant…';
    case 'reconnecting':
      return 'Reconnecting. Nothing you have shared has been lost.';
    case 'ended':
      return 'This call has finished.';
    case 'failed':
      return 'This call has stopped.';
    default:
      return 'Listening…';
  }
}

function turnCopy(state: {
  isLive: boolean;
  isAgentSpeaking: boolean;
  isMuted: boolean;
  isOnHold: boolean;
}): string {
  if (!state.isLive) return 'Not connected';
  if (state.isOnHold) return 'On hold';
  if (state.isMuted) return 'Your microphone is off';
  if (state.isAgentSpeaking) return 'Speaking…';
  return 'Listening…';
}

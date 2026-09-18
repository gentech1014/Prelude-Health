import { Volume2 } from 'lucide-react';
import type { JSX } from 'react';
import '@/features/welcome/HealthJourneyScenery.css';
import '@/features/welcome/Welcome.css';

interface HealthJourneySceneryProps {
  /**
   * True only when the browser is refusing to play audio until the patient
   * interacts with the page. The tap exists for that case alone.
   */
  needsAudioUnlock: boolean;
  onUnlockAudio: () => void;
}

/**
 * The live indicator on the opening screen, over the app's shared
 * background scenery (rendered once, behind every screen — see
 * AppBackgroundScenery) rather than painting its own.
 *
 * There is no Start button. A patient who has opened their prescreening
 * link has already said they want to begin, and the call starts on its
 * own. The one tap that can still appear is the browser's, not ours:
 * autoplay policy blocks playback outside a user gesture on most
 * browsers, so this offers a tap to the patients who actually need one and
 * nothing to everyone else.
 */
export function HealthJourneyScenery({
  needsAudioUnlock,
  onUnlockAudio,
}: HealthJourneySceneryProps): JSX.Element | null {
  if (needsAudioUnlock) {
    return (
      <div className="health-journey-scenery">
        <button type="button" className="welcome__voice-prompt" onClick={onUnlockAudio}>
          <Volume2 size={18} aria-hidden="true" />
          Tap to hear your assistant
        </button>
      </div>
    );
  }

  return null;
}

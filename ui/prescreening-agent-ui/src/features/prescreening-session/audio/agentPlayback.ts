import { decodeBase64Pcm16, pcm16ToFloat32 } from '@/features/prescreening-session/audio/pcm';

/**
 * Plays the agent's speech, in order, and exposes it as a `MediaStream` so
 * the shared waveform can visualize what the patient is actually hearing.
 *
 * Chunks are scheduled against a running clock rather than played on
 * arrival: `start()` with no argument fires immediately, so back-to-back
 * chunks would overlap and the agent would sound like several people
 * talking at once. Each chunk starts where the previous one ends.
 */

/**
 * Small lead before the first chunk of an utterance.
 *
 * Scheduling at exactly `currentTime` means any chunk that arrives a
 * fraction late is already in the past and gets dropped, which is heard as
 * a clipped first syllable.
 */
const SCHEDULING_LEAD_SECONDS = 0.08;

export interface AgentPlayback {
  /** What the patient is hearing, for the waveform. */
  readonly stream: MediaStream;
  /**
   * Whether the browser is currently refusing to play.
   *
   * An `AudioContext` created outside a user gesture starts suspended and
   * silently swallows everything, so this is the difference between "the
   * assistant is quiet" and "the assistant cannot be heard".
   */
  readonly isBlocked: () => boolean;
  /** Try to start playing. Only succeeds from inside a real user gesture. */
  resume: () => Promise<void>;
  enqueue: (base64Audio: string, sampleRate: number) => void;
  /** Drop everything queued — the patient interrupted, so the rest is stale. */
  flush: () => void;
  /**
   * How much longer, in milliseconds, the audio already queued will take to
   * finish playing. Zero once the last scheduled chunk has ended.
   *
   * The model calls `navigate_to_screen` the instant it is done generating
   * an utterance, which is not the same moment the patient finishes
   * hearing it — this is what lets a caller hold a screen change back
   * until the words it was about are actually done playing.
   */
  remainingPlaybackMs: () => number;
  setMuted: (muted: boolean) => void;
  close: () => Promise<void>;
}

/**
 * Creates the playback pipeline, or returns null where Web Audio is
 * unavailable (an old browser, a test environment). Null is a real
 * outcome, not a failure: the call still works with captions and typed
 * answers, and the caller says so rather than dying.
 */
export function createAgentPlayback(): AgentPlayback | null {
  if (typeof AudioContext === 'undefined') return null;

  const audioContext = new AudioContext();
  const gain = audioContext.createGain();
  const streamDestination = audioContext.createMediaStreamDestination();
  gain.connect(audioContext.destination);
  gain.connect(streamDestination);

  let nextStartTime = 0;
  let scheduled: AudioBufferSourceNode[] = [];

  const forget = (source: AudioBufferSourceNode): void => {
    scheduled = scheduled.filter((queued) => queued !== source);
  };

  return {
    stream: streamDestination.stream,

    isBlocked: () => audioContext.state === 'suspended',

    resume: async () => {
      await audioContext.resume().catch(() => undefined);
    },

    enqueue: (base64Audio, sampleRate) => {
      const samples = decodeBase64Pcm16(base64Audio);
      if (samples.length === 0) return;

      // The buffer is created at the frame's own rate and left for the
      // graph to resample. Forcing it to the context's rate here would
      // change the pitch of the agent's voice.
      const buffer = audioContext.createBuffer(1, samples.length, sampleRate);
      buffer.getChannelData(0).set(pcm16ToFloat32(samples));

      const source = audioContext.createBufferSource();
      source.buffer = buffer;
      source.connect(gain);
      source.onended = () => forget(source);

      const startAt = Math.max(audioContext.currentTime + SCHEDULING_LEAD_SECONDS, nextStartTime);
      source.start(startAt);
      nextStartTime = startAt + buffer.duration;
      scheduled.push(source);

      // A context suspended by the browser's autoplay policy silently
      // swallows every chunk, so the agent appears mute with no error.
      if (audioContext.state === 'suspended') void audioContext.resume();
    },

    remainingPlaybackMs: () => Math.max(0, (nextStartTime - audioContext.currentTime) * 1000),

    flush: () => {
      for (const source of scheduled) {
        source.onended = null;
        try {
          source.stop();
        } catch {
          // Already finished; nothing to stop.
        }
      }
      scheduled = [];
      nextStartTime = 0;
    },

    setMuted: (muted) => {
      gain.gain.value = muted ? 0 : 1;
    },

    close: async () => {
      for (const source of scheduled) {
        source.onended = null;
        try {
          source.stop();
        } catch {
          // Already finished; nothing to stop.
        }
      }
      scheduled = [];
      gain.disconnect();
      streamDestination.disconnect();
      await audioContext.close().catch(() => undefined);
    },
  };
}

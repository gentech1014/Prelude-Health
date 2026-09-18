import { useEffect, useRef, type JSX } from 'react';
import '@/components/AudioWaveform.css';

export type AudioWaveformSource = 'agent' | 'user';
export type AudioWaveformSize = 'sm' | 'md' | 'lg';

interface AudioWaveformProps {
  /** Whose audio this represents — drives the bar color (agent: AI accent, user: primary). */
  source: AudioWaveformSource;
  /** A live audio stream to visualize in real time. Omit to show a placeholder until voice/session integration provides one. */
  stream?: MediaStream | null;
  /** Whether audio is currently expected to be flowing — pauses the idle placeholder when false. Ignored once a real `stream` is provided. */
  active?: boolean;
  barCount?: number;
  size?: AudioWaveformSize;
}

const DEFAULT_BAR_COUNT = 5;
const FFT_SIZE = 64;
const BAR_DELAY_STEP_S = 0.1;

const SIZE_HEIGHT_PX: Record<AudioWaveformSize, number> = {
  sm: 20,
  md: 28,
  lg: 40,
};

/**
 * The app's single audio waveform visualizer. Pass a real `stream` (the
 * agent's audio or the patient's mic) once voice/session integration exists
 * and it analyzes that stream live via the Web Audio API; without one it
 * renders a calm idle placeholder so the UI still reads as "live" today.
 * Every screen that shows a waveform should use this instead of drawing its
 * own bars.
 */
export function AudioWaveform({
  source,
  stream = null,
  active = true,
  barCount = DEFAULT_BAR_COUNT,
  size = 'md',
}: AudioWaveformProps): JSX.Element {
  const barsRef = useRef<(HTMLSpanElement | null)[]>([]);

  useEffect(() => {
    if (!stream) {
      return undefined;
    }

    const audioContext = new AudioContext();
    const analyser = audioContext.createAnalyser();
    analyser.fftSize = FFT_SIZE;
    const streamSource = audioContext.createMediaStreamSource(stream);
    streamSource.connect(analyser);
    const frequencyData = new Uint8Array(analyser.frequencyBinCount);

    let animationFrame: number;
    const sampleAudio = (): void => {
      analyser.getByteFrequencyData(frequencyData);
      const chunkSize = Math.max(1, Math.floor(frequencyData.length / barCount));

      for (let index = 0; index < barCount; index++) {
        const start = index * chunkSize;
        let sum = 0;
        for (let offset = 0; offset < chunkSize; offset += 1) {
          sum += frequencyData[start + offset] ?? 0;
        }
        const level = Math.min(1, sum / chunkSize / 255);

        const barElement = barsRef.current[index];
        if (barElement) {
          barElement.style.height = `${Math.max(15, level * 100)}%`;
        }
      }

      animationFrame = requestAnimationFrame(sampleAudio);
    };
    sampleAudio();

    return () => {
      cancelAnimationFrame(animationFrame);
      streamSource.disconnect();
      analyser.disconnect();
      void audioContext.close();
    };
  }, [stream, barCount]);

  return (
    <span
      className={`audio-waveform audio-waveform--${source}`}
      aria-hidden="true"
      style={{ height: SIZE_HEIGHT_PX[size] }}
    >
      {Array.from({ length: barCount }, (_, index) => (
        <span
          key={index}
          ref={(el) => {
            barsRef.current[index] = el;
          }}
          className={
            stream
              ? 'audio-waveform__bar'
              : `audio-waveform__bar audio-waveform__bar--${active ? 'idle' : 'static'}`
          }
          style={!stream && active ? { animationDelay: `${index * BAR_DELAY_STEP_S}s` } : undefined}
        />
      ))}
    </span>
  );
}

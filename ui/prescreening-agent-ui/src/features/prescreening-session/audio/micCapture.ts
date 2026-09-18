import { encodePcm16ToBase64 } from '@/features/prescreening-session/audio/pcm';

/**
 * The patient's microphone, resampled to the rate the agent expects and
 * delivered as base64 PCM16 frames.
 *
 * Resampling happens inside the AudioWorklet rather than by asking for an
 * `AudioContext({ sampleRate: 16000 })`: browsers may quietly ignore that
 * request, and a context running at 48 kHz whose frames are labelled
 * 16 kHz sends speech to the model at a third of its real speed. The
 * worklet reads the context's actual `sampleRate` and converts from it, so
 * the frame rate is correct whatever the hardware does.
 */

/** PCM16 mono at this rate, in both directions — the backend publishes it on connect. */
const TARGET_SAMPLE_RATE = 16_000;

/** ~32 ms per frame at 16 kHz: small enough to keep turn detection responsive. */
const FRAME_SAMPLES = 512;

const WORKLET_NAME = 'intake-pcm-frame';

/**
 * Loaded from a Blob URL rather than a file in `public/`: an AudioWorklet
 * module must be fetched by URL, and a public-directory asset would be a
 * second deployable that can go missing independently of the bundle.
 */
const WORKLET_SOURCE = `
class IntakePcmFrameProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._ratio = sampleRate / ${TARGET_SAMPLE_RATE};
    this._position = 0;
    this._frame = new Int16Array(${FRAME_SAMPLES});
    this._filled = 0;
    this._tail = 0;
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel || channel.length === 0) return true;

    // Linear interpolation across the block boundary: _position carries the
    // fractional read offset, and _tail keeps the previous block's last
    // sample, so no click is introduced every 128 frames.
    while (this._position < channel.length) {
      const index = Math.floor(this._position);
      const fraction = this._position - index;
      const current = index === 0 ? this._tail : channel[index - 1];
      const next = channel[index];
      const value = current + (next - current) * fraction;
      const clamped = Math.max(-1, Math.min(1, value));
      this._frame[this._filled++] = clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;

      if (this._filled === ${FRAME_SAMPLES}) {
        const copy = this._frame.slice(0);
        this.port.postMessage(copy.buffer, [copy.buffer]);
        this._filled = 0;
      }
      this._position += this._ratio;
    }

    this._position -= channel.length;
    this._tail = channel[channel.length - 1];
    return true;
  }
}
registerProcessor('${WORKLET_NAME}', IntakePcmFrameProcessor);
`;

export interface MicCapture {
  /** The raw mic stream, for the waveform to visualize. */
  readonly stream: MediaStream;
  /** Stop delivering frames without dropping the mic permission or the stream. */
  setMuted: (muted: boolean) => void;
  close: () => Promise<void>;
}

export type MicCaptureFailure = 'denied' | 'unsupported' | 'failed';

export class MicCaptureError extends Error {
  readonly reason: MicCaptureFailure;

  constructor(reason: MicCaptureFailure, message: string) {
    super(message);
    this.name = 'MicCaptureError';
    this.reason = reason;
  }
}

/**
 * Opens the microphone and starts delivering frames.
 *
 * Throws `MicCaptureError` with a `reason` the caller can turn into copy —
 * a patient who declined the permission prompt needs different words from
 * one on a browser that cannot do this at all.
 */
export async function startMicCapture(onFrame: (base64: string) => void): Promise<MicCapture> {
  if (typeof AudioContext === 'undefined' || !navigator.mediaDevices?.getUserMedia) {
    throw new MicCaptureError(
      'unsupported',
      'This browser cannot capture audio. Use the typed answers instead.',
    );
  }

  let stream: MediaStream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });
  } catch (cause) {
    const denied = cause instanceof DOMException && cause.name === 'NotAllowedError';
    throw new MicCaptureError(
      denied ? 'denied' : 'failed',
      denied
        ? 'Microphone access was blocked. Allow it, or type your answers instead.'
        : 'We could not reach your microphone. Type your answers instead.',
    );
  }

  const audioContext = new AudioContext();
  let muted = false;

  const teardown = async (): Promise<void> => {
    for (const track of stream.getTracks()) track.stop();
    await audioContext.close().catch(() => undefined);
  };

  let workletUrl: string | null = null;
  try {
    workletUrl = URL.createObjectURL(new Blob([WORKLET_SOURCE], { type: 'text/javascript' }));
    await audioContext.audioWorklet.addModule(workletUrl);
  } catch {
    await teardown();
    throw new MicCaptureError(
      'unsupported',
      'This browser cannot capture audio. Use the typed answers instead.',
    );
  } finally {
    if (workletUrl) URL.revokeObjectURL(workletUrl);
  }

  const source = audioContext.createMediaStreamSource(stream);
  const worklet = new AudioWorkletNode(audioContext, WORKLET_NAME);
  worklet.port.onmessage = (event: MessageEvent<ArrayBuffer>) => {
    if (muted) return;
    onFrame(encodePcm16ToBase64(new Int16Array(event.data)));
  };

  // Connected to a zero-gain sink, not straight to `destination`: some
  // browsers stop pulling from a worklet with no downstream node, and
  // routing it to the speakers would echo the patient back at themselves.
  const silence = audioContext.createGain();
  silence.gain.value = 0;
  source.connect(worklet);
  worklet.connect(silence);
  silence.connect(audioContext.destination);

  // A context can start suspended when it was created outside a user
  // gesture; without this the worklet never runs and no frame is ever sent.
  if (audioContext.state === 'suspended') await audioContext.resume();

  return {
    stream,
    setMuted: (next) => {
      muted = next;
    },
    close: async () => {
      worklet.port.onmessage = null;
      worklet.disconnect();
      source.disconnect();
      silence.disconnect();
      await teardown();
    },
  };
}

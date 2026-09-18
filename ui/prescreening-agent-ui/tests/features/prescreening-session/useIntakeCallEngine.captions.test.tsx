import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { AgentPlayback } from '@/features/prescreening-session/audio/agentPlayback';
import { createAgentPlayback } from '@/features/prescreening-session/audio/agentPlayback';
import { useIntakeCallEngine } from '@/features/prescreening-session/useIntakeCallEngine';
import type * as PrescreeningSessionService from '@/services/prescreeningSessionService';
import { TEST_SESSION_ID } from '../../sessionFixture';

vi.mock('@/services/prescreeningSessionService', async (importOriginal) => ({
  ...(await importOriginal<typeof PrescreeningSessionService>()),
  requestWsTicket: vi.fn(() => Promise.resolve('test-ticket')),
}));

vi.mock('@/features/prescreening-session/audio/agentPlayback', () => ({
  createAgentPlayback: vi.fn(),
}));

/**
 * A caption belongs to the moment the words are *heard*, not the moment the
 * model produced them.
 *
 * Nova Sonic streams a whole turn in a second or two and the browser takes
 * twenty to play it, so every caption used to run a turn ahead of the
 * speech: the patient was still listening to the greeting when the consent
 * request appeared over the top of it, which is what a patient reported as
 * "it started talking about the next page before finishing the welcome".
 * The fix is the same measurement the screen change already used — how much
 * audio is still queued — so these pin it against a playback double whose
 * queue the test controls.
 */
class FakeSocket {
  static instances: FakeSocket[] = [];

  readyState = 1;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: ((event: { code: number; reason: string }) => void) | null = null;
  onerror: (() => void) | null = null;

  constructor() {
    FakeSocket.instances.push(this);
  }

  send(): void {
    // Acks and control frames; nothing here asserts on them.
  }

  close(): void {
    this.readyState = 3;
    this.onclose?.({ code: 1006, reason: 'dropped' });
  }

  deliver(frame: unknown): void {
    this.onmessage?.({ data: JSON.stringify(frame) });
  }
}

/** A playback whose remaining queue the test sets directly. */
function fakePlayback(): AgentPlayback & { queuedMs: number; blocked: boolean } {
  const playback = {
    queuedMs: 0,
    blocked: false,
    stream: null as unknown as MediaStream,
    isBlocked: () => playback.blocked,
    resume: () => Promise.resolve(),
    enqueue: vi.fn(),
    flush: vi.fn(() => {
      playback.queuedMs = 0;
    }),
    remainingPlaybackMs: () => playback.queuedMs,
    setMuted: vi.fn(),
    close: () => Promise.resolve(),
  };
  return playback;
}

const connectedFrame = (screen: string): unknown => ({
  type: 'connected',
  protocol_version: 4,
  screen,
  prefill: {},
  symptoms: { current: null, answers: [], answered_count: 0 },
  audio: { sample_rate: 16_000, channels: 1, format: 'pcm16' },
});

const captionFrame = (role: string, text: string, isFinal: boolean): unknown => ({
  type: 'transcript',
  role,
  text,
  is_final: isFinal,
});

async function openConnection(expectedCount: number): Promise<FakeSocket> {
  await waitFor(() => expect(FakeSocket.instances).toHaveLength(expectedCount), {
    timeout: 4_000,
  });
  const socket = FakeSocket.instances.at(-1);
  if (socket === undefined) throw new Error('no socket was opened');
  act(() => {
    socket.onopen?.();
  });
  return socket;
}

describe('captions follow the audio, not the model', () => {
  let playback: ReturnType<typeof fakePlayback>;

  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    FakeSocket.instances = [];
    vi.stubGlobal('WebSocket', FakeSocket);
    playback = fakePlayback();
    vi.mocked(createAgentPlayback).mockReturnValue(playback);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  function renderEngine(): {
    result: { current: { activeCaption: { text: string; role: string } | null } };
  } {
    return renderHook(() => useIntakeCallEngine({ sessionId: TEST_SESSION_ID, canStart: true }));
  }

  it('shows the first turn straight away, because nothing is queued ahead of it', async () => {
    const { result } = renderEngine();
    const socket = await openConnection(1);

    act(() => socket.deliver(connectedFrame('welcome')));
    act(() => socket.deliver(captionFrame('agent', 'Hello, this is the clinic.', false)));

    expect(result.current.activeCaption?.text).toBe('Hello, this is the clinic.');
  });

  it('holds the next turn until the audio before it has played', async () => {
    // The reported bug. The greeting is still being spoken when the model
    // has already produced the consent request, and printing it there tells
    // the patient the assistant has moved on while it plainly has not.
    const { result } = renderEngine();
    const socket = await openConnection(1);

    act(() => socket.deliver(connectedFrame('welcome')));
    act(() => socket.deliver(captionFrame('agent', 'Hello, this is the clinic.', true)));

    playback.queuedMs = 12_000;
    act(() => socket.deliver(captionFrame('agent', 'Please check your details.', false)));

    expect(result.current.activeCaption?.text).toBe('Hello, this is the clinic.');

    // The held line keeps growing while it waits, so what finally appears is
    // the whole turn rather than the fragment that arrived first.
    act(() => socket.deliver(captionFrame('agent', 'Please check your details are right.', true)));
    expect(result.current.activeCaption?.text).toBe('Hello, this is the clinic.');

    act(() => {
      vi.advanceTimersByTime(12_000);
    });

    expect(result.current.activeCaption?.text).toBe('Please check your details are right.');
  });

  it('never holds the patient back — they are already speaking', async () => {
    const { result } = renderEngine();
    const socket = await openConnection(1);

    act(() => socket.deliver(connectedFrame('welcome')));
    act(() => socket.deliver(captionFrame('agent', 'Hello, this is the clinic.', true)));

    playback.queuedMs = 12_000;
    act(() => socket.deliver(captionFrame('patient', 'Yes, that is me.', false)));

    expect(result.current.activeCaption?.text).toBe('Yes, that is me.');
    expect(result.current.activeCaption?.role).toBe('patient');
  });

  it('holds nothing while the browser is refusing to play audio', async () => {
    // A suspended AudioContext never advances its clock, so its queue only
    // ever grows. Holding against it would strand every caption — on the
    // one patient who has nothing but captions to follow the call with.
    const { result } = renderEngine();
    const socket = await openConnection(1);

    act(() => socket.deliver(connectedFrame('welcome')));
    act(() => socket.deliver(captionFrame('agent', 'Hello, this is the clinic.', true)));

    playback.queuedMs = 12_000;
    playback.blocked = true;
    act(() => socket.deliver(captionFrame('agent', 'Please check your details.', false)));

    expect(result.current.activeCaption?.text).toBe('Please check your details.');
  });

  it('drops a held caption when the patient interrupts, because it was never heard', async () => {
    const { result } = renderEngine();
    const socket = await openConnection(1);

    act(() => socket.deliver(connectedFrame('welcome')));
    act(() => socket.deliver(captionFrame('agent', 'Hello, this is the clinic.', true)));

    playback.queuedMs = 12_000;
    act(() => socket.deliver(captionFrame('agent', 'Please check your details.', true)));
    act(() => socket.deliver({ type: 'interrupted' }));

    act(() => {
      vi.advanceTimersByTime(12_000);
    });

    expect(result.current.activeCaption?.text).toBe('Hello, this is the clinic.');
  });
});

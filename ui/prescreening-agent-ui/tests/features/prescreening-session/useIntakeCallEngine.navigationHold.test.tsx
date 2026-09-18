import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useIntakeCallEngine } from '@/features/prescreening-session/useIntakeCallEngine';
import type * as PrescreeningSessionService from '@/services/prescreeningSessionService';
import { TEST_SESSION_ID } from '../../sessionFixture';

/**
 * How long a screen change waits for the speech it follows.
 *
 * The engine measures that against the playback queue rather than timing
 * anything, which is only observable with a playback pipeline to control —
 * jsdom has no `AudioContext`, so everywhere else in the suite the queue is
 * permanently empty and every move commits instantly.
 */

vi.mock('@/services/prescreeningSessionService', async (importOriginal) => ({
  ...(await importOriginal<typeof PrescreeningSessionService>()),
  requestWsTicket: vi.fn(() => Promise.resolve('test-ticket')),
}));

/** A playback queue the test sets the depth of, in milliseconds. */
const playback = {
  queuedMs: 0,
  blocked: false,
  stream: {} as MediaStream,
  isBlocked: () => playback.blocked,
  resume: () => Promise.resolve(),
  enqueue: () => undefined,
  flush: () => {
    playback.queuedMs = 0;
  },
  remainingPlaybackMs: () => playback.queuedMs,
  setMuted: () => undefined,
  close: () => Promise.resolve(),
};

vi.mock('@/features/prescreening-session/audio/agentPlayback', () => ({
  createAgentPlayback: () => playback,
}));

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
    // The engine acks every screen change; nothing here asserts on that.
  }

  close(): void {
    this.readyState = 3;
    this.onclose?.({ code: 1006, reason: 'dropped' });
  }

  deliver(frame: unknown): void {
    this.onmessage?.({ data: JSON.stringify(frame) });
  }
}

const connectedFrame = (screen: string): unknown => ({
  type: 'connected',
  protocol_version: 4,
  screen,
  prefill: {},
  symptoms: { current: null, answers: [], answered_count: 0 },
  audio: { sample_rate: 16_000, channels: 1, format: 'pcm16' },
});

const navigateFrame = (screen: string, sequence: number): unknown => ({
  type: 'navigate',
  screen,
  sequence,
});

async function openConnection(): Promise<FakeSocket> {
  await waitFor(() => expect(FakeSocket.instances).toHaveLength(1), { timeout: 4_000 });
  const socket = FakeSocket.instances.at(-1);
  if (socket === undefined) throw new Error('no socket was opened');
  act(() => {
    socket.onopen?.();
  });
  return socket;
}

function renderEngine(): { current: { screen: string | null } } {
  return renderHook(() => useIntakeCallEngine({ sessionId: TEST_SESSION_ID, canStart: true }))
    .result;
}

describe('holding a screen change until its speech has played', () => {
  beforeEach(() => {
    FakeSocket.instances = [];
    playback.queuedMs = 0;
    playback.blocked = false;
    vi.stubGlobal('WebSocket', FakeSocket);
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('waits for audio that arrives after the frame, not only for what was queued when it did', async () => {
    // The greeting being cut off in the middle. The hold used to read the
    // queue once and arm a wall-clock timer for exactly that long, which is
    // correct only if no further audio for the same turn ever arrives — and
    // Nova streams a greeting across several completions, so more always
    // does. The screen moved partway through the introduction.
    const result = renderEngine();
    const socket = await openConnection();
    act(() => socket.deliver(connectedFrame('welcome')));

    playback.queuedMs = 1_000;
    act(() => socket.deliver(navigateFrame('confirm-details', 1)));
    expect(result.current.screen).toBe('welcome');

    // The rest of the greeting lands while the first hold is still running.
    playback.queuedMs = 3_000;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_100);
    });
    expect(result.current.screen).toBe('welcome');

    playback.queuedMs = 0;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3_100);
    });
    expect(result.current.screen).toBe('confirm-details');
  });

  it('does not hold at all when the browser is refusing to play', async () => {
    // A suspended context never advances its clock, so its queue only grows
    // and a hold against it would never release — stranding the one patient
    // who has nothing but the screen and the captions to follow.
    playback.blocked = true;
    playback.queuedMs = 60_000;

    const result = renderEngine();
    const socket = await openConnection();
    act(() => socket.deliver(connectedFrame('symptom-story')));
    act(() => socket.deliver(navigateFrame('medication', 1)));

    expect(result.current.screen).toBe('medication');
  });

  it('gives up and moves rather than lagging the conversation forever', async () => {
    // The ceiling. A queue that never drains is a patient reading one topic
    // while the assistant asks about the next one for the rest of the call.
    const result = renderEngine();
    const socket = await openConnection();
    act(() => socket.deliver(connectedFrame('symptom-story')));

    playback.queuedMs = 10_000;
    act(() => socket.deliver(navigateFrame('medication', 1)));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(31_000);
    });

    expect(result.current.screen).toBe('medication');
  });

  it('releases a waiting move when the patient interrupts', async () => {
    // The audio the hold was waiting on is flushed, so it is now counting
    // out speech nobody will hear. Cancelling the move instead stranded the
    // patient on the previous topic permanently.
    const result = renderEngine();
    const socket = await openConnection();
    act(() => socket.deliver(connectedFrame('symptom-story')));

    playback.queuedMs = 20_000;
    act(() => socket.deliver(navigateFrame('medication', 1)));
    expect(result.current.screen).toBe('symptom-story');

    act(() => socket.deliver({ type: 'interrupted' }));

    expect(result.current.screen).toBe('medication');
  });
});

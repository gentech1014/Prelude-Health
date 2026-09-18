import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useIntakeCallEngine } from '@/features/prescreening-session/useIntakeCallEngine';
import type * as PrescreeningSessionService from '@/services/prescreeningSessionService';
import { TEST_SESSION_ID } from '../../sessionFixture';

vi.mock('@/services/prescreeningSessionService', async (importOriginal) => ({
  ...(await importOriginal<typeof PrescreeningSessionService>()),
  requestWsTicket: vi.fn(() => Promise.resolve('test-ticket')),
}));

/**
 * Losing the connection mid-call must not lose the call.
 *
 * The backend defines exactly one of its three endings as terminal:
 * `completed`. `interrupted` and `failed` both mean this CONNECTION ended
 * and the session is still resumable -- progress is persisted on every
 * turn precisely so rejoining continues the conversation.
 *
 * The browser disagreed, and the disagreement was fatal. Both went
 * through `finish`, which latches `isFinishedRef` forever, so every route
 * back closed at once: `scheduleReconnect` returns immediately, and a
 * reconnect already in flight reaches the guard halfway through `connect`
 * and returns without opening a socket. The observed signature was a
 * ws-ticket request that succeeded with nothing after it -- no socket, no
 * error, no retry, and a dead screen.
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

  send(): void {}

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
  protocol_version: 1,
  screen,
  prefill: {},
  symptoms: { current: null, answers: [], answered_count: 0 },
  audio: { sample_rate: 24_000, channels: 1, format: 'pcm16' },
});

const callEndedFrame = (reason: string): unknown => ({ type: 'call_ended', reason });

/** Sockets already driven to their death, so the loop kills each once. */
const killed = new WeakSet<FakeSocket>();

async function openConnection(expectedCount: number): Promise<FakeSocket> {
  await waitFor(() => expect(FakeSocket.instances).toHaveLength(expectedCount), {
    timeout: 6_000,
  });
  const socket = FakeSocket.instances.at(-1);
  if (socket === undefined) throw new Error('no socket was opened');
  act(() => {
    socket.onopen?.();
  });
  return socket;
}

function renderEngine(): {
  result: {
    current: {
      status: string;
      screen: string | null;
      endReason: string | null;
    };
  };
} {
  return renderHook(() => useIntakeCallEngine({ sessionId: TEST_SESSION_ID, canStart: true }));
}

describe('losing the connection mid-call', () => {
  beforeEach(() => {
    FakeSocket.instances = [];
    vi.stubGlobal('WebSocket', FakeSocket);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('reconnects after the server reports the connection was interrupted', async () => {
    const { result } = renderEngine();
    const socket = await openConnection(1);
    act(() => socket.deliver(connectedFrame('symptom-story')));

    // The server's own wording for a dropped connection. Resumable.
    act(() => socket.deliver(callEndedFrame('interrupted')));

    expect(result.current.endReason).toBeNull();
    await openConnection(2);
  });

  it('reconnects after the server reports a failure on its side', async () => {
    const { result } = renderEngine();
    const socket = await openConnection(1);
    act(() => socket.deliver(connectedFrame('medication')));

    act(() => socket.deliver(callEndedFrame('failed')));

    expect(result.current.endReason).toBeNull();
    await openConnection(2);
  });

  it('keeps the patient where they were rather than restarting them', async () => {
    const { result } = renderEngine();
    const first = await openConnection(1);
    act(() => first.deliver(connectedFrame('symptom-story')));
    act(() => first.deliver(callEndedFrame('interrupted')));

    const second = await openConnection(2);
    act(() => second.deliver(connectedFrame('symptom-story')));

    expect(result.current.screen).toBe('symptom-story');
    expect(result.current.status).toBe('live');
  });

  it('still treats a completed call as over', async () => {
    const { result } = renderEngine();
    const socket = await openConnection(1);
    act(() => socket.deliver(connectedFrame('thank-you')));

    act(() => socket.deliver(callEndedFrame('completed')));

    await waitFor(() => expect(result.current.endReason).toBe('completed'));
    expect(FakeSocket.instances).toHaveLength(1);
  });

  it('does not reconnect a patient who pressed End', async () => {
    // The server reports a deliberate hang-up with the same `interrupted`
    // reason as a dropped socket, so only the browser can tell them apart.
    const { result } = renderHook(() =>
      useIntakeCallEngine({ sessionId: TEST_SESSION_ID, canStart: true }),
    );
    const socket = await openConnection(1);
    act(() => socket.deliver(connectedFrame('medication')));

    act(() => (result.current as unknown as { hangUp: () => void }).hangUp());
    act(() => socket.deliver(callEndedFrame('interrupted')));

    await new Promise((resolve) => setTimeout(resolve, 1_500));
    expect(FakeSocket.instances).toHaveLength(1);
  });

  it('backs off instead of looping when every connection dies on arrival', async () => {
    // The shape in the reported log: a connection that lasts about three
    // seconds. Crediting that as a good connection resets the retry
    // budget, so the backoff never grows -- the patient watches
    // "Reconnecting" forever while the backend is opened and torn down
    // every second.
    //
    // The budget now only clears once a connection has held for
    // CONNECTION_STABLE_MS, so the delays escalate (1s, 2s, 4s, 8s...)
    // and run out. Counting sockets over a fixed window is what separates
    // the two: escalating backoff opens a handful, a reset budget opens
    // one per second.
    renderEngine();
    const deadline = Date.now() + 8_000;

    while (Date.now() < deadline) {
      const socket = FakeSocket.instances.at(-1);
      if (socket !== undefined && !killed.has(socket)) {
        killed.add(socket);
        act(() => {
          socket.onopen?.();
        });
        act(() => socket.deliver(connectedFrame('symptom-story')));
        act(() => socket.deliver(callEndedFrame('interrupted')));
      }
      await new Promise((resolve) => setTimeout(resolve, 100));
    }

    // 1s + 2s + 4s of backoff fits in the window; a budget that reset
    // every time would have opened roughly eight.
    expect(FakeSocket.instances.length).toBeLessThanOrEqual(5);
  }, 30_000);
});

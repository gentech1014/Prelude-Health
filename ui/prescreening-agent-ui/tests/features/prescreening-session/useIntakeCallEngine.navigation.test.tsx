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
 * A socket the test drives directly. Only the surface the engine touches is
 * implemented, so a new dependency on the real WebSocket API shows up as a
 * failure rather than a silent no-op.
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
    // The engine acks every screen change; nothing here asserts on that.
  }

  close(): void {
    this.readyState = 3;
    this.onclose?.({ code: 1006, reason: 'dropped' });
  }

  /** Deliver one server frame, as the wire would. */
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

const navigateFrame = (screen: string, sequence: number): unknown => ({
  type: 'navigate',
  screen,
  sequence,
});

/**
 * Wait for the engine to open its next socket, then complete the handshake.
 * The generous timeout covers the engine's first reconnect backoff, which is
 * a real second of wall clock.
 */
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

function renderEngine(onAppointmentChanged?: () => void): {
  result: { current: { screen: string | null; activeCaption: { text: string } | null } };
} {
  return renderHook(() =>
    useIntakeCallEngine({
      sessionId: TEST_SESSION_ID,
      canStart: true,
      ...(onAppointmentChanged ? { onAppointmentChanged } : {}),
    }),
  );
}

const captionFrame = (role: string, text: string, isFinal: boolean): unknown => ({
  type: 'transcript',
  role,
  text,
  is_final: isFinal,
});

describe('screen navigation', () => {
  beforeEach(() => {
    FakeSocket.instances = [];
    vi.stubGlobal('WebSocket', FakeSocket);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('follows the server off the welcome screen', async () => {
    const { result } = renderEngine();
    const socket = await openConnection(1);

    act(() => socket.deliver(connectedFrame('welcome')));
    expect(result.current.screen).toBe('welcome');

    act(() => socket.deliver(navigateFrame('confirm-details', 1)));

    expect(result.current.screen).toBe('confirm-details');
  });

  it('still follows the server after the socket has dropped and rejoined', async () => {
    // The regression this exists for. Sequences are numbered per connection
    // and restart at 1, so a client holding the previous socket's high-water
    // mark discarded every move the new one made: the greeting finished, the
    // assistant went on to ask for consent, and the screen never left welcome.
    const { result } = renderEngine();
    const first = await openConnection(1);

    act(() => first.deliver(connectedFrame('welcome')));
    act(() => first.deliver(navigateFrame('confirm-details', 1)));
    expect(result.current.screen).toBe('confirm-details');

    act(() => first.close());

    // A pre-consent reconnect legitimately restarts on welcome: consent is
    // the only marker that the greeting actually happened, so the server
    // greets again rather than resuming onto a screen nobody heard.
    const second = await openConnection(2);
    act(() => second.deliver(connectedFrame('welcome')));
    expect(result.current.screen).toBe('welcome');

    act(() => second.deliver(navigateFrame('confirm-details', 1)));

    expect(result.current.screen).toBe('confirm-details');
  });

  it('follows the assistant onto the reschedule branch and back to the closing screen', async () => {
    // The one screen outside the ordered flow. It is reached by answering
    // the closing question, so a client that refused it would leave the
    // patient on the summary while the assistant read out times.
    const { result } = renderEngine();
    const socket = await openConnection(1);

    act(() => socket.deliver(connectedFrame('thank-you')));
    act(() => socket.deliver(navigateFrame('appointment-reschedule', 1)));
    expect(result.current.screen).toBe('appointment-reschedule');

    act(() => socket.deliver(navigateFrame('thank-you', 2)));

    expect(result.current.screen).toBe('thank-you');
  });

  it('re-reads the session when the assistant moves the appointment', async () => {
    // The frame carries no time: the session route is the authority, and a
    // second copy of the appointment on this wire is a second thing that
    // can disagree with the screen showing it.
    const onAppointmentChanged = vi.fn();
    renderEngine(onAppointmentChanged);
    const socket = await openConnection(1);

    act(() => socket.deliver(connectedFrame('thank-you')));
    act(() => socket.deliver({ type: 'appointment_updated' }));

    expect(onAppointmentChanged).toHaveBeenCalledTimes(1);
  });

  it('ignores a stale frame from within the same connection', async () => {
    // The guard still has to do its job: frames are published from several
    // tasks, and one arriving late must not drag the patient backwards.
    const { result } = renderEngine();
    const socket = await openConnection(1);

    act(() => socket.deliver(connectedFrame('welcome')));
    act(() => socket.deliver(navigateFrame('confirm-details', 1)));
    act(() => socket.deliver(navigateFrame('patient-concerns', 2)));
    act(() => socket.deliver(navigateFrame('welcome', 1)));

    expect(result.current.screen).toBe('patient-concerns');
  });
});

describe('captions across a screen change', () => {
  beforeEach(() => {
    FakeSocket.instances = [];
    vi.stubGlobal('WebSocket', FakeSocket);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('does not carry a finished caption onto the next screen', async () => {
    // The greeting reappearing on the consent screen. Navigation only
    // commits once that turn's audio has drained, so a finalized caption
    // at that point is over -- and leaving it up read as though the
    // assistant had just said it again on the new topic.
    const { result } = renderEngine();
    const socket = await openConnection(1);

    act(() => socket.deliver(connectedFrame('welcome')));
    act(() => socket.deliver(captionFrame('agent', 'Hello, this is the clinic.', true)));
    expect(result.current.activeCaption?.text).toBe('Hello, this is the clinic.');

    act(() => socket.deliver(navigateFrame('confirm-details', 1)));

    expect(result.current.activeCaption).toBeNull();
  });

  it('never blanks a caption the assistant is still speaking', async () => {
    const { result } = renderEngine();
    const socket = await openConnection(1);

    act(() => socket.deliver(connectedFrame('welcome')));
    act(() => socket.deliver(captionFrame('agent', 'Just checking your', false)));

    act(() => socket.deliver(navigateFrame('confirm-details', 1)));

    expect(result.current.activeCaption?.text).toBe('Just checking your');
  });
});

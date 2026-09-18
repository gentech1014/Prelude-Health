import '@testing-library/jest-dom/vitest';
import { vi } from 'vitest';

// jsdom doesn't implement matchMedia — stub a "no dark preference, desktop width"
// default so every test gets a sane result; individual tests may reassign it.
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  configurable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  })),
});

// The session handshake is stubbed app-wide so page tests exercise their own
// screen, not the network. Tests that care about the handshake itself
// override these per-case with vi.mocked(...).
vi.mock('@/services/prescreeningSessionService', async () => {
  const { buildTestSession } = await import('./sessionFixture');
  return {
    attachPrescreeningSession: vi.fn(() => Promise.resolve(buildTestSession())),
    fetchPrescreeningSessionContext: vi.fn(() => Promise.resolve(buildTestSession())),
    fetchPrescreeningSessionStatus: vi.fn(() =>
      Promise.resolve({
        sessionId: 'test-session',
        status: 'started',
        documentUploaded: false,
        hasReport: false,
      }),
    ),
    submitConsent: vi.fn(() =>
      Promise.resolve(buildTestSession({ consentGiven: true, status: 'in_progress' })),
    ),
    requestWsTicket: vi.fn(() => Promise.resolve('ws.0.nonce.signature')),
    parsePrescreeningSessionContext: vi.fn(() => buildTestSession()),
  };
});

// jsdom has no Web Audio. The call engine treats that as a real outcome —
// captions and typed answers still work — so tests exercise that path
// rather than a fake one, and nothing here needs to pretend otherwise.
Object.defineProperty(window, 'WebSocket', {
  writable: true,
  configurable: true,
  value: class {
    static readonly OPEN = 1;
    readonly readyState = 3;
    close = vi.fn();
    send = vi.fn();
  },
});

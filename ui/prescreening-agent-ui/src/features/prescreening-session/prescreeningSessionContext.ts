import { createContext } from 'react';
import type { ApiError } from '@/services/apiClient';
import type { PrescreeningSessionContext as SessionContext } from '@/services/prescreeningSessionService';

/** Where the session handshake has got to. Screens branch on this, never on a bare boolean. */
export type PrescreeningSessionPhase = 'connecting' | 'ready' | 'failed';

export interface PrescreeningSessionContextValue {
  /** Identifies the patient's prescreening call — the one thing every step in the flow shares. */
  sessionId: string;
  phase: PrescreeningSessionPhase;
  /** The patient and appointment detail behind this session. Null until the handshake lands. */
  session: SessionContext | null;
  /** Why the handshake failed, when `phase` is 'failed'. */
  error: ApiError | null;
  /** Re-reads the session from the server — used after consent, and to retry a failed handshake. */
  refresh: () => Promise<void>;
  /** Applies a context the caller already received, avoiding a redundant round trip. */
  applySession: (session: SessionContext) => void;
}

// Split from PrescreeningSessionProvider so react-refresh/only-export-components stays happy.
export const PrescreeningSessionContext = createContext<
  PrescreeningSessionContextValue | undefined
>(undefined);

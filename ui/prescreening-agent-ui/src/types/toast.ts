import type { ReactNode } from 'react';
import type { Tone } from '@/types/tone';

export interface ToastInput {
  /** Omit to fall back to a sensible default icon for the tone. */
  icon?: ReactNode;
  message: string;
  tone?: Tone;
  /** ms before auto-dismiss; pass 0 for a persistent toast (e.g. an in-flight "Saving…"). */
  durationMs?: number;
}

export interface Toast extends Required<Pick<ToastInput, 'icon' | 'message' | 'tone'>> {
  id: string;
  /** Resolved auto-dismiss duration in ms; 0 means persistent (no countdown bar, no timer). */
  autoDismissMs: number;
}

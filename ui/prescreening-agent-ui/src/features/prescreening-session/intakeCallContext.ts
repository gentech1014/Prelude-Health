import { createContext } from 'react';
import type {
  IntakeScreenName,
  PrescreeningFlowStep,
} from '@/features/prescreening-session/prescreeningFlowSteps';
import type {
  CallEndReason,
  IntakePrefill,
  IntakeSelections,
} from '@/features/prescreening-session/intakeProtocol';

/**
 * Where the live call is. Screens branch on this rather than on a bare
 * boolean, because "not live" covers four situations a patient needs told
 * apart: not started, connecting, dropped-and-retrying, and over.
 */
export type IntakeCallStatus =
  'idle' | 'starting' | 'connecting' | 'live' | 'reconnecting' | 'ended' | 'failed';

/** Why a call could not start or could not continue, in words a patient can act on. */
export interface IntakeCallProblem {
  kind: 'microphone' | 'permission' | 'connection' | 'session' | 'agent' | 'unsupported';
  message: string;
  /** Whether trying again is worth offering. */
  recoverable: boolean;
}

/** One finalized utterance. Partial deltas never become turns. */
export interface IntakeTurn {
  id: string;
  role: 'agent' | 'patient';
  text: string;
}

/**
 * A caption line, which may still be changing.
 *
 * Carries its own role and `turnId` rather than being kept in a
 * role-keyed slot: the panel shows one line at a time, replacing
 * whoever held it, and `turnId` is what tells it "still growing" apart
 * from "a new utterance has started" so it can animate only the latter.
 */
export interface IntakeCaption {
  role: 'agent' | 'patient';
  text: string;
  isFinal: boolean;
  turnId: string;
}

/** The one screening question the assistant currently has on screen. */
export interface SymptomQuestion {
  id: string;
  /** As the assistant asked it aloud, so the screen and the voice agree. */
  text: string;
}

/** One screening question the patient has already answered. */
/** How well the assistant knows one answer. Mirrors the server's `AnswerStatus`. */
export type AnswerCertainty = 'confirmed' | 'uncertain' | 'inferred' | 'undisclosed' | 'unknown';

export interface SymptomAnswerEntry {
  questionId: string;
  question: string;
  answer: string;
  /**
   * Carried so the screen can show a declined or half-remembered answer
   * as what it is. Without it, "Preferred not to say" rendered in an
   * ordinary answer box, indistinguishable from something the patient
   * had actually told the assistant.
   */
  certainty: AnswerCertainty;
}

/**
 * The symptom screen, which is a conversation rather than a form.
 *
 * The assistant chooses each question from the clinic's question bank
 * based on what the patient has already told it, so the screen holds one
 * live question at a time and the record of what came before. Nothing
 * here is a source of clinical truth — the transcript is.
 */
export interface IntakeSymptomIntake {
  current: SymptomQuestion | null;
  answers: readonly SymptomAnswerEntry[];
}

export interface IntakeCallValue {
  status: IntakeCallStatus;
  problem: IntakeCallProblem | null;
  /** Set once the call is over, so a screen can say why rather than just stopping. */
  endReason: CallEndReason | null;

  /** The patient's own microphone, for the waveform. Null until the call starts. */
  micStream: MediaStream | null;
  /** What the patient is hearing from the agent, for the waveform. */
  agentStream: MediaStream | null;
  isAgentSpeaking: boolean;
  isMuted: boolean;
  isOnHold: boolean;

  /**
   * The line currently on screen: whichever of the agent or patient most
   * recently spoke. Replaces the previous line rather than stacking next
   * to it, so the patient is never reading both speakers at once.
   */
  activeCaption: IntakeCaption | null;
  turns: readonly IntakeTurn[];

  /**
   * The screen the agent has moved the patient to, and every screen the
   * call has reached. Both come from the server; the patient does not
   * navigate this flow and neither does the frontend.
   */
  screen: IntakeScreenName | null;
  visitedScreens: readonly IntakeScreenName[];

  /** Values the agent heard, keyed by screen then field. Offered as editable, never as fact. */
  prefill: IntakePrefill;

  /**
   * Which option the agent judged those words to mean, keyed by screen
   * then field, as validated option ids.
   *
   * Read in preference to matching `prefill` text: the agent understood
   * the sentence and the browser can only pattern-match it. Absent for
   * every field it left alone, which is what sends the screen back to the
   * matcher.
   */
  selections: IntakeSelections;

  /** The symptom screen's live question and the answers already given. */
  symptomIntake: IntakeSymptomIntake;

  /** Total duration of the call in seconds, populated when the call ends. */
  callDurationSeconds: number;

  /** True once the agent has offered to take a document, until the patient deals with it. */
  isUploadRequested: boolean;

  /**
   * True when the browser is refusing to play audio until the patient
   * interacts with the page.
   *
   * Autoplay policy: an `AudioContext` created without a user gesture
   * starts suspended, and nothing plays. The call still runs -- captions
   * and typed answers work -- so this is a prompt to offer, not a gate.
   */
  needsAudioUnlock: boolean;
  /** Resumes playback from inside a real tap. */
  unlockAudio: () => void;

  /** Begins the call: microphone, then socket. Safe to call twice. */
  start: () => Promise<void>;
  /** Ends the call from the patient's side. Leaves the session resumable. */
  hangUp: () => void;
  retry: () => Promise<void>;
  setMuted: (muted: boolean) => void;
  setOnHold: (onHold: boolean) => void;
  /** Sends a typed answer as the patient's turn in the same conversation. */
  sendTypedAnswer: (text: string) => void;
  /** Reports a field the patient typed or picked, so it reaches the transcript. */
  reportFieldEdit: (screen: PrescreeningFlowStep, field: string, value: string) => void;
  /** Reports a symptom answer the patient typed, whether new or a correction. */
  reportSymptomAnswer: (questionId: string, value: string) => void;
  /** Tells the agent consent is recorded, so it can move past the consent screen. */
  notifyConsentRecorded: () => void;
  /**
   * Asks the assistant for a different appointment time, for a patient who
   * taps rather than says so. It offers the times and moves the screen —
   * this only asks.
   */
  requestReschedule: () => void;
  /** Tells the assistant the patient moved their appointment on screen, so it confirms rather than re-writing it. */
  reportAppointmentRescheduled: () => void;
  /**
   * Tells the assistant a document landed, so it asks what the report is.
   * Without it the upload is invisible to the call.
   */
  reportDocumentUploaded: (fileName: string) => void;
  /** Tells the assistant the upload failed, so it asks the patient to try again. */
  reportDocumentUploadFailed: () => void;
  dismissUploadRequest: () => void;
}

// Split from the provider so react-refresh/only-export-components stays happy.
export const IntakeCallContext = createContext<IntakeCallValue | undefined>(undefined);

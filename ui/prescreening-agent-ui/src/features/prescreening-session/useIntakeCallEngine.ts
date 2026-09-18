import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  createAgentPlayback,
  type AgentPlayback,
} from '@/features/prescreening-session/audio/agentPlayback';
import {
  MicCaptureError,
  startMicCapture,
  type MicCapture,
} from '@/features/prescreening-session/audio/micCapture';
import {
  buildIntakeSocketUrl,
  intakeClientEvent,
  parseIntakeServerEvent,
  type CallEndReason,
  type IntakeClientEvent,
  type IntakePrefill,
  type IntakeSelections,
  type IntakeSymptomState,
} from '@/features/prescreening-session/intakeProtocol';
import type {
  IntakeCallProblem,
  IntakeCallStatus,
  IntakeCallValue,
  IntakeCaption,
  IntakeSymptomIntake,
  IntakeTurn,
} from '@/features/prescreening-session/intakeCallContext';
import type {
  IntakeScreenName,
  PrescreeningFlowStep,
} from '@/features/prescreening-session/prescreeningFlowSteps';
import { requestWsTicket } from '@/services/prescreeningSessionService';
import { toApiError } from '@/services/apiClient';

/**
 * Owns one patient's live call: the socket, the microphone, playback, the
 * captions, and reconnection. Everything imperative lives in refs;
 * `useState` holds only what a screen actually renders, because audio
 * frames arrive around thirty times a second and must never touch React.
 */

const MAX_RECONNECT_ATTEMPTS = 5;

/**
 * Backoff between reconnects, in milliseconds. Explicit rather than
 * computed so the total wait is readable: about 30 seconds across five
 * attempts, after which the patient is told plainly rather than watching
 * a spinner forever.
 */
const RECONNECT_DELAYS_MS = [1_000, 2_000, 4_000, 8_000, 15_000];

/**
 * The longest a caption may wait for the audio it belongs to.
 *
 * A ceiling on a measurement, not a timing of anything: the queue length is
 * the real signal, and this only bounds what happens if it is ever wrong.
 * A caption that arrives late is a poor caption; one that never arrives is
 * a patient who cannot follow the call at all, and that is the failure this
 * rules out.
 */
const MAX_CAPTION_HOLD_MS = 30_000;

/**
 * The longest a screen change may wait for the speech it follows.
 *
 * Same shape as the caption ceiling above, and for the same reason: the
 * playback queue is the real signal and this only bounds what happens if
 * it is ever wrong. A screen that arrives late is behind the conversation;
 * a screen that never arrives leaves the patient reading one topic while
 * the assistant asks about the next one for the rest of the call.
 */
const MAX_NAVIGATION_HOLD_MS = 30_000;

/**
 * Heartbeat interval. A half-open socket is indistinguishable from a quiet
 * patient, so without a round trip the UI cannot tell "the agent is
 * waiting for me" from "my connection died three minutes ago".
 */
const PING_INTERVAL_MS = 15_000;

/** How long a missing pong is tolerated before the socket is treated as dead. */
const PONG_TIMEOUT_MS = 10_000;

const EMPTY_SYMPTOM_INTAKE: IntakeSymptomIntake = {
  current: null,
  answers: [],
};

/**
 * The wire's symptom snapshot as the screens consume it.
 *
 * Mapped at the boundary rather than passed through, so no component has
 * to know the server's key casing — and so an unrecognized shape has
 * already been rejected by the schema before it reaches one.
 */
function toSymptomIntake(state: IntakeSymptomState): IntakeSymptomIntake {
  return {
    current:
      state.current === null ? null : { id: state.current.question_id, text: state.current.text },
    answers: state.answers.map((entry) => ({
      questionId: entry.question_id,
      question: entry.question,
      answer: entry.answer,
      certainty: entry.status,
    })),
  };
}

/** WebSocket close code the backend uses for an authorization refusal. */
const POLICY_VIOLATION = 1008;

/**
 * Longest the call may stay up purely to finish playing the farewell.
 *
 * The assistant says goodbye and then calls `end_session`, so `call_ended`
 * lands while those words are still queued. Tearing the audio down on
 * arrival cut them off mid-sentence — the patient was told the call would
 * end in a few seconds and it ended instantly, silently. Capped because
 * the queue length is the server's word, not something to trust without a
 * bound.
 */
const FAREWELL_DRAIN_CAP_MS = 20_000;

/**
 * How often to re-check the playback clock while the agent is audible.
 *
 * Coarse on purpose: it only drives one boolean, and the last tick is
 * scheduled for exactly when the queue runs dry, so the indicator still
 * turns off on time.
 */
const SPEAKING_POLL_MS = 250;

/**
 * How long a connection must survive before it counts as a good one.
 *
 * The retry budget used to reset the moment a `connected` frame arrived,
 * which was safe only while a server-ended call was terminal. Now that
 * drops are resumable, a connection that dies three seconds in -- exactly
 * what the reported log shows -- would reset the budget, reconnect, die
 * again, and loop forever: the patient watches "Reconnecting" with no end
 * and the backend is opened and torn down every few seconds.
 *
 * Ten seconds is comfortably longer than the connect-and-die case and far
 * shorter than any real call, so a genuine reconnection still clears the
 * budget and a flapping one still runs out of it.
 */
const CONNECTION_STABLE_MS = 10_000;

const CONNECTION_PROBLEM: IntakeCallProblem = {
  kind: 'connection',
  message: 'We lost the connection to your assistant. Check your signal and try again.',
  recoverable: true,
};

const SESSION_PROBLEM: IntakeCallProblem = {
  kind: 'session',
  message: 'This prescreening can no longer be joined. Reopen your latest link.',
  recoverable: false,
};

interface EngineOptions {
  sessionId: string;
  /**
   * True once the session handshake has landed. Not about consent: consent
   * is asked for *in* the call, so the socket opens before it exists and
   * the agent is restricted server-side until it is recorded.
   */
  canStart: boolean;
  /**
   * Called when the server reports the appointment has moved, so the
   * session context fetched over REST can be re-read.
   *
   * A callback rather than an appointment on the wire: the session route
   * is the authority on when the patient is expected, and the assistant
   * can move that appointment mid-call from the closing screen.
   */
  onAppointmentChanged?: () => void;
}

export function useIntakeCallEngine({
  sessionId,
  canStart,
  onAppointmentChanged,
}: EngineOptions): IntakeCallValue {
  const [status, setStatus] = useState<IntakeCallStatus>('idle');
  const [problem, setProblem] = useState<IntakeCallProblem | null>(null);
  const [endReason, setEndReason] = useState<CallEndReason | null>(null);
  const [micStream, setMicStream] = useState<MediaStream | null>(null);
  const [agentStream, setAgentStream] = useState<MediaStream | null>(null);
  const [isAgentSpeaking, setIsAgentSpeaking] = useState(false);
  const [isMuted, setIsMutedState] = useState(false);
  const [isOnHold, setIsOnHoldState] = useState(false);
  const [activeCaption, setActiveCaption] = useState<IntakeCaption | null>(null);
  const [turns, setTurns] = useState<readonly IntakeTurn[]>([]);
  const [screen, setScreen] = useState<IntakeScreenName | null>(null);
  const [visitedScreens, setVisitedScreens] = useState<readonly IntakeScreenName[]>([]);
  const [prefill, setPrefill] = useState<IntakePrefill>({});
  const [selections, setSelections] = useState<IntakeSelections>({});
  const [symptomIntake, setSymptomIntake] = useState<IntakeSymptomIntake>(EMPTY_SYMPTOM_INTAKE);
  const [isUploadRequested, setIsUploadRequested] = useState(false);
  const [needsAudioUnlock, setNeedsAudioUnlock] = useState(false);
  const [callDurationSeconds, setCallDurationSeconds] = useState(0);

  const socketRef = useRef<WebSocket | null>(null);
  const micRef = useRef<MicCapture | null>(null);
  const playbackRef = useRef<AgentPlayback | null>(null);
  const speakingTimerRef = useRef<number | null>(null);
  const isGeneratingRef = useRef(false);
  const settleSpeakingRef = useRef<() => void>(() => undefined);
  const scheduleReconnectRef = useRef<() => void>(() => undefined);
  /** Clears the retry budget once a connection has proved it will hold. */
  const stableTimerRef = useRef<number | null>(null);
  /**
   * Whether the patient chose to end the call.
   *
   * The server reports a deliberate hang-up and a dropped connection with
   * the same `interrupted` reason, and only the browser knows which it
   * was. Without this, making drops resumable would also make the End
   * button reconnect the patient to a call they just left.
   */
  const endedDeliberatelyRef = useRef(false);
  const attemptsRef = useRef(0);
  const reconnectTimerRef = useRef<number | null>(null);
  const pingTimerRef = useRef<number | null>(null);
  const pongTimerRef = useRef<number | null>(null);
  /** Holds a `navigate` event back until the audio it followed has actually finished playing. */
  const pendingNavigationTimerRef = useRef<number | null>(null);
  /**
   * The screen that hold is for.
   *
   * Kept alongside the timer because an interruption cancels the wait but
   * must not cancel the move: the screen change was the server's
   * instruction, and dropping it left the patient on the previous topic
   * for the rest of the call while the assistant asked about the next one.
   */
  const pendingNavigationRef = useRef<IntakeScreenName | null>(null);
  /** When the current hold must give up and move regardless. */
  const navigationHoldDeadlineRef = useRef(0);
  /** Declared as a ref so the hold can re-arm itself as more audio lands. */
  const holdNavigationRef = useRef<(target: IntakeScreenName) => void>(() => undefined);
  /**
   * Holds a new assistant turn's caption back until the audio queued ahead
   * of it has actually played.
   *
   * The model generates a whole turn seconds before the patient hears a
   * word of it -- the greeting arrives from Nova in a second or two and
   * takes twenty to play -- so a caption shown on arrival is the caption
   * of the *next* thing the assistant will say, printed over the words
   * they are still listening to. Same fix, and the same measurement, as
   * the navigation hold below it.
   */
  const pendingCaptionTimerRef = useRef<number | null>(null);
  /**
   * The held caption, kept up to date while it waits.
   *
   * A turn keeps streaming after it is held, so the line revealed when the
   * audio reaches it is the whole turn as it now stands, not the first
   * fragment that happened to arrive before the hold started.
   */
  const pendingCaptionRef = useRef<IntakeCaption | null>(null);
  /** Lets the farewell finish playing before the call is torn down. */
  const endTimerRef = useRef<number | null>(null);
  const startedAtRef = useRef<number | null>(null);
  const endedAtRef = useRef<number | null>(null);
  /** Set once the call is over for good, so a socket close stops triggering retries. */
  const isFinishedRef = useRef(false);
  /**
   * Synchronous guards against opening the call twice.
   *
   * `status` and `socketRef` are both useless for this: state is stale
   * within a tick, and the socket is only assigned *after* the ticket
   * request awaits. Two callers arriving in the same tick therefore both
   * passed, opened two sockets, and got two agents talking over each
   * other -- which is what "the voice is doubled" actually was.
   */
  const isStartingRef = useRef(false);
  const isConnectingRef = useRef(false);
  const turnCounterRef = useRef(0);
  /**
   * Which utterance is currently on screen. A fresh `turnId` is what tells
   * the panel to animate a new line in rather than mutate the one showing —
   * without it, a new turn's short first partial overwrites the previous
   * turn's full sentence in place, which reads as the caption looping back
   * on itself.
   */
  const activeTurnRef = useRef<{
    role: 'agent' | 'patient';
    turnId: string;
    finalized: boolean;
  } | null>(null);
  const captionTurnCounterRef = useRef(0);
  /**
   * Highest navigate sequence seen on the current connection — a late frame
   * must not drag the patient back. Reset by `connected`, because the server
   * numbers these per socket rather than per session.
   */
  const navSequenceRef = useRef(0);
  const isMountedRef = useRef(true);
  /**
   * The latest `onAppointmentChanged`, held in a ref so the frame handler
   * keeps a stable identity. It is a dependency of the socket's own
   * `onmessage`, and rebuilding that on every render of the provider above
   * would tear the handler off a live socket for a callback change nobody
   * asked for.
   */
  const onAppointmentChangedRef = useRef(onAppointmentChanged);
  onAppointmentChangedRef.current = onAppointmentChanged;

  const send = useCallback((event: IntakeClientEvent): void => {
    const socket = socketRef.current;
    if (socket?.readyState !== WebSocket.OPEN) return;
    socket.send(JSON.stringify(event));
  }, []);

  const clearTimer = (ref: React.RefObject<number | null>): void => {
    if (ref.current !== null) {
      window.clearTimeout(ref.current);
      ref.current = null;
    }
  };

  /**
   * Keeps "speaking" true for as long as audio is still coming out, not
   * for as long as the model is still generating.
   *
   * Those are ~18 seconds apart. Nova streams a whole greeting in a
   * second or two and the browser takes twenty to play it, so the
   * server's `agent_speaking:false` lands while Kiara is mid-sentence.
   * Worse, when that frame goes missing the indicator sticks on forever.
   * Polling the playback clock makes the indicator a fact about the
   * speaker rather than a report about the backend.
   */
  const settleSpeaking = useCallback((): void => {
    clearTimer(speakingTimerRef);
    const remainingMs = playbackRef.current?.remainingPlaybackMs() ?? 0;
    if (remainingMs <= 0) {
      setIsAgentSpeaking(isGeneratingRef.current);
      return;
    }
    setIsAgentSpeaking(true);
    speakingTimerRef.current = window.setTimeout(
      () => settleSpeakingRef.current(),
      Math.min(remainingMs, SPEAKING_POLL_MS),
    );
  }, []);
  settleSpeakingRef.current = settleSpeaking;

  const stopHeartbeat = useCallback((): void => {
    if (pingTimerRef.current !== null) {
      window.clearInterval(pingTimerRef.current);
      pingTimerRef.current = null;
    }
    clearTimer(pongTimerRef);
  }, []);

  const appendTurn = useCallback((role: 'agent' | 'patient', text: string): void => {
    turnCounterRef.current += 1;
    const id = `${role}-${turnCounterRef.current}`;
    setTurns((current) => [...current, { id, role, text }]);
  }, []);

  const closeAudio = useCallback(async (): Promise<void> => {
    const mic = micRef.current;
    const playback = playbackRef.current;
    micRef.current = null;
    playbackRef.current = null;
    setMicStream(null);
    setAgentStream(null);
    await mic?.close();
    await playback?.close();
  }, []);

  const finish = useCallback(
    (reason: CallEndReason, nextProblem: IntakeCallProblem | null): void => {
      isFinishedRef.current = true;
      isConnectingRef.current = false;
      isStartingRef.current = false;
      stopHeartbeat();
      clearTimer(reconnectTimerRef);
      clearTimer(pendingNavigationTimerRef);
      clearTimer(pendingCaptionTimerRef);
      clearTimer(endTimerRef);
      clearTimer(stableTimerRef);
      pendingNavigationRef.current = null;
      pendingCaptionRef.current = null;
      socketRef.current?.close();
      socketRef.current = null;
      void closeAudio();
      if (!isMountedRef.current) return;

      if (startedAtRef.current !== null && endedAtRef.current === null) {
        endedAtRef.current = Date.now();
        setCallDurationSeconds(Math.floor((endedAtRef.current - startedAtRef.current) / 1000));
      }

      setEndReason(reason);
      setProblem(nextProblem);
      setStatus(nextProblem === null ? 'ended' : 'failed');
      setIsAgentSpeaking(false);
    },
    [closeAudio, stopHeartbeat],
  );

  /**
   * Takes a connection the server ended, without ending the *call*.
   *
   * `interrupted` and `failed` are both resumable by design -- the backend
   * persists progress on every turn precisely so rejoining continues the
   * conversation. The browser did not agree: both went through `finish`,
   * which sets `isFinishedRef` permanently, and from then on every path
   * back was closed. `scheduleReconnect` returns immediately, and a
   * reconnect already in flight reaches `if (isFinishedRef.current)`
   * halfway through `connect` and returns without opening a socket -- so
   * the ws-ticket request succeeds, nothing follows it, and the app is
   * simply dead with no error and no retry.
   *
   * Audio is deliberately left running: the microphone and playback graph
   * survive the socket, and tearing them down would make the resumed call
   * silent even once it reconnects.
   */
  const resumeAfterDrop = useCallback((): void => {
    stopHeartbeat();
    clearTimer(stableTimerRef);
    // Detached before closing, so the socket's own `onclose` sees itself
    // superseded and does not schedule a second reconnect alongside this.
    const socket = socketRef.current;
    socketRef.current = null;
    socket?.close();

    isGeneratingRef.current = false;
    clearTimer(speakingTimerRef);
    setIsAgentSpeaking(false);
    setEndReason(null);
    scheduleReconnectRef.current();
  }, [stopHeartbeat]);

  /** Declared as a ref so `connect` and `scheduleReconnect` can call each other. */
  const connectRef = useRef<() => Promise<void>>(() => Promise.resolve());

  const scheduleReconnect = useCallback((): void => {
    if (isFinishedRef.current) return;

    const attempt = attemptsRef.current;
    if (attempt >= MAX_RECONNECT_ATTEMPTS) {
      finish('interrupted', CONNECTION_PROBLEM);
      return;
    }
    attemptsRef.current = attempt + 1;
    setStatus('reconnecting');

    // Jittered so a clinic-wide network blip does not reconnect every
    // patient in lockstep and stampede the backend.
    const base = RECONNECT_DELAYS_MS[attempt] ?? 15_000;
    const delay = base + Math.floor(Math.random() * 500);
    reconnectTimerRef.current = window.setTimeout(() => {
      void connectRef.current();
    }, delay);
  }, [finish]);
  scheduleReconnectRef.current = scheduleReconnect;

  /**
   * Puts the patient on a screen the server has asked for.
   *
   * `visitedScreens` is recorded when the frame *arrives*, not here: it is
   * the record of what the call covered, which is true the moment the
   * server says so, and the closing recap reads "Not covered" against
   * anything missing from it.
   */
  const commitNavigation = useCallback(
    (target: IntakeScreenName): void => {
      pendingNavigationRef.current = null;
      setScreen(target);
      // A finished caption belongs to the topic being left, so carrying it
      // across made the greeting reappear on the consent screen and read
      // as though the assistant had just said it there. Navigation only
      // commits once the audio for that turn has drained, so a finalized
      // caption at this point is genuinely over.
      //
      // A caption still streaming is left alone: that is the assistant
      // talking right now, and clearing it would blank a live subtitle.
      setActiveCaption((current) => (current === null || current.isFinal ? null : current));
      send(intakeClientEvent.screenAck(target));
    },
    [send],
  );

  /**
   * Holds a screen change until the speech it follows has actually played.
   *
   * Re-measures rather than trusting one reading. The old version took a
   * single snapshot of the queue when the frame arrived and armed a
   * wall-clock timer for exactly that long, which is only correct if no
   * further audio for the same turn ever arrives — and for the greeting,
   * which Nova streams in several completions, more always does. The
   * screen then moved partway through the introduction.
   *
   * A blocked context is treated as an empty queue, not a full one. Its
   * clock never advances, so what it reports only grows and a hold against
   * it would never be released — stranding the one patient who has nothing
   * but the screen and the captions to follow.
   */
  const holdNavigationUntilPlayed = useCallback(
    (target: IntakeScreenName): void => {
      clearTimer(pendingNavigationTimerRef);
      pendingNavigationRef.current = target;

      const playback = playbackRef.current;
      const queuedMs =
        playback === null || playback.isBlocked() ? 0 : playback.remainingPlaybackMs();
      const budgetMs = navigationHoldDeadlineRef.current - Date.now();
      const waitMs = Math.min(queuedMs, budgetMs);
      if (waitMs <= 0) {
        commitNavigation(target);
        return;
      }

      pendingNavigationTimerRef.current = window.setTimeout(() => {
        pendingNavigationTimerRef.current = null;
        holdNavigationRef.current(target);
      }, waitMs);
    },
    [commitNavigation],
  );
  holdNavigationRef.current = holdNavigationUntilPlayed;

  /**
   * Re-evaluates a waiting screen change after the queue was emptied.
   *
   * A flush leaves the hold waiting on audio that will never play, and the
   * timer it armed still carries the old, long delay — so without this the
   * patient sits on the previous topic for that whole stretch.
   */
  const releaseNavigationHold = useCallback((): void => {
    const held = pendingNavigationRef.current;
    if (held !== null) holdNavigationRef.current(held);
  }, []);

  /** Puts a held caption on screen, at the moment its audio starts playing. */
  const revealPendingCaption = useCallback((): void => {
    clearTimer(pendingCaptionTimerRef);
    const held = pendingCaptionRef.current;
    pendingCaptionRef.current = null;
    if (held !== null) setActiveCaption(held);
  }, []);

  const handleServerFrame = useCallback(
    (raw: unknown): void => {
      const event = parseIntakeServerEvent(raw);
      // An unrecognized frame is ignored, not fatal: an older build against
      // a newer server must not lose a call that is otherwise working.
      if (event === null) return;

      switch (event.type) {
        case 'connected': {
          if (startedAtRef.current === null) {
            startedAtRef.current = Date.now();
          }
          // Deferred, not immediate: a connection that dies seconds after
          // opening has not proved anything, and crediting it would make
          // a flapping server an unbounded loop rather than five tries.
          clearTimer(stableTimerRef);
          stableTimerRef.current = window.setTimeout(() => {
            stableTimerRef.current = null;
            attemptsRef.current = 0;
          }, CONNECTION_STABLE_MS);
          // Sequences are numbered per connection and restart at 1, so
          // carrying the previous socket's high-water mark across a reconnect
          // made every later move look stale and dropped it silently. Reset
          // on starting a call already; a reconnect never goes through that.
          navSequenceRef.current = 0;
          setStatus('live');
          setProblem(null);
          setScreen(event.screen);
          setVisitedScreens((current) =>
            current.includes(event.screen) ? current : [...current, event.screen],
          );
          setPrefill((current) => ({ ...event.prefill, ...current }));
          setSelections((current) => ({ ...event.selections, ...current }));
          // Replaces rather than merges: the server's snapshot is the whole
          // record, and a reconnect must not resurrect a question that was
          // answered while this client was away.
          setSymptomIntake(toSymptomIntake(event.symptoms));
          send(intakeClientEvent.screenAck(event.screen));
          break;
        }

        case 'agent_audio':
          playbackRef.current?.enqueue(event.audio, event.sample_rate);
          // Audio in the queue IS the agent speaking, whatever the server
          // last said -- and this is what recovers the indicator when the
          // model never sends its completion event at all.
          settleSpeaking();
          break;

        case 'transcript': {
          // A new turn is whatever isn't a continuation of the one already
          // showing: a role switch, or another turn from the same role once
          // the last one finalized. Anything else is that same turn still
          // growing, and keeps its id.
          const current = activeTurnRef.current;
          const isNewTurn = current === null || current.role !== event.role || current.finalized;
          const turnId = isNewTurn
            ? `${event.role}-${(captionTurnCounterRef.current += 1)}`
            : current.turnId;
          activeTurnRef.current = { role: event.role, turnId, finalized: event.is_final };
          const caption: IntakeCaption = {
            role: event.role,
            text: event.text,
            isFinal: event.is_final,
            turnId,
          };
          if (event.is_final) appendTurn(event.role, event.text);

          // Already waiting on its audio: keep the line up to date, but do
          // not put it on screen ahead of the words it captions.
          if (pendingCaptionRef.current?.turnId === turnId) {
            pendingCaptionRef.current = caption;
            break;
          }

          // What is still queued when a new assistant turn first appears is
          // everything said *before* it, so that is exactly how long until
          // this turn is audible. That rests on Nova emitting an
          // utterance's planned text ahead of its own audio, which is the
          // same ordering the server's transcript assembler is built on.
          // The patient's own turns are never held: they are speaking now.
          //
          // Nothing is held while the browser is refusing to play: a
          // suspended `AudioContext` never advances its clock, so the queue
          // only ever grows and every caption would wait forever -- on the
          // one patient who has nothing but captions to read.
          const playback = playbackRef.current;
          const queuedMs =
            isNewTurn && event.role === 'agent' && playback !== null && !playback.isBlocked()
              ? playback.remainingPlaybackMs()
              : 0;
          if (queuedMs > 0) {
            clearTimer(pendingCaptionTimerRef);
            pendingCaptionRef.current = caption;
            pendingCaptionTimerRef.current = window.setTimeout(
              revealPendingCaption,
              Math.min(queuedMs, MAX_CAPTION_HOLD_MS),
            );
            break;
          }

          setActiveCaption(caption);
          break;
        }

        case 'agent_speaking':
          // What the frame actually reports is whether the MODEL is still
          // producing, which stops long before the patient stops hearing.
          isGeneratingRef.current = event.speaking;
          settleSpeaking();
          break;

        case 'interrupted': {
          // The model has abandoned the rest of that utterance, so playing
          // it out would talk over the patient seconds after they spoke.
          playbackRef.current?.flush();
          isGeneratingRef.current = false;
          settleSpeaking();
          // A caption still waiting on that audio is captioning words the
          // patient will now never hear, so it is dropped rather than
          // revealed -- unlike the screen change below, which was the
          // server's instruction and still has to happen.
          clearTimer(pendingCaptionTimerRef);
          pendingCaptionRef.current = null;
          // A screen change waiting on that now-flushed audio has nothing
          // left to wait for, so it happens now. Cancelling it instead
          // stranded the patient on the previous topic permanently.
          releaseNavigationHold();
          break;
        }

        case 'navigate': {
          if (event.sequence <= navSequenceRef.current) return;
          navSequenceRef.current = event.sequence;

          // Recorded on arrival, before any hold: a screen superseded by a
          // later frame while still waiting was still a topic the call
          // covered, and the closing recap is built from this list.
          setVisitedScreens((current) =>
            current.includes(event.screen) ? current : [...current, event.screen],
          );

          // The model calls `navigate_to_screen` the moment it is done
          // generating that turn, seconds before the patient's browser
          // finishes playing the audio for it. Moving the screen right away
          // would show the next topic's question while the previous one is
          // still audible -- most jarring on `welcome`, whose whole point is
          // to finish before anything else does.
          //
          // The budget is set here, per frame, so a hold that keeps
          // re-arming against audio that keeps arriving still has an end.
          navigationHoldDeadlineRef.current = Date.now() + MAX_NAVIGATION_HOLD_MS;
          holdNavigationUntilPlayed(event.screen);
          break;
        }

        case 'form_prefill':
          setPrefill((current) => ({
            ...current,
            [event.screen]: { ...current[event.screen], ...event.fields },
          }));
          // Merged, not replaced, and kept even when this frame carries no
          // ids for the field: a later turn that only corrects the wording
          // must not un-tick a card the agent already committed to.
          setSelections((current) => ({
            ...current,
            [event.screen]: { ...current[event.screen], ...event.selections },
          }));
          break;

        case 'symptom_state':
          setSymptomIntake(toSymptomIntake(event));
          break;

        case 'upload_requested':
          setIsUploadRequested(true);
          break;

        case 'appointment_updated':
          // Read back from the session route rather than patched from this
          // frame: the screen the patient is about to be sent back to shows
          // the appointment, and a locally patched copy is a second version
          // of it that can disagree with the server.
          onAppointmentChangedRef.current?.();
          break;

        case 'call_ended': {
          // Only `completed` actually ends the call. The other two mean
          // this CONNECTION ended and the session is still resumable, so
          // treating them as terminal is what left the patient staring at
          // a dead screen after a network blip.
          if (event.reason !== 'completed' && !endedDeliberatelyRef.current) {
            resumeAfterDrop();
            break;
          }
          const problem = event.reason === 'completed' ? null : CONNECTION_PROBLEM;
          // A completed call ends on a spoken goodbye that is still
          // playing. Anything else already stopped talking, so waiting
          // would only leave the patient staring at a dead screen.
          const farewellMs =
            event.reason === 'completed'
              ? Math.min(playbackRef.current?.remainingPlaybackMs() ?? 0, FAREWELL_DRAIN_CAP_MS)
              : 0;
          if (farewellMs > 0) {
            settleSpeaking();
            endTimerRef.current = window.setTimeout(() => {
              endTimerRef.current = null;
              finish(event.reason, problem);
            }, farewellMs);
          } else {
            finish(event.reason, problem);
          }
          break;
        }

        case 'error':
          // A recoverable error is a status line, not the end of the call —
          // the socket is still open and the agent may well continue.
          if (event.recoverable) {
            setProblem({ kind: 'agent', message: event.message, recoverable: true });
          } else {
            finish('failed', { kind: 'agent', message: event.message, recoverable: false });
          }
          break;

        case 'pong':
          clearTimer(pongTimerRef);
          break;

        case 'agent_ready':
          setProblem(null);
          break;
      }
    },
    [
      appendTurn,
      finish,
      holdNavigationUntilPlayed,
      releaseNavigationHold,
      resumeAfterDrop,
      revealPendingCaption,
      send,
      settleSpeaking,
    ],
  );

  const startHeartbeat = useCallback((): void => {
    stopHeartbeat();
    pingTimerRef.current = window.setInterval(() => {
      if (pongTimerRef.current !== null) return; // a probe is already outstanding
      send(intakeClientEvent.ping());
      pongTimerRef.current = window.setTimeout(() => {
        pongTimerRef.current = null;
        // Closing rather than waiting: the socket reports itself open but
        // nothing is getting through, which no browser event will tell us.
        socketRef.current?.close();
      }, PONG_TIMEOUT_MS);
    }, PING_INTERVAL_MS);
  }, [send, stopHeartbeat]);

  const connect = useCallback(async (): Promise<void> => {
    // Checked and set in the same synchronous step, before any await.
    if (isFinishedRef.current || isConnectingRef.current || socketRef.current !== null) return;
    isConnectingRef.current = true;
    setStatus((current) => (current === 'reconnecting' ? current : 'connecting'));

    let ticket: string;
    try {
      // Requested per connection, including every reconnect: a ticket is
      // single-use and expires in about a minute, so one cannot be cached.
      ticket = await requestWsTicket(sessionId);
    } catch (cause) {
      isConnectingRef.current = false;
      const error = toApiError(cause);
      if (error.kind === 'unauthorized' || error.kind === 'not-found') {
        finish('failed', SESSION_PROBLEM);
        return;
      }
      scheduleReconnect();
      return;
    }

    if (isFinishedRef.current) {
      isConnectingRef.current = false;
      return;
    }

    let socket: WebSocket;
    try {
      socket = new WebSocket(buildIntakeSocketUrl(sessionId, ticket));
    } catch {
      isConnectingRef.current = false;
      scheduleReconnect();
      return;
    }
    // From here `socketRef` itself guards re-entry, so the in-flight flag
    // has done its job.
    socketRef.current = socket;
    isConnectingRef.current = false;

    socket.onmessage = (message: MessageEvent<string>) => {
      try {
        handleServerFrame(JSON.parse(message.data));
      } catch {
        // Not JSON at all — nothing this app sent could have caused it.
      }
    };

    socket.onopen = () => {
      startHeartbeat();
    };

    socket.onclose = (closeEvent) => {
      if (socketRef.current !== socket) return; // superseded by a newer socket
      socketRef.current = null;
      stopHeartbeat();
      clearTimer(stableTimerRef);
      if (isFinishedRef.current) return;
      // A policy-violation close is the backend refusing the session, not a
      // network problem: retrying it would fail identically five times.
      if (closeEvent.code === POLICY_VIOLATION) {
        finish('failed', SESSION_PROBLEM);
        return;
      }
      scheduleReconnect();
    };

    socket.onerror = () => {
      // Always followed by `onclose`, which owns the recovery decision.
    };
  }, [finish, handleServerFrame, scheduleReconnect, sessionId, startHeartbeat, stopHeartbeat]);

  connectRef.current = connect;

  /**
   * Acquires audio and connects, in that order.
   *
   * Called automatically as soon as the session lands — there is no Start
   * button, because a patient who has opened their prescreening link has
   * already said they want to begin, and asking them to say it twice is a
   * step that earns nothing.
   *
   * What that costs is autoplay: an `AudioContext` created outside a user
   * gesture starts suspended on most browsers. Rather than reintroduce a
   * button for everyone, this reports `needsAudioUnlock` and the UI offers
   * one tap only to the patients who actually need it. The call itself
   * connects and runs regardless, so captions and typed answers work even
   * if playback never unblocks.
   */
  const start = useCallback(async (): Promise<void> => {
    // A ref, not `status`: state does not update within the tick, so two
    // callers in the same tick would both arm the microphone and open a
    // second call.
    if (isStartingRef.current) return;
    isStartingRef.current = true;
    setStatus('starting');
    setProblem(null);
    isFinishedRef.current = false;

    const playback = createAgentPlayback();
    playbackRef.current = playback;
    setAgentStream(playback?.stream ?? null);
    if (playback === null) {
      setProblem({
        kind: 'unsupported',
        message: 'This browser cannot play audio. You can still type your answers.',
        recoverable: false,
      });
    } else {
      await playback.resume();
      setNeedsAudioUnlock(playback.isBlocked());
    }

    try {
      const mic = await startMicCapture((frame) => send(intakeClientEvent.audio(frame)));
      micRef.current = mic;
      setMicStream(mic.stream);
    } catch (cause) {
      // A patient who cannot or will not use a microphone is never blocked:
      // the call still connects, and every screen keeps its typed path.
      const reason = cause instanceof MicCaptureError ? cause : null;
      setProblem({
        kind: reason?.reason === 'denied' ? 'permission' : 'microphone',
        message:
          reason?.message ?? 'We could not reach your microphone. Type your answers instead.',
        recoverable: true,
      });
    }

    if (canStart) await connect();
  }, [canStart, connect, send]);

  // Starts the call the moment the session is real. Both `start` and
  // `connect` refuse re-entry synchronously, so this effect re-running --
  // which React does on every remount, and always once in development --
  // cannot open a second call.
  useEffect(() => {
    if (!canStart) return;
    void start();
  }, [canStart, start]);

  const retry = useCallback(async (): Promise<void> => {
    endedDeliberatelyRef.current = false;
    isConnectingRef.current = false;
    attemptsRef.current = 0;
    navSequenceRef.current = 0;
    isFinishedRef.current = false;
    setEndReason(null);
    setProblem(null);
    if (micRef.current === null && playbackRef.current === null) {
      setStatus('idle');
      return;
    }
    await connect();
  }, [connect]);

  const hangUp = useCallback((): void => {
    // Told to the server first: it is what turns a dropped socket into a
    // deliberate end, and the session's own state depends on the
    // difference. The socket then closes from the server's side.
    endedDeliberatelyRef.current = true;
    send(intakeClientEvent.control('hangup'));
    finish('interrupted', null);
  }, [finish, send]);

  const setMuted = useCallback(
    (muted: boolean): void => {
      setIsMutedState(muted);
      micRef.current?.setMuted(muted);
      // Sent as well as applied locally: mute is a privacy control, and one
      // that only the client enforces is not one.
      send(intakeClientEvent.control(muted ? 'mute' : 'unmute'));
    },
    [send],
  );

  const setOnHold = useCallback(
    (onHold: boolean): void => {
      setIsOnHoldState(onHold);
      micRef.current?.setMuted(onHold || isMuted);
      playbackRef.current?.setMuted(onHold);
      if (onHold) {
        playbackRef.current?.flush();
        // The queue a waiting screen change was holding against is gone, so
        // its timer is now counting out speech nobody will hear.
        releaseNavigationHold();
      }
      send(intakeClientEvent.control(onHold ? 'hold' : 'resume'));
    },
    [isMuted, releaseNavigationHold, send],
  );

  const sendTypedAnswer = useCallback(
    (text: string): void => {
      const trimmed = text.trim();
      if (trimmed.length === 0) return;
      send(intakeClientEvent.text(trimmed));
      appendTurn('patient', trimmed);
    },
    [appendTurn, send],
  );

  /**
   * Sends one field's value as the patient's own turn.
   *
   * Call this only when the patient explicitly submits — Send button or
   * Enter, via `AnswerInput` — never on every keystroke. It used to fire
   * itself on a debounced pause in typing, which meant a patient pausing
   * mid-sentence to think had that partial sentence sent and treated as
   * the finished answer.
   */
  const reportFieldEdit = useCallback(
    (editedScreen: PrescreeningFlowStep, field: string, value: string): void => {
      send(intakeClientEvent.formUpdate(editedScreen, field, value));
    },
    [send],
  );

  /** Sends one symptom question's answer. Same explicit-submit-only contract as `reportFieldEdit`. */
  const reportSymptomAnswer = useCallback(
    (questionId: string, value: string): void => {
      send(intakeClientEvent.symptomAnswer(questionId, value));
    },
    [send],
  );

  const reportDocumentUploaded = useCallback(
    (fileName: string): void => {
      send(intakeClientEvent.documentUploaded(fileName));
    },
    [send],
  );

  const reportDocumentUploadFailed = useCallback((): void => {
    send(intakeClientEvent.documentUploadFailed());
  }, [send]);

  const dismissUploadRequest = useCallback((): void => setIsUploadRequested(false), []);

  const unlockAudio = useCallback((): void => {
    const playback = playbackRef.current;
    if (playback === null) return;
    // Fire-and-forget from inside the tap: awaiting first would put the
    // resume outside the gesture, which is the one thing that makes it work.
    void playback.resume().then(() => setNeedsAudioUnlock(playback.isBlocked()));
  }, []);

  const notifyConsentRecorded = useCallback((): void => {
    send(intakeClientEvent.consentRecorded());
  }, [send]);

  const requestReschedule = useCallback((): void => {
    send(intakeClientEvent.rescheduleRequested());
  }, [send]);

  const reportAppointmentRescheduled = useCallback((): void => {
    send(intakeClientEvent.appointmentRescheduled());
  }, [send]);

  useEffect(() => {
    // Re-armed on every mount, not just at the initializer: React runs an
    // effect's cleanup and then re-runs it on a remount (and always does so
    // once in development), so a flag only ever set to false would leave a
    // live call unable to report that it had ended.
    isMountedRef.current = true;

    return () => {
      isMountedRef.current = false;
      isFinishedRef.current = true;
      isConnectingRef.current = false;
      isStartingRef.current = false;
      stopHeartbeat();
      clearTimer(reconnectTimerRef);
      clearTimer(pendingNavigationTimerRef);
      clearTimer(pendingCaptionTimerRef);
      clearTimer(endTimerRef);
      clearTimer(speakingTimerRef);
      clearTimer(stableTimerRef);
      socketRef.current?.close();
      socketRef.current = null;
      void micRef.current?.close();
      void playbackRef.current?.close();
      micRef.current = null;
      playbackRef.current = null;
    };
  }, [stopHeartbeat]);

  return useMemo(
    () => ({
      status,
      problem,
      endReason,
      micStream,
      agentStream,
      isAgentSpeaking,
      isMuted,
      isOnHold,
      activeCaption,
      turns,
      screen,
      visitedScreens,
      prefill,
      selections,
      symptomIntake,
      callDurationSeconds,
      isUploadRequested,
      needsAudioUnlock,
      unlockAudio,
      start,
      hangUp,
      retry,
      setMuted,
      setOnHold,
      sendTypedAnswer,
      reportFieldEdit,
      reportSymptomAnswer,
      notifyConsentRecorded,
      requestReschedule,
      reportAppointmentRescheduled,
      reportDocumentUploaded,
      reportDocumentUploadFailed,
      dismissUploadRequest,
    }),
    [
      status,
      problem,
      endReason,
      micStream,
      agentStream,
      isAgentSpeaking,
      isMuted,
      isOnHold,
      activeCaption,
      turns,
      screen,
      visitedScreens,
      prefill,
      selections,
      symptomIntake,
      callDurationSeconds,
      isUploadRequested,
      needsAudioUnlock,
      unlockAudio,
      start,
      hangUp,
      retry,
      setMuted,
      setOnHold,
      sendTypedAnswer,
      reportFieldEdit,
      reportSymptomAnswer,
      notifyConsentRecorded,
      requestReschedule,
      reportAppointmentRescheduled,
      reportDocumentUploaded,
      reportDocumentUploadFailed,
      dismissUploadRequest,
    ],
  );
}

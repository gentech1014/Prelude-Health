import { useCallback, useMemo, useRef, useState } from 'react';
import type { PrescreeningFlowStep } from '@/features/prescreening-session/prescreeningFlowSteps';
import {
  formatRecordedPairs,
  matchBoolean,
  matchLabel,
  matchOptions,
  parseRecordedPairs,
  parseSelectedIds,
  type MatchableOption,
  type RecordedPair,
} from '@/features/prescreening-session/prefillMatching';
import { useIntakeCall } from '@/features/prescreening-session/useIntakeCall';

/**
 * Binds one screen control to what the agent heard, and reports what the
 * patient types back into the same conversation.
 *
 * Three rules across all of these:
 *
 * - **The server leads until the patient touches the control.** Prefill
 *   arrives asynchronously and can arrive twice, so a control seeded from
 *   it once with `useState` would stay stale.
 * - **The patient wins after that.** Once they have corrected a value, no
 *   later prefill overwrites it — the whole point of showing the value is
 *   that they can fix a mishearing.
 * - **An id the agent chose beats an id the browser inferred.** For every
 *   control with a fixed set of options the agent may name the option
 *   directly; only where it declined does the value fall back to matching
 *   its words. The words are still what the patient reads and corrects.
 *
 * Nothing here is a source of clinical truth. Every edit is sent to the
 * server as the patient's own turn, and the transcript is what the report
 * is built from.
 */

/** What the agent recorded for one field, if anything. */
function useRecorded(screen: PrescreeningFlowStep, field: string): string | undefined {
  const { prefill } = useIntakeCall();
  return prefill[screen]?.[field];
}

/**
 * Which option ids the agent picked for one field, if it picked any.
 *
 * `undefined` and "picked nothing" are the same answer here, and both mean
 * fall back to the words — the server drops a field whose ids were all
 * unrecognized rather than sending an empty string, precisely so an empty
 * selection cannot be mistaken for a deliberate one.
 */
function useSelected(screen: PrescreeningFlowStep, field: string): string | undefined {
  const { selections } = useIntakeCall();
  return selections[screen]?.[field];
}

/**
 * Tracks whether the patient has taken over a control.
 *
 * Deliberately does not send anything itself: whether taking ownership
 * should also report the value depends on the control (a click reports
 * immediately; typed text waits for an explicit submit — see
 * `usePrefilledText` and `usePrefilledPairs`), so that decision is left to
 * each hook below rather than baked in here.
 */
function useOwnership(): { isOwned: boolean; markOwned: () => void } {
  const [isOwned, setIsOwned] = useState(false);
  const ownedRef = useRef(false);

  const markOwned = useCallback(() => {
    if (ownedRef.current) return;
    ownedRef.current = true;
    setIsOwned(true);
  }, []);

  return { isOwned, markOwned };
}

export interface PrefilledText {
  value: string;
  onChange: (value: string) => void;
  /**
   * Sends the current value as the patient's own turn. Call this only from
   * an explicit action — a Send button or Enter (see `AnswerInput`) — never
   * on every keystroke; a debounced auto-send used to treat a pause for
   * thought as a finished answer. A no-op until the patient has actually
   * edited the field, so confirming a prefilled value by leaving it alone
   * does not re-send it as though it were a correction.
   */
  onSubmit: () => void;
  /** True while the value on screen is what the agent heard, not what the patient typed. */
  isFromAgent: boolean;
}

/** A free-text field: a note, a narrative, a phone number. */
export function usePrefilledText(
  screen: PrescreeningFlowStep,
  field: string,
  fallback = '',
): PrefilledText {
  const recorded = useRecorded(screen, field);
  const { reportFieldEdit } = useIntakeCall();
  const { isOwned, markOwned } = useOwnership();
  const [typed, setTyped] = useState('');

  const value = isOwned ? typed : (recorded ?? fallback);

  return {
    value,
    isFromAgent: !isOwned && recorded !== undefined,
    onChange: (next) => {
      markOwned();
      setTyped(next);
    },
    onSubmit: () => {
      if (!isOwned) return;
      reportFieldEdit(screen, field, typed);
    },
  };
}

export interface PrefilledMultiSelect<TId extends string> {
  selectedIds: readonly TId[];
  toggle: (id: TId) => void;
}

/** A multi-select card grid: concerns, conditions, family history. */
export function usePrefilledMultiSelect<TId extends string>(
  screen: PrescreeningFlowStep,
  field: string,
  options: readonly MatchableOption<TId>[],
): PrefilledMultiSelect<TId> {
  const recorded = useRecorded(screen, field);
  const selected = useSelected(screen, field);
  const { reportFieldEdit } = useIntakeCall();
  const { isOwned, markOwned } = useOwnership();
  const [picked, setPicked] = useState<readonly TId[]>([]);

  const matched = useMemo(() => {
    const chosen = parseSelectedIds(selected, options);
    if (chosen !== null) return chosen;
    return recorded === undefined ? [] : matchOptions(recorded, options);
  }, [options, recorded, selected]);
  const selectedIds = isOwned ? picked : matched;

  const toggle = useCallback(
    (id: TId) => {
      const next = selectedIds.includes(id)
        ? selectedIds.filter((selected) => selected !== id)
        : [...selectedIds, id];
      markOwned();
      setPicked(next);
      // Reported as labels, not ids: the value goes into the patient's own
      // transcript, where `chronic-condition-management` reads as noise.
      // A click is one discrete action, not typed text, so it reports
      // immediately rather than waiting on a Send action.
      reportFieldEdit(
        screen,
        field,
        next
          .map((selected) => options.find((option) => option.id === selected)?.label ?? selected)
          .join(', '),
      );
    },
    [field, markOwned, options, reportFieldEdit, screen, selectedIds],
  );

  return { selectedIds, toggle };
}

export interface PrefilledChoice {
  value: string | null;
  onChange: (value: string) => void;
}

/** A single-choice pill row whose options are plain labels. */
export function usePrefilledChoice(
  screen: PrescreeningFlowStep,
  field: string,
  labels: readonly string[],
): PrefilledChoice {
  const recorded = useRecorded(screen, field);
  const selected = useSelected(screen, field);
  const { reportFieldEdit } = useIntakeCall();
  const { isOwned, markOwned } = useOwnership();
  const [picked, setPicked] = useState<string | null>(null);

  const matched = useMemo(() => {
    // The pill rows use the visible label as the value, so a validated id
    // is already the value — there is nothing to map.
    const chosen = labels.find((label) => label.toLowerCase() === selected?.trim().toLowerCase());
    if (chosen !== undefined) return chosen;
    return recorded === undefined ? null : matchLabel(recorded, labels);
  }, [labels, recorded, selected]);

  return {
    value: isOwned ? picked : matched,
    onChange: (next) => {
      markOwned();
      setPicked(next);
      reportFieldEdit(screen, field, next);
    },
  };
}

export interface PrefilledBoolean {
  value: boolean | null;
  onChange: (value: boolean) => void;
}

/**
 * A yes/no question. Stays null when the agent heard something that is
 * neither, so an uncertain answer is never rendered as a denial.
 */
export function usePrefilledBoolean(screen: PrescreeningFlowStep, field: string): PrefilledBoolean {
  const recorded = useRecorded(screen, field);
  const selected = useSelected(screen, field);
  const { reportFieldEdit } = useIntakeCall();
  const { isOwned, markOwned } = useOwnership();
  const [picked, setPicked] = useState<boolean | null>(null);

  // The agent's own answer first. These are the fields where reading the
  // words is genuinely ambiguous — a bare "no" against `no_known_allergies`
  // could be either polarity — and the id is the field's proposition
  // answered directly, so it settles what the text cannot.
  const chosen = selected?.trim().toLowerCase();
  const matched =
    chosen === 'yes' || chosen === 'no'
      ? chosen === 'yes'
      : recorded === undefined
        ? null
        : matchBoolean(recorded);

  return {
    value: isOwned ? picked : matched,
    onChange: (next) => {
      markOwned();
      setPicked(next);
      reportFieldEdit(screen, field, next ? 'yes' : 'no');
    },
  };
}

export interface PrefilledPairs {
  entries: readonly (RecordedPair & { id: string })[];
  change: (id: string, part: 'primary' | 'secondary', value: string) => void;
  add: () => void;
  remove: (id: string) => void;
  /**
   * Sends every row as the patient's own turn. Call this only from an
   * explicit Send action on the field that was just edited — see
   * `PrefilledText.onSubmit` for why typed text doesn't report itself.
   * `add`/`remove` are discrete actions, not typed text, and report
   * immediately on their own.
   */
  submit: () => void;
}

/**
 * A repeating two-part list: allergen/reaction, hospital stay/timeframe.
 *
 * Always renders at least one empty row so the patient has somewhere to
 * type, and never lets the last row be removed.
 */
export function usePrefilledPairs(screen: PrescreeningFlowStep, field: string): PrefilledPairs {
  const recorded = useRecorded(screen, field);
  const { reportFieldEdit } = useIntakeCall();
  const { isOwned, markOwned } = useOwnership();
  const [edited, setEdited] = useState<readonly (RecordedPair & { id: string })[]>([]);
  const nextId = useRef(0);

  const matched = useMemo(() => {
    const parsed = recorded === undefined ? [] : parseRecordedPairs(recorded);
    const rows = parsed.length > 0 ? parsed : [{ primary: '', secondary: '' }];
    return rows.map((pair, index) => ({ ...pair, id: `${field}-${index}` }));
  }, [field, recorded]);

  const entries = isOwned ? edited : matched;

  return {
    entries,
    // Typed text: updates the row locally only. Reporting waits for an
    // explicit submit, the same as `usePrefilledText`.
    change: (id, part, value) => {
      markOwned();
      setEdited(entries.map((entry) => (entry.id === id ? { ...entry, [part]: value } : entry)));
    },
    // Adding/removing a row is a discrete click, not typed text with a
    // "pause to think" problem, so it reports immediately — including
    // whatever is already typed into the other rows at that moment.
    add: () => {
      nextId.current += 1;
      const next = [...entries, { id: `${field}-new-${nextId.current}`, primary: '', secondary: '' }];
      markOwned();
      setEdited(next);
      reportFieldEdit(screen, field, formatRecordedPairs(next));
    },
    remove: (id) => {
      const next = entries.length > 1 ? entries.filter((entry) => entry.id !== id) : entries;
      markOwned();
      setEdited(next);
      reportFieldEdit(screen, field, formatRecordedPairs(next));
    },
    submit: () => {
      if (!isOwned) return;
      reportFieldEdit(screen, field, formatRecordedPairs(entries));
    },
  };
}

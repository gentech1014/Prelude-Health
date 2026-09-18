/**
 * Turns what the agent heard into values a screen's own controls can hold.
 *
 * This is the **fallback** path. The agent now names option ids outright
 * (`selections` on the `form_prefill` frame), because it is the thing in
 * the system that actually understood the sentence: a patient who says
 * "my sugar's been bad" named diabetes, and the model knew that. What
 * remains here is for the fields it left unclaimed — and for older frames,
 * where words are all there is.
 *
 * Three rules hold everything below together:
 *
 * - **Token boundaries, never raw substrings.** Matching on `includes`
 *   made "heart" reachable from any sentence that happened to contain it.
 * - **A negation is not an answer.** "No diabetes", "tested negative for
 *   diabetes" and "diabetes" are not the same claim. The earlier matcher
 *   ticked the Diabetes card for all three, which is the one failure mode
 *   that puts a fabricated condition in front of a physician.
 * - **An unmatched value stays unmatched.** Guessing which checkbox a
 *   patient meant is the invention the clinical rules forbid, and the
 *   transcript already carries what they actually said.
 */

import {
  NEGATIVE_OPTION_IDS,
  OPTION_ALIASES,
} from '@/features/prescreening-session/prefillAliases';

/** One selectable option, as the screens define them. */
export interface MatchableOption<TId extends string> {
  id: TId;
  label: string;
}

/**
 * What separates one listed item from the next.
 *
 * Deliberately *not* " and ". Splitting on it ran before the negation
 * guard, so a denial covering two things only suppressed the first:
 * "no heart disease and diabetes" became the fragments "no heart disease"
 * and "diabetes", and the second had no cue left in it — so a condition
 * the patient had just ruled out was ticked on their screen and sent to
 * their doctor.
 *
 * Leaving conjunctions inside the fragment costs nothing, because
 * `matchOptions` already collects every option that hits within one
 * fragment: "diabetes and high blood pressure" still matches both. What
 * it buys is that the backward scan in `isNegated` can see the "no".
 */
const SEPARATORS = /[,;\n]/;

/**
 * Lowercase, apostrophe-free, single-spaced.
 *
 * Apostrophes are *removed* rather than replaced with a space, so "don't"
 * survives as one token. Splitting it gave "don" and "t", and the negation
 * guard then saw no cue at all — which read "I don't smoke" as a current
 * smoker.
 */
function normalize(value: string): string {
  return value
    .toLowerCase()
    .replace(/['’ʼ]/g, '')
    .replace(/[^a-z0-9]+/g, ' ')
    .trim();
}

function tokenize(value: string): string[] {
  const normalized = normalize(value);
  return normalized.length === 0 ? [] : normalized.split(' ');
}

/** Split a recorded value into the individual items the patient listed. */
export function splitRecordedList(value: string): string[] {
  return value
    .split(SEPARATORS)
    .map((part) => part.trim())
    .filter((part) => part.length > 0);
}

// --------------------------------------------------------------------------
// Negation
// --------------------------------------------------------------------------

const NEGATION_CUES = new Set([
  'no',
  'not',
  'never',
  'none',
  'nothing',
  'neither',
  'nor',
  'without',
  'nope',
  'denies',
  'denied',
  'deny',
  'negative',
  'free',
  'clear',
  'ruled',
  // Contractions, which `normalize` keeps whole. Spelled out rather than
  // matched by suffix so "int" and "cant" cannot be confused for cues.
  'dont',
  'doesnt',
  'didnt',
  'hasnt',
  'havent',
  'hadnt',
  'isnt',
  'arent',
  'wasnt',
  'werent',
  'wont',
  'cant',
  'couldnt',
  'wouldnt',
  'shouldnt',
]);

const CLAUSE_BREAKS = new Set(['but', 'however', 'although', 'though', 'except', 'apart']);

/**
 * Whether the phrase starting at `index` sits inside a denial.
 *
 * Walks backwards from the match to the start of its clause, stopping at
 * a clause break: in "no asthma but I do have diabetes", the `but` is
 * what keeps the denial off diabetes. Deliberately syntactic and shallow
 * — it catches how people actually deny things out loud, and makes no
 * attempt at the general case.
 *
 * The scan used to stop after four tokens instead. That covered "no
 * history of diabetes" and stopped one word short of how people actually
 * talk: "I don't have any history of heart disease" puts six tokens
 * between the denial and the condition, so the cue was never seen and the
 * Heart disease card was ticked for a patient who had just ruled it out.
 * A fabricated condition in front of a physician is the worst thing this
 * file can produce, so the bound is now the clause rather than a count —
 * clause breaks are what stop a denial running on, and they do it on
 * meaning rather than on distance.
 */
function isNegated(tokens: readonly string[], index: number): boolean {
  for (let position = index - 1; position >= 0; position -= 1) {
    const token = tokens[position] ?? '';
    if (CLAUSE_BREAKS.has(token)) return false;
    if (NEGATION_CUES.has(token)) return true;
  }
  return false;
}

// --------------------------------------------------------------------------
// Phrase matching
// --------------------------------------------------------------------------

/** Where a phrase was found, or -1. Whole tokens only. */
function findPhrase(tokens: readonly string[], phrase: readonly string[]): number {
  if (phrase.length === 0 || phrase.length > tokens.length) return -1;
  for (let start = 0; start + phrase.length <= tokens.length; start += 1) {
    let matched = true;
    for (let offset = 0; offset < phrase.length; offset += 1) {
      if (tokens[start + offset] !== phrase[offset]) {
        matched = false;
        break;
      }
    }
    if (matched) return start;
  }
  return -1;
}

interface Candidate<TId extends string> {
  id: TId;
  /** Tokens in the matched phrase. Longer means more specific, so it wins. */
  weight: number;
  /** Where it matched, so an earlier mention breaks a tie. */
  at: number;
  negated: boolean;
}

/** Every phrase that identifies an option: its label, then its aliases. */
function phrasesFor<TId extends string>(option: MatchableOption<TId>): string[][] {
  const aliases = OPTION_ALIASES[option.id] ?? OPTION_ALIASES[option.label] ?? [];
  return [tokenize(option.label), ...aliases.map(tokenize)].filter((phrase) => phrase.length > 0);
}

/**
 * The best match for one option in one text, or null.
 *
 * "Best" is the longest phrase that hits: "high blood pressure" beats the
 * bare "pressure", so a sentence containing both is attributed once, to
 * the more specific reading.
 */
function bestCandidate<TId extends string>(
  tokens: readonly string[],
  option: MatchableOption<TId>,
): Candidate<TId> | null {
  let best: Candidate<TId> | null = null;
  for (const phrase of phrasesFor(option)) {
    const at = findPhrase(tokens, phrase);
    if (at < 0) continue;
    const better =
      best === null ||
      phrase.length > best.weight ||
      (phrase.length === best.weight && at < best.at);
    if (better) {
      best = { id: option.id, weight: phrase.length, at, negated: isNegated(tokens, at) };
    }
  }
  return best;
}

function strongest<TId extends string>(
  left: Candidate<TId> | null,
  right: Candidate<TId>,
): Candidate<TId> {
  if (left === null) return right;
  if (right.weight > left.weight) return right;
  if (right.weight === left.weight && right.at < left.at) return right;
  return left;
}

/**
 * The one option a recorded value refers to, or null.
 *
 * Never returns an option the text denied, and never falls back to an
 * `other`-style catch-all: that would swallow everything unrecognized and
 * present a guess as an answer.
 */
export function matchOption<TId extends string>(
  value: string,
  options: readonly MatchableOption<TId>[],
): TId | null {
  const tokens = tokenize(value);
  if (tokens.length === 0) return null;

  let best: Candidate<TId> | null = null;
  for (const option of options) {
    const candidate = bestCandidate(tokens, option);
    if (candidate === null || candidate.negated) continue;
    best = strongest(best, candidate);
  }
  return best?.id ?? null;
}

/**
 * Every option a recorded list refers to, in the order the list gave them.
 *
 * Each comma-separated fragment is matched on its own so a list names
 * several options, and a denial inside a fragment suppresses only that
 * fragment — "diabetes, no heart disease" is one condition, not two.
 */
export function matchOptions<TId extends string>(
  value: string,
  options: readonly MatchableOption<TId>[],
): TId[] {
  const matched: TId[] = [];
  for (const item of splitRecordedList(value)) {
    const tokens = tokenize(item);
    if (tokens.length === 0) continue;
    const hits = options
      .map((option) => bestCandidate(tokens, option))
      .filter((candidate): candidate is Candidate<TId> => candidate !== null && !candidate.negated)
      .sort((left, right) => left.at - right.at || right.weight - left.weight);
    for (const hit of hits) {
      if (!matched.includes(hit.id)) matched.push(hit.id);
    }
  }
  return matched;
}

/**
 * The single option a recorded value refers to, matched against plain labels.
 *
 * Used by the single-choice pill rows, where the visible label *is* the
 * value. Unlike `matchOption`, a sentence whose every candidate was denied
 * falls through to the row's negative option when it has one: "I don't
 * smoke" denies Current, and the answer that leaves behind is Never.
 */
export function matchLabel(value: string, labels: readonly string[]): string | null {
  const options = labels.map((label) => ({ id: label, label }));
  const direct = matchOption(value, options);
  if (direct !== null) return direct;

  const negativeLabel = labels.find((label) => NEGATIVE_OPTION_IDS.has(label));
  if (negativeLabel === undefined) return null;

  // Nothing affirmative matched, so the only reading left is a denial —
  // and `matchBoolean` is already the thing that knows a denial from a
  // doubt. "No, I don't drink" affirms no option and is Never; "I'm not
  // sure" and "I'd rather not say" also affirm nothing, and must stay
  // blank rather than be recorded as an answer the patient never gave.
  return matchBoolean(value) === false ? negativeLabel : null;
}

// --------------------------------------------------------------------------
// Yes / no
// --------------------------------------------------------------------------

const AFFIRMATIVE = new Set([
  'yes',
  'y',
  'yeah',
  'yep',
  'yup',
  'true',
  'correct',
  'right',
  'affirmative',
  'definitely',
  'absolutely',
]);

const UNCERTAIN = new Set(['unsure', 'maybe', 'perhaps', 'possibly', 'unknown', 'sure', 'know']);

/**
 * A recorded yes/no answer, or null when it is neither.
 *
 * Reads a phrase, not a bare word. The old exact-set check meant the very
 * value the agent naturally writes for `no_known_allergies` — "no known
 * allergies" — resolved to null and left the box unticked; every boolean
 * on the form only worked when the model happened to emit a lone "yes" or
 * "no". This scans for the first polarity cue instead, so "yes, a
 * cardiologist last month" and "not that I know of" both land.
 *
 * Null is a real answer, and the uncertainty check runs first: "I'm not
 * sure" contains a negation cue, and reading it as No would tell the
 * physician the patient denied something they never denied.
 *
 * One ambiguity this cannot resolve. A bare "no" against a field named
 * `no_known_allergies` could be the answer to the field's own proposition
 * or a denial of allergies, and those invert each other. That is why the
 * agent is asked to send `selections` for these fields, where the field
 * name is the proposition and `yes` means it holds. This is the fallback
 * for when it did not.
 */
const REFUSALS = [
  ['rather', 'not'],
  ['prefer', 'not'],
  ['decline'],
  ['declined'],
  ['no', 'comment'],
  ['skip'],
];
/** Phrases that decline the question. Neither answer, and not a denial.

Kept apart from `UNCERTAIN` because they are a different thing being
said — "I would rather not say" is a boundary, not a doubt — and because
both would otherwise be read as No: they contain a negation cue. */

/**
 * The part of an answer that actually answers, before any elaboration.
 *
 * Punctuation has to be cut here rather than after `tokenize`, which
 * replaces every comma with a space — so by the time a value is tokens,
 * the boundary between "yes" and everything the patient went on to say
 * is gone.
 */
function firstClause(value: string): string {
  const upToPunctuation = value.split(/[,;:\n]/)[0] ?? value;
  const upToConjunction = upToPunctuation.split(
    /\s+(?:but|however|although|though|except|apart)\s+/i,
  )[0];
  return upToConjunction ?? upToPunctuation;
}

export function matchBoolean(value: string): boolean | null {
  const tokens = tokenize(value);
  if (tokens.length === 0) return null;

  if (REFUSALS.some((phrase) => findPhrase(tokens, phrase) >= 0)) return null;

  for (let index = 0; index < tokens.length; index += 1) {
    const token = tokens[index] ?? '';
    if (!UNCERTAIN.has(token)) continue;
    // "sure" and "know" are only uncertainty under a negation — "not sure",
    // "don't know". On their own they are an emphatic yes.
    const qualified = token === 'sure' || token === 'know';
    if (!qualified || NEGATION_CUES.has(tokens[index - 1] ?? '')) return null;
  }

  // Only the FIRST clause answers the question. The polarity scan used to
  // run over the whole sentence and take the first cue it found anywhere,
  // so a cue belonging to a later, unrelated clause decided the answer:
  // "I had a scan last month, nothing showed up" contains "nothing", and
  // that was read as No — telling the physician the patient denied having
  // had any tests, when they had just described one.
  //
  // A person answers a yes/no question and then elaborates. The answer is
  // in the answering part; everything after the first comma or "but" is
  // the elaboration, and it is not a second vote.
  //
  // Falling through to null where the first clause carries no cue is the
  // right outcome and not a regression: an unmatched value stays
  // unmatched, the patient's words are still shown to them, and the agent
  // sends `selections` for exactly these fields. A blank control is
  // recoverable; an inverted clinical claim is not.
  for (const token of tokenize(firstClause(value))) {
    if (CLAUSE_BREAKS.has(token)) break;
    if (AFFIRMATIVE.has(token)) return true;
    if (NEGATION_CUES.has(token)) return false;
  }
  return null;
}

// --------------------------------------------------------------------------
// Two-part entries
// --------------------------------------------------------------------------

/** One two-part entry, e.g. an allergen and its reaction. */
export interface RecordedPair {
  primary: string;
  secondary: string;
}

const PAIR_SPLIT =
  /\s*(?::|\s-{1,2}\s|\s(?:causes|cause|caused|gives me|brings on|leads to)\s)\s*/i;

/**
 * Parse a recorded list of two-part entries.
 *
 * Accepts `allergen: reaction`, `allergen - reaction`, and the phrasings a
 * model reaches for when it is writing down what it heard rather than
 * filling a form — "penicillin causes a rash". Entries are separated by
 * semicolons or newlines.
 *
 * An entry with no separator keeps its whole text as the primary value and
 * leaves the secondary blank, rather than splitting on a comma and turning
 * "penicillin, ibuprofen" into one allergen with a reaction of "ibuprofen".
 */
export function parseRecordedPairs(value: string): RecordedPair[] {
  return value
    .split(/[;\n]/)
    .map((entry) => entry.trim())
    .filter((entry) => entry.length > 0)
    .map((entry) => {
      const [primary, ...rest] = entry.split(PAIR_SPLIT);
      return { primary: (primary ?? '').trim(), secondary: rest.join(': ').trim() };
    })
    .filter((pair) => pair.primary.length > 0);
}

/** Render entries back into the `primary: secondary` form the agent reads. */
export function formatRecordedPairs(pairs: readonly RecordedPair[]): string {
  return pairs
    .filter((pair) => pair.primary.trim().length > 0)
    .map((pair) =>
      pair.secondary.trim().length > 0
        ? `${pair.primary.trim()}: ${pair.secondary.trim()}`
        : pair.primary.trim(),
    )
    .join('; ');
}

// --------------------------------------------------------------------------
// Agent-supplied selections
// --------------------------------------------------------------------------

/**
 * The option ids the agent committed to for one field, or null.
 *
 * Null means "the agent did not answer this one" and is what sends the
 * caller back to matching the words. The server has already checked every
 * id against the field's own option list, so all that is left here is to
 * drop an id this build cannot render — a frontend whose options have
 * moved on from the server's must show nothing rather than something else.
 */
export function parseSelectedIds<TId extends string>(
  recorded: string | undefined,
  options: readonly MatchableOption<TId>[],
): TId[] | null {
  if (recorded === undefined) return null;
  const wanted = new Set(
    recorded
      .split(',')
      .map((id) => id.trim().toLowerCase())
      .filter((id) => id.length > 0),
  );
  if (wanted.size === 0) return null;
  const kept = options
    .filter((option) => wanted.has(option.id.toLowerCase()))
    .map((option) => option.id);
  return kept.length > 0 ? kept : null;
}

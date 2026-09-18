import { describe, expect, it } from 'vitest';
import {
  formatRecordedPairs,
  matchBoolean,
  matchLabel,
  matchOption,
  matchOptions,
  parseRecordedPairs,
  parseSelectedIds,
  splitRecordedList,
} from '@/features/prescreening-session/prefillMatching';

/**
 * These functions turn a patient's own words into screen controls, which
 * makes them the one place the app could invent a clinical answer nobody
 * gave. Every case below is either "matched what they actually said",
 * "left it alone", or — the group that matters most — "refused to read a
 * denial as an answer".
 *
 * There is deliberately no case where a near-miss is rounded up to the
 * closest option.
 */

const OPTIONS = [
  { id: 'diabetes', label: 'Diabetes' },
  { id: 'high-blood-pressure', label: 'High blood pressure' },
  { id: 'heart-disease', label: 'Heart disease' },
  { id: 'asthma-or-lung-condition', label: 'Asthma or lung condition' },
  { id: 'other', label: 'Other' },
] as const;

describe('matchOption', () => {
  it('matches an exact label regardless of case and punctuation', () => {
    expect(matchOption('diabetes', OPTIONS)).toBe('diabetes');
    expect(matchOption('Heart Disease', OPTIONS)).toBe('heart-disease');
  });

  it('matches a label that appears inside the patient’s phrasing', () => {
    expect(matchOption('I have high blood pressure', OPTIONS)).toBe('high-blood-pressure');
  });

  it('matches the lay word a patient actually uses', () => {
    // The whole reason label matching was never enough: nobody says
    // "high blood pressure" or "asthma or lung condition" out loud.
    expect(matchOption('hypertension', OPTIONS)).toBe('high-blood-pressure');
    expect(matchOption('my sugars have been all over the place', OPTIONS)).toBe('diabetes');
    expect(matchOption('I use an inhaler', OPTIONS)).toBe('asthma-or-lung-condition');
    expect(matchOption('I had a stent put in', OPTIONS)).toBe('heart-disease');
  });

  it('prefers the more specific reading when both could match', () => {
    // "blood pressure" is an alias of the same card, so a sentence naming
    // the full condition is attributed once, to the longer phrase.
    expect(matchOption('high blood pressure', OPTIONS)).toBe('high-blood-pressure');
  });

  it('returns null rather than guessing at something it does not recognize', () => {
    expect(matchOption('my ears have been ringing', OPTIONS)).toBeNull();
    expect(matchOption('', OPTIONS)).toBeNull();
  });

  it('never falls back to a catch-all option', () => {
    // "Other" would otherwise swallow everything unmatched and present a
    // guess as the patient's answer.
    expect(matchOption('something unrelated entirely', OPTIONS)).not.toBe('other');
  });

  it('does not match on a fragment that is not a whole word', () => {
    // Substring matching reached "heart" from anywhere it appeared. Token
    // boundaries are what stop that.
    expect(matchOption('hearty appetite', OPTIONS)).toBeNull();
  });
});

/**
 * The group that fixes a real defect. Substring matching made every
 * denial an affirmation: "no diabetes" contained "diabetes", so the card
 * ticked and the physician was told the patient had a condition they had
 * just denied.
 */
describe('matchOption, on a denial', () => {
  it.each([
    'no diabetes',
    'not diabetes',
    'no history of diabetes',
    'never had diabetes',
    'tested negative for diabetes',
    'she denies diabetes',
    'no diabetes at all',
  ])('refuses to read "%s" as an answer', (value) => {
    expect(matchOption(value, OPTIONS)).toBeNull();
  });

  it('keeps a denial from reaching past a clause break', () => {
    // "but" is what separates what the patient denied from what they
    // affirmed in the same breath.
    expect(matchOption('no asthma but I do have diabetes', OPTIONS)).toBe('diabetes');
  });
});

describe('matchOptions', () => {
  it('matches every option a list names, in the order given', () => {
    expect(matchOptions('diabetes, heart disease', OPTIONS)).toEqual(['diabetes', 'heart-disease']);
  });

  it('reads "and" as a separator, the way a patient speaks a list', () => {
    expect(matchOptions('diabetes and heart disease', OPTIONS)).toEqual([
      'diabetes',
      'heart-disease',
    ]);
  });

  it('keeps only what it recognized, and never duplicates', () => {
    expect(matchOptions('diabetes; ringing ears; diabetes', OPTIONS)).toEqual(['diabetes']);
  });

  it('drops the denied item from a list and keeps the rest', () => {
    expect(matchOptions('diabetes; no heart disease', OPTIONS)).toEqual(['diabetes']);
  });

  it('matches nothing at all when the whole list is denied', () => {
    expect(matchOptions('no diabetes; no heart disease', OPTIONS)).toEqual([]);
  });
});

describe('splitRecordedList', () => {
  it('drops empty fragments left by trailing separators', () => {
    expect(splitRecordedList('diabetes, , heart disease,')).toEqual(['diabetes', 'heart disease']);
  });
});

describe('matchLabel', () => {
  const TOBACCO = ['Never', 'Former', 'Current'] as const;
  const ALCOHOL = ['Never', 'Occasional', 'Regular'] as const;

  it('matches the label itself', () => {
    expect(matchLabel('Former', TOBACCO)).toBe('Former');
  });

  it('reads how a patient describes their own habit', () => {
    expect(matchLabel('I quit about ten years ago', TOBACCO)).toBe('Former');
    expect(matchLabel('about ten a day', TOBACCO)).toBe('Current');
    expect(matchLabel('just socially, at weekends', ALCOHOL)).toBe('Occasional');
    expect(matchLabel('a glass of wine most days', ALCOHOL)).toBe('Regular');
  });

  it('reads a denial as the row’s negative option', () => {
    // "I don't smoke" names no affirmative option at all, so without this
    // the row stayed blank on the single most common answer to it.
    expect(matchLabel("I don't smoke", TOBACCO)).toBe('Never');
    expect(matchLabel('I never have', TOBACCO)).toBe('Never');
    expect(matchLabel('no, I do not drink', ALCOHOL)).toBe('Never');
  });

  it('prefers "used to" over the denial inside the same sentence', () => {
    // "I don't any more, I used to" is Former, not Never, and the more
    // specific phrase is what decides it.
    expect(matchLabel('I used to, but not any more', TOBACCO)).toBe('Former');
  });

  it('leaves the row alone when it recognizes nothing', () => {
    expect(matchLabel('would rather not say', TOBACCO)).toBeNull();
  });
});

describe('matchBoolean', () => {
  it.each([
    ['yes', true],
    ['Yes', true],
    ['yeah', true],
    ['no', false],
    ['none', false],
    ['never', false],
  ])('reads %s as %s', (value, expected) => {
    expect(matchBoolean(value)).toBe(expected);
  });

  it.each([
    ['no known allergies', false],
    ['yes, a cardiologist last month', true],
    ['not that I know of', false],
    ['no, nothing like that', false],
    ['yes I did', true],
  ])('reads the phrase "%s" as %s, not as nothing', (value, expected) => {
    // The old exact-set check only ever matched a bare word, so the value
    // the agent naturally writes — "no known allergies" — left the
    // checkbox untouched and the answer invisible.
    expect(matchBoolean(value)).toBe(expected);
  });

  it('leaves an uncertain answer unanswered rather than reading it as a denial', () => {
    // Defaulting to No would tell the physician the patient denied
    // something they never denied.
    expect(matchBoolean('I am not sure')).toBeNull();
    expect(matchBoolean('I do not know')).toBeNull();
    expect(matchBoolean('maybe')).toBeNull();
    expect(matchBoolean('would rather not say')).toBeNull();
  });
});

describe('parseSelectedIds', () => {
  it('takes the ids the agent chose, in the screen’s own order', () => {
    expect(parseSelectedIds('heart-disease, diabetes', OPTIONS)).toEqual([
      'diabetes',
      'heart-disease',
    ]);
  });

  it('drops an id this build cannot render', () => {
    // A server ahead of this frontend must not be able to name a card
    // that is not on screen.
    expect(parseSelectedIds('diabetes, gout', OPTIONS)).toEqual(['diabetes']);
  });

  it('returns null when the agent chose nothing, so the caller matches the words', () => {
    expect(parseSelectedIds(undefined, OPTIONS)).toBeNull();
    expect(parseSelectedIds('', OPTIONS)).toBeNull();
    expect(parseSelectedIds('gout', OPTIONS)).toBeNull();
  });
});

describe('parseRecordedPairs', () => {
  it('splits an allergen from its reaction', () => {
    expect(parseRecordedPairs('penicillin: comes up in a rash')).toEqual([
      { primary: 'penicillin', secondary: 'comes up in a rash' },
    ]);
  });

  it('accepts a dash as the separator too', () => {
    expect(parseRecordedPairs('peanuts - swelling')).toEqual([
      { primary: 'peanuts', secondary: 'swelling' },
    ]);
  });

  it('accepts the way a model writes down what it heard', () => {
    expect(parseRecordedPairs('penicillin causes a rash')).toEqual([
      { primary: 'penicillin', secondary: 'a rash' },
    ]);
  });

  it('keeps separate entries separate', () => {
    expect(parseRecordedPairs('penicillin: rash; peanuts: swelling')).toHaveLength(2);
  });

  it('does not split a comma-separated list into an allergen and a reaction', () => {
    // "penicillin, ibuprofen" is two allergens, and treating the second as
    // a reaction to the first would be a fabricated clinical detail.
    expect(parseRecordedPairs('penicillin, ibuprofen')).toEqual([
      { primary: 'penicillin, ibuprofen', secondary: '' },
    ]);
  });

  it('keeps an entry with no reaction rather than dropping it', () => {
    expect(parseRecordedPairs('latex')).toEqual([{ primary: 'latex', secondary: '' }]);
  });
});

describe('formatRecordedPairs', () => {
  it('round-trips through parseRecordedPairs', () => {
    const text = 'penicillin: rash; peanuts: swelling';

    expect(formatRecordedPairs(parseRecordedPairs(text))).toBe(text);
  });

  it('omits an entry the patient has not filled in yet', () => {
    expect(
      formatRecordedPairs([
        { primary: 'latex', secondary: '' },
        { primary: '', secondary: '' },
      ]),
    ).toBe('latex');
  });
});

/**
 * Three ways an ambiguous sentence used to become a confident tick.
 *
 * Every case here produced a *wrong* control on the patient's screen and
 * a wrong line in the physician's record — not a missing one. That is the
 * distinction this whole module turns on: an unmatched value is
 * recoverable, because the patient's own words are still shown to them
 * and they can correct the box themselves. An inverted clinical claim is
 * not, because nothing downstream knows to doubt it.
 */
describe('ambiguity never becomes a confident answer', () => {
  it('reads the answer from the answering clause, not from the elaboration', () => {
    // "nothing" belongs to the second clause, describing the result. The
    // first clause says they had the scan. Reading the whole sentence and
    // taking the first cue anywhere told the physician the patient denied
    // having had any tests.
    expect(matchBoolean('I had a scan last month, nothing showed up')).not.toBe(false);
  });

  it('still reads a plain yes or no with elaboration after it', () => {
    expect(matchBoolean('Yes, a cardiologist last month')).toBe(true);
    expect(matchBoolean('No, nobody else')).toBe(false);
    expect(matchBoolean('not that I know of')).toBe(false);
  });

  it('does not let a later clause flip an affirmative answer', () => {
    expect(matchBoolean('Yes, but nothing came of it')).toBe(true);
  });

  it('sees a denial that is more than four tokens from what it denies', () => {
    // "I don't have any history of heart disease" — six tokens between
    // the denial and the condition. The old fixed window stopped short,
    // so the Heart disease card was ticked for a patient ruling it out.
    expect(
      matchOptions("I don't have any history of heart disease", [
        { id: 'heart-disease', label: 'Heart disease' },
      ]),
    ).toEqual([]);
  });

  it('keeps a denial from running past a clause break', () => {
    expect(
      matchOptions('no asthma but I do have diabetes', [
        { id: 'asthma-or-lung-condition', label: 'Asthma or lung condition' },
        { id: 'diabetes', label: 'Diabetes' },
      ]),
    ).toEqual(['diabetes']);
  });

  it('carries a denial across "and" instead of splitting it away', () => {
    // Splitting on " and " ran before the negation guard, so the denial
    // only suppressed the first of the two things it covered.
    expect(
      matchOptions('no heart disease and diabetes', [
        { id: 'heart-disease', label: 'Heart disease' },
        { id: 'diabetes', label: 'Diabetes' },
      ]),
    ).toEqual([]);
  });

  it('still matches both halves of an affirmative "and" list', () => {
    expect(
      matchOptions('diabetes and high blood pressure', [
        { id: 'diabetes', label: 'Diabetes' },
        { id: 'high-blood-pressure', label: 'High blood pressure' },
      ]),
    ).toEqual(['diabetes', 'high-blood-pressure']);
  });

  it('leaves a refusal and a doubt as no answer at all', () => {
    expect(matchBoolean("I'd rather not say")).toBeNull();
    expect(matchBoolean('not sure')).toBeNull();
  });
});

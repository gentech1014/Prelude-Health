/**
 * The words patients actually use for each on-screen option.
 *
 * The agent now names option ids directly (see `selections` in
 * `intakeProtocol`), so this table is the *fallback* path: what the browser
 * falls back to when the model recorded words without committing to an
 * option. It exists because label matching alone never had a chance --
 * nobody says "high blood pressure", they say "hypertension" or "my BP",
 * and "Asthma or lung condition" is a form's phrasing, not a person's.
 *
 * Keyed by option id rather than by field: `diabetes` means the same thing
 * on the conditions grid and the family-history grid, and duplicating the
 * synonyms per screen would guarantee the two drift.
 *
 * Every entry is a phrase the patient would have to say *affirmatively* for
 * the option to be right. The negation guard in `prefillMatching` is what
 * stops "no diabetes" reaching the Diabetes card, so nothing here needs to
 * encode polarity -- and nothing here should try to.
 */
export const OPTION_ALIASES: Readonly<Record<string, readonly string[]>> = {
  // --- conditions, personal and family ---
  diabetes: [
    'diabetic',
    'sugar',
    'sugars',
    'blood sugar',
    'high sugar',
    'type 1',
    'type 2',
    't1d',
    't2d',
    't2dm',
    'a1c',
    'hba1c',
    'insulin',
  ],
  'high-blood-pressure': [
    'hypertension',
    'hypertensive',
    'bp',
    'high bp',
    'blood pressure',
    'pressure is high',
    'high pressure',
  ],
  'asthma-or-lung-condition': [
    'asthma',
    'asthmatic',
    'copd',
    'emphysema',
    'bronchitis',
    'inhaler',
    'wheezing',
    'wheezy',
    'lung',
    'lungs',
    'breathing problem',
    'short of breath',
  ],
  'heart-disease': [
    'heart',
    'cardiac',
    'cardiovascular',
    'angina',
    'heart attack',
    'coronary',
    'bypass',
    'stent',
    'heart failure',
    'afib',
    'atrial fibrillation',
  ],
  'thyroid-disorder': [
    'thyroid',
    'hypothyroid',
    'hyperthyroid',
    'underactive thyroid',
    'overactive thyroid',
    'levothyroxine',
  ],
  'anxiety-or-depression': [
    'anxiety',
    'anxious',
    'depression',
    'depressed',
    'panic',
    'panic attacks',
    'feeling low',
    'low mood',
    'mental health',
  ],
  'mental-health-conditions': [
    'anxiety',
    'depression',
    'bipolar',
    'schizophrenia',
    'mental illness',
    'mental health',
    'psychiatric',
  ],
  cancer: [
    'tumour',
    'tumor',
    'carcinoma',
    'leukemia',
    'leukaemia',
    'lymphoma',
    'chemo',
    'chemotherapy',
    'oncology',
  ],
  stroke: ['brain attack', 'cva', 'mini stroke', 'tia'],

  // --- reason for the visit ---
  'pain-or-discomfort': [
    'pain',
    'painful',
    'ache',
    'aching',
    'sore',
    'soreness',
    'hurts',
    'hurting',
    'discomfort',
    'cramping',
  ],
  'injury-follow-up': [
    'injury',
    'injured',
    'hurt myself',
    'accident',
    'fall',
    'fell',
    'fracture',
    'broke',
    'broken',
    'sprain',
    'follow up on an injury',
  ],
  'chronic-condition-management': [
    'chronic',
    'ongoing condition',
    'long term condition',
    'manage my condition',
    'repeat prescription',
    'medication review',
  ],
  'routine-check-up': [
    'check up',
    'checkup',
    'routine',
    'annual',
    'yearly',
    'physical',
    'general check',
  ],
  'mental-health-support': [
    'mental health',
    'anxiety',
    'depression',
    'stress',
    'counselling',
    'counseling',
    'therapy',
    'not coping',
  ],
  'preventive-care-or-wellness': [
    'preventive',
    'preventative',
    'prevention',
    'wellness',
    'screening',
    'vaccination',
    'vaccine',
    'immunisation',
    'immunization',
    'flu shot',
  ],

  // --- tobacco and alcohol pills (ids are the visible labels) ---
  // No negation words here on purpose: "I don't smoke" is matched as a
  // *negated* Current, and `NEGATIVE_OPTION_IDS` is what turns that into
  // Never. Listing 'no' as an alias of Never instead would make the two
  // paths fight over the same sentence.
  Never: ['never', 'non smoker', 'nonsmoker', 'teetotal', 'tee total'],
  Former: [
    'former',
    'used to',
    'quit',
    'gave up',
    'given up',
    'stopped',
    'ex smoker',
    'exsmoker',
    'in the past',
    'not any more',
    'not anymore',
  ],
  Current: [
    'current',
    'currently',
    'still',
    'smoke',
    'smokes',
    'smoker',
    'smoking',
    'a day',
    'a pack',
    'pack a day',
  ],
  // No 'a glass' here: it is a serving, not a frequency, and it appears in
  // "a glass most days" too -- where it outranked the phrase that actually
  // answers the question.
  Occasional: [
    'occasional',
    'occasionally',
    'socially',
    'social',
    'now and then',
    'once in a while',
    'rarely',
    'weekends',
    'special occasions',
    'light',
  ],
  Regular: [
    'regular',
    'regularly',
    'daily',
    'every day',
    'most days',
    'every night',
    'a week',
    'heavy',
  ],
};

/**
 * Options that are themselves a denial, so a negated sentence *is* them.
 *
 * Without this, "I don't smoke" would match nothing: the negation guard
 * correctly refuses to tick Current, and there is no affirmative phrase
 * left to match. With it, a single-choice row whose every candidate was
 * negated falls to its negative option instead of staying blank.
 *
 * Ids, not labels-per-field, for the same reason as the aliases above:
 * `Never` denies the same thing on the tobacco row and the alcohol row.
 */
export const NEGATIVE_OPTION_IDS: ReadonlySet<string> = new Set(['Never', 'no']);

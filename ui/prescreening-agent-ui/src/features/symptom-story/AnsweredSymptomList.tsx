import { motion } from 'framer-motion';
import { useId, useState, type JSX } from 'react';
import { AnswerInput } from '@/components/AnswerInput';
import type { SymptomAnswerEntry } from '@/features/prescreening-session/intakeCallContext';
import '@/features/symptom-story/AnsweredSymptomList.css';
import { LIST_ITEM_VARIANTS, LIST_STAGGER_VARIANTS } from '@/lib/pageMotion';

const ChatIcon = () => (
  <svg
    width="24"
    height="24"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
  </svg>
);

const EditIcon = () => (
  <svg
    width="20"
    height="20"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z" />
  </svg>
);

interface AnsweredSymptomListProps {
  answers: readonly SymptomAnswerEntry[];
  onCorrect: (questionId: string, value: string) => void;
}

/**
 * What the patient has already answered on this screen, in the order it
 * was asked, and still editable.
 */
export function AnsweredSymptomList({
  answers,
  onCorrect,
}: AnsweredSymptomListProps): JSX.Element | null {
  if (answers.length === 0) return null;

  return (
    <section className="answered-symptom-list" aria-label="Answers so far">
      <div className="answered-symptom-list__header">
        <div className="answered-symptom-list__title-row">
          <h2 className="answered-symptom-list__heading">Answers so far</h2>
          <span className="answered-symptom-list__count-pill">{answers.length} answers</span>
        </div>
        <p className="answered-symptom-list__note">
          Here's what you've told me. Tap any answer to edit it.
        </p>
      </div>

      <motion.ul
        className="answered-symptom-list__items"
        variants={LIST_STAGGER_VARIANTS}
        initial="hidden"
        animate="visible"
      >
        {[...answers].reverse().map((entry) => (
          <motion.li key={entry.questionId} variants={LIST_ITEM_VARIANTS}>
            <AnsweredSymptom entry={entry} onCorrect={onCorrect} />
          </motion.li>
        ))}
      </motion.ul>
    </section>
  );
}

interface AnsweredSymptomProps {
  entry: SymptomAnswerEntry;
  onCorrect: (questionId: string, value: string) => void;
}

/**
 * What to show beside an answer that is not a plain statement of fact.
 *
 * Null for `confirmed`, which is the ordinary case and needs no comment —
 * labelling every answer would bury the three that matter. `inferred` is
 * marked too, because an answer the assistant worked out rather than
 * heard is exactly the kind the patient should be invited to correct.
 */
const CERTAINTY_NOTE: Partial<Record<SymptomAnswerEntry['certainty'], string>> = {
  uncertain: 'Recorded as uncertain',
  undisclosed: 'You chose not to answer this',
  unknown: 'Recorded as not known',
  inferred: 'Inferred from what you said — edit if incorrect',
};

function AnsweredSymptom({ entry, onCorrect }: AnsweredSymptomProps): JSX.Element {
  const inputId = useId();
  // Null until the patient types
  const [correction, setCorrection] = useState<string | null>(null);
  const value = correction ?? entry.answer;
  const note = CERTAINTY_NOTE[entry.certainty] ?? null;

  return (
    <div className="answered-symptom">
      <div className="answered-symptom__icon">
        <ChatIcon />
      </div>

      <div className="answered-symptom__content">
        <label className="answered-symptom__question" htmlFor={inputId}>
          {entry.question}
        </label>
        {note !== null && <p className="answered-symptom__certainty">{note}</p>}
        <div className="answered-symptom__answer-wrap">
          <AnswerInput
            id={inputId}
            value={value}
            onChange={setCorrection}
            onSubmit={() => onCorrect(entry.questionId, value)}
            multiline
            rows={1}
          />
        </div>
      </div>

      <div className="answered-symptom__edit-icon">
        <EditIcon />
      </div>
    </div>
  );
}

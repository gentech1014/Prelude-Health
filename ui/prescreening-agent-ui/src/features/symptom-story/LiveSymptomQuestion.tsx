import { useId, useState, type JSX } from 'react';
import { AnswerInput } from '@/components/AnswerInput';
import type { SymptomQuestion } from '@/features/prescreening-session/intakeCallContext';
import '@/features/symptom-story/LiveSymptomQuestion.css';

const ChatIcon = () => (
  <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
  </svg>
);

interface LiveSymptomQuestionProps {
  question: SymptomQuestion;
  /** Position of this question in the call, 1-based. */
  position: number;
  onAnswer: (questionId: string, value: string) => void;
}

/**
 * The one screening question the assistant is asking right now.
 *
 * Shows the position but no total, because there is no total: how long a
 * screening runs depends on what the patient has already said. The old
 * "QUESTION 3 OF 10" was a promise the conversation could not keep.
 */
export function LiveSymptomQuestion({
  question,
  position,
  onAnswer,
}: LiveSymptomQuestionProps): JSX.Element {
  const inputId = useId();
  const [draft, setDraft] = useState('');

  return (
    <section className="live-symptom-question">
      <div className="live-symptom-question__header-row">
        <p className="live-symptom-question__progress-text">{`QUESTION ${position}`}</p>
      </div>

      <div className="live-symptom-question__prompt-row">
        <div className="live-symptom-question__icon">
          <ChatIcon />
        </div>
        <label className="live-symptom-question__prompt" htmlFor={inputId}>
          {question.text}
        </label>
      </div>

      <div className="live-symptom-question__input-wrap">
        <AnswerInput
          id={inputId}
          value={draft}
          onChange={setDraft}
          onSubmit={() => onAnswer(question.id, draft)}
          placeholder="Say it, or type it here..."
          multiline
          rows={3}
          maxLength={300}
        />
      </div>

      <p className="live-symptom-question__hint">
        Answer out loud, or type it and press Send. If you are not sure, say so — that is a useful
        answer too.
      </p>
    </section>
  );
}

interface SymptomQuestionPlaceholderProps {
  /** True once at least one answer has been given, so the copy can say why the pause. */
  hasAnswers: boolean;
}

/**
 * What stands in the question's place between questions.
 */
export function SymptomQuestionPlaceholder({
  hasAnswers,
}: SymptomQuestionPlaceholderProps): JSX.Element {
  return (
    <section className="live-symptom-question live-symptom-question--waiting" aria-live="polite">
      <p className="live-symptom-question__hint">
        {hasAnswers
          ? 'Answer saved. Listening for the next question.'
          : 'Listening. Your questions will appear here one at a time as we talk.'}
      </p>
    </section>
  );
}

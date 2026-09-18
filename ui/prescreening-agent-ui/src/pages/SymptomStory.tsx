import type { JSX } from 'react';
import { PageSection } from '@/components/PageSection';
import { CallScreenLayout } from '@/features/prescreening-session/CallScreenLayout';
import { useIntakeCall } from '@/features/prescreening-session/useIntakeCall';
import { AnsweredSymptomList } from '@/features/symptom-story/AnsweredSymptomList';
import {
  LiveSymptomQuestion,
  SymptomQuestionPlaceholder,
} from '@/features/symptom-story/LiveSymptomQuestion';

/**
 * The detail of what the patient is experiencing — one question at a time.
 *
 * The only screen with no fixed fields. The assistant infers the symptom
 * area from what the patient has already said, draws the questions from
 * the clinic's own question bank, and decides which to ask next from the
 * answers it has: a chronic condition and a new complaint produce
 * different questions, and neither is knowable when this screen is built.
 * So the screen holds whatever question is live, and the answers behind
 * it, and the conversation decides both.
 *
 * Deliberately no numeric severity field and no "here's what I heard"
 * summary: scoring a patient's pain is a clinical judgment, and reading
 * answers back is what the physician's report is for.
 */
export function SymptomStory(): JSX.Element {
  const { symptomIntake, reportSymptomAnswer } = useIntakeCall();
  const { current, answers } = symptomIntake;

  // A typed answer is recorded the moment it is sent, while its question
  // is still on screen being answered. Showing it in both places at once
  // reads as the same question asked twice.
  const settled = answers.filter((entry) => entry.questionId !== current?.id);

  return (
    <CallScreenLayout
      title="Tell me more about your symptoms"
      subtitle="I will ask one question at a time. Answer out loud, or type it here."
    >
      <PageSection>
        {current === null ? (
          <SymptomQuestionPlaceholder hasAnswers={settled.length > 0} />
        ) : (
          <LiveSymptomQuestion
            // Remounts per question, which is what empties the answer box.
            key={current.id}
            question={current}
            position={settled.length + 1}
            onAnswer={reportSymptomAnswer}
          />
        )}
      </PageSection>

      <PageSection>
        <AnsweredSymptomList answers={settled} onCorrect={reportSymptomAnswer} />
      </PageSection>
    </CallScreenLayout>
  );
}

import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { IntakeSymptomIntake } from '@/features/prescreening-session/intakeCallContext';
import { SymptomStory } from '@/pages/SymptomStory';
import { renderPageInSession } from '../testUtils';

const EMPTY: IntakeSymptomIntake = { current: null, answers: [] };

function withSymptomIntake(symptomIntake: IntakeSymptomIntake): {
  symptomIntake: IntakeSymptomIntake;
} {
  return { symptomIntake };
}

describe('SymptomStory', () => {
  it('shows the one question the assistant is asking, and nothing else', () => {
    // The assistant picks each question from the clinic's bank based on
    // what the patient has already said, so the screen holds one at a time
    // rather than a fixed set of fields.
    renderPageInSession(<SymptomStory />, {
      step: 'symptom-story',
      call: withSymptomIntake({
        current: { id: 'lung-03', text: 'How far can you walk before you notice it?' },
        answers: [],
      }),
    });

    expect(
      screen.getByRole('heading', { name: /tell me more about your symptoms/i }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText('How far can you walk before you notice it?')).toBeInTheDocument();
    expect(screen.getAllByRole('textbox')).toHaveLength(1);
    // Loose on wording: the progress line is presentation, the position is the contract.
    // No total: how long a screening runs depends on what the patient
    // has already said, so the screen shows position and nothing more.
    expect(screen.getByText(/^question 1$/i)).toBeInTheDocument();
    expect(screen.queryByText(/ of /i)).not.toBeInTheDocument();
  });

  it('shows a declined answer as a boundary the patient set, not a fact they gave', () => {
    // "Preferred not to say" used to render in an ordinary answer box,
    // indistinguishable from something they had actually told us.
    renderPageInSession(<SymptomStory />, {
      step: 'symptom-story',
      call: withSymptomIntake({
        current: { id: 'lung-02', text: 'Do you smoke?' },
        answers: [
          {
            questionId: 'lung-01',
            question: 'How much do you drink?',
            answer: 'Preferred not to say',
            certainty: 'undisclosed',
          },
        ],
      }),
    });

    expect(screen.getByText(/you chose not to answer this/i)).toBeInTheDocument();
  });

  it('marks an answer the assistant inferred rather than heard', () => {
    renderPageInSession(<SymptomStory />, {
      step: 'symptom-story',
      call: withSymptomIntake({
        current: { id: 'lung-02', text: 'Do you smoke?' },
        answers: [
          {
            questionId: 'lung-01',
            question: 'Do you use an inhaler?',
            answer: 'the blue one',
            certainty: 'inferred',
          },
        ],
      }),
    });

    expect(screen.getByText(/inferred from what you said/i)).toBeInTheDocument();
  });

  it('says nothing extra about an ordinary confirmed answer', () => {
    renderPageInSession(<SymptomStory />, {
      step: 'symptom-story',
      call: withSymptomIntake({
        current: { id: 'lung-02', text: 'Do you smoke?' },
        answers: [
          {
            questionId: 'lung-01',
            question: 'How long have you had this?',
            answer: 'three weeks',
            certainty: 'confirmed',
          },
        ],
      }),
    });

    expect(screen.queryByText(/recorded as/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/chose not to answer/i)).not.toBeInTheDocument();
  });

  it('waits visibly when the assistant has not chosen the next question yet', () => {
    renderPageInSession(<SymptomStory />, {
      step: 'symptom-story',
      call: withSymptomIntake(EMPTY),
    });

    expect(screen.getByText(/one at a time as we talk/i)).toBeInTheDocument();
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  });

  it('never asks the patient to score anything on a numeric scale', () => {
    renderPageInSession(<SymptomStory />, {
      step: 'symptom-story',
      call: withSymptomIntake(EMPTY),
    });

    expect(screen.queryByText(/1 to 10/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/severity/i)).not.toBeInTheDocument();
  });

  it('moves an answered question onto the record behind the live one', () => {
    renderPageInSession(<SymptomStory />, {
      step: 'symptom-story',
      call: withSymptomIntake({
        current: { id: 'lung-02', text: 'Do you have a cough with it?' },
        answers: [
          {
            questionId: 'lung-00',
            question: 'How long have you had this?',
            answer: 'about three weeks',
            certainty: 'confirmed',
          },
        ],
      }),
    });

    expect(screen.getByLabelText('How long have you had this?')).toHaveValue('about three weeks');
    // The live question's own box is empty and waiting.
    expect(screen.getByLabelText('Do you have a cough with it?')).toHaveValue('');
    expect(screen.getByText(/^question 2$/i)).toBeInTheDocument();
  });

  it('reports a typed answer against the question it belongs to', async () => {
    const user = userEvent.setup();
    const reportSymptomAnswer = vi.fn();
    renderPageInSession(<SymptomStory />, {
      step: 'symptom-story',
      call: {
        ...withSymptomIntake({
          current: { id: 'lung-01', text: 'Does it happen at rest?' },
          answers: [],
        }),
        reportSymptomAnswer,
      },
    });

    await user.type(screen.getByLabelText('Does it happen at rest?'), 'only on stairs');
    // Typing alone must never report the answer -- only an explicit Send
    // (or Enter) does, so a pause mid-sentence is never read as finished.
    expect(reportSymptomAnswer).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: /send this answer/i }));

    expect(reportSymptomAnswer).toHaveBeenLastCalledWith('lung-01', 'only on stairs');
  });

  it('lets the patient correct an answer already recorded', async () => {
    const user = userEvent.setup();
    const reportSymptomAnswer = vi.fn();
    renderPageInSession(<SymptomStory />, {
      step: 'symptom-story',
      call: {
        ...withSymptomIntake({
          current: null,
          answers: [
            {
              questionId: 'lung-00',
              question: 'How long have you had this?',
              answer: 'two weeks',
              certainty: 'confirmed',
            },
          ],
        }),
        reportSymptomAnswer,
      },
    });

    const answered = screen.getByLabelText('How long have you had this?');
    await user.clear(answered);
    await user.type(answered, 'three days');

    expect(answered).toHaveValue('three days');
    expect(reportSymptomAnswer).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: /send this answer/i }));

    expect(reportSymptomAnswer).toHaveBeenLastCalledWith('lung-00', 'three days');
  });

  it('has no manual back/next controls', () => {
    renderPageInSession(<SymptomStory />, {
      step: 'symptom-story',
      call: withSymptomIntake(EMPTY),
    });

    expect(screen.queryByRole('button', { name: 'Back' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Next' })).not.toBeInTheDocument();
  });
});

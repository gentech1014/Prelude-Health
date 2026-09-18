import { useId, type JSX } from 'react';
import { AnswerInput } from '@/components/AnswerInput';
import '@/features/family-social-history/SocialHistoryFields.css';

interface SocialHistoryFieldsProps {
  occupation: string;
  livingSituation: string;
  onOccupationChange: (value: string) => void;
  onOccupationSubmit: () => void;
  onLivingSituationChange: (value: string) => void;
  onLivingSituationSubmit: () => void;
}

/**
 * Occupation and living situation.
 *
 * Two independent fields rather than one object: the assistant records
 * whichever the patient answered, and a combined value would hold back one
 * until the other arrived.
 */
export function SocialHistoryFields({
  occupation,
  livingSituation,
  onOccupationChange,
  onOccupationSubmit,
  onLivingSituationChange,
  onLivingSituationSubmit,
}: SocialHistoryFieldsProps): JSX.Element {
  const occupationId = useId();
  const livingSituationId = useId();

  return (
    <div className="social-history-fields">
      <div className="social-history-fields__field">
        <label className="social-history-fields__field-label" htmlFor={occupationId}>
          Occupation
        </label>
        <AnswerInput
          id={occupationId}
          value={occupation}
          onChange={onOccupationChange}
          onSubmit={onOccupationSubmit}
          placeholder="What you do, or that you are retired"
          autoComplete="organization-title"
        />
      </div>

      <div className="social-history-fields__field">
        <label className="social-history-fields__field-label" htmlFor={livingSituationId}>
          Living situation
        </label>
        <AnswerInput
          id={livingSituationId}
          value={livingSituation}
          onChange={onLivingSituationChange}
          onSubmit={onLivingSituationSubmit}
          placeholder="Who you live with"
        />
      </div>
    </div>
  );
}

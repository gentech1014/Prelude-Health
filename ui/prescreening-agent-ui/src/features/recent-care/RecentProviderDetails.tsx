import { useId, type JSX } from 'react';
import { AnswerInput } from '@/components/AnswerInput';
import '@/features/recent-care/RecentProviderDetails.css';

interface RecentProviderDetailsProps {
  who: string;
  when: string;
  reason: string;
  onWhoChange: (value: string) => void;
  onWhoSubmit: () => void;
  onWhenChange: (value: string) => void;
  onWhenSubmit: () => void;
  onReasonChange: (value: string) => void;
  onReasonSubmit: () => void;
}

/**
 * Who the patient saw recently, when, and why — the follow-up to "seen
 * anyone else?".
 *
 * Three separate fields rather than one object, because each is filled
 * independently: the assistant records whichever of them the patient
 * actually answered, and a combined value would need all three before any
 * of them could appear.
 */
export function RecentProviderDetails({
  who,
  when,
  reason,
  onWhoChange,
  onWhoSubmit,
  onWhenChange,
  onWhenSubmit,
  onReasonChange,
  onReasonSubmit,
}: RecentProviderDetailsProps): JSX.Element {
  const whoId = useId();
  const whenId = useId();
  const reasonId = useId();

  return (
    <div className="recent-provider-details">
      <div className="recent-provider-details__field">
        <label className="recent-provider-details__field-label" htmlFor={whoId}>
          Who did you see?
        </label>
        <AnswerInput
          id={whoId}
          value={who}
          onChange={onWhoChange}
          onSubmit={onWhoSubmit}
          placeholder="A doctor's name, urgent care, or the hospital"
        />
      </div>

      <div className="recent-provider-details__field">
        <label className="recent-provider-details__field-label" htmlFor={whenId}>
          When?
        </label>
        <AnswerInput
          id={whenId}
          value={when}
          onChange={onWhenChange}
          onSubmit={onWhenSubmit}
          placeholder="Roughly is fine"
        />
      </div>

      <div className="recent-provider-details__field">
        <label className="recent-provider-details__field-label" htmlFor={reasonId}>
          What did they say or do?
        </label>
        <AnswerInput
          id={reasonId}
          value={reason}
          onChange={onReasonChange}
          onSubmit={onReasonSubmit}
          placeholder="In your own words"
        />
      </div>
    </div>
  );
}

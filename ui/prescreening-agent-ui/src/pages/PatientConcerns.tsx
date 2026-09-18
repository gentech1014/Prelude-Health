import type { JSX } from 'react';
import { OptionalDetailsField } from '@/components/OptionalDetailsField';
import { PageSection } from '@/components/PageSection';
import { CallScreenLayout } from '@/features/prescreening-session/CallScreenLayout';
import {
  usePrefilledMultiSelect,
  usePrefilledText,
} from '@/features/prescreening-session/usePrefill';
import { ConcernSelectionGrid } from '@/features/patient-concerns/ConcernSelectionGrid';
import { PATIENT_CONCERN_OPTIONS } from '@/features/patient-concerns/patientConcernOptions';

/**
 * What brings the patient in. The assistant asks it as one open question,
 * and whichever of these cards matches what they said is ticked as they
 * speak — so the patient can correct a mishearing without repeating
 * themselves.
 */
export function PatientConcerns(): JSX.Element {
  const concerns = usePrefilledMultiSelect('patient-concerns', 'concerns', PATIENT_CONCERN_OPTIONS);
  const details = usePrefilledText('patient-concerns', 'concern_details');

  return (
    <CallScreenLayout
      title="What brings you in today?"
      subtitle="Say it in your own words. Correct anything below if I mishear you."
    >
      <PageSection>
        <ConcernSelectionGrid selectedIds={concerns.selectedIds} onToggle={concerns.toggle} />
      </PageSection>

      <PageSection>
        <OptionalDetailsField
          value={details.value}
          onChange={details.onChange}
          onSubmit={details.onSubmit}
          label="Tell me more about your concerns (optional)"
          helperText="You can add more details about your concerns."
        />
      </PageSection>
    </CallScreenLayout>
  );
}

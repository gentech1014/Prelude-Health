import type { JSX } from 'react';
import { MultiSelectCardGrid } from '@/components/MultiSelectCardGrid';
import { OptionalDetailsField } from '@/components/OptionalDetailsField';
import { PageSection } from '@/components/PageSection';
import { ONGOING_CONDITION_OPTIONS } from '@/features/medical-history/conditionOptions';
import { HospitalStayList } from '@/features/medical-history/HospitalStayList';
import { CallScreenLayout } from '@/features/prescreening-session/CallScreenLayout';
import {
  usePrefilledBoolean,
  usePrefilledMultiSelect,
  usePrefilledPairs,
  usePrefilledText,
} from '@/features/prescreening-session/usePrefill';

/**
 * Ongoing conditions and hospital stays.
 *
 * Two separate questions, and the assistant asks them separately: a
 * condition someone is treated for and an admission years ago are
 * different facts, and merging them loses whichever the patient answered
 * second.
 */
export function MedicalHistory(): JSX.Element {
  const conditions = usePrefilledMultiSelect(
    'medical-history',
    'conditions',
    ONGOING_CONDITION_OPTIONS,
  );
  const conditionDetails = usePrefilledText('medical-history', 'condition_details');
  const noStays = usePrefilledBoolean('medical-history', 'no_hospital_stays');
  const stays = usePrefilledPairs('medical-history', 'hospital_stays');

  return (
    <CallScreenLayout
      title="Medical history"
      subtitle="Anything you are treated for, and any hospital stays worth knowing about."
    >
      <PageSection>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
          <h2
            style={{
              margin: 0,
              fontSize: '1rem',
              fontWeight: 'var(--fw-semibold)',
              color: 'var(--color-text)',
            }}
          >
            Ongoing conditions
          </h2>
          <MultiSelectCardGrid
            options={ONGOING_CONDITION_OPTIONS}
            selectedIds={conditions.selectedIds}
            onToggle={conditions.toggle}
          />
          <OptionalDetailsField
            value={conditionDetails.value}
            onChange={conditionDetails.onChange}
            onSubmit={conditionDetails.onSubmit}
            label="Tell me more about your ongoing conditions (optional)"
            helperText="You can add more details, like when it was diagnosed or how it's managed."
          />
        </div>
      </PageSection>

      <PageSection>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
          <h2
            style={{
              margin: 0,
              fontSize: '1rem',
              fontWeight: 'var(--fw-semibold)',
              color: 'var(--color-text)',
            }}
          >
            Hospital stays
          </h2>
          <HospitalStayList
            hasNoHospitalStays={noStays.value === true}
            onToggleNoHospitalStays={noStays.onChange}
            entries={stays.entries}
            onEntryChange={stays.change}
            onRemoveEntry={stays.remove}
            onAddEntry={stays.add}
            onSubmitEntries={stays.submit}
          />
        </div>
      </PageSection>
    </CallScreenLayout>
  );
}

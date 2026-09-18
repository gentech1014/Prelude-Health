import type { JSX } from 'react';
import { MultiSelectCardGrid } from '@/components/MultiSelectCardGrid';
import { OptionalDetailsField } from '@/components/OptionalDetailsField';
import { PageSection } from '@/components/PageSection';
import { SingleChoicePills } from '@/components/SingleChoicePills';
import { FAMILY_HISTORY_OPTIONS } from '@/features/family-social-history/familyHistoryOptions';
import { SocialHistoryFields } from '@/features/family-social-history/SocialHistoryFields';
import { CallScreenLayout } from '@/features/prescreening-session/CallScreenLayout';
import {
  usePrefilledChoice,
  usePrefilledMultiSelect,
  usePrefilledText,
} from '@/features/prescreening-session/usePrefill';

const TOBACCO_USE_OPTIONS = ['Never', 'Former', 'Current'] as const;
const ALCOHOL_USE_OPTIONS = ['Never', 'Occasional', 'Regular'] as const;

/**
 * Family conditions and social history.
 *
 * Whether a patient reaches this screen at all is the assistant's call,
 * not this component's: it is relevant to some visits and not others, and
 * the decision belongs with the conversation. There is no skip logic here
 * — if the call navigates here, these are the questions being asked.
 */
export function FamilySocialHistory(): JSX.Element {
  const familyConditions = usePrefilledMultiSelect(
    'family-social-history',
    'family_conditions',
    FAMILY_HISTORY_OPTIONS,
  );
  const familyDetails = usePrefilledText('family-social-history', 'family_details');
  const tobacco = usePrefilledChoice('family-social-history', 'tobacco_use', TOBACCO_USE_OPTIONS);
  const alcohol = usePrefilledChoice('family-social-history', 'alcohol_use', ALCOHOL_USE_OPTIONS);
  const occupation = usePrefilledText('family-social-history', 'occupation');
  const livingSituation = usePrefilledText('family-social-history', 'living_situation');

  return (
    <CallScreenLayout
      title="Family and social history"
      subtitle="A few short questions. Say so if you would rather not answer any of them."
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
            Family history
          </h2>
          <MultiSelectCardGrid
            options={FAMILY_HISTORY_OPTIONS}
            selectedIds={familyConditions.selectedIds}
            onToggle={familyConditions.toggle}
          />
          <OptionalDetailsField
            value={familyDetails.value}
            onChange={familyDetails.onChange}
            onSubmit={familyDetails.onSubmit}
            label="More about your family history (optional)"
            helperText="Which relative, and their age at diagnosis if you know it."
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
            Social history
          </h2>
          <SingleChoicePills
            label="Tobacco use"
            options={TOBACCO_USE_OPTIONS}
            value={tobacco.value}
            onChange={tobacco.onChange}
          />
          <SingleChoicePills
            label="Alcohol use"
            options={ALCOHOL_USE_OPTIONS}
            value={alcohol.value}
            onChange={alcohol.onChange}
          />
          <SocialHistoryFields
            occupation={occupation.value}
            livingSituation={livingSituation.value}
            onOccupationChange={occupation.onChange}
            onOccupationSubmit={occupation.onSubmit}
            onLivingSituationChange={livingSituation.onChange}
            onLivingSituationSubmit={livingSituation.onSubmit}
          />
        </div>
      </PageSection>
    </CallScreenLayout>
  );
}

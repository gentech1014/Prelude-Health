import type { JSX } from 'react';
import { PageSection } from '@/components/PageSection';
import { AllergyEntryList } from '@/features/allergies/AllergyEntryList';
import { CallScreenLayout } from '@/features/prescreening-session/CallScreenLayout';
import { usePrefilledBoolean, usePrefilledPairs } from '@/features/prescreening-session/usePrefill';

/**
 * Allergies and what happens on exposure.
 *
 * One of the few things asked about explicitly rather than folded into
 * free text, and the only screen with an opt-out: "no known allergies" is
 * a clinically meaningful answer, and it must be distinguishable from an
 * empty list nobody got round to filling in.
 */
export function Allergies(): JSX.Element {
  const noneKnown = usePrefilledBoolean('allergies', 'no_known_allergies');
  const entries = usePrefilledPairs('allergies', 'allergies');

  return (
    <CallScreenLayout
      title="Allergies"
      subtitle="This is how your care team keeps you safe during treatment."
    >
      <PageSection>
        <AllergyEntryList
          hasNoKnownAllergies={noneKnown.value === true}
          onToggleNoKnownAllergies={noneKnown.onChange}
          entries={entries.entries}
          onEntryChange={entries.change}
          onRemoveEntry={entries.remove}
          onAddEntry={entries.add}
          onSubmitEntries={entries.submit}
        />
      </PageSection>
    </CallScreenLayout>
  );
}

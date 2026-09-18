import type { JSX } from 'react';
import { PageSection } from '@/components/PageSection';
import { CallScreenLayout } from '@/features/prescreening-session/CallScreenLayout';
import { usePrefilledBoolean, usePrefilledText } from '@/features/prescreening-session/usePrefill';
import { RecentProviderDetails } from '@/features/recent-care/RecentProviderDetails';
import { YesNoQuestion } from '@/features/recent-care/YesNoQuestion';

const UserPlusIcon = () => (
  <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" />
    <circle cx="9" cy="7" r="4" />
    <line x1="19" y1="8" x2="19" y2="14" />
    <line x1="22" y1="11" x2="16" y2="11" />
  </svg>
);

const ClipboardListIcon = () => (
  <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <rect width="8" height="4" x="8" y="2" rx="1" ry="1" />
    <path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2" />
    <path d="M12 11h4" />
    <path d="M12 16h4" />
    <path d="M8 11h.01" />
    <path d="M8 16h.01" />
  </svg>
);

/**
 * Whether anyone else has recently been involved in the patient's care,
 * and any recent test results.
 *
 * When the patient says "yes" to having test results, the AI agent drives
 * the document upload via DocumentUploadPrompt (shown via showUploadPrompt).
 * No inline upload widget is rendered here to avoid duplication.
 */
export function RecentCare(): JSX.Element {
  const sawOther = usePrefilledBoolean('recent-care', 'saw_other_provider');
  const who = usePrefilledText('recent-care', 'provider_who');
  const when = usePrefilledText('recent-care', 'provider_when');
  const reason = usePrefilledText('recent-care', 'provider_reason');
  const hadTests = usePrefilledBoolean('recent-care', 'had_recent_tests');

  return (
    <CallScreenLayout
      title="Recent care and tests"
      subtitle='This helps me understand your care so far. You can say "yes" or "no", or tap an option.'
      showUploadPrompt
    >
      <PageSection>
        <YesNoQuestion
          icon={<UserPlusIcon />}
          label="Have you seen anyone else about this recently?"
          subtitle="Another doctor, a specialist, or urgent care?"
          value={sawOther.value}
          onChange={sawOther.onChange}
        >
          <RecentProviderDetails
            who={who.value}
            when={when.value}
            reason={reason.value}
            onWhoChange={who.onChange}
            onWhoSubmit={who.onSubmit}
            onWhenChange={when.onChange}
            onWhenSubmit={when.onSubmit}
            onReasonChange={reason.onChange}
            onReasonSubmit={reason.onSubmit}
          />
        </YesNoQuestion>
      </PageSection>

      <PageSection>
        <YesNoQuestion
          icon={<ClipboardListIcon />}
          label="Have you had any tests or results recently?"
          subtitle="Like blood tests, X-rays, scans, or any other tests?"
          value={hadTests.value}
          onChange={hadTests.onChange}
        />
      </PageSection>
    </CallScreenLayout>
  );
}

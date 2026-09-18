import type { JSX } from 'react';
import { PageSection } from '@/components/PageSection';
import { CallScreenLayout } from '@/features/prescreening-session/CallScreenLayout';
import { usePrefilledText } from '@/features/prescreening-session/usePrefill';
import { useDocumentUpload } from '@/features/prescreening-session/useDocumentUpload';
import { MedicationInput } from '@/features/medication/MedicationInput';

/**
 * Medications, vitamins and supplements.
 *
 * A photo of the box is offered alongside the text, because a medication
 * list is the hardest thing in this call to get right by voice — brand
 * names and doses are exactly where a mishearing matters most, and a
 * picture removes the guesswork rather than compounding it.
 */
export function Medication(): JSX.Element {
  const medications = usePrefilledText('medication', 'medications');
  const upload = useDocumentUpload();

  return (
    <CallScreenLayout
      title="Medication"
      subtitle="Anything you take regularly, including over-the-counter and supplements."
    >
      <PageSection>
        <MedicationInput
          value={medications.value}
          onChange={medications.onChange}
          onSubmit={medications.onSubmit}
          isUploading={upload.isUploading}
          uploadedFileName={upload.fileName}
          uploadError={upload.error}
          onPhotoSelected={(file) => void upload.upload(file)}
        />
      </PageSection>
    </CallScreenLayout>
  );
}

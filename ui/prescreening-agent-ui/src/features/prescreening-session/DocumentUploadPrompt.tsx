import { X } from 'lucide-react';
import type { JSX } from 'react';
import { InlineBanner } from '@/components/states';
import { MedicalReportUpload } from '@/features/recent-care/MedicalReportUpload';
import { useDocumentUpload } from '@/features/prescreening-session/useDocumentUpload';
import { useIntakeCall } from '@/features/prescreening-session/useIntakeCall';

/**
 * Appears only when the agent has actually asked for a document, on
 * whatever screen the call happens to be on.
 *
 * The agent does not wait for the file, so neither does this: the patient
 * can dismiss it and carry on talking, and the upload continues in the
 * background if they started one.
 */
export function DocumentUploadPrompt(): JSX.Element | null {
  const { isUploadRequested, dismissUploadRequest } = useIntakeCall();
  const upload = useDocumentUpload();

  if (!isUploadRequested) return null;

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 'var(--space-3)',
        padding: 'var(--space-4)',
        borderRadius: 'var(--radius-lg)',
        background: 'var(--color-surface)',
        border: '1px solid var(--color-primary-border)',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 'var(--space-2)' }}>
        <p
          style={{
            margin: 0,
            flex: 1,
            fontSize: '0.95rem',
            fontWeight: 'var(--fw-semibold)',
            color: 'var(--color-text)',
          }}
        >
          Add a document for your doctor
        </p>
        <button
          type="button"
          onClick={dismissUploadRequest}
          aria-label="Dismiss the document request"
          style={{
            border: 'none',
            background: 'transparent',
            color: 'var(--color-text-secondary)',
            padding: 0,
            cursor: 'pointer',
          }}
        >
          <X size={16} aria-hidden="true" />
        </button>
      </div>

      <MedicalReportUpload
        fileName={upload.fileName}
        isUploading={upload.isUploading}
        onFileSelected={(file) => void upload.upload(file)}
        onRemove={upload.clear}
      />

      {upload.error ? <InlineBanner tone="error" message={upload.error} /> : null}
    </div>
  );
}

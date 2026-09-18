import { useCallback, useState } from 'react';
import { useIntakeCall } from '@/features/prescreening-session/useIntakeCall';
import { usePrescreeningSession } from '@/features/prescreening-session/usePrescreeningSession';
import { useToast } from '@/hooks/useToast';
import { DocumentUploadError, uploadSupportingDocument } from '@/services/documentUploadService';

export interface DocumentUpload {
  /** The file currently attached to this session, by name. Null when none is. */
  fileName: string | null;
  isUploading: boolean;
  error: string | null;
  upload: (file: File) => Promise<void>;
  clear: () => void;
}

/**
 * Sends one supporting document to storage and links it to the session.
 *
 * Shared by every screen that can take a file, because the agent may open
 * the upload prompt from any of them. The name is held locally only as
 * confirmation of what was sent — the session document is authoritative
 * about whether a file actually landed.
 */
export function useDocumentUpload(): DocumentUpload {
  const { sessionId, refresh } = usePrescreeningSession();
  const { reportDocumentUploaded, reportDocumentUploadFailed } = useIntakeCall();
  const { showToast } = useToast();
  const [fileName, setFileName] = useState<string | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const upload = useCallback(
    async (file: File): Promise<void> => {
      setIsUploading(true);
      setError(null);
      try {
        await uploadSupportingDocument(sessionId, file);
        setFileName(file.name);
        showToast({ message: 'Your file was added for your doctor.', tone: 'success' });
        // Re-read so `documentUploaded` on the session reflects reality
        // rather than this component's local belief about it.
        await refresh();
        // Only once the write is real. The upload is a REST call the socket
        // cannot see, so before this the assistant never learned the file
        // had arrived — it had been told not to wait and not to ask, and
        // the patient had no way of knowing they were expected to announce
        // it. It now asks them what the report is.
        reportDocumentUploaded(file.name);
      } catch (cause) {
        const message =
          cause instanceof DocumentUploadError
            ? cause.message
            : 'We could not upload that file. Try again.';
        setError(message);
        setFileName(null);
        // A failure used to be known only to this screen, so the assistant
        // carried on to the next topic while the patient sat looking at an
        // error waiting to be told what to do about it.
        reportDocumentUploadFailed();
      } finally {
        setIsUploading(false);
      }
    },
    [refresh, reportDocumentUploadFailed, reportDocumentUploaded, sessionId, showToast],
  );

  return {
    fileName,
    isUploading,
    error,
    upload,
    // Clears this screen's confirmation only. The file itself stays with
    // the session — the patient has already shared it with their doctor,
    // and a control here must not imply it has been withdrawn.
    clear: () => {
      setFileName(null);
      setError(null);
    },
  };
}

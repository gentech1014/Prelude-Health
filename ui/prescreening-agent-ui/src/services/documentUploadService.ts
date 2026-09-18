import { z } from 'zod';
import { apiClient, ApiError, toApiError } from '@/services/apiClient';

/**
 * The patient's supporting-document upload: a lab result, a scan, a photo
 * of a medication box.
 *
 * Three steps, and the middle one deliberately does not go through this
 * API: the bytes are `PUT` straight to object storage against a presigned
 * URL, so a large file never occupies a server request, and the live call
 * is never blocked behind it.
 */

const MAX_FILE_BYTES = 15 * 1024 * 1024;

/**
 * What the storage bucket will accept. An allowlist rather than a
 * blocklist: this file ends up in front of a clinician, and the presigned
 * URL is signed for whatever content type is requested here.
 */
const ALLOWED_CONTENT_TYPES = [
  'image/jpeg',
  'image/png',
  'image/heic',
  'image/webp',
  'application/pdf',
] as const;

const uploadUrlResponseSchema = z.object({ upload_url: z.string(), key: z.string() });

export type DocumentUploadFailure = 'too-large' | 'unsupported-type' | 'failed';

export class DocumentUploadError extends Error {
  readonly reason: DocumentUploadFailure;

  constructor(reason: DocumentUploadFailure, message: string) {
    super(message);
    this.name = 'DocumentUploadError';
    this.reason = reason;
  }
}

function validate(file: File): void {
  if (file.size > MAX_FILE_BYTES) {
    throw new DocumentUploadError('too-large', 'That file is too large. Try a photo under 15 MB.');
  }
  if (!ALLOWED_CONTENT_TYPES.includes(file.type as (typeof ALLOWED_CONTENT_TYPES)[number])) {
    throw new DocumentUploadError(
      'unsupported-type',
      'We can take a photo or a PDF. Try one of those.',
    );
  }
}

/**
 * Uploads one document and links it to the session.
 *
 * Throws `DocumentUploadError` with a `reason` the caller can turn into
 * copy. Nothing here retries: a failed upload is something the patient
 * should be told about and can repeat, and a silent retry of a 15 MB PUT
 * on a phone connection is worse than an honest failure.
 */
export async function uploadSupportingDocument(sessionId: string, file: File): Promise<void> {
  validate(file);

  let presigned: { upload_url: string; key: string };
  try {
    const response = await apiClient.post(`/sessions/${sessionId}/documents/upload-url`, {
      content_type: file.type,
    });
    const parsed = uploadUrlResponseSchema.safeParse(response.data);
    if (!parsed.success) {
      throw new ApiError('malformed', 'We received an unexpected response. Try again shortly.');
    }
    presigned = parsed.data;
  } catch (cause) {
    throw new DocumentUploadError('failed', toApiError(cause).message);
  }

  // Bypasses `apiClient` on purpose: this request goes to the storage
  // provider, not to our API, and must carry neither the session cookie
  // nor our JSON content type. `withCredentials` here would also break
  // the bucket's CORS policy.
  const upload = await fetch(presigned.upload_url, {
    method: 'PUT',
    body: file,
    headers: { 'Content-Type': file.type },
  }).catch(() => null);

  if (upload === null || !upload.ok) {
    throw new DocumentUploadError(
      'failed',
      'We could not upload that file. Check your connection and try again.',
    );
  }

  try {
    await apiClient.post(`/sessions/${sessionId}/documents/complete`, {
      storage_key: presigned.key,
    });
  } catch (cause) {
    // The bytes did land, but nothing points at them: the session has no
    // record, so the physician would never see the file. Reported as a
    // failure rather than quietly succeeding.
    throw new DocumentUploadError('failed', toApiError(cause).message);
  }
}

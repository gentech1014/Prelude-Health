import { z } from 'zod';
import { apiClient, ApiError, toApiError } from '@/services/apiClient';

/**
 * The booking page's "view a generated report" lookup -- a patient (or
 * anyone the session id is shared with) can fetch their own report PDF by
 * id alone. Backed by `GET /sessions/{id}/report-download-url`, which
 * deliberately carries no auth beyond the id itself; see that route's own
 * docstring for the tradeoff this accepts. Never conflate with
 * `GET /sessions/{id}/report`, the physician-key-gated structured-data
 * route this app does not call from here.
 */

const downloadUrlResponseSchema = z.object({ download_url: z.string().nullable() });

export type ReportLookupFailure = 'not-found' | 'not-ready' | 'failed';

export class ReportLookupError extends Error {
  readonly reason: ReportLookupFailure;

  constructor(reason: ReportLookupFailure, message: string) {
    super(message);
    this.name = 'ReportLookupError';
    this.reason = reason;
  }
}

/**
 * Resolves a session id to a pre-signed, directly-openable PDF URL.
 *
 * Throws `ReportLookupError` with a `reason` the screen can turn into
 * copy: `'not-found'` for an id that matches no session at all,
 * `'not-ready'` for a real session whose report has not been generated
 * yet (the call may still be in progress, or summarization may still be
 * running), `'failed'` for anything else.
 */
export async function fetchReportDownloadUrl(sessionId: string): Promise<string> {
  const trimmed = sessionId.trim();
  if (trimmed.length === 0) {
    throw new ReportLookupError('not-found', 'Enter a session id to look up a report.');
  }

  let response;
  try {
    response = await apiClient.get(`/sessions/${trimmed}/report-download-url`);
  } catch (cause) {
    const error = toApiError(cause);
    if (error.kind === 'not-found') {
      throw new ReportLookupError(
        'not-found',
        'No session matches that id. Check it and try again.',
      );
    }
    throw new ReportLookupError('failed', error.message);
  }

  const parsed = downloadUrlResponseSchema.safeParse(response.data);
  if (!parsed.success) {
    throw new ReportLookupError(
      'failed',
      new ApiError('malformed', 'We received an unexpected response. Try again shortly.').message,
    );
  }

  if (parsed.data.download_url === null) {
    throw new ReportLookupError(
      'not-ready',
      'This report is not ready yet. It appears once the call is complete and summarized.',
    );
  }

  return parsed.data.download_url;
}

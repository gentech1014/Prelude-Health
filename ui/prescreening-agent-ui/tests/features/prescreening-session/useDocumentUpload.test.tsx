import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { DocumentUploadPrompt } from '@/features/prescreening-session/DocumentUploadPrompt';
import { DocumentUploadError } from '@/services/documentUploadService';
import * as documentUploadService from '@/services/documentUploadService';
import { renderPageInSession } from '../../testUtils';

/**
 * The upload is a REST call the socket cannot see, so whether the file
 * landed is knowable only to the browser. These pin that it says so — in
 * both directions — because the assistant is explicitly told not to wait
 * for the file and not to ask whether it finished.
 */

vi.mock('@/services/documentUploadService', async (importOriginal) => ({
  ...(await importOriginal<typeof documentUploadService>()),
  uploadSupportingDocument: vi.fn(),
}));

const uploadSupportingDocument = vi.mocked(documentUploadService.uploadSupportingDocument);

function renderPrompt(call: Record<string, unknown>): void {
  renderPageInSession(<DocumentUploadPrompt />, {
    step: 'recent-care',
    call: { isUploadRequested: true, ...call },
  });
}

async function attach(): Promise<void> {
  const user = userEvent.setup();
  const file = new File(['x'], 'results.pdf', { type: 'application/pdf' });
  const input = document.querySelector<HTMLInputElement>('input[type="file"]');
  if (input === null) throw new Error('no file input on the upload prompt');
  await user.upload(input, file);
}

describe('reporting an upload back to the call', () => {
  beforeEach(() => {
    uploadSupportingDocument.mockReset();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it('tells the assistant when a document lands, so it can ask what the report is', async () => {
    uploadSupportingDocument.mockResolvedValue(undefined);
    const reportDocumentUploaded = vi.fn();
    const reportDocumentUploadFailed = vi.fn();
    renderPrompt({ reportDocumentUploaded, reportDocumentUploadFailed });

    await attach();

    await waitFor(() => expect(reportDocumentUploaded).toHaveBeenCalledTimes(1));
    expect(reportDocumentUploaded).toHaveBeenCalledWith('results.pdf');
    expect(reportDocumentUploadFailed).not.toHaveBeenCalled();
  });

  it('tells the assistant when it fails, rather than leaving the patient on an error alone', async () => {
    uploadSupportingDocument.mockRejectedValue(
      new DocumentUploadError('failed', 'We could not upload that file. Try again.'),
    );
    const reportDocumentUploaded = vi.fn();
    const reportDocumentUploadFailed = vi.fn();
    renderPrompt({ reportDocumentUploaded, reportDocumentUploadFailed });

    await attach();

    await waitFor(() => expect(reportDocumentUploadFailed).toHaveBeenCalledTimes(1));
    expect(reportDocumentUploaded).not.toHaveBeenCalled();
    expect(await screen.findByText(/could not upload/i)).toBeInTheDocument();
  });
});

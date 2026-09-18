import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, type RenderResult } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ThemeProvider } from '@/app/ThemeProvider';
import { ReportLookup } from '@/pages/ReportLookup';
import type * as reportLookupService from '@/services/reportLookupService';
import { fetchReportDownloadUrl, ReportLookupError } from '@/services/reportLookupService';

vi.mock('@/services/reportLookupService', async (importOriginal) => {
  const actual = await importOriginal<typeof reportLookupService>();
  return { ...actual, fetchReportDownloadUrl: vi.fn() };
});

const fetchReportDownloadUrlMock = vi.mocked(fetchReportDownloadUrl);

function renderReportLookup(): RenderResult {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });

  return render(
    <MemoryRouter initialEntries={['/reports']}>
      <ThemeProvider>
        <QueryClientProvider client={queryClient}>
          <ReportLookup />
        </QueryClientProvider>
      </ThemeProvider>
    </MemoryRouter>,
  );
}

describe('ReportLookup', () => {
  beforeEach(() => {
    fetchReportDownloadUrlMock.mockReset();
  });

  it('has nothing to submit until a session id is typed', () => {
    renderReportLookup();

    expect(screen.getByRole('button', { name: /find report/i })).toBeDisabled();
  });

  it('shows the report as an openable file, not an embedded viewer', async () => {
    // Embedding the PDF in an <iframe> was tried first and dropped: it
    // isn't scrollable on mobile, which is where this page is actually
    // used. A file the patient opens in a new tab always works.
    const user = userEvent.setup();
    fetchReportDownloadUrlMock.mockResolvedValue('https://example-bucket.s3.amazonaws.com/report.pdf');
    renderReportLookup();

    await user.type(screen.getByLabelText(/session id/i), 'sess_1');
    await user.click(screen.getByRole('button', { name: /find report/i }));

    expect(fetchReportDownloadUrlMock.mock.calls[0]?.[0]).toBe('sess_1');
    const fileLink = await screen.findByRole('link', { name: /open pre-screening report/i });
    expect(fileLink).toHaveAttribute('href', 'https://example-bucket.s3.amazonaws.com/report.pdf');
    expect(fileLink).toHaveAttribute('target', '_blank');
    expect(screen.queryByTitle(/pre-screening report for session/i)).not.toBeInTheDocument();
  });

  it('shows the lookup failure message, not a generic one', async () => {
    const user = userEvent.setup();
    fetchReportDownloadUrlMock.mockRejectedValue(
      new ReportLookupError('not-found', 'No session matches that id. Check it and try again.'),
    );
    renderReportLookup();

    await user.type(screen.getByLabelText(/session id/i), 'sess_missing');
    await user.click(screen.getByRole('button', { name: /find report/i }));

    expect(
      await screen.findByText('No session matches that id. Check it and try again.'),
    ).toBeInTheDocument();
  });

  it('tells the patient plainly when the report is not ready yet', async () => {
    const user = userEvent.setup();
    fetchReportDownloadUrlMock.mockRejectedValue(
      new ReportLookupError(
        'not-ready',
        'This report is not ready yet. It appears once the call is complete and summarized.',
      ),
    );
    renderReportLookup();

    await user.type(screen.getByLabelText(/session id/i), 'sess_1');
    await user.click(screen.getByRole('button', { name: /find report/i }));

    expect(
      await screen.findByText(/this report is not ready yet/i),
    ).toBeInTheDocument();
  });
});

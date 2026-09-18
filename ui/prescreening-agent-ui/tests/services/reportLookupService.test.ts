import { beforeEach, describe, expect, it, vi } from 'vitest';
import { apiClient, ApiError } from '@/services/apiClient';
import { fetchReportDownloadUrl, ReportLookupError } from '@/services/reportLookupService';

describe('fetchReportDownloadUrl', () => {
  beforeEach(() => vi.restoreAllMocks());

  it('returns the pre-signed url once the report is ready', async () => {
    vi.spyOn(apiClient, 'get').mockResolvedValue({
      data: { download_url: 'https://example-bucket.s3.amazonaws.com/reports/sess_1/report.pdf' },
    });

    const url = await fetchReportDownloadUrl('sess_1');

    expect(url).toBe('https://example-bucket.s3.amazonaws.com/reports/sess_1/report.pdf');
  });

  it('trims the id before sending it, so a pasted trailing space still resolves', async () => {
    const get = vi.spyOn(apiClient, 'get').mockResolvedValue({
      data: { download_url: 'https://example.com/report.pdf' },
    });

    await fetchReportDownloadUrl('  sess_1  ');

    expect(get).toHaveBeenCalledWith('/sessions/sess_1/report-download-url');
  });

  it('rejects an empty id without making a request', async () => {
    const get = vi.spyOn(apiClient, 'get');

    await expect(fetchReportDownloadUrl('   ')).rejects.toMatchObject({
      reason: 'not-found',
    });
    expect(get).not.toHaveBeenCalled();
  });

  it('reports a session that does not exist as not-found, not a generic failure', async () => {
    vi.spyOn(apiClient, 'get').mockRejectedValue(new ApiError('not-found', 'no such session'));

    await expect(fetchReportDownloadUrl('sess_missing')).rejects.toMatchObject({
      reason: 'not-found',
    });
  });

  it('reports a real session with no report yet as not-ready, distinct from not-found', async () => {
    vi.spyOn(apiClient, 'get').mockResolvedValue({ data: { download_url: null } });

    await expect(fetchReportDownloadUrl('sess_1')).rejects.toMatchObject({
      reason: 'not-ready',
    });
  });

  it('rejects a malformed response at the boundary', async () => {
    vi.spyOn(apiClient, 'get').mockResolvedValue({ data: { unexpected: true } });

    await expect(fetchReportDownloadUrl('sess_1')).rejects.toBeInstanceOf(ReportLookupError);
  });
});

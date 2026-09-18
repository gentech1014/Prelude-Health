import { useMutation } from '@tanstack/react-query';
import { ArrowLeft, ExternalLink, FileText, Search } from 'lucide-react';
import { useState, type FormEvent, type JSX } from 'react';
import { useNavigate } from 'react-router-dom';
import { EmptyState, InlineBanner, LoadingState } from '@/components/states';
import '@/features/booking/booking.css';
import '@/pages/ReportLookup.css';
import { fetchReportDownloadUrl, ReportLookupError } from '@/services/reportLookupService';

/**
 * Demo report viewer: a patient (or anyone the session id is shared with)
 * looks their own report up by id alone, reached from the booking page's
 * settings menu.
 *
 * Stands in for real delivery, which this hackathon build sends to the
 * doctor's Google Calendar instead — see `fetchReportDownloadUrl`'s
 * docstring for why this lookup deliberately carries no auth beyond the id.
 */
export function ReportLookup(): JSX.Element {
  const navigate = useNavigate();
  const [sessionId, setSessionId] = useState('');
  const [submittedId, setSubmittedId] = useState<string | null>(null);

  const lookup = useMutation({
    mutationFn: fetchReportDownloadUrl,
  });

  function handleSubmit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    const trimmed = sessionId.trim();
    if (trimmed.length === 0) return;
    setSubmittedId(trimmed);
    lookup.mutate(trimmed);
  }

  return (
    <div className="booking-shell">
      <header className="report-lookup-header">
        <button
          type="button"
          className="report-lookup-back"
          onClick={() => navigate('/book')}
          aria-label="Back to booking"
        >
          <ArrowLeft size={18} aria-hidden="true" />
        </button>
        <h1 className="report-lookup-title">Generated report</h1>
      </header>

      <div className="report-lookup-body">
        <form className="report-lookup-search" onSubmit={handleSubmit}>
          <label className="report-lookup-search__label" htmlFor="report-session-id">
            Session ID
          </label>
          <div className="report-lookup-search__row">
            <input
              id="report-session-id"
              type="text"
              className="report-lookup-search__input"
              value={sessionId}
              onChange={(event) => setSessionId(event.target.value)}
              placeholder="e.g. 74f0abead32e4786973b4fcf67ff45f8"
              autoComplete="off"
              spellCheck={false}
            />
            <button
              type="submit"
              className="report-lookup-search__button"
              disabled={sessionId.trim().length === 0 || lookup.isPending}
            >
              <Search size={16} aria-hidden="true" />
              Find report
            </button>
          </div>
          <p className="report-lookup-search__hint">
            Find this in your booking confirmation, as part of the pre-visit screening link.
          </p>
        </form>

        {lookup.isPending ? <LoadingState label="Looking up that session…" /> : null}

        {lookup.isError ? (
          <InlineBanner
            tone="error"
            message={
              lookup.error instanceof ReportLookupError
                ? lookup.error.message
                : 'Something went wrong. Try again shortly.'
            }
          />
        ) : null}

        {lookup.isSuccess ? (
          <a
            className="report-lookup-file"
            href={lookup.data}
            target="_blank"
            rel="noreferrer"
            aria-label={`Open pre-screening report for session ${submittedId} in a new tab`}
          >
            <span className="report-lookup-file__icon" aria-hidden="true">
              <FileText size={22} />
            </span>
            <span className="report-lookup-file__text">
              <span className="report-lookup-file__name">
                Pre-Screening_Report_{submittedId}.pdf
              </span>
              <span className="report-lookup-file__hint">Opens in a new tab</span>
            </span>
            <ExternalLink className="report-lookup-file__external" size={18} aria-hidden="true" />
          </a>
        ) : null}

        {!lookup.isPending && !lookup.isError && !lookup.isSuccess ? (
          <EmptyState
            icon={<FileText size={24} aria-hidden="true" />}
            title="No report loaded yet"
            description="Enter a session id above to find its generated report."
          />
        ) : null}
      </div>
    </div>
  );
}

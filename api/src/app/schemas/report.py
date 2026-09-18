"""Physician-facing report API schema. Re-exports the domain model as-is.

Kept as an explicit re-export (rather than importing `app.models.report`
directly in the API layer) so the wire contract can diverge from the
internal model later without touching persistence code.
"""

from pydantic import BaseModel

from app.models.report import Finding, PreScreeningReport

__all__ = ["Finding", "PreScreeningReport", "ReportDownloadUrlResponse"]


class ReportDownloadUrlResponse(BaseModel):
    """A pre-signed link to the generated report PDF, for the session-id
    lookup on the booking page's demo report viewer.

    `download_url` is `None` when the session exists but has not reached
    `SUMMARY_READY` yet -- the PDF is only written to storage once a report
    is attached, so there is nothing to sign a link to before then.
    """

    download_url: str | None

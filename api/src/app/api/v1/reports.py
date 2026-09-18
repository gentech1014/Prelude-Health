"""Endpoints for retrieving a completed pre-screening report."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import get_session_repository, get_storage_client, require_physician_api_key
from app.core.exceptions import SessionNotFoundError
from app.integrations.storage import ObjectStorageClient
from app.repositories.session_repository import SessionRepository
from app.schemas.report import PreScreeningReport, ReportDownloadUrlResponse

router = APIRouter(prefix="/sessions", tags=["reports"])


@router.get(
    "/{session_id}/report",
    response_model=PreScreeningReport | None,
    dependencies=[Depends(require_physician_api_key)],
)
async def get_report(
    session_id: str,
    session_repository: Annotated[SessionRepository, Depends(get_session_repository)],
) -> PreScreeningReport | None:
    """Return the structured report for a session, or None if not ready yet.

    Requires the `X-API-Key` header -- this is the one PHI-bearing route in
    the service, so it is gated separately from the patient-facing routes'
    session-scoped intake token. Raises 404 (via `SessionNotFoundError`) if
    `session_id` does not exist at all -- distinct from a valid session
    whose report simply isn't ready, which returns `None` with a 200.
    """
    session = await session_repository.get_by_id(session_id)
    if session is None:
        raise SessionNotFoundError(session_id)

    return session.report


@router.get("/{session_id}/report-download-url", response_model=ReportDownloadUrlResponse)
async def get_report_download_url(
    session_id: str,
    session_repository: Annotated[SessionRepository, Depends(get_session_repository)],
    storage: Annotated[ObjectStorageClient, Depends(get_storage_client)],
) -> ReportDownloadUrlResponse:
    """Return a pre-signed link to the report PDF, keyed by session id alone.

    Deliberately carries no auth beyond the session id itself -- this backs
    the booking page's "view a generated report" demo lookup, where the
    patient (or anyone the id is shared with) looks their own report up by
    the id from their booking confirmation. Unlike `get_report`, there is no
    `X-API-Key` gate here: a conscious scope-narrowing for the hackathon
    demo, not an oversight -- see the booking-page report viewer's own
    docstring for the tradeoff this accepts.

    404 (`SessionNotFoundError`) if the session itself does not exist.
    `download_url: null` with a 200, same convention as `get_report`, when
    the session exists but has not reached `SUMMARY_READY` yet -- the PDF is
    only written to storage once a report is attached (see
    `SessionService.attach_report`), so there is nothing to sign a link to
    before then.
    """
    session = await session_repository.get_by_id(session_id)
    if session is None:
        raise SessionNotFoundError(session_id)

    if session.report is None:
        return ReportDownloadUrlResponse(download_url=None)

    key = f"reports/{session_id}/report.pdf"
    download_url = await storage.generate_download_url(key)
    return ReportDownloadUrlResponse(download_url=download_url)

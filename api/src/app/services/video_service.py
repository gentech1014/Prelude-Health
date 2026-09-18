"""Handles the optional screen-recording artifact.

The recording is a plain browser `MediaRecorder` capture of the call, not
agent-generated. It is uploaded independently of the written report and
must never block report delivery — the written summary is the primary
deliverable.
"""

import structlog

from app.core.constants import SessionState
from app.core.exceptions import SessionNotFoundError, StorageError
from app.integrations.calendar_delivery import CalendarDelivery
from app.integrations.storage import ObjectStorageClient
from app.models.session import Session
from app.repositories.doctor_repository import DoctorRepository
from app.repositories.session_repository import SessionRepository

log = structlog.get_logger(__name__)


class VideoService:
    """Persists the uploaded recording reference against its session."""

    def __init__(
        self,
        storage: ObjectStorageClient,
        session_repository: SessionRepository,
        doctor_repository: DoctorRepository,
        calendar_delivery: CalendarDelivery,
    ) -> None:
        self._storage = storage
        self._sessions = session_repository
        self._doctors = doctor_repository
        self._calendar = calendar_delivery

    async def attach_recording(self, session_id: str, storage_key: str) -> Session:
        """Link an already-uploaded recording to its session, moved to VIDEO_READY.

        Takes the object's storage key, not raw bytes: the frontend uploads
        directly to object storage via a pre-signed URL (see
        `ObjectStorageClient.generate_upload_url`), so by the time this
        runs the bytes are already there -- this call only records that
        fact.
        """
        uri = self._storage.object_uri(storage_key)
        session = await self._sessions.set_video_ref(session_id, uri)
        if session is None:
            raise SessionNotFoundError(session_id)
        updated = await self._sessions.set_status(session_id, SessionState.VIDEO_READY)
        if updated is None:
            raise SessionNotFoundError(session_id)

        await self._deliver_to_calendar(updated, storage_key)
        return updated

    async def _deliver_to_calendar(self, session: Session, storage_key: str) -> None:
        """Best-effort: append the recording link to the doctor's calendar event.

        Uses a pre-signed download URL, not `Session.video_ref`'s internal
        `s3://...` reference -- that URI is not openable in a browser, and
        the whole point of this line is that the doctor can click it.
        Never raises -- a Calendar failure must not fail an otherwise
        successful upload. Silently does nothing if the session has no
        `calendar_event_id` (e.g. it was not booked through the mock
        booking platform) or its doctor has no calendar mapping on file.
        """
        if not session.calendar_event_id:
            return

        doctor = await self._doctors.get_by_name(session.physician)
        if doctor is None:
            log.warning("calendar_delivery_skipped_unknown_doctor", session_id=session.session_id)
            return

        try:
            download_url = await self._storage.generate_download_url(storage_key)
        except StorageError:
            log.exception(
                "calendar_delivery_skipped_no_download_url", session_id=session.session_id
            )
            return

        delivered = await self._calendar.append_line(
            doctor, session.calendar_event_id, f"Screen Recording: {download_url}"
        )
        log.info(
            "calendar_link_delivery",
            session_id=session.session_id,
            label="Screen Recording",
            attempted=delivered,
        )

"""Session lifecycle orchestration: booking -> consent -> call -> summary -> video."""

import asyncio
import mimetypes
import uuid
from datetime import UTC, datetime, time
from html import escape

import structlog

from app.agents.summarizer.agent import describe_document, summarize_session
from app.core.clinic_time import resolve_clinic_zone
from app.core.config import Settings
from app.core.constants import SessionState
from app.core.exceptions import (
    InvalidSessionStateError,
    SessionNotFoundError,
    StorageError,
    SummarizationFailedError,
)
from app.core.security import generate_intake_token
from app.core.timezones import resolve_appointment_timezone
from app.integrations.calendar_delivery import CalendarDelivery
from app.integrations.storage import ObjectStorageClient
from app.models.consent import Consent
from app.models.report import PreScreeningReport
from app.models.session import PatientRef, Session
from app.repositories.doctor_repository import DoctorRepository
from app.repositories.session_repository import SessionRepository
from app.schemas.booking import BookingConfirmedWebhook
from app.services.notification_service import NotificationService
from app.services.report_pdf import DocumentAttachment, render_report_pdf

log = structlog.get_logger(__name__)

_CONSENTABLE_STATES = frozenset({SessionState.NOTIFICATION_SENT, SessionState.STARTED})

_INTERRUPTIBLE_STATES = frozenset({SessionState.IN_PROGRESS, SessionState.INTERRUPTED})
"""States a live-call teardown may write INTERRUPTED from.

Deliberately narrow: a socket closing *after* `complete_call` has already
run would otherwise undo a finished, summarized session."""


class SessionService:
    """Coordinates session state transitions across repositories and integrations."""

    def __init__(
        self,
        session_repository: SessionRepository,
        notification_service: NotificationService,
        settings: Settings,
        doctor_repository: DoctorRepository,
        calendar_delivery: CalendarDelivery,
        storage: ObjectStorageClient,
    ) -> None:
        self._sessions = session_repository
        self._notifications = notification_service
        self._settings = settings
        self._doctors = doctor_repository
        self._calendar = calendar_delivery
        self._storage = storage

    async def create_from_booking(self, webhook: BookingConfirmedWebhook) -> Session:
        """Create a session from a booking-confirmed webhook and send the intake link.

        Idempotent by `appointment_id`: a retried webhook (network blips
        are normal on the sending side) returns the existing session
        instead of creating a duplicate.

        Implements BOOKING_CREATED -> AI_LINK_READY -> NOTIFICATION_SENT.
        """
        existing = await self._sessions.get_by_appointment_id(webhook.appointment_id)
        if existing is not None:
            log.info(
                "booking_webhook_duplicate_ignored",
                appointment_id=webhook.appointment_id,
                session_id=existing.session_id,
            )
            return existing

        doctor = await self._doctors.get_by_name(webhook.physician)
        appointment_timezone = resolve_appointment_timezone(doctor, self._settings)

        now = datetime.now(UTC)
        session = Session(
            session_id=uuid.uuid4().hex,
            appointment_id=webhook.appointment_id,
            patient=PatientRef(
                name=webhook.patient_name,
                patient_id=webhook.patient_id,
                # A plain `date` from the webhook, widened to a UTC-midnight
                # `datetime` here -- BSON has no date-only type, so storing
                # `webhook.date_of_birth` as-is would fail on write. See
                # `PatientRef.date_of_birth`'s docstring.
                date_of_birth=datetime.combine(webhook.date_of_birth, time.min, tzinfo=UTC),
                sex=webhook.sex,
                contact_phone=webhook.contact_phone,
            ),
            physician=webhook.physician,
            appointment_datetime=webhook.scheduled_at,
            appointment_timezone=appointment_timezone,
            booking_visit_type=webhook.visit_type,
            booking_reason=webhook.booking_reason,
            status=SessionState.BOOKING_CREATED,
            created_at=now,
            updated_at=now,
        )
        await self._sessions.create(session)

        intake_url = self.build_intake_url(session.session_id)
        await self._sessions.set_status(session.session_id, SessionState.AI_LINK_READY)

        await self._notifications.send_intake_link(webhook.contact_phone, intake_url)
        notified = await self._sessions.set_status(
            session.session_id, SessionState.NOTIFICATION_SENT
        )

        log.info(
            "booking_session_created",
            session_id=session.session_id,
            appointment_id=webhook.appointment_id,
        )
        # Return the reloaded document, not the local object built before the
        # status writes: that one still says BOOKING_CREATED, so the webhook's
        # 202 response would contradict what is actually stored.
        return self._require(notified, session.session_id)

    def build_intake_message(self, session: Session) -> str:
        """The intake SMS body for a session, appointment time and link included.

        Public for the same reason as `build_intake_url`: the mock booking
        platform shows the patient what was sent, and must not re-compose the
        wording (or the timezone conversion) itself.
        """
        local = session.appointment_datetime.astimezone(resolve_clinic_zone(self._settings))
        # Built piecewise rather than with `%-d`/`%-I`: those are glibc
        # extensions and raise ValueError on Windows.
        appointment_label = (
            f"{local.strftime('%A, %B')} {local.day} at {local.strftime('%I:%M %p').lstrip('0')}"
        )
        return self._notifications.compose_intake_message(
            patient_name=session.patient.name,
            physician=session.physician,
            appointment_label=appointment_label,
            intake_url=self.build_intake_url(session.session_id),
        )

    def build_intake_url(self, session_id: str) -> str:
        """Build the signed patient intake link for a session.

        A public method, not a `create_from_booking`-local helper: the
        mock booking platform (`app.api.v1.mock_booking`) needs the same
        URL to return in its response, and must not duplicate the
        token/URL construction logic.
        """
        token = generate_intake_token(
            session_id, self._settings.intake_link_secret, self._settings.intake_link_ttl_seconds
        )
        # `/prescreen/`, not `/intake/`: this must match the patient app's own
        # route (`/prescreen/:sessionId` in prescreening-agent-ui), or every
        # link minted here 404s in the browser.
        return f"{self._settings.patient_app_base_url}/prescreen/{session_id}?token={token}"

    async def record_consent(self, session_id: str, given: bool) -> Session:
        """Record the patient's consent decision.

        Called after the patient has opened the link (STARTED). A
        declined consent (`given=False`) transitions straight to
        DECLINED; an affirmative one moves to IN_PROGRESS, clearing the
        way for the WebSocket call to begin. The live call must never
        start without a recorded, affirmative consent -- this is also
        enforced defensively at the WebSocket boundary in
        `app.api.ws.intake`.

        Only legal from NOTIFICATION_SENT or STARTED: once a session has
        already moved on (IN_PROGRESS, DECLINED, COMPLETED, CANCELLED,
        ...), a replayed or forged consent request must not be able to
        re-trigger or silently overwrite a decision the patient already
        made -- including, since the scheduling feature shipped, resurrecting
        a cancelled session by racing a concurrent cancel.

        A single guarded write, not the previous read-check-then-two-writes:
        the precondition and the write happen atomically, so nothing can
        interleave between the check and the mutation the way a
        check-then-act sequence allows.
        """
        consent = Consent(given=given, recorded_at=datetime.now(UTC))
        new_status = SessionState.IN_PROGRESS if given else SessionState.DECLINED
        updated = await self._sessions.set_consent_and_status(
            session_id, consent, new_status, expected_status=_CONSENTABLE_STATES
        )
        if updated is not None:
            return updated

        # Guard failed -- one extra read, off the hot path, only to tell
        # "session not found" apart from "session already moved on."
        session = await self._sessions.get_by_id(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)
        raise InvalidSessionStateError(
            session_id, session.status, " or ".join(sorted(_CONSENTABLE_STATES))
        )

    async def start_call(self, session_id: str) -> Session:
        """Transition a session to STARTED when the patient opens the intake link.

        Precedes consent: STARTED means the link was opened, not that the
        conversation has begun -- see `record_consent` for the
        STARTED -> IN_PROGRESS / DECLINED transition.
        """
        return self._require(
            await self._sessions.set_status(session_id, SessionState.STARTED), session_id
        )

    async def resume_call(self, session_id: str) -> Session:
        """Put a reconnecting session back into the state its progress implies.

        Called once the live socket is accepted, not when the patient
        merely reopens their link: a session should only read as in
        progress while there is actually a call on it.

        A call that dropped *before* consent goes back to STARTED, not
        IN_PROGRESS. IN_PROGRESS is not a consentable state, so promoting
        it here would leave that patient permanently unable to consent --
        the agent would ask, the browser would post, and the post would
        409 forever.
        """
        session = await self._sessions.get_by_id(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)

        consented = session.consent is not None and session.consent.given
        resumed = SessionState.IN_PROGRESS if consented else SessionState.STARTED
        return self._require(await self._sessions.set_status(session_id, resumed), session_id)

    async def mark_interrupted(self, session_id: str) -> Session:
        """Record that a live call ended without the agent finishing it.

        A dropped socket, a patient who pressed End, or a failure on our
        side all land here. The distinction from COMPLETED is the whole
        point: no report is generated, so the physician view never shows an
        empty summary as though the call had finished, and the patient can
        reopen their link and carry on from the persisted resume marker.

        Only written from IN_PROGRESS or INTERRUPTED. A call that already
        completed and summarized must not be dragged backwards by a late
        socket teardown arriving after `complete_call` has run.
        """
        session = await self._sessions.get_by_id(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)
        if session.status not in _INTERRUPTIBLE_STATES:
            log.info(
                "interrupt_ignored_for_state",
                session_id=session_id,
                status=session.status.value,
            )
            return session

        return self._require(
            await self._sessions.set_status(session_id, SessionState.INTERRUPTED), session_id
        )

    async def complete_call(self, session_id: str) -> Session:
        """Mark the call COMPLETED and run summarization.

        This is the handoff between the two agents. The live call is over
        by the time this runs, so nobody is waiting on the seconds it
        takes -- but the caller still should not await it inside the
        WebSocket handler, or the socket stays open for the duration.

        A summarization failure is contained here: the session lands in
        SUMMARY_FAILED and the transcript is untouched, so the physician
        view can say plainly that no summary was produced instead of
        rendering an empty one as though it were complete.
        """
        session = self._require(
            await self._sessions.set_status(session_id, SessionState.COMPLETED), session_id
        )
        await self._sessions.set_status(session_id, SessionState.SUMMARIZING)

        try:
            report = await summarize_session(
                session_id,
                session.turns,
                self._settings,
                session.collected_details,
                session.symptom_answers,
                session.collected_statuses,
            )
        except SummarizationFailedError as exc:
            log.warning(
                "summarization_failed",
                session_id=session_id,
                stop_reason=exc.stop_reason,
            )
            return await self.mark_summary_failed(session_id, exc.stop_reason)
        except Exception:
            log.exception("summarization_errored", session_id=session_id)
            return await self.mark_summary_failed(session_id, "exception")

        return await self.attach_report(session_id, report)

    async def attach_report(self, session_id: str, report: PreScreeningReport) -> Session:
        """Attach a completed summarization result and move to SUMMARY_READY."""
        await self._sessions.set_report(session_id, report.model_dump())
        log.info("summary_ready", session_id=session_id, gaps=len(report.gaps))
        session = self._require(
            await self._sessions.set_status(session_id, SessionState.SUMMARY_READY), session_id
        )
        await self._deliver_report_to_calendar(session)
        return session

    async def _deliver_report_to_calendar(self, session: Session) -> None:
        """Best-effort: append a clickable report link to the doctor's calendar event.

        The report already lives in Mongo, gated behind the physician
        `X-API-Key` -- but a Calendar description can only hold plain text,
        not a header, so a raw link to that endpoint would not actually be
        openable. A copy of the report is written to object storage instead,
        specifically so a pre-signed URL can be generated for it: the
        Calendar line becomes a real, clickable link, scoped to this one
        report and expiring in 6 days, rather than the shared physician key
        that endpoint needs.

        Rendered as a PDF, not raw JSON -- the doctor opens this link
        directly; a formatted two-page document (cover + findings) is what
        actually reads as a report. See `app.services.report_pdf`.

        If a supporting document was already uploaded (`document_ref` set),
        a link to it is embedded directly in the PDF, and the file's own
        bytes are fetched and embedded alongside it as a PDF attachment --
        generated fresh here rather than reused from the earlier Calendar
        delivery, since that happened at a different time and this render
        is the only place that needs it. Both are best-effort: a storage
        failure on either just omits that piece rather than blocking report
        delivery, and rendering itself runs off the event loop
        (`asyncio.to_thread`) since it launches a real browser process.
        """
        if session.report is None:
            return

        document_download_url: str | None = None
        attachment: DocumentAttachment | None = None
        if session.document_ref:
            try:
                document_download_url = await self._storage.generate_download_url(
                    session.document_ref
                )
            except StorageError:
                log.exception("report_pdf_document_link_skipped", session_id=session.session_id)
            try:
                data, content_type = await self._storage.download(session.document_ref)
                # The storage key itself is a bare uuid4 hex with no
                # extension (see `create_document_upload_url`) and no
                # original filename is kept anywhere -- the content-type
                # S3 hands back is the only thing this can name the
                # attachment from.
                extension = mimetypes.guess_extension(content_type) or ""
                filename = f"supporting-document{extension}"
                attachment = DocumentAttachment(
                    filename=filename, data=data, content_type=content_type
                )
            except StorageError:
                log.exception(
                    "report_pdf_document_attachment_skipped", session_id=session.session_id
                )

        key = f"reports/{session.session_id}/report.pdf"
        try:
            pdf_bytes = await asyncio.to_thread(
                render_report_pdf, session, document_download_url, attachment
            )
            await self._storage.upload(key, pdf_bytes, "application/pdf")
        except StorageError:
            log.exception("calendar_delivery_skipped_upload_failed", session_id=session.session_id)
            return
        except Exception:
            # Rendering launches a real browser process, and only
            # `StorageError` used to be caught around it -- so anything
            # Playwright itself raised (a missing Chromium, a crashed
            # render, a font read) escaped this method, escaped
            # `complete_call`, and surfaced as a bare `intake_call_errored`
            # on the route. The report survives either way, because it is
            # already on the session in Mongo; what was lost was the
            # doctor's only link to it, and any trace of why.
            log.exception("calendar_delivery_skipped_render_failed", session_id=session.session_id)
            return
        await self._append_calendar_link(session, key, "Pre-Consultation Report")

    async def attach_document(self, session_id: str, storage_key: str) -> Session:
        """Link an already-uploaded supporting document to its session.

        Mirrors `VideoService.attach_recording`: the frontend has already
        put the bytes in object storage via a pre-signed URL (see
        `app.agents.tools.upload`'s docstring on why documents are never
        proxied through the live call), so this only records that fact,
        best-effort describes the document's type, and delivers a
        clickable link to the doctor's calendar -- previously
        `document_uploaded` was set with no way for the physician to
        actually reach the file.
        """
        session = self._require(
            await self._sessions.set_document_ref(session_id, storage_key), session_id
        )
        session = await self._describe_document(session, storage_key)
        await self._append_calendar_link(session, storage_key, "Supporting Document")
        return session

    async def _describe_document(self, session: Session, storage_key: str) -> Session:
        """Best-effort: fetch the uploaded file and record what type it is.

        Never blocks or fails `attach_document` -- a storage read failure
        here just leaves `document_summary` unset, the same tolerance
        `_append_calendar_link` already gives a `StorageError` right after
        this runs. `describe_document` itself never raises; only the
        `storage.download` call needs guarding here.
        """
        try:
            data, content_type = await self._storage.download(storage_key)
        except StorageError:
            log.exception("document_description_skipped_no_bytes", session_id=session.session_id)
            return session
        summary = await describe_document(content_type, data, self._settings)
        return await self._sessions.set_document_summary(session.session_id, summary) or session

    async def _append_calendar_link(self, session: Session, storage_key: str, label: str) -> None:
        """Best-effort: append a `label: <clickable link>` line to the doctor's event.

        Shared by report and document delivery. Never raises -- a Calendar
        (or storage) failure must not undo the upload or summarization that
        already succeeded. Does nothing if the session has no
        `calendar_event_id` (not booked through the mock booking platform)
        or its doctor has no calendar mapping on file.

        Which Google credential this goes out under is
        `CalendarDelivery`'s decision, not this service's -- see that
        module for the delivery that silently went nowhere for every
        doctor who had registered their own calendar.
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

        # An anchor, not the bare URL. A presigned S3 link is several
        # hundred characters of credential-bearing query string, and
        # Calendar rendered every one of them in full across eight lines of
        # the doctor's event -- burying the appointment's own description
        # and putting the signature on screen for anyone glancing at it.
        anchor = f'<a href="{escape(download_url, quote=True)}">{escape(label)}</a>'
        delivered = await self._calendar.append_line(doctor, session.calendar_event_id, anchor)
        log.info(
            "calendar_link_delivery",
            session_id=session.session_id,
            label=label,
            attempted=delivered,
        )

    async def mark_summary_failed(self, session_id: str, stop_reason: str | None) -> Session:
        """Move a session to SUMMARY_FAILED without discarding the transcript.

        `stop_reason` is persisted rather than only logged: a summary can
        fail for reasons worth telling apart later -- a token budget trip
        versus a model error -- and the physician view has no other way to
        explain the failure it is displaying.
        """
        return self._require(
            await self._sessions.set_summary_failure(session_id, stop_reason),
            session_id,
        )

    @staticmethod
    def _require(session: Session | None, session_id: str) -> Session:
        """Guard against updating a session that no longer exists."""
        if session is None:
            raise SessionNotFoundError(session_id)
        return session

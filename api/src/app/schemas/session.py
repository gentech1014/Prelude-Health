"""Session-facing API schemas: status polling, attach, and link resolution."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.core.config import Settings
from app.core.constants import IntakeScreen, SessionState, Sex
from app.models.doctor import Doctor
from app.models.session import Session


class SessionStatusResponse(BaseModel):
    """Returned to the patient's intake page or the physician's dashboard.

    Carries no PHI on purpose -- it is the one session shape that is safe
    to poll frequently and to log. Patient and appointment detail lives on
    `SessionContextResponse` instead, behind the session cookie.
    """

    session_id: str
    status: SessionState
    document_uploaded: bool
    has_report: bool
    has_video: bool

    @classmethod
    def from_session(cls, session: Session) -> SessionStatusResponse:
        """Build the response from a domain `Session`.

        Centralized here so every endpoint that returns session status
        (status polling, consent submission, the booking webhook) stays
        consistent rather than re-deriving `has_report`/`has_video` at
        each call site.
        """
        return cls(
            session_id=session.session_id,
            status=session.status,
            document_uploaded=session.document_uploaded,
            has_report=session.report is not None,
            has_video=session.video_ref is not None,
        )


class PatientContext(BaseModel):
    """Who the patient app should greet, and confirm details against."""

    name: str
    date_of_birth: datetime
    sex: Sex
    contact_phone: str | None = Field(
        default=None,
        description=(
            "The number from the booking, so the patient checks it rather than "
            "types it. Returned only on the patient's own authenticated "
            "session, which is the same scope that already carries their name "
            "and date of birth."
        ),
    )


class AppointmentContext(BaseModel):
    """The appointment this session is preparing for."""

    appointment_id: str
    physician: str
    physician_credential: str | None = Field(
        default=None,
        description=(
            "Post-nominal from the doctor's own record, e.g. 'MD'. Null when the "
            "record carries none -- the patient app then shows no subtitle rather "
            "than a plausible-looking job title nobody stated."
        ),
    )
    scheduled_at: datetime
    duration_minutes: int = Field(
        description=(
            "Appointment length, from the clinic's configured slot size. Served "
            "rather than assumed so the patient app never renders an end time it "
            "invented."
        )
    )
    booking_reason: str | None = None


class AssistantContext(BaseModel):
    """Who the patient is talking to, as the deployment names them."""

    name: str
    role: str


class ClinicContext(BaseModel):
    """The facility's own identity. Not per-session data, but not the frontend's either."""

    name: str | None = None
    location: str | None = None
    timezone: str


class CallProgressContext(BaseModel):
    """How far a live call has already got, for resume and prefill.

    `screen` is null before the first call starts. `details` holds the
    patient's own words keyed by screen then field; the patient app offers
    them as editable values, never as confirmed facts.
    """

    screen: IntakeScreen | None = None
    details: dict[str, dict[str, str]] = Field(default_factory=dict)


class SessionContextResponse(BaseModel):
    """Everything the patient app needs to render the call, in one response.

    **This carries PHI.** It exists because the patient app previously ran
    on a hardcoded fixture, and a patient cannot confirm details they are
    never shown. It is deliberately gated behind the `HttpOnly` session
    cookie -- never behind the intake token, which travels in a URL -- and
    is returned by `attach` and by `GET /sessions/{id}/context` only,
    never by the pollable status route.

    It also carries the deployment's clinic and assistant identity. Those
    are not PHI, but serving them here rather than letting the frontend
    hold its own copy is what keeps the patient app free of values that
    disagree with the deployment behind it.
    """

    session_id: str
    status: SessionState
    consent_given: bool
    document_uploaded: bool
    has_report: bool
    patient: PatientContext
    appointment: AppointmentContext
    assistant: AssistantContext
    clinic: ClinicContext
    call_progress: CallProgressContext

    @classmethod
    def from_session(
        cls,
        session: Session,
        settings: Settings,
        doctor: Doctor | None = None,
    ) -> SessionContextResponse:
        """Build the full context response from a domain `Session`.

        `doctor` is optional because not every caller has one on hand: a
        session names its physician by free-text string, and a typo means
        no record matches. A missing record costs the credential line, not
        the response.
        """
        return cls(
            session_id=session.session_id,
            status=session.status,
            consent_given=session.consent is not None and session.consent.given,
            document_uploaded=session.document_uploaded,
            has_report=session.report is not None,
            patient=PatientContext(
                name=session.patient.name,
                date_of_birth=session.patient.date_of_birth,
                sex=session.patient.sex,
                contact_phone=session.patient.contact_phone,
            ),
            appointment=AppointmentContext(
                appointment_id=session.appointment_id,
                physician=session.physician,
                physician_credential=doctor.credential if doctor else None,
                scheduled_at=session.appointment_datetime,
                duration_minutes=settings.clinic_slot_minutes,
                booking_reason=session.booking_reason,
            ),
            assistant=AssistantContext(name=settings.assistant_name, role=settings.assistant_role),
            clinic=ClinicContext(
                name=settings.clinic_name or None,
                location=settings.clinic_location or None,
                timezone=settings.clinic_timezone,
            ),
            call_progress=CallProgressContext(
                screen=session.last_screen,
                details={
                    screen: dict(fields) for screen, fields in session.collected_details.items()
                },
            ),
        )


class WsTicketResponse(BaseModel):
    """A single-use, seconds-long ticket authorizing one WebSocket connect."""

    ticket: str
    expires_in_seconds: int


class SessionLinkResponse(BaseModel):
    """The AI intake link + short-lived token sent to the patient."""

    session_id: str
    intake_url: str
    expires_at: str

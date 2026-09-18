"""FastAPI dependency providers.

Every service/repository is constructed here from `app.state`, never
imported and instantiated ad hoc inside a route -- keeps route handlers
testable by dependency override.
"""

import hmac
from typing import Annotated

import httpx
from fastapi import Depends, Header
from starlette.requests import HTTPConnection

from app.core.config import Settings, get_settings
from app.core.exceptions import IntakeTokenInvalidError, PhysicianAuthError, SessionAuthError
from app.core.security import verify_intake_token, verify_session_token
from app.integrations.booking_platform import BookingPlatformClient
from app.integrations.calendar_delivery import CalendarDelivery
from app.integrations.doctor_calendar import DoctorCalendarClient
from app.integrations.google_calendar import GoogleCalendarClient
from app.integrations.google_oauth import GoogleOAuthClient
from app.integrations.storage import ObjectStorageClient
from app.repositories.doctor_registration_repository import DoctorRegistrationRepository
from app.repositories.doctor_repository import DoctorRepository
from app.repositories.question_bank_repository import QuestionBankRepository
from app.repositories.scheduling_event_repository import SchedulingEventRepository
from app.repositories.session_repository import SessionRepository
from app.repositories.slot_repository import SlotRepository
from app.repositories.ws_ticket_repository import WsTicketRepository
from app.services.appointment_service import AppointmentService
from app.services.availability_service import AvailabilityService
from app.services.doctor_registration_service import DoctorRegistrationService
from app.services.notification_service import NotificationService
from app.services.question_bank_service import QuestionBankService
from app.services.scheduling_service import SchedulingService
from app.services.session_service import SessionService
from app.services.video_service import VideoService

SettingsDep = Annotated[Settings, Depends(get_settings)]


def require_intake_token(session_id: str, token: str, settings: SettingsDep) -> None:
    """Gate the attach route behind the signed token from the patient's link.

    `session_id` and `token` are resolved by FastAPI from the route's own
    path parameter and query string respectively -- this only works because
    every route that depends on this also declares `{session_id}` in its
    path.

    Used by `POST /sessions/{id}/attach` and nothing else. Every other
    patient route moved to `require_session_cookie`: the intake token
    travels in a URL, which means browser history, `Referer` headers, and
    server logs, so it should be spent once and exchanged rather than
    presented on every request.
    """
    if not verify_intake_token(session_id, token, settings.intake_link_secret):
        raise IntakeTokenInvalidError(session_id)


def require_session_cookie(
    session_id: str,
    connection: HTTPConnection,
    settings: SettingsDep,
) -> None:
    """Gate a patient-facing route behind the `HttpOnly` session cookie.

    The cookie is bound to this one `session_id` by its signature, so a
    cookie minted for another session cannot be replayed here even though
    the browser would happily send it -- the cookie is scoped to the API
    origin, not to a session.

    Typed `HTTPConnection`, not `Request`, for the same reason as the
    providers below.
    """
    cookie = connection.cookies.get(settings.session_cookie_name)
    if cookie is None or not verify_session_token(session_id, cookie, settings.intake_link_secret):
        raise SessionAuthError(session_id)


def get_ws_ticket_repository(connection: HTTPConnection) -> WsTicketRepository:
    """Return the `WsTicketRepository` bound to the app's Mongo database."""
    return WsTicketRepository(connection.app.state.mongo.db)


def require_physician_api_key(
    settings: SettingsDep,
    x_api_key: Annotated[str | None, Header()] = None,
) -> None:
    """Gate a physician-facing route behind a shared `X-API-Key` header.

    A single shared secret rather than per-doctor accounts -- appropriate
    for this project's scope, and a real improvement over the alternative
    of no auth at all on a route that returns PHI.
    """
    if x_api_key is None or not hmac.compare_digest(x_api_key, settings.physician_api_key):
        raise PhysicianAuthError()


def get_session_repository(connection: HTTPConnection) -> SessionRepository:
    """Return the `SessionRepository` bound to the app's Mongo database.

    Typed `HTTPConnection`, not `Request`: it is the common base of both
    `Request` and `WebSocket`, and FastAPI cannot satisfy a `Request`
    dependency inside a WebSocket route. With `Request` here, every intake
    call failed at connection time.
    """
    return SessionRepository(connection.app.state.mongo.db)


def get_question_bank_repository(connection: HTTPConnection) -> QuestionBankRepository:
    """Return the request-scoped `QuestionBankRepository`."""
    return QuestionBankRepository(connection.app.state.mongo.db)


def get_question_bank_service(connection: HTTPConnection) -> QuestionBankService:
    """Return the process-wide `QuestionBankService`, preloaded at startup."""
    return connection.app.state.question_bank_service


def get_http_client(connection: HTTPConnection) -> httpx.AsyncClient:
    """Return the process-wide `httpx.AsyncClient`, opened once at startup for connection reuse."""
    return connection.app.state.http_client


def get_booking_platform_client(
    settings: SettingsDep,
    http_client: Annotated[httpx.AsyncClient, Depends(get_http_client)],
) -> BookingPlatformClient:
    """Construct a `BookingPlatformClient` for this request."""
    return BookingPlatformClient(settings, http_client)


def get_doctor_repository(connection: HTTPConnection) -> DoctorRepository:
    """Return the `DoctorRepository` bound to the app's Mongo database."""
    return DoctorRepository(connection.app.state.mongo.db)


def get_storage_client(settings: SettingsDep) -> ObjectStorageClient:
    """Construct an `ObjectStorageClient` for this request.

    Cheap to construct per-request (a boto3 client holds no open
    connection until first used), unlike the process-wide `httpx.AsyncClient`.
    """
    return ObjectStorageClient(settings)


def get_google_calendar_client(connection: HTTPConnection) -> GoogleCalendarClient:
    """Return the process-wide `GoogleCalendarClient`, built once at startup.

    Built once, not per-request: loading and validating the service-account
    credentials file on every request would be wasted work for a client
    that has no per-request state.
    """
    return connection.app.state.calendar_client


def get_doctor_calendar_client(settings: SettingsDep) -> DoctorCalendarClient:
    """Construct a `DoctorCalendarClient` for this request.

    Per-request unlike `get_google_calendar_client`: this one holds no
    credentials of its own -- it builds them per doctor from the grant on
    that doctor's record -- so there is nothing to load once and reuse.
    """
    return DoctorCalendarClient(settings)


def get_calendar_delivery(
    doctor_calendar: Annotated[DoctorCalendarClient, Depends(get_doctor_calendar_client)],
    service_account_calendar: Annotated[GoogleCalendarClient, Depends(get_google_calendar_client)],
) -> CalendarDelivery:
    """Construct the delivery seam that picks between the two Calendar clients."""
    return CalendarDelivery(doctor_calendar, service_account_calendar)


def get_session_service(
    session_repository: Annotated[SessionRepository, Depends(get_session_repository)],
    settings: SettingsDep,
    doctor_repository: Annotated[DoctorRepository, Depends(get_doctor_repository)],
    calendar_delivery: Annotated[CalendarDelivery, Depends(get_calendar_delivery)],
    storage: Annotated[ObjectStorageClient, Depends(get_storage_client)],
) -> SessionService:
    """Construct a `SessionService` for this request."""
    return SessionService(
        session_repository,
        NotificationService(),
        settings,
        doctor_repository,
        calendar_delivery,
        storage,
    )


def get_slot_repository(connection: HTTPConnection) -> SlotRepository:
    """Return the `SlotRepository` bound to the app's Mongo database."""
    return SlotRepository(connection.app.state.mongo.db)


def get_scheduling_event_repository(connection: HTTPConnection) -> SchedulingEventRepository:
    """Return the `SchedulingEventRepository` bound to the app's Mongo database."""
    return SchedulingEventRepository(connection.app.state.mongo.db)


def get_scheduling_service(
    session_repository: Annotated[SessionRepository, Depends(get_session_repository)],
    slot_repository: Annotated[SlotRepository, Depends(get_slot_repository)],
    scheduling_events: Annotated[
        SchedulingEventRepository, Depends(get_scheduling_event_repository)
    ],
    doctor_repository: Annotated[DoctorRepository, Depends(get_doctor_repository)],
    calendar_client: Annotated[GoogleCalendarClient, Depends(get_google_calendar_client)],
    settings: SettingsDep,
) -> SchedulingService:
    """Construct a `SchedulingService` for this request."""
    return SchedulingService(
        session_repository,
        slot_repository,
        scheduling_events,
        doctor_repository,
        calendar_client,
        settings,
    )


def get_video_service(
    storage: Annotated[ObjectStorageClient, Depends(get_storage_client)],
    session_repository: Annotated[SessionRepository, Depends(get_session_repository)],
    doctor_repository: Annotated[DoctorRepository, Depends(get_doctor_repository)],
    calendar_delivery: Annotated[CalendarDelivery, Depends(get_calendar_delivery)],
) -> VideoService:
    """Construct a `VideoService` for this request."""
    return VideoService(storage, session_repository, doctor_repository, calendar_delivery)


def get_doctor_registration_repository(connection: HTTPConnection) -> DoctorRegistrationRepository:
    """Return the `DoctorRegistrationRepository` bound to the app's Mongo database."""
    return DoctorRegistrationRepository(connection.app.state.mongo.db)


def get_google_oauth_client(
    settings: SettingsDep,
    http_client: Annotated[httpx.AsyncClient, Depends(get_http_client)],
) -> GoogleOAuthClient:
    """Construct a `GoogleOAuthClient` on the process-wide HTTP client."""
    return GoogleOAuthClient(settings, http_client)


def get_availability_service(
    settings: SettingsDep,
    calendar_client: Annotated[DoctorCalendarClient, Depends(get_doctor_calendar_client)],
    session_repository: Annotated[SessionRepository, Depends(get_session_repository)],
) -> AvailabilityService:
    """Construct an `AvailabilityService` for this request."""
    return AvailabilityService(settings, calendar_client, session_repository)


def get_appointment_service(
    session_repository: Annotated[SessionRepository, Depends(get_session_repository)],
    doctor_repository: Annotated[DoctorRepository, Depends(get_doctor_repository)],
    availability_service: Annotated[AvailabilityService, Depends(get_availability_service)],
    calendar_delivery: Annotated[CalendarDelivery, Depends(get_calendar_delivery)],
) -> AppointmentService:
    """Construct an `AppointmentService` for this request or live call."""
    return AppointmentService(
        session_repository, doctor_repository, availability_service, calendar_delivery
    )


def get_doctor_registration_service(
    settings: SettingsDep,
    registrations: Annotated[
        DoctorRegistrationRepository, Depends(get_doctor_registration_repository)
    ],
    doctors: Annotated[DoctorRepository, Depends(get_doctor_repository)],
    oauth: Annotated[GoogleOAuthClient, Depends(get_google_oauth_client)],
) -> DoctorRegistrationService:
    """Construct a `DoctorRegistrationService` for this request."""
    return DoctorRegistrationService(
        registrations, doctors, oauth, settings.doctor_registration_ttl_seconds
    )

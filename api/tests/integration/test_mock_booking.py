"""Integration test for the mock scheduling platform endpoint.

Exercises `POST /mock-booking/appointments` end-to-end against the
in-memory Mongo double: doctor lookup, (no-op) calendar event creation,
session creation, and the calendar_event_id write-back.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, time, timedelta

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from mongomock_motor import AsyncMongoMockClient

from app.api.v1.mock_booking import router
from app.core.config import Settings, get_settings
from app.core.constants import BookingVisitType
from app.core.exceptions import PrescreeningError
from app.integrations.google_calendar import GoogleCalendarClient
from app.models.doctor import Doctor
from app.repositories.doctor_repository import DoctorRepository
from app.repositories.session_repository import SessionRepository

_INTAKE_SECRET = "mock-booking-test-intake-secret-32-chars"  # noqa: S105 - test constant
_WEBHOOK_SECRET = "mock-booking-test-webhook-secret-32-chars"  # noqa: S105 - test constant
_PHYSICIAN_API_KEY = "mock-booking-test-physician-key-32-chars"  # noqa: S105 - test constant


def _next_open_slot() -> datetime:
    """The clinic's 9:30am UTC on the next weekday at least a day out.

    Computed rather than hard-coded because the endpoint now re-checks
    availability at submit time: a fixed literal date silently rots into
    the past (or onto a weekend) and the test would fail for a reason that
    has nothing to do with what it is asserting.
    """
    day = (datetime.now(UTC) + timedelta(days=1)).date()
    while day.weekday() > 4:
        day += timedelta(days=1)
    return datetime.combine(day, time(hour=9, minute=30), tzinfo=UTC)


_REQUEST_BODY = {
    "patient_name": "Asha Rao",
    "patient_id": "pt_2290",
    "date_of_birth": "1990-05-14",
    "sex": "female",
    "physician": "Dr. Mehta",
    "scheduled_at": _next_open_slot().isoformat(),
    "booking_reason": "Short of breath on stairs",
    "contact_phone": "+10000000000",
}


@pytest.fixture
def client() -> Iterator[TestClient]:
    """A client whose app.state mirrors what the real lifespan installs."""
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    @app.exception_handler(PrescreeningError)
    async def handle_prescreening_error(request: Request, exc: PrescreeningError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})

    database = AsyncMongoMockClient(tz_aware=True)["mock_booking_test"]

    class _Mongo:
        db = database

    app.state.mongo = _Mongo()
    settings = Settings(
        _env_file=None,  # isolate from the developer's real .env -- see tests/conftest.py
        intake_link_secret=_INTAKE_SECRET,
        booking_webhook_secret=_WEBHOOK_SECRET,
        physician_api_key=_PHYSICIAN_API_KEY,
        patient_app_base_url="https://example.test",
    )
    app.state.calendar_client = GoogleCalendarClient(settings)
    app.dependency_overrides[get_settings] = lambda: settings

    with TestClient(app) as test_client:
        yield test_client


async def test_scheduling_a_known_doctor_creates_a_session(client: TestClient) -> None:
    """A known doctor's booking must produce a session with a valid intake link."""
    doctors = DoctorRepository(client.app.state.mongo.db)
    await doctors.upsert(Doctor(name="Dr. Mehta", google_calendar_id="dr-mehta@example.com"))

    response = client.post("/api/v1/mock-booking/appointments", json=_REQUEST_BODY)

    assert response.status_code == 201
    body = response.json()
    assert "token=" in body["intake_url"]
    # Calendar delivery is not configured in this test settings fixture, so
    # event creation no-ops -- the id is an empty string, not an error.
    assert body["calendar_event_id"] == ""

    sessions = SessionRepository(client.app.state.mongo.db)
    session = await sessions.get_by_id(body["session_id"])
    assert session is not None
    assert session.patient.name == "Asha Rao"
    assert session.calendar_event_id == ""


async def test_booking_a_time_outside_clinic_hours_is_rejected(client: TestClient) -> None:
    """A slot the availability service would never offer must not become a session."""
    doctors = DoctorRepository(client.app.state.mongo.db)
    await doctors.upsert(Doctor(name="Dr. Mehta", google_calendar_id="dr-mehta@example.com"))

    midnight = _next_open_slot().replace(hour=3, minute=0)
    response = client.post(
        "/api/v1/mock-booking/appointments",
        json={**_REQUEST_BODY, "scheduled_at": midnight.isoformat()},
    )

    assert response.status_code == 409


async def test_the_same_slot_cannot_be_booked_twice(client: TestClient) -> None:
    """The second patient to submit the same time must be told to pick another.

    This is the check that makes booking safe without the doctor having
    connected a calendar: their existing sessions here are the only record
    of what is taken.
    """
    doctors = DoctorRepository(client.app.state.mongo.db)
    await doctors.upsert(Doctor(name="Dr. Mehta", google_calendar_id="dr-mehta@example.com"))

    first = client.post("/api/v1/mock-booking/appointments", json=_REQUEST_BODY)
    assert first.status_code == 201

    second = client.post(
        "/api/v1/mock-booking/appointments",
        json={**_REQUEST_BODY, "patient_id": "pt_2291", "patient_name": "Nils Berg"},
    )
    assert second.status_code == 409


async def test_booking_by_doctor_id_resolves_the_physician_name(client: TestClient) -> None:
    """The booking UI addresses a doctor by id; the name comes from their record."""
    doctors = DoctorRepository(client.app.state.mongo.db)
    await doctors.upsert(Doctor(name="Dr. Mehta", google_calendar_id="dr-mehta@example.com"))

    body = {key: value for key, value in _REQUEST_BODY.items() if key != "physician"}
    response = client.post(
        "/api/v1/mock-booking/appointments",
        json={**body, "doctor_id": "dr-mehta", "visit_type": "lung"},
    )

    assert response.status_code == 201
    assert response.json()["physician"] == "Dr. Mehta"

    sessions = SessionRepository(client.app.state.mongo.db)
    session = await sessions.get_by_id(response.json()["session_id"])
    assert session is not None
    # The selection is stored as a value, not as prose inside the reason.
    # It used to be flattened into that sentence and then discarded, which
    # left the live call with nothing to confirm or screen against.
    assert session.booking_visit_type is BookingVisitType.LUNG
    assert session.booking_reason == "Short of breath on stairs"


async def test_booking_without_a_patient_id_mints_a_provisional_one(
    client: TestClient,
) -> None:
    """Most patients cannot recall an MRN; that must not block a booking.

    The session still needs a key, so the server mints one -- visibly
    provisional, so whoever reconciles the record later cannot mistake it
    for a clinic-issued identifier.
    """
    doctors = DoctorRepository(client.app.state.mongo.db)
    await doctors.upsert(Doctor(name="Dr. Mehta", google_calendar_id="dr-mehta@example.com"))

    body = {key: value for key, value in _REQUEST_BODY.items() if key != "patient_id"}
    response = client.post("/api/v1/mock-booking/appointments", json=body)

    assert response.status_code == 201
    assert response.json()["patient_id_is_provisional"] is True
    assert response.json()["patient_id"].startswith("provisional-")

    sessions = SessionRepository(client.app.state.mongo.db)
    session = await sessions.get_by_id(response.json()["session_id"])
    assert session is not None
    assert session.patient.patient_id == response.json()["patient_id"]


async def test_a_supplied_patient_id_is_kept_as_given(client: TestClient) -> None:
    """A clinic-issued id must never be replaced by a generated one."""
    doctors = DoctorRepository(client.app.state.mongo.db)
    await doctors.upsert(Doctor(name="Dr. Mehta", google_calendar_id="dr-mehta@example.com"))

    response = client.post("/api/v1/mock-booking/appointments", json=_REQUEST_BODY)

    assert response.json()["patient_id"] == "pt_2290"
    assert response.json()["patient_id_is_provisional"] is False


async def test_the_response_carries_the_message_the_patient_was_sent(
    client: TestClient,
) -> None:
    """The booking screen shows the real SMS, so the API must return it."""
    doctors = DoctorRepository(client.app.state.mongo.db)
    await doctors.upsert(Doctor(name="Dr. Mehta", google_calendar_id="dr-mehta@example.com"))

    response = client.post("/api/v1/mock-booking/appointments", json=_REQUEST_BODY)

    body = response.json()
    message = body["notification_message"]
    assert body["intake_url"] in message
    assert "Dr. Mehta" in message
    # First name only: an SMS is not a secure channel, so it carries no
    # date of birth, patient id, or reason for the visit.
    assert "Asha" in message
    assert "pt_2290" not in message
    assert "1990-05-14" not in message
    assert "Short of breath on stairs" not in message


async def test_an_unscoped_visit_type_is_still_a_screenable_reason(
    client: TestClient,
) -> None:
    """A general checkup is a reason the call can screen, not an absent one.

    It used to map to no clinical category at all, so the live call arrived
    with nothing to work from and inferred one from whichever seeded set
    sounded closest -- reliably the stomach set. It is now stored as itself,
    with its own coverage brief behind it.
    """
    doctors = DoctorRepository(client.app.state.mongo.db)
    await doctors.upsert(Doctor(name="Dr. Mehta", google_calendar_id="dr-mehta@example.com"))

    body = {key: value for key, value in _REQUEST_BODY.items() if key != "physician"}
    response = client.post(
        "/api/v1/mock-booking/appointments",
        json={**body, "doctor_id": "dr-mehta", "visit_type": "general_checkup"},
    )

    assert response.status_code == 201
    sessions = SessionRepository(client.app.state.mongo.db)
    session = await sessions.get_by_id(response.json()["session_id"])
    assert session is not None
    assert session.booking_visit_type is BookingVisitType.GENERAL_CHECKUP


async def test_scheduling_an_unknown_doctor_is_rejected(client: TestClient) -> None:
    """A physician name with no calendar mapping must not silently create a session."""
    response = client.post(
        "/api/v1/mock-booking/appointments", json={**_REQUEST_BODY, "physician": "Dr. Nobody"}
    )

    assert response.status_code == 404

    sessions = SessionRepository(client.app.state.mongo.db)
    assert await sessions.get_by_appointment_id("appt_9001") is None

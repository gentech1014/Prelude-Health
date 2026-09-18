"""Integration test for the booking-confirmed webhook -> session-created flow.

Exercises the API boundary (FastAPI TestClient) down to the in-memory
Mongo double, without any real network, agent, or Google Calendar calls.
"""

import hashlib
import hmac
from collections.abc import Iterator

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from mongomock_motor import AsyncMongoMockClient

from app.api.v1.webhooks import router
from app.core.config import Settings, get_settings
from app.core.exceptions import PrescreeningError
from app.integrations.google_calendar import GoogleCalendarClient
from app.repositories.session_repository import SessionRepository

_INTAKE_SECRET = "webhook-test-intake-secret-long-enough-32"  # noqa: S105 - test constant
_WEBHOOK_SECRET = "webhook-test-webhook-secret-long-enough"  # noqa: S105 - test constant
_PHYSICIAN_API_KEY = "webhook-test-physician-api-key-long-enough"  # noqa: S105 - test constant

_PAYLOAD = (
    b'{"appointment_id":"appt_9001","patient_name":"Asha Rao","patient_id":"pt_2290",'
    b'"date_of_birth":"1990-05-14","sex":"female",'
    b'"physician":"Dr. Mehta","scheduled_at":"2026-09-03T09:30:00Z",'
    b'"booking_reason":"Short of breath on stairs","contact_phone":"+10000000000"}'
)


def _sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture
def client() -> Iterator[TestClient]:
    """A client whose app.state mirrors what the real lifespan installs."""
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    @app.exception_handler(PrescreeningError)
    async def handle_prescreening_error(request: Request, exc: PrescreeningError) -> JSONResponse:
        """Mirror `app.main`'s handler -- otherwise a domain error here surfaces
        as an unhandled exception instead of the HTTP status code it maps to."""
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})

    database = AsyncMongoMockClient(tz_aware=True)["webhook_test"]

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
    app.state.http_client = httpx.AsyncClient(timeout=10.0)
    app.dependency_overrides[get_settings] = lambda: settings

    with TestClient(app) as test_client:
        yield test_client


async def test_booking_confirmed_creates_session_and_sends_link(client: TestClient) -> None:
    """A valid, correctly-signed booking-confirmed webhook should create a session in AI_LINK_READY."""
    signature = _sign(_WEBHOOK_SECRET, _PAYLOAD)

    response = client.post(
        "/api/v1/webhooks/booking-confirmed",
        content=_PAYLOAD,
        headers={"content-type": "application/json", "x-signature": signature},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "notification_sent"

    repository = SessionRepository(client.app.state.mongo.db)
    session = await repository.get_by_appointment_id("appt_9001")
    assert session is not None
    assert session.patient.name == "Asha Rao"


async def test_booking_confirmed_rejects_bad_signature(client: TestClient) -> None:
    """A webhook with an invalid signature must be rejected before touching the database."""
    response = client.post(
        "/api/v1/webhooks/booking-confirmed",
        content=_PAYLOAD,
        headers={"content-type": "application/json", "x-signature": "0" * 64},
    )

    assert response.status_code == 401

    repository = SessionRepository(client.app.state.mongo.db)
    assert await repository.get_by_appointment_id("appt_9001") is None


async def test_booking_confirmed_requires_a_signature_header(client: TestClient) -> None:
    """A missing `x-signature` header must be rejected, not treated as an open door."""
    response = client.post(
        "/api/v1/webhooks/booking-confirmed",
        content=_PAYLOAD,
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 401

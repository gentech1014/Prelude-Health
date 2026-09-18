"""Integration tests for doctor self-registration via Google OAuth.

The security-relevant properties are the ones pinned hardest: the `state`
works exactly once (so a callback URL cannot be replayed), the refresh
token never appears in a response or a redirect URL, and an unconfigured
deployment refuses to start a flow it cannot finish.

Google itself is never called -- `GoogleOAuthClient` is overridden with a
double, since the real endpoints would need live credentials and a browser.
"""

from collections.abc import Iterator

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from mongomock_motor import AsyncMongoMockClient

from app.api.deps import get_google_oauth_client
from app.api.v1.doctors import router
from app.core.config import Settings, get_settings
from app.core.exceptions import PrescreeningError
from app.integrations.google_oauth import GoogleOAuthClient, GoogleOAuthGrant
from app.repositories.doctor_repository import DoctorRepository

_REFRESH_TOKEN = "doctor-refresh-token-must-not-leak"  # noqa: S105 - test constant

_REGISTRATION = {
    "name": "Dr. Test",
    "credential": "MD",
    "categories": ["heart", "lung"],
    "modality": "in_person_and_virtual",
}


class _StubOAuth(GoogleOAuthClient):
    """A configured OAuth client that never touches Google."""

    def __init__(self) -> None:
        self.states: list[str] = []

    @property
    def is_configured(self) -> bool:
        return True

    def authorization_url(self, state: str) -> str:
        self.states.append(state)
        return f"https://accounts.google.test/consent?state={state}"

    async def exchange_code(self, code: str) -> GoogleOAuthGrant:
        return GoogleOAuthGrant(
            email="dr.test@example.com",
            refresh_token=_REFRESH_TOKEN,
            scopes=["https://www.googleapis.com/auth/calendar"],
            picture_url="https://lh3.googleusercontent.test/dr-test",
        )


@pytest.fixture
def oauth() -> _StubOAuth:
    return _StubOAuth()


@pytest.fixture
def client(oauth: _StubOAuth) -> Iterator[TestClient]:
    """A client whose app.state mirrors what the real lifespan installs."""
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    @app.exception_handler(PrescreeningError)
    async def handle_prescreening_error(request: Request, exc: PrescreeningError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})

    database = AsyncMongoMockClient(tz_aware=True)["doctor_registration_test"]

    class _Mongo:
        db = database

    app.state.mongo = _Mongo()
    settings = Settings(
        _env_file=None,  # isolate from the developer's real .env -- see tests/conftest.py
        intake_link_secret="registration-test-secret-32-charact",  # noqa: S106 - test fixture
        booking_webhook_secret="registration-webhook-secret-32-cha",  # noqa: S106 - test fixture
        physician_api_key="registration-physician-key-32-chars",  # noqa: S106 - test fixture
        booking_app_base_url="https://booking.test",
    )
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_google_oauth_client] = lambda: oauth

    with TestClient(app) as test_client:
        yield test_client


def test_starting_registration_returns_a_consent_url(client: TestClient) -> None:
    """The dialog gets a URL to navigate to, and nothing else."""
    response = client.post("/api/v1/doctors/registration", json=_REGISTRATION)

    assert response.status_code == 201
    assert response.json()["authorization_url"].startswith("https://accounts.google.test/consent")


async def test_completing_the_callback_persists_the_doctor_and_their_grant(
    client: TestClient, oauth: _StubOAuth
) -> None:
    """A finished consent flow leaves a bookable doctor with a calendar grant."""
    client.post("/api/v1/doctors/registration", json=_REGISTRATION)
    state = oauth.states[-1]

    response = client.get(
        "/api/v1/doctors/google/callback",
        params={"state": state, "code": "auth-code"},
        follow_redirects=False,
    )

    assert response.status_code == 307
    location = response.headers["location"]
    assert location.startswith("https://booking.test/book?")
    assert "doctor_registration=connected" in location
    # The credential must never travel through the doctor's browser history.
    assert _REFRESH_TOKEN not in location

    doctors = DoctorRepository(client.app.state.mongo.db)
    doctor = await doctors.get_by_id("dr-test")
    assert doctor is not None
    assert doctor.google_grant is not None
    assert doctor.google_grant.refresh_token == _REFRESH_TOKEN
    # Connecting Google replaces any placeholder calendar id.
    assert doctor.google_calendar_id == "dr.test@example.com"
    assert doctor.categories == ["heart", "lung"]
    # Google's own account photo becomes the booking-card portrait, so a
    # registering doctor has nothing to upload.
    assert doctor.photo_url == "https://lh3.googleusercontent.test/dr-test"


async def test_a_callback_state_cannot_be_replayed(client: TestClient, oauth: _StubOAuth) -> None:
    """Re-opening the callback URL must not re-register anyone."""
    client.post("/api/v1/doctors/registration", json=_REGISTRATION)
    state = oauth.states[-1]
    params = {"state": state, "code": "auth-code"}

    first = client.get("/api/v1/doctors/google/callback", params=params, follow_redirects=False)
    second = client.get("/api/v1/doctors/google/callback", params=params, follow_redirects=False)

    assert "doctor_registration=connected" in first.headers["location"]
    assert "doctor_registration=failed" in second.headers["location"]


def test_a_declined_consent_is_reported_as_declined_not_failed(client: TestClient) -> None:
    """A doctor who cancels at Google is not an error, and the UI is told so."""
    response = client.get(
        "/api/v1/doctors/google/callback",
        params={"error": "access_denied", "state": "whatever"},
        follow_redirects=False,
    )

    assert "doctor_registration=declined" in response.headers["location"]


def test_registration_without_an_oauth_client_is_unavailable(client: TestClient) -> None:
    """An unconfigured deployment says so instead of starting a dead-end flow.

    Uses the real `GoogleOAuthClient` over blank credentials rather than the
    stub: the refusal lives in the real client, and a stub asserting it
    would only be testing the stub.
    """
    unconfigured = Settings(
        _env_file=None,  # isolate from the developer's real .env -- see tests/conftest.py
        intake_link_secret="registration-test-secret-32-charact",  # noqa: S106 - test fixture
        booking_webhook_secret="registration-webhook-secret-32-cha",  # noqa: S106 - test fixture
        physician_api_key="registration-physician-key-32-chars",  # noqa: S106 - test fixture
    )
    client.app.dependency_overrides[get_google_oauth_client] = lambda: GoogleOAuthClient(
        unconfigured,
        http_client=None,  # type: ignore[arg-type]
    )

    response = client.post("/api/v1/doctors/registration", json=_REGISTRATION)

    assert response.status_code == 503

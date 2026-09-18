"""Integration tests for the patient session routes.

These cover the credential exchange the whole patient app hangs off:
`attach` is the only route that accepts the intake link's token, and it
must hand back a cookie that every other route accepts and that a
different session's cookie cannot substitute for.
"""

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from mongomock_motor import AsyncMongoMockClient

from app.api.v1.sessions import router
from app.core.config import Settings, get_settings
from app.core.constants import SessionState, Sex
from app.core.exceptions import PrescreeningError
from app.core.security import generate_intake_token, verify_ws_ticket
from app.integrations.google_calendar import GoogleCalendarClient
from app.models.session import PatientRef, Session
from app.repositories.session_repository import SessionRepository

_SECRET = "sessions-test-intake-secret-32-characters"  # noqa: S105 - test constant
_WEBHOOK_SECRET = "sessions-test-webhook-secret-32-characters"  # noqa: S105 - test constant
_PHYSICIAN_API_KEY = "sessions-test-physician-key-32-characters"  # noqa: S105 - test constant
_COOKIE = "prelude_health_session"


@pytest.fixture
def client() -> Iterator[TestClient]:
    """A client whose app.state mirrors what the real lifespan installs."""
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    @app.exception_handler(PrescreeningError)
    async def handle_prescreening_error(request: Request, exc: PrescreeningError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})

    database = AsyncMongoMockClient(tz_aware=True)["sessions_test"]

    class _Mongo:
        db = database

    app.state.mongo = _Mongo()
    settings = Settings(
        _env_file=None,  # isolate from the developer's real .env -- see tests/conftest.py
        intake_link_secret=_SECRET,
        booking_webhook_secret=_WEBHOOK_SECRET,
        physician_api_key=_PHYSICIAN_API_KEY,
    )
    app.state.calendar_client = GoogleCalendarClient(settings)
    app.dependency_overrides[get_settings] = lambda: settings

    app.state.repository = SessionRepository(database)
    with TestClient(app) as test_client:
        yield test_client


async def _seed(client: TestClient, session_id: str, status: SessionState) -> None:
    now = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    await client.app.state.repository.create(
        Session(
            session_id=session_id,
            appointment_id=f"appt_{session_id}",
            patient=PatientRef(
                name="Asha Rao",
                patient_id="pt_2290",
                date_of_birth=datetime(1990, 5, 14, tzinfo=UTC),
                sex=Sex.FEMALE,
            ),
            physician="Dr. Mehta",
            appointment_datetime=datetime(2026, 9, 3, 9, 30, tzinfo=UTC),
            booking_reason="Short of breath on stairs",
            status=status,
            created_at=now,
            updated_at=now,
        )
    )


def _attach(client: TestClient, session_id: str) -> None:
    token = generate_intake_token(session_id, _SECRET, ttl_seconds=3600)
    response = client.post(f"/api/v1/sessions/{session_id}/attach?token={token}")
    assert response.status_code == 200


async def test_attach_exchanges_the_link_token_for_a_cookie(client: TestClient) -> None:
    """The link's token buys a cookie, once, and the cookie does the rest."""
    await _seed(client, "sess_1", SessionState.NOTIFICATION_SENT)
    token = generate_intake_token("sess_1", _SECRET, ttl_seconds=3600)

    response = client.post(f"/api/v1/sessions/sess_1/attach?token={token}")

    assert response.status_code == 200
    assert _COOKIE in response.cookies


async def test_the_session_cookie_is_http_only(client: TestClient) -> None:
    """Page JavaScript must not be able to read the credential.

    This attribute is the entire reason the exchange exists -- without it
    an XSS on the patient app could lift the session the same way it could
    lift a token from `localStorage`.
    """
    await _seed(client, "sess_1", SessionState.NOTIFICATION_SENT)
    token = generate_intake_token("sess_1", _SECRET, ttl_seconds=3600)

    response = client.post(f"/api/v1/sessions/sess_1/attach?token={token}")

    assert "httponly" in response.headers["set-cookie"].lower()


async def test_attach_rejects_a_forged_token(client: TestClient) -> None:
    """A token not signed with the server secret buys nothing."""
    await _seed(client, "sess_1", SessionState.NOTIFICATION_SENT)

    response = client.post("/api/v1/sessions/sess_1/attach?token=intake.999999999.deadbeef")

    assert response.status_code == 401


async def test_attach_marks_the_session_started(client: TestClient) -> None:
    """Opening the link is what STARTED means; nothing wrote it before now."""
    await _seed(client, "sess_1", SessionState.NOTIFICATION_SENT)
    token = generate_intake_token("sess_1", _SECRET, ttl_seconds=3600)

    response = client.post(f"/api/v1/sessions/sess_1/attach?token={token}")

    assert response.json()["status"] == SessionState.STARTED


async def test_re_attaching_does_not_drag_a_live_session_backwards(client: TestClient) -> None:
    """A refresh mid-call must not reset an IN_PROGRESS session to STARTED.

    Re-attach is a normal event -- a reload, a second tab -- so it has to
    be idempotent with respect to state, not just safe to call.
    """
    await _seed(client, "sess_live", SessionState.IN_PROGRESS)
    token = generate_intake_token("sess_live", _SECRET, ttl_seconds=3600)

    response = client.post(f"/api/v1/sessions/sess_live/attach?token={token}")

    assert response.json()["status"] == SessionState.IN_PROGRESS


async def test_attach_returns_the_patient_and_appointment_context(client: TestClient) -> None:
    """The patient app cannot ask someone to confirm details it never receives."""
    await _seed(client, "sess_1", SessionState.NOTIFICATION_SENT)
    token = generate_intake_token("sess_1", _SECRET, ttl_seconds=3600)

    body = client.post(f"/api/v1/sessions/sess_1/attach?token={token}").json()

    assert body["patient"]["name"] == "Asha Rao"
    assert body["appointment"]["physician"] == "Dr. Mehta"
    assert body["appointment"]["booking_reason"] == "Short of breath on stairs"


async def test_status_route_rejects_a_request_with_no_cookie(client: TestClient) -> None:
    """Every route except attach requires the exchanged cookie."""
    await _seed(client, "sess_1", SessionState.NOTIFICATION_SENT)

    assert client.get("/api/v1/sessions/sess_1").status_code == 401


async def test_a_cookie_does_not_unlock_a_different_session(client: TestClient) -> None:
    """The cookie is scoped to the API origin, so the signature must bind the session.

    Otherwise one patient's cookie would read another patient's session
    simply because the browser sent it.
    """
    await _seed(client, "sess_mine", SessionState.NOTIFICATION_SENT)
    await _seed(client, "sess_theirs", SessionState.NOTIFICATION_SENT)
    _attach(client, "sess_mine")

    assert client.get("/api/v1/sessions/sess_theirs").status_code == 401


async def test_status_route_carries_no_patient_detail(client: TestClient) -> None:
    """The pollable route must stay free of PHI; context is a separate route."""
    await _seed(client, "sess_1", SessionState.NOTIFICATION_SENT)
    _attach(client, "sess_1")

    body = client.get("/api/v1/sessions/sess_1").json()

    assert "patient" not in body
    assert "appointment" not in body


async def test_consent_moves_the_session_in_progress(client: TestClient) -> None:
    """An affirmative consent is what clears the way for the live call."""
    await _seed(client, "sess_1", SessionState.NOTIFICATION_SENT)
    _attach(client, "sess_1")

    body = client.post("/api/v1/sessions/sess_1/consent", json={"given": True}).json()

    assert body["consent_given"] is True
    assert body["status"] == SessionState.IN_PROGRESS


async def test_declined_consent_ends_the_journey(client: TestClient) -> None:
    """A refusal is recorded as its own terminal state, not as an error."""
    await _seed(client, "sess_1", SessionState.NOTIFICATION_SENT)
    _attach(client, "sess_1")

    body = client.post("/api/v1/sessions/sess_1/consent", json={"given": False}).json()

    assert body["consent_given"] is False
    assert body["status"] == SessionState.DECLINED


async def test_consent_cannot_be_replayed_after_the_decision(client: TestClient) -> None:
    """A second consent post must not overwrite a decision already made."""
    await _seed(client, "sess_1", SessionState.NOTIFICATION_SENT)
    _attach(client, "sess_1")
    client.post("/api/v1/sessions/sess_1/consent", json={"given": False})

    replay = client.post("/api/v1/sessions/sess_1/consent", json={"given": True})

    assert replay.status_code == 409


async def test_ws_ticket_is_minted_for_the_authenticated_session(client: TestClient) -> None:
    """The ticket must verify against this session and carry a spendable nonce."""
    await _seed(client, "sess_1", SessionState.NOTIFICATION_SENT)
    _attach(client, "sess_1")

    body = client.post("/api/v1/sessions/sess_1/ws-ticket").json()

    assert verify_ws_ticket("sess_1", body["ticket"], _SECRET)


async def test_ws_ticket_requires_the_session_cookie(client: TestClient) -> None:
    """A ticket is only as good as the session that mints it."""
    await _seed(client, "sess_1", SessionState.NOTIFICATION_SENT)

    assert client.post("/api/v1/sessions/sess_1/ws-ticket").status_code == 401

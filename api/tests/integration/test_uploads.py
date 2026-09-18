"""Integration tests for the presigned-URL document + recording upload routes.

Covers both the session-cookie auth gate (shared with every other
patient route) and the happy path down to the in-memory Mongo double +
fake S3 (via moto).
"""

from collections.abc import Iterator
from datetime import UTC, datetime

import boto3
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from mongomock_motor import AsyncMongoMockClient
from moto import mock_aws

from app.agents.summarizer.agent import DESCRIPTION_UNAVAILABLE
from app.api.v1.uploads import router
from app.core.config import Settings, get_settings
from app.core.constants import Sex
from app.core.exceptions import PrescreeningError
from app.core.security import generate_session_token
from app.integrations.google_calendar import GoogleCalendarClient
from app.models.session import PatientRef, Session
from app.repositories.session_repository import SessionRepository

_INTAKE_SECRET = "uploads-test-intake-secret-32-characters"  # noqa: S105 - test constant
_WEBHOOK_SECRET = "uploads-test-webhook-secret-32-characters"  # noqa: S105 - test constant
_PHYSICIAN_API_KEY = "uploads-test-physician-key-32-characters"  # noqa: S105 - test constant
_BUCKET = "test-uploads-bucket"


@pytest.fixture
def client() -> Iterator[TestClient]:
    """A client whose app.state mirrors what the real lifespan installs."""
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    @app.exception_handler(PrescreeningError)
    async def handle_prescreening_error(request: Request, exc: PrescreeningError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})

    database = AsyncMongoMockClient(tz_aware=True)["uploads_test"]

    class _Mongo:
        db = database

    app.state.mongo = _Mongo()
    settings = Settings(
        _env_file=None,  # isolate from the developer's real .env -- see tests/conftest.py
        intake_link_secret=_INTAKE_SECRET,
        booking_webhook_secret=_WEBHOOK_SECRET,
        physician_api_key=_PHYSICIAN_API_KEY,
        storage_bucket_name=_BUCKET,
        # moto never validates these, but boto3 still requires credentials
        # to exist. Supplied explicitly so this test does not quietly
        # depend on the developer's machine having AWS credentials at all.
        aws_access_key_id="testing",
        aws_secret_access_key="testing",  # noqa: S106 - moto placeholder
    )
    app.state.calendar_client = GoogleCalendarClient(settings)
    app.dependency_overrides[get_settings] = lambda: settings

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def moto_bucket() -> Iterator[None]:
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=_BUCKET)
        yield


async def _seed_session(client: TestClient, session_id: str) -> None:
    now = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    session = Session(
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
        created_at=now,
        updated_at=now,
    )
    await SessionRepository(client.app.state.mongo.db).create(session)


def _authenticate(client: TestClient, session_id: str) -> None:
    """Give the client the HttpOnly cookie the patient app would hold.

    Set directly rather than round-tripped through `attach`: these tests
    cover the upload routes, and going through the session router would
    couple them to a route they are not testing.
    """
    client.cookies.set(
        "prelude_health_session",
        generate_session_token(session_id, _INTAKE_SECRET, ttl_seconds=3600),
    )


async def test_upload_url_route_rejects_a_missing_cookie(
    client: TestClient, moto_bucket: None
) -> None:
    """Without a valid session cookie, no presigned URL should be handed out."""
    await _seed_session(client, "sess_1")

    response = client.post(
        "/api/v1/sessions/sess_1/documents/upload-url",
        json={"content_type": "application/pdf"},
    )

    assert response.status_code == 401


async def test_upload_url_route_rejects_another_sessions_cookie(
    client: TestClient, moto_bucket: None
) -> None:
    """A cookie is bound to one session by its signature, not just by name.

    The browser sends whatever cookie it holds for this origin, so the
    server must reject one minted for a different session rather than
    trusting its mere presence.
    """
    await _seed_session(client, "sess_1")
    await _seed_session(client, "sess_other")
    _authenticate(client, "sess_other")

    response = client.post(
        "/api/v1/sessions/sess_1/documents/upload-url",
        json={"content_type": "application/pdf"},
    )

    assert response.status_code == 401


async def test_document_upload_flow_marks_the_document_uploaded(
    client: TestClient, moto_bucket: None
) -> None:
    """The full presigned-URL round trip: request a URL, then confirm completion."""
    await _seed_session(client, "sess_2")
    _authenticate(client, "sess_2")

    url_response = client.post(
        "/api/v1/sessions/sess_2/documents/upload-url",
        json={"content_type": "application/pdf"},
    )
    assert url_response.status_code == 200
    key = url_response.json()["key"]

    complete_response = client.post(
        "/api/v1/sessions/sess_2/documents/complete",
        json={"storage_key": key},
    )
    assert complete_response.status_code == 200
    assert complete_response.json()["document_uploaded"] is True

    stored = await SessionRepository(client.app.state.mongo.db).get_by_id("sess_2")
    assert stored is not None
    assert stored.document_ref == key


async def test_document_complete_describes_an_unsupported_format_as_unavailable(
    client: TestClient, moto_bucket: None
) -> None:
    """The full flow, with real bytes actually landing in S3 (unlike the
    test above, which only requests a presigned URL): an unrecognized
    content-type must resolve to the safe fallback string rather than
    reaching Bedrock or leaving `document_summary` unset. This is the one
    describe-document path testable without a live model call -- see
    `tests/unit/test_document_description.py`'s module docstring."""
    await _seed_session(client, "sess_4")
    _authenticate(client, "sess_4")

    url_response = client.post(
        "/api/v1/sessions/sess_4/documents/upload-url",
        json={"content_type": "application/octet-stream"},
    )
    key = url_response.json()["key"]
    boto3.client("s3", region_name="us-east-1").put_object(
        Bucket=_BUCKET, Key=key, Body=b"raw-bytes", ContentType="application/octet-stream"
    )

    complete_response = client.post(
        "/api/v1/sessions/sess_4/documents/complete",
        json={"storage_key": key},
    )
    assert complete_response.status_code == 200

    stored = await SessionRepository(client.app.state.mongo.db).get_by_id("sess_4")
    assert stored is not None
    assert stored.document_summary == DESCRIPTION_UNAVAILABLE


async def test_recording_complete_marks_video_ready(client: TestClient, moto_bucket: None) -> None:
    """Confirming a recording upload must move the session to VIDEO_READY."""
    await _seed_session(client, "sess_3")
    _authenticate(client, "sess_3")

    response = client.post(
        "/api/v1/sessions/sess_3/recording/complete",
        json={"storage_key": "recordings/sess_3/call.webm"},
    )

    assert response.status_code == 200
    assert response.json()["has_video"] is True

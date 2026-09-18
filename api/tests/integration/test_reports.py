"""Integration tests for the report retrieval routes.

Covers both the physician-key-gated `get_report` and the new,
deliberately-unauthenticated `get_report_download_url` that backs the
booking page's session-id report lookup -- see that route's own docstring
for why it carries no `X-API-Key` gate.
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

from app.api.v1.reports import router
from app.core.config import Settings, get_settings
from app.core.constants import PrescreeningCategory, Sex
from app.core.exceptions import PrescreeningError
from app.models.report import PreScreeningReport
from app.models.session import PatientRef, Session
from app.repositories.session_repository import SessionRepository

_INTAKE_SECRET = "reports-test-intake-secret-32-characters"  # noqa: S105 - test constant
_WEBHOOK_SECRET = "reports-test-webhook-secret-32-characters"  # noqa: S105 - test constant
_PHYSICIAN_API_KEY = "reports-test-physician-key-32-characters"  # noqa: S105 - test constant
_BUCKET = "test-reports-bucket"


@pytest.fixture
def client() -> Iterator[TestClient]:
    """A client whose app.state mirrors what the real lifespan installs."""
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    @app.exception_handler(PrescreeningError)
    async def handle_prescreening_error(request: Request, exc: PrescreeningError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})

    database = AsyncMongoMockClient(tz_aware=True)["reports_test"]

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
    app.dependency_overrides[get_settings] = lambda: settings

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def moto_bucket() -> Iterator[None]:
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=_BUCKET)
        yield


def _report() -> PreScreeningReport:
    return PreScreeningReport(
        chief_concern="Shortness of breath on exertion",
        category=PrescreeningCategory.LUNG,
        clinical_summary=(
            "Reports gradually worsening exertional dyspnea over three weeks, "
            "no chest pain, no fever."
        ),
    )


async def _seed_session(
    client: TestClient, session_id: str, *, report: PreScreeningReport | None = None
) -> None:
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
        report=report,
        created_at=now,
        updated_at=now,
    )
    await SessionRepository(client.app.state.mongo.db).create(session)


class TestGetReport:
    """The physician-facing route -- unchanged behaviour, still gated."""

    def test_requires_the_physician_api_key(self, client: TestClient) -> None:
        response = client.get("/api/v1/sessions/sess_1/report")

        assert response.status_code in (401, 403)

    async def test_404s_for_an_unknown_session(self, client: TestClient) -> None:
        response = client.get(
            "/api/v1/sessions/sess_missing/report",
            headers={"X-API-Key": _PHYSICIAN_API_KEY},
        )

        assert response.status_code == 404


class TestGetReportDownloadUrl:
    """The new, deliberately-unauthenticated session-id report lookup."""

    async def test_carries_no_auth_gate(self, client: TestClient) -> None:
        """The whole point of this route: no `X-API-Key`, no cookie, nothing
        but the session id -- see the route's own docstring for why."""
        await _seed_session(client, "sess_1")

        response = client.get("/api/v1/sessions/sess_1/report-download-url")

        assert response.status_code == 200

    async def test_404s_for_an_unknown_session(self, client: TestClient) -> None:
        response = client.get("/api/v1/sessions/sess_missing/report-download-url")

        assert response.status_code == 404

    async def test_returns_a_null_url_before_the_report_is_ready(self, client: TestClient) -> None:
        await _seed_session(client, "sess_1")

        response = client.get("/api/v1/sessions/sess_1/report-download-url")

        assert response.status_code == 200
        assert response.json() == {"download_url": None}

    async def test_returns_a_presigned_url_targeting_the_report_once_attached(
        self, client: TestClient, moto_bucket: None
    ) -> None:
        """The URL addresses the right bucket/key -- same assertion style as
        `test_storage.py`'s presigned-URL tests, which don't fetch through
        the URL either: moto intercepts botocore, not an arbitrary HTTP
        client, so a real fetch here would prove nothing extra."""
        await _seed_session(client, "sess_1", report=_report())
        boto3.client("s3", region_name="us-east-1").put_object(
            Bucket=_BUCKET, Key="reports/sess_1/report.pdf", Body=b"%PDF-fake"
        )

        response = client.get("/api/v1/sessions/sess_1/report-download-url")

        assert response.status_code == 200
        download_url = response.json()["download_url"]
        assert download_url is not None
        assert _BUCKET in download_url
        assert "reports/sess_1/report.pdf" in download_url

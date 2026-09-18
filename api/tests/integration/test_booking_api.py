"""Integration tests for the booking screen's read API.

Covers what the booking UI depends on being true: the visit types are this
service's own symptom vocabulary, providers can be filtered by visit type,
availability is returned per day, and no response ever leaks a doctor's
stored Google credentials.
"""

from collections.abc import Iterator

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from mongomock_motor import AsyncMongoMockClient

from app.api.v1.booking import router
from app.core.config import Settings, get_settings
from app.core.exceptions import PrescreeningError
from app.models.doctor import Doctor, DoctorGoogleGrant
from app.repositories.doctor_repository import DoctorRepository


@pytest.fixture
def client() -> Iterator[TestClient]:
    """A client whose app.state mirrors what the real lifespan installs."""
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    @app.exception_handler(PrescreeningError)
    async def handle_prescreening_error(request: Request, exc: PrescreeningError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})

    database = AsyncMongoMockClient(tz_aware=True)["booking_api_test"]

    class _Mongo:
        db = database

    app.state.mongo = _Mongo()
    settings = Settings(
        _env_file=None,  # isolate from the developer's real .env -- see tests/conftest.py
        intake_link_secret="booking-api-test-secret-32-characters",  # noqa: S106 - test fixture
        booking_webhook_secret="booking-api-webhook-secret-32-chars",  # noqa: S106 - test fixture
        physician_api_key="booking-api-physician-key-32-charact",  # noqa: S106 - test fixture
        clinic_timezone="UTC",
    )
    app.dependency_overrides[get_settings] = lambda: settings

    with TestClient(app) as test_client:
        yield test_client


def test_visit_types_cover_every_clinical_category(client: TestClient) -> None:
    """Each of the five categories the agent screens for stays bookable."""
    response = client.get("/api/v1/booking/visit-types")

    assert response.status_code == 200
    mapped = {entry["symptom_category"] for entry in response.json()}
    assert mapped >= {"diabetes", "blood_pressure", "heart", "lung", "stomach"}
    assert all(entry["label"] and entry["description"] for entry in response.json())


def test_a_patient_with_no_condition_in_mind_has_somewhere_to_go(client: TestClient) -> None:
    """A routine checkup, and not knowing what is wrong, are both bookable.

    Without these the five condition cards force a patient who fits none of
    them to pick a wrong one, which is worse than not pre-scoping at all.
    """
    unscoped = {
        entry["visit_type"]: entry
        for entry in client.get("/api/v1/booking/visit-types").json()
        if entry["symptom_category"] is None
    }

    assert set(unscoped) == {"general_checkup", "not_sure"}
    # Unscoped means unscoped: no category is invented for them.
    assert all(entry["symptom_category"] is None for entry in unscoped.values())


def test_unscoped_visit_types_lead_the_list(client: TestClient) -> None:
    """They come first, before a wall of conditions to rule oneself out of."""
    visit_types = [
        entry["visit_type"] for entry in client.get("/api/v1/booking/visit-types").json()
    ]

    assert visit_types[:2] == ["general_checkup", "not_sure"]


async def test_providers_are_filtered_by_visit_type(client: TestClient) -> None:
    """A doctor who does not accept the chosen visit type must not be offered."""
    doctors = DoctorRepository(client.app.state.mongo.db)
    await doctors.upsert(
        Doctor(name="Dr. Lung", google_calendar_id="lung@example.com", categories=["lung"])
    )
    await doctors.upsert(
        Doctor(name="Dr. Heart", google_calendar_id="heart@example.com", categories=["heart"])
    )

    response = client.get("/api/v1/booking/providers", params={"visit_type": "lung"})

    assert response.status_code == 200
    assert [entry["name"] for entry in response.json()] == ["Dr. Lung"]


async def test_an_unscoped_visit_type_offers_every_provider(client: TestClient) -> None:
    """There is no condition to match specialties against, so nobody is hidden."""
    doctors = DoctorRepository(client.app.state.mongo.db)
    await doctors.upsert(
        Doctor(name="Dr. Lung", google_calendar_id="lung@example.com", categories=["lung"])
    )
    await doctors.upsert(
        Doctor(name="Dr. Heart", google_calendar_id="heart@example.com", categories=["heart"])
    )

    response = client.get("/api/v1/booking/providers", params={"visit_type": "general_checkup"})

    assert sorted(entry["name"] for entry in response.json()) == ["Dr. Heart", "Dr. Lung"]


async def test_a_doctor_with_no_categories_is_offered_for_every_visit_type(
    client: TestClient,
) -> None:
    """Doctors seeded before visit types existed must stay bookable."""
    doctors = DoctorRepository(client.app.state.mongo.db)
    await doctors.upsert(Doctor(name="Dr. General", google_calendar_id="general@example.com"))

    response = client.get("/api/v1/booking/providers", params={"visit_type": "stomach"})

    assert [entry["name"] for entry in response.json()] == ["Dr. General"]


async def test_a_doctor_document_missing_categories_entirely_is_still_offered(
    client: TestClient,
) -> None:
    """A legacy document has no `categories` field at all, not an empty one.

    Mongo's `$size: 0` does not match a missing field, so this is a
    different case from the test above -- and getting it wrong made every
    pre-existing doctor disappear from filtered booking searches.
    """
    await client.app.state.mongo.db["doctors"].insert_one(
        {"name": "Dr. Legacy", "google_calendar_id": "legacy@example.com", "doctor_id": "dr-legacy"}
    )

    response = client.get("/api/v1/booking/providers", params={"visit_type": "stomach"})

    assert [entry["name"] for entry in response.json()] == ["Dr. Legacy"]


async def test_provider_response_never_exposes_a_stored_google_credential(
    client: TestClient,
) -> None:
    """A doctor's refresh token is a calendar credential and must never be served.

    Asserted on the raw response text, not the parsed model, so a future
    field that happens to embed it is caught too.
    """
    doctors = DoctorRepository(client.app.state.mongo.db)
    await doctors.register(
        Doctor(
            name="Dr. Connected",
            google_calendar_id="connected@example.com",
            google_grant=DoctorGoogleGrant(
                email="connected@example.com",
                refresh_token="super-secret-refresh-token",  # noqa: S106 - test fixture
                scopes=["https://www.googleapis.com/auth/calendar"],
                granted_at="2026-09-01T00:00:00Z",
            ),
        )
    )

    response = client.get("/api/v1/booking/providers")

    assert "super-secret-refresh-token" not in response.text
    assert response.json()[0]["calendar_connected"] is True


async def test_a_providers_portrait_is_served_when_one_is_on_file(client: TestClient) -> None:
    """The booking card's photo is live data, not a URL hardcoded in the frontend."""
    doctors = DoctorRepository(client.app.state.mongo.db)
    await doctors.upsert(
        Doctor(
            name="Dr. Portrait",
            google_calendar_id="portrait@example.com",
            photo_url="https://images.example.test/portrait.jpg",
        )
    )
    await doctors.upsert(Doctor(name="Dr. Anonymous", google_calendar_id="anon@example.com"))

    response = client.get("/api/v1/booking/providers")

    by_name = {entry["name"]: entry for entry in response.json()}
    assert by_name["Dr. Portrait"]["photo_url"] == "https://images.example.test/portrait.jpg"
    # Absent rather than a placeholder URL: the card falls back to initials.
    assert by_name["Dr. Anonymous"]["photo_url"] is None


async def test_availability_returns_every_day_in_the_window(client: TestClient) -> None:
    """Closed days are returned empty so the date strip does not misalign."""
    doctors = DoctorRepository(client.app.state.mongo.db)
    await doctors.upsert(Doctor(name="Dr. Test", google_calendar_id="test@example.com"))

    response = client.get(
        "/api/v1/booking/providers/dr-test/availability", params={"from": "2026-09-14", "days": 7}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["timezone"] == "UTC"
    assert [day["day"] for day in body["days"]] == [
        "2026-09-14",
        "2026-09-15",
        "2026-09-16",
        "2026-09-17",
        "2026-09-18",
        "2026-09-19",
        "2026-09-20",
    ]
    # Saturday and Sunday close the week out with nothing bookable.
    assert body["days"][5]["slots"] == []
    assert body["days"][6]["slots"] == []
    assert body["days"][0]["slots"][0]["label"] == "9:00 AM"


def test_availability_for_an_unknown_provider_is_a_404(client: TestClient) -> None:
    """An unknown doctor id must not render as an empty calendar."""
    response = client.get("/api/v1/booking/providers/dr-nobody/availability")

    assert response.status_code == 404

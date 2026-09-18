"""Integration tests for the `doctors` collection's schema migration.

`doctor_id` was added after doctors had already been seeded, and the unique
index on it cannot build while several documents share a missing (null) one
-- which took the whole app down at startup, not just this collection. The
backfill is what fixes that, so it is pinned here.
"""

import pytest
from mongomock_motor import AsyncMongoMockClient

from app.models.doctor import Doctor
from app.repositories.doctor_repository import DoctorRepository


@pytest.fixture
def doctors() -> DoctorRepository:
    return DoctorRepository(AsyncMongoMockClient(tz_aware=True)["doctor_repository_test"])


async def test_doctors_seeded_before_doctor_id_existed_are_backfilled(
    doctors: DoctorRepository,
) -> None:
    """Two legacy documents with no doctor_id must both come out addressable."""
    await doctors.collection.insert_many(
        [
            {"name": "Dr. Legacy One", "google_calendar_id": "one@example.com"},
            {"name": "Dr. Legacy Two", "google_calendar_id": "two@example.com"},
        ]
    )

    await doctors.ensure_indexes()

    assert await doctors.get_by_id("dr-legacy-one") is not None
    assert await doctors.get_by_id("dr-legacy-two") is not None


async def test_two_names_that_slugify_alike_still_get_distinct_ids(
    doctors: DoctorRepository,
) -> None:
    """A slug collision must not cost the collection its unique index."""
    await doctors.collection.insert_many(
        [
            {"name": "Dr. Test", "google_calendar_id": "a@example.com"},
            {"name": "Dr Test", "google_calendar_id": "b@example.com"},
        ]
    )

    await doctors.ensure_indexes()

    ids = {
        doc["doctor_id"] async for doc in doctors.collection.find({}, {"_id": 0, "doctor_id": 1})
    }
    assert len(ids) == 2


async def test_backfill_leaves_an_existing_doctor_id_alone(doctors: DoctorRepository) -> None:
    """An id already handed to the booking UI must never change underneath it."""
    await doctors.upsert(Doctor(name="Dr. Kept", google_calendar_id="kept@example.com"))

    await doctors.ensure_indexes()
    await doctors.ensure_indexes()

    doctor = await doctors.get_by_name("Dr. Kept")
    assert doctor is not None
    assert doctor.doctor_id == "dr-kept"

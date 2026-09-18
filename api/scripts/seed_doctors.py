"""Seed the `doctors` collection with the doctor -> Google Calendar mapping.

Run once against a fresh environment: `make seed-doctors`. Idempotent --
upserts by name, so it is safe to re-run after editing the list below.

Each `google_calendar_id` is the target calendar's id (for a doctor's
primary calendar, this is their Google account email address; for a
secondary calendar, find it under that calendar's Settings -> "Integrate
calendar" -> Calendar ID). The doctor must share that calendar with the
service account's email (Editor access) before events can be created on it
-- see `GOOGLE_SERVICE_ACCOUNT_FILE` in `.env.example`.
"""

import asyncio

import structlog
import typer
from motor.motor_asyncio import AsyncIOMotorClient

from app.core.config import get_settings
from app.core.constants import CareModality, SymptomCategory
from app.models.doctor import Doctor
from app.repositories.doctor_repository import DoctorRepository

log = structlog.get_logger(__name__)

cli = typer.Typer()


def _unsplash_portrait(photo_id: str) -> str:
    """Build a square, face-cropped portrait URL for one Unsplash photo.

    `fit=facearea` is what makes a landscape source usable as a round avatar
    -- a plain square crop decapitates most of these shots. Sized for the
    booking card's 2x rendering and no larger.

    These are stock portraits standing in for real provider photography.
    They are placeholders for a demo, not the likeness of the named doctor,
    and must be replaced with each clinician's own photo (or left blank, which
    falls back to initials) before this is shown to patients.
    """
    return (
        f"https://images.unsplash.com/{photo_id}"
        "?auto=format&fit=facearea&facepad=2.8&w=256&h=256&q=80"
    )


_DOCTORS: list[Doctor] = [
    # Replace with real doctors and their shared Google Calendar ids
    # before running a real demo -- these are placeholders.
    #
    # `categories` decides which booking visit types a doctor is offered
    # for; an empty list means unrestricted. A doctor who registers
    # themselves through the booking UI's Google flow sets all of this
    # themselves and does not need seeding here at all.
    Doctor(
        name="Dr. Mehta",
        google_calendar_id="REPLACE_WITH_DR_MEHTA_CALENDAR_ID",
        credential="MD",
        categories=[SymptomCategory.HEART, SymptomCategory.BLOOD_PRESSURE],
        modality=CareModality.IN_PERSON_AND_VIRTUAL,
        photo_url=_unsplash_portrait("photo-1622253692010-333f2da6031d"),
    ),
    Doctor(
        name="Dr. Test",
        google_calendar_id="REPLACE_WITH_DR_TEST_CALENDAR_ID",
        credential="MD",
        categories=[SymptomCategory.LUNG, SymptomCategory.STOMACH, SymptomCategory.DIABETES],
        modality=CareModality.IN_PERSON,
        photo_url=_unsplash_portrait("photo-1594824476967-48c8b964273f"),
    ),
    Doctor(
        name="Dr. Iyer",
        google_calendar_id="REPLACE_WITH_DR_IYER_CALENDAR_ID",
        credential="MD",
        categories=[SymptomCategory.HEART, SymptomCategory.LUNG],
        modality=CareModality.VIRTUAL,
        photo_url=_unsplash_portrait("photo-1659353888906-adb3e0041693"),
    ),
    Doctor(
        name="Dr. Nair",
        google_calendar_id="gentech1014@gmail.com",
        credential="MBBS",
        categories=[SymptomCategory.DIABETES, SymptomCategory.BLOOD_PRESSURE],
        modality=CareModality.IN_PERSON_AND_VIRTUAL,
        photo_url=_unsplash_portrait("photo-1637059824899-a441006a6875"),
    ),
    Doctor(
        name="Dr. Sharma",
        google_calendar_id="REPLACE_WITH_DR_SHARMA_CALENDAR_ID",
        credential="MD",
        categories=[SymptomCategory.STOMACH, SymptomCategory.DIABETES],
        modality=CareModality.IN_PERSON,
        photo_url=_unsplash_portrait("photo-1643297654416-05795d62e39c"),
    ),
    Doctor(
        name="Dr. Bose",
        google_calendar_id="REPLACE_WITH_DR_BOSE_CALENDAR_ID",
        credential="MD",
        categories=[SymptomCategory.HEART, SymptomCategory.STOMACH],
        modality=CareModality.IN_PERSON_AND_VIRTUAL,
        photo_url=_unsplash_portrait("photo-1645066928295-2506defde470"),
    ),
]


@cli.command()
def seed() -> None:
    """Upsert all known doctor -> calendar mappings into Mongo."""
    asyncio.run(_seed())


async def _seed() -> None:
    settings = get_settings()
    client = AsyncIOMotorClient(settings.mongo_uri)
    repository = DoctorRepository(client[settings.mongo_db_name])

    await repository.ensure_indexes()

    for doctor in _DOCTORS:
        await repository.upsert(doctor)
        log.info("seeded_doctor", doctor_id=doctor.doctor_id, name=doctor.name)

    log.info("seed_complete", doctors=len(_DOCTORS))
    client.close()


if __name__ == "__main__":
    cli()

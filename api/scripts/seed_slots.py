"""Seed the `appointment_slots` collection with a working-hours grid for
every seeded doctor.

Run once against a fresh environment: `make seed-slots` (after
`make seed-doctors`, since it reads the `doctors` collection). Idempotent
-- upserts by `slot_id`, so re-running after adjusting the day/hour range
below updates in place rather than duplicating.

Placeholder working hours, not a real scheduling policy: every doctor
gets the same 09:00-17:00 window, `slot_duration_minutes` apart, for
`_DAYS_AHEAD` days -- replace with real per-doctor hours before a real
demo needs them.

Generated in each doctor's own LOCAL time, then converted to UTC for
storage -- not the reverse. See `Session.appointment_timezone`'s
docstring for why: a UTC-first grid for a clinic on a non-hour offset
(e.g. Asia/Kathmandu, +05:45) would not land on that clinic's actual
appointment times.

Builds its own Mongo client via `MongoConnection`, not a bare
`AsyncIOMotorClient(...)` the way `seed_doctors.py` does: this script
compares timestamps, and a client without `tz_aware=True` reads them back
naive, raising `TypeError` the moment they meet `datetime.now(UTC)`.
"""

import asyncio
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import structlog
import typer

from app.core.config import get_settings
from app.db.mongo import MongoConnection
from app.models.slot import AppointmentSlot
from app.repositories.doctor_repository import DoctorRepository
from app.repositories.slot_repository import SlotRepository

log = structlog.get_logger(__name__)

cli = typer.Typer()

_WORKING_HOURS_START = time(9, 0)
_WORKING_HOURS_END = time(17, 0)
_DAYS_AHEAD = 14


@cli.command()
def seed() -> None:
    """Upsert a working-hours slot grid for every known doctor into Mongo."""
    asyncio.run(_seed())


async def _seed() -> None:
    settings = get_settings()
    mongo = MongoConnection(settings)
    await mongo.connect()

    doctor_repository = DoctorRepository(mongo.db)
    slot_repository = SlotRepository(mongo.db)
    await slot_repository.ensure_indexes()

    doctors = await doctor_repository.get_all()
    total = 0
    for doctor in doctors:
        tz = ZoneInfo(doctor.timezone or settings.clinic_timezone)
        today = datetime.now(tz).date()
        for day_offset in range(_DAYS_AHEAD):
            day = today + timedelta(days=day_offset)
            total += await _seed_day(
                slot_repository, doctor.name, day, tz, settings.slot_duration_minutes
            )
        log.info("seeded_doctor_slots", physician=doctor.name, timezone=str(tz))

    log.info("seed_complete", doctors=len(doctors), slots=total)
    await mongo.disconnect()


async def _seed_day(
    slot_repository: SlotRepository,
    physician: str,
    day: date,
    tz: ZoneInfo,
    duration_minutes: int,
) -> int:
    """One working day's worth of back-to-back slots. Returns how many
    were written, purely for the summary log line."""
    duration = timedelta(minutes=duration_minutes)
    local_start = datetime.combine(day, _WORKING_HOURS_START, tzinfo=tz)
    local_end = datetime.combine(day, _WORKING_HOURS_END, tzinfo=tz)

    count = 0
    starts_at = local_start
    while starts_at + duration <= local_end:
        starts_at_utc = starts_at.astimezone(UTC)
        slot = AppointmentSlot(
            slot_id=f"slot_{physician}_{starts_at_utc.isoformat()}".replace(" ", "_"),
            physician=physician,
            starts_at=starts_at_utc,
            ends_at=(starts_at + duration).astimezone(UTC),
            updated_at=datetime.now(UTC),
        )
        await slot_repository.create_if_missing(slot)
        count += 1
        starts_at += duration
    return count


if __name__ == "__main__":
    cli()

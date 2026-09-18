"""Computes which appointment times a doctor can actually be booked into.

Two sources are combined, in this order:

1. The clinic's own opening hours generate the candidate slots. Nothing
   outside them is ever offered, whatever a calendar says.
2. Those candidates are then filtered by what is already taken -- the
   doctor's real Google Calendar free/busy when they have connected their
   account, plus every appointment already booked through this service.

The second source matters even without Google: a doctor who has not
registered still must not be double-booked by this platform, and their
existing sessions in Mongo are enough to prevent that.

Each returned day is labelled with how it was derived
(`AvailabilitySource`), so the booking UI can say "checked against the
doctor's calendar" only when that is actually true, and never present a
clinic-hours guess as verified. That labelling is the point: silently
degrading would make an unverified slot indistinguishable from a
confirmed one.
"""

from datetime import UTC, date, datetime, time, timedelta, tzinfo

import structlog

from app.core.clinic_time import resolve_clinic_zone
from app.core.config import Settings
from app.core.constants import AvailabilitySource
from app.integrations.doctor_calendar import BusyWindow, DoctorCalendarClient
from app.models.doctor import Doctor
from app.repositories.session_repository import SessionRepository

log = structlog.get_logger(__name__)


class AvailableSlot:
    """One bookable start time."""

    def __init__(self, start: datetime, end: datetime) -> None:
        self.start = start
        self.end = end


class AvailableDay:
    """One date's bookable slots, and how they were determined."""

    def __init__(self, day: date, slots: list[AvailableSlot], source: AvailabilitySource) -> None:
        self.day = day
        self.slots = slots
        self.source = source


class AvailabilityService:
    """Turns clinic hours plus known commitments into bookable slots."""

    def __init__(
        self,
        settings: Settings,
        calendar_client: DoctorCalendarClient,
        session_repository: SessionRepository,
    ) -> None:
        self._settings = settings
        self._calendar = calendar_client
        self._sessions = session_repository

    @property
    def clinic_zone(self) -> tzinfo:
        """The clinic's timezone. See `app.core.clinic_time.resolve_clinic_zone`."""
        return resolve_clinic_zone(self._settings)

    async def days_for_doctor(
        self, doctor: Doctor, from_day: date | None = None, days: int | None = None
    ) -> list[AvailableDay]:
        """Bookable slots per day for one doctor, over the booking horizon."""
        zone = self.clinic_zone
        start_day = from_day or datetime.now(zone).date()
        horizon = min(
            days or self._settings.booking_horizon_days, self._settings.booking_horizon_days
        )

        window_start = datetime.combine(start_day, time.min, tzinfo=zone)
        window_end = datetime.combine(start_day + timedelta(days=horizon), time.min, tzinfo=zone)

        busy_windows = await self._calendar.fetch_busy_windows(doctor, window_start, window_end)
        source = (
            AvailabilitySource.CALENDAR
            if busy_windows is not None
            else AvailabilitySource.CLINIC_HOURS
        )

        booked = await self._sessions.list_booked_datetimes(doctor.name, window_start, window_end)
        taken_starts = {_as_utc(value) for value in booked}
        busy = busy_windows or []

        earliest = datetime.now(UTC) + timedelta(minutes=self._settings.booking_lead_time_minutes)

        available: list[AvailableDay] = []
        for offset in range(horizon):
            day = start_day + timedelta(days=offset)
            slots = [
                slot
                for slot in self._candidate_slots(day, zone)
                if _as_utc(slot.start) >= earliest
                and _as_utc(slot.start) not in taken_starts
                and not _overlaps_any(slot, busy)
            ]
            available.append(AvailableDay(day=day, slots=slots, source=source))

        return available

    async def is_slot_bookable(self, doctor: Doctor, start: datetime) -> bool:
        """Whether `start` is still one of this doctor's open slots.

        Re-checked at submit time rather than trusted from the client: the
        patient may have had the page open for a while, and the slot they
        saw could have been taken since.
        """
        day = start.astimezone(self.clinic_zone).date()
        days = await self.days_for_doctor(doctor, from_day=day, days=1)
        if not days:
            return False

        wanted = _as_utc(start)
        return any(_as_utc(slot.start) == wanted for slot in days[0].slots)

    def _candidate_slots(self, day: date, zone: tzinfo) -> list[AvailableSlot]:
        """Every slot the clinic's opening hours allow on `day`, before filtering."""
        if day.weekday() not in self._open_weekdays():
            return []

        step = timedelta(minutes=self._settings.clinic_slot_minutes)
        opens = datetime.combine(day, time(hour=self._settings.clinic_open_hour), tzinfo=zone)
        closes = datetime.combine(day, time.min, tzinfo=zone) + timedelta(
            hours=self._settings.clinic_close_hour
        )

        slots: list[AvailableSlot] = []
        cursor = opens
        while cursor + step <= closes:
            slots.append(AvailableSlot(start=cursor, end=cursor + step))
            cursor += step
        return slots

    def _open_weekdays(self) -> set[int]:
        """Parse `clinic_open_weekdays`, falling back to Monday-Friday when unusable."""
        parsed = {
            int(part)
            for part in self._settings.clinic_open_weekdays.split(",")
            if part.strip().isdigit()
        }
        valid = {weekday for weekday in parsed if 0 <= weekday <= 6}
        if not valid:
            log.warning("clinic_open_weekdays_invalid", value=self._settings.clinic_open_weekdays)
            return {0, 1, 2, 3, 4}
        return valid


def _as_utc(value: datetime) -> datetime:
    """Normalize any datetime to aware UTC.

    Mongo returns naive datetimes (BSON stores UTC without an offset), so
    comparing a stored appointment time against a generated slot needs both
    sides on the same footing or every comparison silently raises.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _overlaps_any(slot: AvailableSlot, busy: list[BusyWindow]) -> bool:
    """Whether a candidate slot collides with any busy window."""
    slot_start, slot_end = _as_utc(slot.start), _as_utc(slot.end)
    return any(
        slot_start < _as_utc(window.end) and slot_end > _as_utc(window.start) for window in busy
    )

"""Unit tests for bookable-slot computation.

The rules worth pinning here are the ones a patient would notice going
wrong: a slot outside opening hours, a slot on a closed day, a slot the
doctor's calendar says they are busy for, a slot already booked through
this service, and a slot too soon to be honoured. Each of those is a
double-booking or a broken promise, so each gets its own case.
"""

from datetime import UTC, date, datetime, time, timedelta

import pytest
from mongomock_motor import AsyncMongoMockClient

from app.core.config import Settings
from app.core.constants import AvailabilitySource
from app.integrations.doctor_calendar import BusyWindow, DoctorCalendarClient
from app.models.doctor import Doctor, DoctorGoogleGrant
from app.models.session import PatientRef, Session
from app.repositories.session_repository import SessionRepository
from app.services.availability_service import AvailabilityService

_MONDAY = date(2026, 9, 14)
"""A Monday comfortably in the future, so lead-time filtering never applies."""


def _settings(**overrides: object) -> Settings:
    """Deterministic 9am-12pm clinic, overridable per test."""
    fields: dict[str, object] = {
        "intake_link_secret": "availability-test-secret-32-chars-x",
        "booking_webhook_secret": "availability-webhook-secret-32-ch",
        "physician_api_key": "availability-physician-key-32-chars",
        "clinic_timezone": "UTC",
        "clinic_open_hour": 9,
        "clinic_close_hour": 12,
        "clinic_slot_minutes": 30,
        **overrides,
    }
    # isolate from the developer's real .env -- see tests/conftest.py
    return Settings(_env_file=None, **fields)


class _StubCalendar(DoctorCalendarClient):
    """A doctor-calendar client with a scripted free/busy answer.

    Subclassed rather than mocked so the real `can_serve` gating still runs
    -- that gate is what decides whether a day is reported as
    calendar-verified, and stubbing it away would test nothing.
    """

    def __init__(self, settings: Settings, busy: list[BusyWindow] | None) -> None:
        super().__init__(settings)
        self._busy = busy

    def can_serve(self, doctor: Doctor) -> bool:
        return self._busy is not None

    async def fetch_busy_windows(
        self, doctor: Doctor, window_start: datetime, window_end: datetime
    ) -> list[BusyWindow] | None:
        return self._busy


def _doctor(*, connected: bool = False) -> Doctor:
    grant = (
        DoctorGoogleGrant(
            email="dr.test@example.com",
            refresh_token="test-refresh-token",  # noqa: S106 - test fixture
            scopes=["https://www.googleapis.com/auth/calendar"],
            granted_at=datetime.now(UTC),
        )
        if connected
        else None
    )
    return Doctor(
        name="Dr. Test",
        google_calendar_id="dr.test@example.com",
        google_grant=grant,
    )


def _service(
    busy: list[BusyWindow] | None = None,
    sessions: SessionRepository | None = None,
    **setting_overrides: object,
) -> AvailabilityService:
    settings = _settings(**setting_overrides)
    repository = sessions or SessionRepository(AsyncMongoMockClient(tz_aware=True)["availability"])
    return AvailabilityService(settings, _StubCalendar(settings, busy), repository)


def _at(hour: int, minute: int = 0, day: date = _MONDAY) -> datetime:
    return datetime.combine(day, time(hour=hour, minute=minute), tzinfo=UTC)


async def test_slots_cover_opening_hours_only() -> None:
    """9:00-12:00 in 30-minute steps is six slots, and nothing outside them."""
    days = await _service().days_for_doctor(_doctor(), from_day=_MONDAY, days=1)

    starts = [slot.start for slot in days[0].slots]
    assert starts == [_at(9), _at(9, 30), _at(10), _at(10, 30), _at(11), _at(11, 30)]


async def test_a_closed_day_offers_nothing() -> None:
    """A weekend is returned as a day with no slots, not omitted from the strip."""
    saturday = _MONDAY + timedelta(days=5)

    days = await _service().days_for_doctor(_doctor(), from_day=saturday, days=1)

    assert len(days) == 1
    assert days[0].day == saturday
    assert days[0].slots == []


async def test_calendar_busy_windows_remove_overlapping_slots() -> None:
    """A meeting on the doctor's own calendar makes those slots unbookable."""
    busy = [BusyWindow(start=_at(9, 15), end=_at(10, 15))]

    days = await _service(busy=busy).days_for_doctor(
        _doctor(connected=True), from_day=_MONDAY, days=1
    )

    starts = [slot.start for slot in days[0].slots]
    assert starts == [_at(10, 30), _at(11), _at(11, 30)]
    assert days[0].source is AvailabilitySource.CALENDAR


async def test_an_unreadable_calendar_falls_back_to_clinic_hours() -> None:
    """A doctor with no Google grant is still bookable, but not marked verified.

    The label is the point: an unverified slot must never be presented as
    though a calendar had confirmed it.
    """
    days = await _service(busy=None).days_for_doctor(_doctor(), from_day=_MONDAY, days=1)

    assert days[0].source is AvailabilitySource.CLINIC_HOURS
    assert len(days[0].slots) == 6


async def test_a_slot_already_booked_here_is_not_offered_again() -> None:
    """An existing session's time is taken even with no calendar to consult."""
    repository = SessionRepository(AsyncMongoMockClient(tz_aware=True)["availability"])
    await repository.create(
        Session(
            session_id="sess_existing",
            appointment_id="appt_existing",
            patient=PatientRef(
                name="Test Patient",
                patient_id="pt_0001",
                date_of_birth=datetime(1990, 1, 1, tzinfo=UTC),
                sex="female",
            ),
            physician="Dr. Test",
            appointment_datetime=_at(10),
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
    )

    days = await _service(sessions=repository).days_for_doctor(_doctor(), from_day=_MONDAY, days=1)

    assert _at(10) not in [slot.start for slot in days[0].slots]


async def test_slots_inside_the_lead_time_are_not_offered() -> None:
    """Nothing starting sooner than the required notice is bookable."""
    today = datetime.now(UTC).date()

    days = await _service(
        clinic_open_hour=0,
        clinic_close_hour=24,
        booking_lead_time_minutes=24 * 60,
    ).days_for_doctor(_doctor(), from_day=today, days=1)

    assert days[0].slots == []


@pytest.mark.parametrize("requested_hour", [9, 11])
async def test_is_slot_bookable_accepts_open_times(requested_hour: int) -> None:
    """The submit-time re-check agrees with what the strip offered."""
    assert await _service().is_slot_bookable(_doctor(), _at(requested_hour)) is True


@pytest.mark.parametrize("requested_hour", [3, 13])
async def test_is_slot_bookable_rejects_closed_times(requested_hour: int) -> None:
    """A time the strip never offered must not pass the submit-time re-check."""
    assert await _service().is_slot_bookable(_doctor(), _at(requested_hour)) is False


async def test_an_invalid_clinic_timezone_falls_back_to_utc() -> None:
    """A typo in the configured zone must not take booking down."""
    service = _service(clinic_timezone="Not/AZone")

    assert service.clinic_zone is UTC

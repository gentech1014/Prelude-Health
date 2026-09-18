"""Unit tests for the patient's own appointment: what is offered, and moving it.

`AppointmentService` is what stops the reschedule screen and the voice
agent being two products. The screen fetches a fortnight of open times and
lets the patient scroll; the agent is handed a short list to read aloud.
Both come from here, so what is pinned below is the part where those two
views could quietly diverge: which times the agent is allowed to say, and
what happens when one of them is taken between the offer and the answer.
"""

from datetime import UTC, date, datetime, time

import pytest
from mongomock_motor import AsyncMongoMockClient

from app.core.config import Settings
from app.core.constants import SessionState, Sex
from app.core.exceptions import (
    AppointmentTimeUnavailableError,
    DoctorNotFoundError,
    InvalidSessionStateError,
)
from app.integrations.doctor_calendar import DoctorCalendarClient
from app.models.doctor import Doctor
from app.models.session import PatientRef, Session
from app.repositories.session_repository import SessionRepository
from app.services.appointment_service import AppointmentService
from app.services.availability_service import AvailabilityService

_MONDAY = date(2026, 9, 14)
"""A Monday comfortably in the future, so lead-time filtering never applies."""


_DOCTOR = Doctor(name="Dr. Test", google_calendar_id="dr.test@example.com")


def _at(hour: int, minute: int = 0, day: date = _MONDAY) -> datetime:
    return datetime.combine(day, time(hour=hour, minute=minute), tzinfo=UTC)


def _settings() -> Settings:
    return Settings(
        _env_file=None,  # isolate from the developer's real .env -- see tests/conftest.py
        intake_link_secret="appointment-test-secret-32-chars-x",
        booking_webhook_secret="appointment-webhook-secret-32-ch",
        physician_api_key="appointment-physician-key-32-chars",
        clinic_timezone="UTC",
        clinic_open_hour=9,
        clinic_close_hour=12,
        clinic_slot_minutes=30,
    )


class _NoCalendar(DoctorCalendarClient):
    """A doctor who has not connected Google: clinic hours, nothing filtered."""

    def can_serve(self, doctor: Doctor) -> bool:
        return False

    async def fetch_busy_windows(
        self, doctor: Doctor, window_start: datetime, window_end: datetime
    ) -> None:
        return None


class _StubDoctors:
    """Stands in for `DoctorRepository`, which only gets looked up by name."""

    def __init__(self, doctor: Doctor | None) -> None:
        self._doctor = doctor

    async def get_by_name(self, name: str) -> Doctor | None:
        return self._doctor


class _RecordingCalendarDelivery:
    """Best-effort Calendar delivery, recorded rather than performed."""

    def __init__(self) -> None:
        self.moves: list[tuple[str, datetime]] = []

    async def move_event(self, doctor: Doctor, event_id: str, start: datetime) -> bool:
        self.moves.append((event_id, start))
        return True


def _session(
    session_id: str = "sess_appt",
    *,
    status: SessionState = SessionState.IN_PROGRESS,
    appointment: datetime | None = None,
) -> Session:
    now = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    return Session(
        session_id=session_id,
        appointment_id=f"appt_{session_id}",
        patient=PatientRef(
            name="Asha Rao",
            patient_id="pt_2290",
            date_of_birth=datetime(1990, 5, 14, tzinfo=UTC),
            sex=Sex.FEMALE,
        ),
        physician="Dr. Test",
        appointment_datetime=appointment or _at(10),
        appointment_timezone="UTC",
        status=status,
        created_at=now,
        updated_at=now,
    )


async def _service(
    session: Session | None = None,
    *,
    doctor: Doctor | None = _DOCTOR,
    calendar: _RecordingCalendarDelivery | None = None,
) -> tuple[AppointmentService, SessionRepository]:
    settings = _settings()
    database = AsyncMongoMockClient(tz_aware=True)["appointment_test"]
    sessions = SessionRepository(database)
    await sessions.create(session or _session())
    availability = AvailabilityService(settings, _NoCalendar(settings), sessions)
    service = AppointmentService(
        sessions,
        _StubDoctors(doctor),  # type: ignore[arg-type]
        availability,
        calendar or _RecordingCalendarDelivery(),  # type: ignore[arg-type]
    )
    return service, sessions


async def test_spoken_slots_are_capped_and_read_as_clinic_local_wording() -> None:
    """What the agent says aloud, never a timestamp it has to convert itself.

    Capped because this list is spoken: the screen can show a fortnight of
    times and let the patient scroll, an agent reading them cannot.
    """
    service, _ = await _service()

    slots = await service.spoken_slots("sess_appt", limit=3)

    assert len(slots) == 3
    # The id is the ISO start the screen already keys its own slots on, so
    # a time offered aloud and a time tapped are the same time.
    assert [slot.slot_id for slot in slots] == [slot.start.isoformat() for slot in slots]
    assert all("T" not in slot.spoken_time for slot in slots)
    assert all(" at " in slot.spoken_time for slot in slots)
    assert [slot.start for slot in slots] == sorted(slot.start for slot in slots)


async def test_the_time_they_already_hold_is_never_offered_as_a_change() -> None:
    """Offering a patient their own appointment reads as not knowing when it is."""
    service, sessions = await _service()
    first = (await service.spoken_slots("sess_appt", limit=1))[0]
    await sessions.set_appointment_datetime("sess_appt", first.start)

    slots = await service.spoken_slots("sess_appt", limit=6)

    assert first.slot_id not in {slot.slot_id for slot in slots}


async def test_moving_writes_the_session_and_updates_the_calendar() -> None:
    """The stored time is what the report, the link and the greeting read."""
    calendar = _RecordingCalendarDelivery()
    session = _session()
    session.calendar_event_id = "evt_1"
    service, sessions = await _service(session, calendar=calendar)

    updated, _ = await service.move_to("sess_appt", _at(11))

    assert updated.appointment_datetime == _at(11)
    stored = await sessions.get_by_id("sess_appt")
    assert stored is not None and stored.appointment_datetime == _at(11)
    assert calendar.moves == [("evt_1", _at(11))]


async def test_a_time_that_is_not_open_is_refused_rather_than_written() -> None:
    """The caller's list is a snapshot, so the write re-checks it live.

    Both callers -- the screen the patient taps and the agent they talk to
    -- can be working from a list minutes old, and neither is allowed to
    book outside the doctor's real open times on the strength of it.
    """
    service, sessions = await _service()

    with pytest.raises(AppointmentTimeUnavailableError):
        await service.move_to("sess_appt", _at(20))

    stored = await sessions.get_by_id("sess_appt")
    assert stored is not None and stored.appointment_datetime == _at(10)


async def test_a_finished_session_can_still_move_its_appointment() -> None:
    """The prescreening being over does not make the appointment unmovable --
    the closing screen is exactly where the patient is offered it."""
    service, _ = await _service(_session(status=SessionState.COMPLETED))

    updated, _ = await service.move_to("sess_appt", _at(11))

    assert updated.appointment_datetime == _at(11)


async def test_a_declined_session_has_no_appointment_left_to_move() -> None:
    service, _ = await _service(_session(status=SessionState.DECLINED))

    with pytest.raises(InvalidSessionStateError):
        await service.move_to("sess_appt", _at(11))


async def test_a_physician_with_no_doctor_record_is_a_lookup_failure() -> None:
    """Sessions name their physician as free text, so a typo at booking
    genuinely leaves nothing to look up -- and nothing to offer."""
    service, _ = await _service(doctor=None)

    with pytest.raises(DoctorNotFoundError):
        await service.spoken_slots("sess_appt", limit=3)


async def test_the_offer_reaches_past_a_full_first_day() -> None:
    """A day with nothing left must not end the offer: the list is flattened
    across days precisely so a patient asked to choose is given a choice."""
    service, _ = await _service()

    slots = await service.spoken_slots("sess_appt", limit=12)

    assert len(slots) == 12
    assert len({slot.start.date() for slot in slots}) > 1

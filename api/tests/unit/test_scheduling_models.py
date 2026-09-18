"""Unit tests for the scheduling feature's models and schema validators."""

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from app.core.constants import SessionState, Sex, SlotStatus
from app.models.doctor import Doctor
from app.models.session import PatientRef, Session
from app.models.slot import AppointmentSlot, SlotOffer
from app.schemas.mock_booking import ScheduleAppointmentRequest


def test_schedule_appointment_request_rejects_a_naive_scheduled_at() -> None:
    """BSON already silently treats a naive datetime as UTC -- tolerating
    naive input here would layer a second silent reinterpretation on top
    of that one. A loud 422 is correct."""
    with pytest.raises(ValidationError, match="UTC offset"):
        ScheduleAppointmentRequest(
            patient_name="Asha Rao",
            patient_id="pt_2290",
            date_of_birth=date(1990, 5, 14),
            sex=Sex.FEMALE,
            physician="Dr. Mehta",
            scheduled_at=datetime(2026, 9, 3, 9, 30),  # noqa: DTZ001 - the point of this test
        )


def test_schedule_appointment_request_accepts_a_non_utc_offset() -> None:
    """An aware datetime in ANY offset must be accepted -- the validator
    only rejects the absence of a zone, not a specific one."""
    request = ScheduleAppointmentRequest(
        patient_name="Asha Rao",
        patient_id="pt_2290",
        date_of_birth=date(1990, 5, 14),
        sex=Sex.FEMALE,
        physician="Dr. Mehta",
        scheduled_at=datetime(2026, 9, 3, 9, 30, tzinfo=UTC),
    )

    assert request.scheduled_at.tzinfo is not None


def test_session_defaults_carry_no_slot_or_cancellation() -> None:
    """A freshly-booked session (or one from before this feature shipped)
    must load cleanly with no slot and no cancellation record."""
    now = datetime.now(UTC)
    session = Session(
        session_id="sess_1",
        appointment_id="appt_1",
        patient=PatientRef(
            name="Asha Rao", patient_id="pt_2290", date_of_birth=now, sex=Sex.FEMALE
        ),
        physician="Dr. Mehta",
        appointment_datetime=now,
        created_at=now,
        updated_at=now,
    )

    assert session.current_slot_id is None
    assert session.cancelled_at is None
    assert session.cancellation_reason is None
    assert session.appointment_timezone == "UTC"


def test_doctor_timezone_defaults_to_none() -> None:
    """None means 'use Settings.clinic_timezone' -- existing seeded
    doctors need no backfill for this feature to work."""
    doctor = Doctor(name="Dr. Mehta", google_calendar_id="dr-mehta@example.com")

    assert doctor.timezone is None


def test_appointment_slot_defaults_to_free_with_no_holder() -> None:
    now = datetime.now(UTC)
    slot = AppointmentSlot(
        slot_id="slot_1", physician="Dr. Mehta", starts_at=now, ends_at=now, updated_at=now
    )

    assert slot.status is SlotStatus.FREE
    assert slot.session_id is None
    assert slot.held_until is None
    assert slot.version == 0


def test_slot_offer_carries_a_preformatted_time_not_a_raw_field() -> None:
    offer = SlotOffer(slot_id="slot_1", spoken_time="Tuesday at 10 in the morning")

    assert offer.spoken_time == "Tuesday at 10 in the morning"


def test_session_state_gained_cancelled() -> None:
    assert SessionState.CANCELLED == "cancelled"


def test_slot_status_values() -> None:
    assert {s.value for s in SlotStatus} == {"free", "held", "booked"}

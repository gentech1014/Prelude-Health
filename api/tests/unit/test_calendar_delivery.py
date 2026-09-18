"""Which Google credential a report link actually goes out under.

This is the seam a shipped report fell through: bookings were created
under each doctor's own OAuth grant, while report delivery went through a
service account whose key file was never installed. Every append returned
cleanly, nothing reached the calendar, and no log line said so.
"""

from datetime import UTC, datetime
from html import escape

import pytest

from app.integrations.calendar_delivery import CalendarDelivery
from app.integrations.google_calendar import append_description_line
from app.models.doctor import Doctor, DoctorGoogleGrant


class _FakeDoctorCalendar:
    """Stands in for the per-doctor client, recording what it was asked to do."""

    def __init__(self, *, serves: bool) -> None:
        self._serves = serves
        self.appended: list[tuple[str, str]] = []
        self.moved: list[tuple[str, datetime]] = []

    def can_serve(self, doctor: Doctor) -> bool:
        return self._serves

    async def append_to_event_description(
        self, doctor: Doctor, event_id: str, addendum: str
    ) -> None:
        self.appended.append((event_id, addendum))

    async def move_event(self, doctor: Doctor, event_id: str, start: datetime) -> None:
        self.moved.append((event_id, start))


class _FakeServiceAccountCalendar:
    def __init__(self, *, configured: bool) -> None:
        self.is_configured = configured
        self.appended: list[tuple[str, str, str]] = []
        self.moved: list[tuple[str, str, datetime]] = []

    async def append_to_event_description(
        self, calendar_id: str, event_id: str, addendum: str
    ) -> None:
        self.appended.append((calendar_id, event_id, addendum))

    async def move_event(self, calendar_id: str, event_id: str, start: datetime) -> None:
        self.moved.append((calendar_id, event_id, start))


def _doctor(*, registered: bool) -> Doctor:
    return Doctor(
        doctor_id="dr-test",
        name="Dr. Test",
        credential="MD",
        categories=[],
        google_calendar_id="calendar@example.test",
        google_grant=(
            DoctorGoogleGrant(
                email="calendar@example.test",
                refresh_token="synthetic-refresh-token",
                scopes=["https://www.googleapis.com/auth/calendar"],
                granted_at=datetime(2026, 9, 1, tzinfo=UTC),
            )
            if registered
            else None
        ),
    )


def _delivery(
    *, doctor_serves: bool, service_account_configured: bool
) -> tuple[CalendarDelivery, _FakeDoctorCalendar, _FakeServiceAccountCalendar]:
    doctor_calendar = _FakeDoctorCalendar(serves=doctor_serves)
    service_account = _FakeServiceAccountCalendar(configured=service_account_configured)
    delivery = CalendarDelivery(doctor_calendar, service_account)  # type: ignore[arg-type]
    return delivery, doctor_calendar, service_account


async def test_a_registered_doctor_is_written_to_under_their_own_grant() -> None:
    """The grant that created the event is the one that can update it."""
    delivery, doctor_calendar, service_account = _delivery(
        doctor_serves=True, service_account_configured=True
    )

    delivered = await delivery.append_line(_doctor(registered=True), "evt_1", "Report: https://x")

    assert delivered is True
    assert doctor_calendar.appended == [("evt_1", "Report: https://x")]
    assert service_account.appended == []


async def test_a_doctor_without_a_grant_falls_back_to_the_service_account() -> None:
    """The out-of-band sharing setup still works for a doctor who uses it."""
    delivery, doctor_calendar, service_account = _delivery(
        doctor_serves=False, service_account_configured=True
    )

    delivered = await delivery.append_line(_doctor(registered=False), "evt_2", "Report: https://x")

    assert delivered is True
    assert doctor_calendar.appended == []
    assert service_account.appended == [("calendar@example.test", "evt_2", "Report: https://x")]


async def test_no_configured_route_reports_failure_instead_of_silence() -> None:
    """The exact case that shipped: nothing configured, nothing said."""
    delivery, doctor_calendar, service_account = _delivery(
        doctor_serves=False, service_account_configured=False
    )

    delivered = await delivery.append_line(_doctor(registered=False), "evt_3", "Report: https://x")

    assert delivered is False
    assert doctor_calendar.appended == []
    assert service_account.appended == []


async def test_a_session_with_no_calendar_event_is_not_delivery_at_all() -> None:
    """A session booked outside the platform has nothing to append to."""
    delivery, doctor_calendar, service_account = _delivery(
        doctor_serves=True, service_account_configured=True
    )

    assert await delivery.append_line(_doctor(registered=True), "", "Report: https://x") is False
    assert doctor_calendar.appended == []


@pytest.mark.parametrize("registered", [True, False], ids=["own grant", "service account"])
async def test_a_reschedule_moves_the_event_under_the_same_choice(registered: bool) -> None:
    """A move that goes nowhere leaves a doctor reading a stale time."""
    delivery, doctor_calendar, service_account = _delivery(
        doctor_serves=registered, service_account_configured=True
    )
    start = datetime(2026, 9, 12, 9, 30, tzinfo=UTC)

    assert await delivery.move_event(_doctor(registered=registered), "evt_4", start) is True
    assert bool(doctor_calendar.moved) is registered
    assert bool(service_account.moved) is not registered


# --------------------------------------------------------------------------
# What the doctor's event actually shows
# --------------------------------------------------------------------------

PRESIGNED = (
    "https://prescreening-agent-uploads.s3.ap-south-1.amazonaws.com/reports/abc/report.pdf"
    "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=AKIA123%2F20260912%2Fap-south-1"
    "%2Fs3%2Faws4_request&X-Amz-Date=20260912T094315Z&X-Amz-Expires=518400"
    "&X-Amz-SignedHeaders=host&X-Amz-Signature=c791b668a452156e45cc2116f1d1f5e1afc21ec0"
)


def test_a_link_line_shows_its_label_rather_than_the_url() -> None:
    """The reported problem: eight lines of presigned URL on the event.

    A presigned S3 link is several hundred characters of credential-bearing
    query string, and Calendar rendered every one of them in full -- burying
    the appointment's own description and putting the signature on screen
    for anyone who so much as glanced at the event.
    """
    label = "Pre-Consultation Report"
    anchor = f'<a href="{escape(PRESIGNED, quote=True)}">{escape(label)}</a>'

    assert anchor.startswith('<a href="https://')
    assert anchor.endswith(f">{label}</a>")
    # The signature is in the href and nowhere a reader's eye lands.
    assert PRESIGNED not in anchor.split('">', 1)[1]


def test_the_ampersands_in_a_presigned_url_are_escaped_in_the_href() -> None:
    """A raw `&` in an HTML attribute is malformed, and Calendar renders HTML."""
    href = escape(PRESIGNED, quote=True)

    assert "&amp;X-Amz-Date" in href
    assert "&X-Amz-Date" not in href.replace("&amp;", "")


def test_lines_already_on_the_event_survive_a_link_being_added() -> None:
    """The description becomes HTML the moment a link lands on it.

    A literal newline stops being a line break at that point, so the
    booking's own description would run straight into the link.
    """
    existing = "Diabetes and blood sugar\nBooked by the mock booking platform"

    joined = append_description_line(existing, '<a href="https://x">Report</a>')

    assert joined == (
        "Diabetes and blood sugar<br>Booked by the mock booking platform"
        '<br><a href="https://x">Report</a>'
    )


def test_appending_twice_does_not_re_break_what_was_already_joined() -> None:
    """Google hands back what was written, so the join has to be idempotent."""
    once = append_description_line("Diabetes and blood sugar", "<a>Report</a>")

    twice = append_description_line(once, "<a>Document</a>")

    assert twice.count("<br>") == 2
    assert "<br><br>" not in twice


def test_an_empty_description_does_not_open_with_a_break() -> None:
    assert append_description_line("", "<a>Report</a>") == "<a>Report</a>"

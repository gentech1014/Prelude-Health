"""Unit tests for `GoogleCalendarClient`'s not-configured no-op path.

No test here makes a real Google API call or needs real credentials: this
client's whole design point is that it must degrade harmlessly when
`google_service_account_file` is unset, since Calendar delivery is
additive and must never block the report/video artifacts that depend on
it succeeding.
"""

from datetime import UTC, datetime, timedelta

from app.core.config import Settings
from app.integrations.google_calendar import GoogleCalendarClient, _event_body, _start_end_pair


def _unconfigured_settings() -> Settings:
    return Settings(
        _env_file=None,  # isolate from the developer's real .env -- see tests/conftest.py
        intake_link_secret="unused-but-required-secret-32-chars",  # noqa: S106 - test fixture
        booking_webhook_secret="unused-but-required-secret-32-chars",  # noqa: S106 - test fixture
        physician_api_key="unused-but-required-key-32-characters",  # noqa: S106 - test fixture
        # google_service_account_file left at its default: ""
    )


async def test_create_event_returns_empty_string_when_not_configured() -> None:
    """No credentials -> no API call -> an empty event id, not an exception."""
    client = GoogleCalendarClient(_unconfigured_settings())

    event_id = await client.create_event(
        "calendar@example.com",
        "Appointment",
        datetime(2026, 9, 3, 9, 30, tzinfo=UTC),
        tz="Asia/Kolkata",
    )

    assert event_id == ""


async def test_update_event_time_is_a_silent_no_op_when_not_configured() -> None:
    """No credentials -> no API call -> returns without raising."""
    client = GoogleCalendarClient(_unconfigured_settings())

    await client.update_event_time(
        "calendar@example.com",
        "evt_123",
        datetime(2026, 9, 3, 9, 30, tzinfo=UTC),
        tz="Asia/Kolkata",
    )


async def test_update_event_time_no_ops_on_an_empty_event_id() -> None:
    """An empty `event_id` is also a no-op, even if configured."""
    client = GoogleCalendarClient(_unconfigured_settings())

    await client.update_event_time(
        "calendar@example.com", "", datetime(2026, 9, 3, 9, 30, tzinfo=UTC), tz="Asia/Kolkata"
    )


async def test_delete_event_is_a_silent_no_op_when_not_configured() -> None:
    """No credentials -> no API call -> returns without raising."""
    client = GoogleCalendarClient(_unconfigured_settings())

    await client.delete_event("calendar@example.com", "evt_123")


async def test_delete_event_no_ops_on_an_empty_event_id() -> None:
    """An empty `event_id` is also a no-op, even if configured."""
    client = GoogleCalendarClient(_unconfigured_settings())

    await client.delete_event("calendar@example.com", "")


async def test_append_to_event_description_is_a_silent_no_op_when_not_configured() -> None:
    """No credentials -> nothing to patch -> returns without raising."""
    client = GoogleCalendarClient(_unconfigured_settings())

    # Must not raise, even with an event id that could not possibly be real.
    await client.append_to_event_description("calendar@example.com", "evt_123", "Report ready")


async def test_append_to_event_description_no_ops_on_an_empty_event_id() -> None:
    """An empty `event_id` (Calendar was unset at booking time) is also a no-op, even if configured."""
    client = GoogleCalendarClient(_unconfigured_settings())

    await client.append_to_event_description("calendar@example.com", "", "Report ready")


async def test_prepend_to_event_summary_is_a_silent_no_op_when_not_configured() -> None:
    """No credentials -> no API call -> returns without raising.

    Used for the safety-escalation title prefix, which must never block
    the escalation record that already succeeded in Mongo.
    """
    client = GoogleCalendarClient(_unconfigured_settings())

    await client.prepend_to_event_summary("calendar@example.com", "evt_123", "\U0001f6a8 URGENT — ")


async def test_prepend_to_event_summary_no_ops_on_an_empty_event_id() -> None:
    """An empty `event_id` is also a no-op, even if configured."""
    client = GoogleCalendarClient(_unconfigured_settings())

    await client.prepend_to_event_summary("calendar@example.com", "", "\U0001f6a8 URGENT — ")


async def test_a_configured_but_missing_key_file_degrades_to_a_no_op() -> None:
    """A missing key file must not take the app's startup down.

    Calendar delivery is additive over the written report; a broken
    credentials path is an operator misconfiguration to warn about, not a
    reason for the whole service to refuse to boot.
    """
    settings = Settings(
        _env_file=None,  # isolate from the developer's real .env -- see tests/conftest.py
        intake_link_secret="calendar-test-secret-32-characters",  # noqa: S106 - test fixture
        booking_webhook_secret="calendar-webhook-secret-32-charac",  # noqa: S106 - test fixture
        physician_api_key="calendar-physician-key-32-characte",  # noqa: S106 - test fixture
        google_service_account_file="does/not/exist.json",
    )

    client = GoogleCalendarClient(settings)

    assert (
        await client.create_event("cal-1", summary="Test", start=datetime.now(UTC), tz="UTC") == ""
    )


def test_start_end_pair_sends_both_offset_and_explicit_timezone() -> None:
    """The Calendar API's discovery doc requires a UTC offset unless
    `timeZone` is given -- a naive/offset-less `dateTime` with no
    `timeZone` used to be silently rejected with a 400 (swallowed by the
    bare `except HttpError` in every method here). Both fields, always.
    """
    start = datetime(2026, 9, 3, 9, 30, tzinfo=UTC)

    start_field, end_field = _start_end_pair(start, "Asia/Kolkata", timedelta(minutes=30))

    assert start_field["timeZone"] == "Asia/Kolkata"
    assert end_field["timeZone"] == "Asia/Kolkata"
    assert "+00:00" in start_field["dateTime"]  # start's own offset, carried through isoformat()
    assert start_field["dateTime"] != end_field["dateTime"]


def test_event_body_is_reachable_and_pure() -> None:
    """This dict was previously unreachable by any test: `_enabled` is
    False in every fixture in this file, so no test ever exercised the
    body a real `create_event` call would send. Extracting it into a
    pure function fixes that."""
    start = datetime(2026, 9, 3, 9, 30, tzinfo=UTC)

    body = _event_body("Appointment", start, "Some notes", "Asia/Kolkata", timedelta(minutes=30))

    assert body["summary"] == "Appointment"
    assert body["description"] == "Some notes"
    assert body["start"]["timeZone"] == "Asia/Kolkata"
    assert body["end"]["timeZone"] == "Asia/Kolkata"

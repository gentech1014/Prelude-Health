"""Delivers pre-screening outputs onto the doctor's Google Calendar event.

Not part of the BA scope (which only requires a simulated calendar/dashboard)
-- this is the real Google Calendar integration the team chose to build on
top of it, so the doctor can open the report and recording directly from
their own appointment.

Auth is a service account, not per-doctor OAuth: each doctor shares their
calendar with the service account's email (Editor access) once, out of
band. That trade avoids building and demoing an OAuth consent flow inside
the hackathon's timeline, at the cost of that one manual sharing step.
"""

import asyncio
from datetime import datetime, timedelta

import structlog
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.core.config import Settings

log = structlog.get_logger(__name__)


def append_description_line(existing: str, addendum: str) -> str:
    """Join one more line onto a Calendar event description.

    Google renders a description as HTML as soon as it contains markup,
    and the lines appended here carry anchors -- so a real newline stops
    being a line break the moment the first link lands, and everything
    already on the event runs together into one paragraph.

    Splitting on line breaks and rejoining with a tag keeps them apart,
    and is idempotent: what comes back from Google on the next append has
    already been normalized, so there are no line breaks left to split.
    """
    parts = [*existing.splitlines(), addendum]
    return "<br>".join(part for part in parts if part.strip())


_SCOPES = ["https://www.googleapis.com/auth/calendar"]
_DEFAULT_EVENT_DURATION = timedelta(minutes=30)


def _start_end_pair(start: datetime, tz: str, duration: timedelta) -> tuple[dict, dict]:
    """The `start`/`end` field pair shared by event creation and rescheduling.

    Both `dateTime` (which carries `start`'s own UTC offset via
    `isoformat()`) and an explicit `timeZone` are sent: the Calendar API's
    discovery document requires an offset unless `timeZone` is given, and
    only `timeZone` controls how the event actually renders for the
    doctor. Sending just one or the other is how a naive, offset-less
    `scheduled_at` used to reach this method and get silently rejected by
    Calendar with a 400 that the bare `except HttpError` below swallows.

    Kept separate from `_event_body` so `update_event_time` (which patches
    only the time, not the summary or description) can reuse it without
    touching unrelated fields.
    """
    return (
        {"dateTime": start.isoformat(), "timeZone": tz},
        {"dateTime": (start + duration).isoformat(), "timeZone": tz},
    )


def _event_body(
    summary: str, start: datetime, description: str, tz: str, duration: timedelta
) -> dict:
    """The full event-creation body. Pure, and deliberately extracted: every
    existing test builds `GoogleCalendarClient` with no service-account
    file, so `_enabled` is `False` everywhere and this dict was previously
    unreachable by any test at all."""
    start_field, end_field = _start_end_pair(start, tz, duration)
    return {
        "summary": summary,
        "description": description,
        "start": start_field,
        "end": end_field,
    }


class GoogleCalendarClient:
    """Thin wrapper around the Calendar API v3, via a service account.

    The Google client library is synchronous, so every call runs on a
    worker thread via `asyncio.to_thread` -- same reasoning as
    `app.integrations.storage.ObjectStorageClient` for boto3: this must
    never block the event loop other sessions' traffic runs on.

    Delivery here is deliberately best-effort. `google_service_account_file`
    is optional: when it is not configured, every method logs a warning and
    returns a harmless default instead of raising. The written report is
    the primary deliverable per the BA scope and must never be blocked by
    a Calendar integration that is, by the user's own design, additive.
    """

    def __init__(self, settings: Settings) -> None:
        self._enabled = bool(settings.google_service_account_file)
        if not self._enabled:
            log.warning("google_calendar_not_configured")
            return

        # A configured-but-missing or malformed key file degrades to the same
        # no-op as no configuration at all. It used to raise here, which broke
        # the promise in this class's own docstring by taking the whole app's
        # startup down over an additive integration.
        try:
            credentials = service_account.Credentials.from_service_account_file(
                settings.google_service_account_file, scopes=_SCOPES
            )
        except (OSError, ValueError):
            log.warning(
                "google_calendar_credentials_unusable",
                path=settings.google_service_account_file,
            )
            self._enabled = False
            return

        self._service = build("calendar", "v3", credentials=credentials, cache_discovery=False)

    @property
    def is_configured(self) -> bool:
        """Whether a usable service-account key was loaded.

        Public because delivery has to choose between this client and the
        doctor's own grant, and a caller that cannot tell an unconfigured
        client from a successful write reports a link it never delivered.
        """
        return self._enabled

    async def create_event(
        self,
        calendar_id: str,
        summary: str,
        start: datetime,
        tz: str,
        description: str = "",
        duration: timedelta = _DEFAULT_EVENT_DURATION,
    ) -> str:
        """Create the appointment event and return its `event_id`.

        `tz` is required, not optional: an omitted zone is exactly the
        bug this parameter exists to make impossible to reintroduce --
        see `_start_end_pair`'s docstring.

        Returns an empty string, without calling the API, when Calendar
        delivery is not configured -- callers persist this as
        `Session.calendar_event_id` and treat an empty value as "nothing to
        update later," not as an error.
        """
        if not self._enabled:
            return ""

        body = _event_body(summary, start, description, tz, duration)
        try:
            event = await asyncio.to_thread(
                self._service.events().insert(calendarId=calendar_id, body=body).execute
            )
        except HttpError:
            log.exception("google_calendar_create_event_failed", calendar_id=calendar_id)
            return ""
        return event["id"]

    async def move_event(self, calendar_id: str, event_id: str, start: datetime) -> None:
        """Move an existing appointment event to a new start time.

        Patches only the times, leaving the summary and description (and
        therefore any report or document links already appended to it)
        untouched.

        Best-effort like every other method here: the appointment's new
        time is already stored on the session by the time this runs, and a
        Calendar hiccup must not undo it. What it does cost is a doctor
        looking at a stale time on their own calendar, so the failure is
        logged rather than swallowed silently.
        """
        if not self._enabled or not event_id:
            if self._enabled:
                log.warning("google_calendar_move_skipped_no_event_id")
            return

        body = {
            "start": {"dateTime": start.isoformat()},
            "end": {"dateTime": (start + _DEFAULT_EVENT_DURATION).isoformat()},
        }
        try:
            await asyncio.to_thread(
                self._service.events()
                .patch(calendarId=calendar_id, eventId=event_id, body=body)
                .execute
            )
        except HttpError:
            log.exception("google_calendar_move_failed", calendar_id=calendar_id, event_id=event_id)

    async def update_event_time(
        self,
        calendar_id: str,
        event_id: str,
        start: datetime,
        tz: str,
        duration: timedelta = _DEFAULT_EVENT_DURATION,
    ) -> None:
        """Move an event to a new start/end time -- used when a reschedule
        moves the appointment to a different slot. Best-effort, same
        contract as every other method here: a Calendar hiccup must never
        undo a reschedule that already succeeded in Mongo.

        Unlike `move_event`, this carries the appointment's resolved IANA
        zone, so the doctor's calendar renders the new time in the same
        zone every other surface speaks it in.
        """
        if not self._enabled or not event_id:
            if self._enabled:
                log.warning("google_calendar_update_time_skipped_no_event_id")
            return

        start_field, end_field = _start_end_pair(start, tz, duration)
        try:
            await asyncio.to_thread(
                self._service.events()
                .patch(
                    calendarId=calendar_id,
                    eventId=event_id,
                    body={"start": start_field, "end": end_field},
                )
                .execute
            )
        except HttpError:
            log.exception(
                "google_calendar_update_time_failed", calendar_id=calendar_id, event_id=event_id
            )

    async def delete_event(self, calendar_id: str, event_id: str) -> None:
        """Delete the event -- used when a patient cancels their appointment.

        Best-effort, same contract as every other method here: a Calendar
        hiccup must never block a cancellation that already succeeded in
        Mongo. Deleting an already-deleted event returns 410 from the API,
        which lands here as an `HttpError` like any other failure -- an
        acceptable, harmless log line rather than special-cased, matching
        this file's existing style of not inspecting status codes.
        """
        if not self._enabled or not event_id:
            if self._enabled:
                log.warning("google_calendar_delete_skipped_no_event_id")
            return

        try:
            await asyncio.to_thread(
                self._service.events().delete(calendarId=calendar_id, eventId=event_id).execute
            )
        except HttpError:
            log.exception(
                "google_calendar_delete_failed", calendar_id=calendar_id, event_id=event_id
            )

    async def prepend_to_event_summary(self, calendar_id: str, event_id: str, prefix: str) -> None:
        """Prepend a short prefix to an event's title, without disturbing the rest.

        Used only for a safety escalation, where a description line 6 days
        from now can go unread but a title prefix shows on the calendar
        grid without opening the event.

        Idempotent: does nothing if the prefix is already there, so a
        retry can't stack the same warning twice. Best-effort, same as
        `append_to_event_description` -- a Calendar hiccup must never
        block the escalation record that already succeeded in Mongo.
        """
        if not self._enabled or not event_id:
            if self._enabled:
                log.warning("google_calendar_prepend_skipped_no_event_id")
            return

        try:
            event = await asyncio.to_thread(
                self._service.events().get(calendarId=calendar_id, eventId=event_id).execute
            )
            existing = event.get("summary", "")
            if existing.startswith(prefix):
                return
            await asyncio.to_thread(
                self._service.events()
                .patch(
                    calendarId=calendar_id,
                    eventId=event_id,
                    body={"summary": f"{prefix}{existing}"},
                )
                .execute
            )
        except HttpError:
            log.exception(
                "google_calendar_prepend_failed", calendar_id=calendar_id, event_id=event_id
            )

    async def append_to_event_description(
        self, calendar_id: str, event_id: str, addendum: str
    ) -> None:
        """Append one line to an event's description, without disturbing the rest.

        Called independently for the report and the recording -- whichever
        artifact finishes first appends its line; the other appends its own
        line later. Safe to call twice: each call only adds a line, never
        replaces the description outright, so it does not matter which
        artifact arrives first or whether the second ever arrives at all.

        Failures are logged and swallowed, not raised: a Calendar hiccup
        must never fail the request that generated the report or attached
        the recording, both of which have already succeeded by the time
        this runs.
        """
        if not self._enabled or not event_id:
            if self._enabled:
                log.warning("google_calendar_append_skipped_no_event_id")
            return

        try:
            event = await asyncio.to_thread(
                self._service.events().get(calendarId=calendar_id, eventId=event_id).execute
            )
            existing = event.get("description", "")
            updated = append_description_line(existing, addendum)
            await asyncio.to_thread(
                self._service.events()
                .patch(calendarId=calendar_id, eventId=event_id, body={"description": updated})
                .execute
            )
        except HttpError:
            log.exception(
                "google_calendar_append_failed", calendar_id=calendar_id, event_id=event_id
            )

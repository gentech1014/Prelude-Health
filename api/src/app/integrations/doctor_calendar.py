"""Reads and writes a doctor's calendar under the doctor's own Google grant.

The sibling `app.integrations.google_calendar` acts as one shared service
account that each doctor must grant access to out of band. This module is
the other half of the picture: once a doctor has registered through
`GoogleOAuthClient`, their stored refresh token lets this service query
their real free/busy windows and put appointments on their calendar as
themselves -- no manual sharing step.

Same threading rule as every other Google client here: the library is
synchronous, so every call runs on a worker thread via `asyncio.to_thread`
and never blocks the event loop other sessions' traffic runs on.

Best-effort by the same principle too. A doctor's calendar being briefly
unreachable degrades availability to the clinic-hours fallback and lets a
booking still be recorded, rather than taking booking down -- but the
caller is told which happened, so the UI never presents a guess as verified.
"""

import asyncio
from datetime import datetime, timedelta
from typing import Any

import structlog
from google.auth.exceptions import GoogleAuthError
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.core.config import Settings
from app.integrations.google_calendar import append_description_line
from app.integrations.google_oauth import CALENDAR_SCOPES
from app.models.doctor import Doctor

log = structlog.get_logger(__name__)

_TOKEN_URI = "https://oauth2.googleapis.com/token"
_DEFAULT_EVENT_DURATION = timedelta(minutes=30)


class BusyWindow:
    """One interval the doctor is already occupied for."""

    def __init__(self, start: datetime, end: datetime) -> None:
        self.start = start
        self.end = end


class DoctorCalendarClient:
    """Per-doctor Calendar access, authorized by that doctor's own refresh token."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def can_serve(self, doctor: Doctor) -> bool:
        """Whether this doctor's calendar can be reached under their own grant."""
        return doctor.google_grant is not None and bool(
            self._settings.google_oauth_client_id and self._settings.google_oauth_client_secret
        )

    def _service(self, doctor: Doctor) -> Any:
        """Build a Calendar service for one doctor. Callers must check `can_serve` first.

        Returns `Any` because the discovery client builds its resource
        methods (`.events()`, `.freebusy()`) at runtime -- no static type
        describes them, and annotating `Resource` makes the type checker
        reject every real call.
        """
        grant = doctor.google_grant
        assert grant is not None, "can_serve() must gate this call"

        credentials = Credentials(
            token=None,
            refresh_token=grant.refresh_token,
            token_uri=_TOKEN_URI,
            client_id=self._settings.google_oauth_client_id,
            client_secret=self._settings.google_oauth_client_secret,
            scopes=CALENDAR_SCOPES,
        )
        return build("calendar", "v3", credentials=credentials, cache_discovery=False)

    async def fetch_busy_windows(
        self, doctor: Doctor, window_start: datetime, window_end: datetime
    ) -> list[BusyWindow] | None:
        """Busy intervals from the doctor's calendar, or None if it could not be read.

        None is meaningfully different from an empty list: empty means the
        calendar is genuinely clear, None means we do not know and the
        caller must not claim the slots it offers are calendar-verified.
        """
        if not self.can_serve(doctor):
            return None

        body = {
            "timeMin": window_start.isoformat(),
            "timeMax": window_end.isoformat(),
            "items": [{"id": doctor.google_calendar_id}],
        }
        try:
            service = await asyncio.to_thread(self._service, doctor)
            response = await asyncio.to_thread(service.freebusy().query(body=body).execute)
        except (HttpError, GoogleAuthError):
            log.exception("doctor_calendar_freebusy_failed", doctor_id=doctor.doctor_id)
            return None

        calendar = response.get("calendars", {}).get(doctor.google_calendar_id, {})
        if calendar.get("errors"):
            log.warning(
                "doctor_calendar_freebusy_calendar_error",
                doctor_id=doctor.doctor_id,
                errors=calendar["errors"],
            )
            return None

        windows: list[BusyWindow] = []
        for period in calendar.get("busy", []):
            windows.append(
                BusyWindow(
                    start=datetime.fromisoformat(period["start"]),
                    end=datetime.fromisoformat(period["end"]),
                )
            )
        return windows

    async def create_event(
        self, doctor: Doctor, summary: str, start: datetime, description: str = ""
    ) -> str:
        """Create the appointment on the doctor's own calendar; returns its event id.

        Returns an empty string when the doctor has no grant or the call
        fails -- callers persist that as `Session.calendar_event_id` and
        treat it as "nothing to update later", exactly as they already do
        for unconfigured service-account delivery.
        """
        if not self.can_serve(doctor):
            return ""

        body = {
            "summary": summary,
            "description": description,
            "start": {"dateTime": start.isoformat()},
            "end": {"dateTime": (start + _DEFAULT_EVENT_DURATION).isoformat()},
        }
        try:
            service = await asyncio.to_thread(self._service, doctor)
            event = await asyncio.to_thread(
                service.events().insert(calendarId=doctor.google_calendar_id, body=body).execute
            )
        except (HttpError, GoogleAuthError):
            log.exception("doctor_calendar_create_event_failed", doctor_id=doctor.doctor_id)
            return ""
        return str(event["id"])

    async def append_to_event_description(
        self, doctor: Doctor, event_id: str, addendum: str
    ) -> None:
        """Append one line to an event's description, under the doctor's own grant.

        Mirrors `GoogleCalendarClient.append_to_event_description`,
        including its read-modify-write and its best-effort failure
        handling: whichever artifact finishes first appends its line, and
        a Calendar hiccup must never fail the request that produced the
        report or the recording, both already stored by the time this runs.
        """
        if not self.can_serve(doctor) or not event_id:
            return

        try:
            service = await asyncio.to_thread(self._service, doctor)
            event = await asyncio.to_thread(
                service.events().get(calendarId=doctor.google_calendar_id, eventId=event_id).execute
            )
            existing = event.get("description", "")
            updated = append_description_line(existing, addendum)
            await asyncio.to_thread(
                service.events()
                .patch(
                    calendarId=doctor.google_calendar_id,
                    eventId=event_id,
                    body={"description": updated},
                )
                .execute
            )
        except (HttpError, GoogleAuthError):
            log.exception(
                "doctor_calendar_append_failed", doctor_id=doctor.doctor_id, event_id=event_id
            )

    async def move_event(self, doctor: Doctor, event_id: str, start: datetime) -> None:
        """Move an appointment to a new start time, under the doctor's own grant.

        Patches only the times, so any report or document link already
        appended to the description survives the move.
        """
        if not self.can_serve(doctor) or not event_id:
            return

        body = {
            "start": {"dateTime": start.isoformat()},
            "end": {"dateTime": (start + _DEFAULT_EVENT_DURATION).isoformat()},
        }
        try:
            service = await asyncio.to_thread(self._service, doctor)
            await asyncio.to_thread(
                service.events()
                .patch(calendarId=doctor.google_calendar_id, eventId=event_id, body=body)
                .execute
            )
        except (HttpError, GoogleAuthError):
            log.exception(
                "doctor_calendar_move_failed", doctor_id=doctor.doctor_id, event_id=event_id
            )

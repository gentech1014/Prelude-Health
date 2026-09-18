"""Puts a link on the doctor's appointment, under whichever grant works.

Two Calendar clients exist for two different setups: one shared service
account each doctor grants access to out of band
(`app.integrations.google_calendar`), and per-doctor OAuth for a doctor who
registered themselves (`app.integrations.doctor_calendar`). A session's
event lives on the doctor's own calendar either way -- only the
authorization differs -- so nothing else in the app should have to know
which one a given doctor is on.

This exists because getting that wrong has already cost a real report.
Bookings were created under the doctor's own grant, while report and
recording delivery went through the service account, whose key file was
never installed: every append was a silent no-op, the PDF sat in object
storage, and the doctor's event stayed empty. Delivery now tries the
doctor's own grant first and says so in the log when neither client can
reach the calendar, instead of reporting success it never had.
"""

from datetime import datetime

import structlog

from app.integrations.doctor_calendar import DoctorCalendarClient
from app.integrations.google_calendar import GoogleCalendarClient
from app.models.doctor import Doctor

log = structlog.get_logger(__name__)


class CalendarDelivery:
    """Chooses the client that can actually write to this doctor's calendar."""

    def __init__(
        self,
        doctor_calendar: DoctorCalendarClient,
        service_account_calendar: GoogleCalendarClient,
    ) -> None:
        self._doctor_calendar = doctor_calendar
        self._service_account = service_account_calendar

    async def append_line(self, doctor: Doctor, event_id: str, line: str) -> bool:
        """Append one line to the doctor's event. True if a client was able to try.

        The return value is not "the doctor can see it" -- both clients
        swallow their own API failures by design, since the artifact being
        linked has already been stored. It distinguishes an attempt from
        the case worth an operator's attention: no configured route to the
        calendar at all.
        """
        if not event_id:
            return False

        if self._doctor_calendar.can_serve(doctor):
            await self._doctor_calendar.append_to_event_description(doctor, event_id, line)
            return True

        if self._service_account.is_configured:
            await self._service_account.append_to_event_description(
                doctor.google_calendar_id, event_id, line
            )
            return True

        log.warning("calendar_delivery_unavailable", doctor_id=doctor.doctor_id)
        return False

    async def move_event(self, doctor: Doctor, event_id: str, start: datetime) -> bool:
        """Move the doctor's event to a new start time. Same contract as `append_line`."""
        if not event_id:
            return False

        if self._doctor_calendar.can_serve(doctor):
            await self._doctor_calendar.move_event(doctor, event_id, start)
            return True

        if self._service_account.is_configured:
            await self._service_account.move_event(doctor.google_calendar_id, event_id, start)
            return True

        log.warning("calendar_delivery_unavailable", doctor_id=doctor.doctor_id)
        return False

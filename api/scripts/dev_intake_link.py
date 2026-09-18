"""Mint a real session and print its intake link, for local frontend work.

Exists because the alternative was developing against
`NewPrescreeningSession`'s `crypto.randomUUID()`, which names no session in
any database -- so every authenticated call 401s and the failure looks like
a bug in the code under test rather than a missing fixture.

Writes straight through `SessionService` rather than calling the mock
booking endpoint over HTTP, so it needs no running server and no seeded
doctor/Calendar mapping. Use `make dev-link`.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import typer
from motor.motor_asyncio import AsyncIOMotorClient

from app.core.config import get_settings
from app.core.constants import Sex
from app.integrations.google_calendar import GoogleCalendarClient
from app.integrations.storage import ObjectStorageClient
from app.repositories.doctor_repository import DoctorRepository
from app.repositories.session_repository import SessionRepository
from app.schemas.booking import BookingConfirmedWebhook
from app.services.notification_service import NotificationService
from app.services.session_service import SessionService

cli = typer.Typer()


@cli.command()
def create(
    patient_name: str = "Test Patient",
    physician: str = "Dr. Mehta",
    hours_from_now: int = 24,
) -> None:
    """Create one pre-screening session and print its intake URL."""
    asyncio.run(_create(patient_name, physician, hours_from_now))


async def _create(patient_name: str, physician: str, hours_from_now: int) -> None:
    settings = get_settings()
    client = AsyncIOMotorClient(settings.mongo_uri, tz_aware=True)
    database = client[settings.mongo_db_name]

    sessions = SessionRepository(database)
    await sessions.ensure_indexes()

    service = SessionService(
        sessions,
        NotificationService(),
        settings,
        DoctorRepository(database),
        GoogleCalendarClient(settings),
        ObjectStorageClient(settings),
    )

    session = await service.create_from_booking(
        BookingConfirmedWebhook(
            appointment_id=f"dev_{uuid.uuid4().hex[:8]}",
            patient_name=patient_name,
            patient_id=f"pt_{uuid.uuid4().hex[:6]}",
            # Obviously synthetic, per the project's sensitive-test-data rule.
            date_of_birth=datetime(1990, 1, 1, tzinfo=UTC).date(),
            sex=Sex.OTHER,
            physician=physician,
            scheduled_at=datetime.now(UTC) + timedelta(hours=hours_from_now),
            booking_reason="Short of breath on stairs",
        )
    )

    typer.echo("")
    typer.echo(f"  session_id : {session.session_id}")
    typer.echo(f"  status     : {session.status}")
    typer.echo(f"  intake url : {service.build_intake_url(session.session_id)}")
    typer.echo("")
    if not settings.patient_app_base_url:
        typer.echo("  PATIENT_APP_BASE_URL is unset -- prefix the path above with your UI origin.")
        typer.echo("")

    client.close()


if __name__ == "__main__":
    cli()

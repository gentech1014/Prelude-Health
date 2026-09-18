"""Run one slot/session reconciliation pass by hand.

The same `SweeperService.run_once()` the FastAPI lifespan runs on a
60-second timer (see `app.main`), exposed as a CLI for manual/demo runs
and as the forward-compatible seam for a future real CronJob -- no code
change needed there beyond invoking this instead of a lifespan task.

Builds its own Mongo client via `MongoConnection`, not a bare
`AsyncIOMotorClient(...)` the way `seed_doctors.py` does: this script
compares timestamps, and a client without `tz_aware=True` reads them back
naive, raising `TypeError` the moment they meet `datetime.now(UTC)`.
"""

import asyncio

import structlog
import typer

from app.core.config import get_settings
from app.db.mongo import MongoConnection
from app.repositories.scheduling_event_repository import SchedulingEventRepository
from app.repositories.session_repository import SessionRepository
from app.repositories.slot_repository import SlotRepository
from app.services.sweeper_service import SweeperService

log = structlog.get_logger(__name__)

cli = typer.Typer()


@cli.command()
def sweep() -> None:
    """Reclaim abandoned slot holds and expire stale sessions, once."""
    asyncio.run(_sweep())


async def _sweep() -> None:
    settings = get_settings()
    mongo = MongoConnection(settings)
    await mongo.connect()

    sweeper = SweeperService(
        SessionRepository(mongo.db),
        SlotRepository(mongo.db),
        SchedulingEventRepository(mongo.db),
        settings,
    )
    result = await sweeper.run_once()
    log.info("manual_sweep_complete", **result.model_dump())

    await mongo.disconnect()


if __name__ == "__main__":
    cli()

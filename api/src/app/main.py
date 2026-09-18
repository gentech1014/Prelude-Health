"""FastAPI application factory and lifespan.

The lifespan owns four boot-time side effects: connecting to Mongo,
preloading the question bank into memory (so the live agent's
`start_prescreening` tool never makes a live DB call mid-conversation),
opening one shared `httpx.AsyncClient` for outbound calls to the booking
platform, and filtering the voice stream's teardown noise out of the log
(see `app.core.stream_noise`).
"""

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pymongo.errors import PyMongoError

from app.api.v1.router import api_router
from app.api.ws.intake import router as ws_router
from app.core.config import Settings, get_settings
from app.core.exceptions import DatabaseUnavailableError, PrescreeningError
from app.core.logging import configure_logging
from app.core.secrets_loader import load_secrets_into_env
from app.core.stream_noise import install_stream_noise_filter
from app.db.mongo import MongoConnection
from app.integrations.google_calendar import GoogleCalendarClient
from app.repositories.doctor_registration_repository import DoctorRegistrationRepository
from app.repositories.doctor_repository import DoctorRepository
from app.repositories.question_bank_repository import QuestionBankRepository
from app.repositories.scheduling_event_repository import SchedulingEventRepository
from app.repositories.session_repository import SessionRepository
from app.repositories.slot_repository import SlotRepository
from app.repositories.sweeper_lock_repository import SweeperLockRepository
from app.repositories.ws_ticket_repository import WsTicketRepository
from app.services.moss_question_bank import MossQuestionBankClient
from app.services.question_bank_service import QuestionBankService
from app.services.sweeper_service import SweeperService

log = structlog.get_logger(__name__)

_LOCAL_DEV_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]
"""Vite's default dev origins. Used only when `allowed_origins` is unset."""


def _warn_on_unusable_aws_config(settings: Settings) -> None:
    """Say at boot what would otherwise fail mid-call, on a patient.

    Both of these have cost a live call already. Warnings rather than a
    refusal to start: the report, booking and consent paths all work
    without Bedrock, so a deployment that is only serving those should not
    be blocked -- but nobody should have to read a `403` or a
    `ValidationException` out of a call transcript to find out.
    """
    if not settings.aws_access_key_id or not settings.aws_secret_access_key:
        log.warning("aws_credentials_not_configured")

    if "application-inference-profile" in settings.bidi_model_id:
        log.warning(
            "bidi_model_id_is_an_inference_profile",
            detail=(
                "InvokeModelWithBidirectionalStream rejects application "
                "inference profile ARNs. Use a bare model id, e.g. "
                "amazon.nova-2-sonic-v1:0."
            ),
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Connect to Mongo, preload the question bank, open the shared HTTP
    client, and start the sweeper task on startup."""
    load_secrets_into_env()
    settings = get_settings()
    configure_logging(settings)

    mongo = MongoConnection(settings)
    await mongo.connect()

    # Indexes before anything reads or writes: the unique index on
    # appointment_id is what makes webhook idempotency safe under a race,
    # and the compound index on (physician, starts_at) plays the same
    # role for slot creation.
    await SessionRepository(mongo.db).ensure_indexes()
    await DoctorRepository(mongo.db).ensure_indexes()
    # The unique index on `state` is what makes a doctor-registration
    # callback single-use, and the TTL index is what stops abandoned
    # consent attempts accumulating.
    await DoctorRegistrationRepository(mongo.db).ensure_indexes()
    # The unique index here is the whole enforcement mechanism for
    # single-use WebSocket tickets; without it a ticket replays freely.
    await WsTicketRepository(mongo.db).ensure_indexes()

    question_bank_repository = QuestionBankRepository(mongo.db)
    await question_bank_repository.ensure_indexes()

    # Warns rather than fails on a reason with no reference questions: the
    # agent writes its own questions from the coverage brief, so an empty
    # category is a reason nobody has been screened for yet, not a
    # misconfiguration worth refusing to start over.
    # Optional and additive: only constructed when this deployment has its
    # own Moss project configured. Left blank, `question_bank_service`
    # behaves exactly as it always has -- see `MossQuestionBankClient`.
    moss_client = None
    if settings.moss_project_id and settings.moss_project_key:
        moss_client = MossQuestionBankClient(settings)
        await moss_client.ensure_loaded()

    question_bank_service = QuestionBankService(question_bank_repository, moss_client)
    await question_bank_service.preload()

    _warn_on_unusable_aws_config(settings)

    # Installed on the running loop, not at import: it wraps whatever
    # exception handler is already there. See `app.core.stream_noise` for
    # what it filters and why nothing else is affected.
    restore_exception_handlers = install_stream_noise_filter()

    app.state.mongo = mongo
    app.state.question_bank_service = question_bank_service
    app.state.http_client = httpx.AsyncClient(timeout=10.0)
    # Built once: loading/validating the service-account credentials file
    # on every request would be wasted work for a client with no
    # per-request state. See `app.integrations.google_calendar` for why a
    # missing credentials file degrades to a no-op instead of failing boot.
    app.state.calendar_client = GoogleCalendarClient(settings)

    sweep_instance_id = uuid.uuid4().hex
    sweep_task = asyncio.create_task(_sweep_loop(mongo, settings, sweep_instance_id))

    yield

    sweep_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await sweep_task
    await app.state.http_client.aclose()
    await mongo.disconnect()
    restore_exception_handlers()


async def _sweep_loop(mongo: MongoConnection, settings: Settings, instance_id: str) -> None:
    """Run `SweeperService.run_once()` on a timer, one replica at a time.

    Every replica runs this same task; `SweeperLockRepository` decides
    which one actually executes a given tick (see its module docstring),
    so scaling to more than one instance costs no change here.
    """
    lock_repository = SweeperLockRepository(mongo.db)
    await lock_repository.ensure_seeded()
    sweeper = SweeperService(
        SessionRepository(mongo.db),
        SlotRepository(mongo.db),
        SchedulingEventRepository(mongo.db),
        settings,
    )
    while True:
        try:
            if await lock_repository.try_acquire(instance_id, settings.sweep_interval_seconds):
                await sweeper.run_once()
        except Exception:
            log.exception("sweep_tick_failed")
        await asyncio.sleep(settings.sweep_interval_seconds)


def register_exception_handlers(app: FastAPI) -> None:
    """Install the two handlers that decide what a failure looks like to a caller.

    A named function rather than inline registration so a test can mount
    them onto a bare app and check the translation itself. The bug this
    guards against is a handler simply not being registered, and a test
    that re-declares the handlers it is testing cannot catch that.
    """

    @app.exception_handler(PrescreeningError)
    async def handle_prescreening_error(request: Request, exc: PrescreeningError) -> JSONResponse:
        """Translate any domain error into an HTTP response using its `status_code`.

        Keeps route handlers free of try/except HTTPException translation
        -- they just raise the domain error and this handler does the rest.
        """
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})

    @app.exception_handler(PyMongoError)
    async def handle_database_error(request: Request, exc: PyMongoError) -> JSONResponse:
        """Answer 503 when the database is unreachable, instead of crashing the request.

        Without this, every driver-level failure escaped to Starlette's
        error middleware as an unhandled exception: the client got a bare
        500 with no body it could act on, and the log got a sixty-frame
        traceback through pymongo's retry machinery that said nothing the
        first line did not -- one per request, for as long as the link was
        degraded.

        The driver's own message is deliberately not returned to the
        client. It carries the replica-set topology, every node's hostname
        and the internal resolver's address: infrastructure detail that
        belongs in the log, not in an HTTP body served to a browser.
        """
        log.warning(
            "database_unavailable",
            path=request.url.path,
            error=type(exc).__name__,
            # One line, not a traceback: the type and the driver's summary
            # are the whole diagnosis, and the frames are always the same.
            detail=str(exc)[:300],
        )
        error = DatabaseUnavailableError()
        return JSONResponse(status_code=error.status_code, content={"detail": str(error)})


def create_app() -> FastAPI:
    """Build the FastAPI application."""
    app = FastAPI(title="Pre-Screening Agent", lifespan=lifespan)

    # CORS must allow credentials: the patient app authenticates with an
    # HttpOnly cookie, and a browser sends that cross-origin only when both
    # `allow_credentials` here and `withCredentials` on the client are set.
    # A wildcard origin is illegal in that combination -- browsers reject
    # `Access-Control-Allow-Origin: *` alongside credentials -- so an
    # unconfigured `allowed_origins` falls back to the local dev origins
    # rather than to `*`, which would look permissive and work nowhere.
    settings = get_settings()
    configured = [o.strip() for o in settings.allowed_origins.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=configured or _LOCAL_DEV_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type"],
    )

    app.include_router(api_router)
    app.include_router(ws_router)

    register_exception_handlers(app)

    @app.get("/healthz", tags=["ops"])
    async def healthz() -> dict[str, str]:
        """Liveness probe. Deliberately does not touch Mongo -- a slow DB should not flip readiness."""
        return {"status": "ok"}

    return app


app = create_app()

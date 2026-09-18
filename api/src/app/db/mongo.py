"""Motor (async Mongo) client lifecycle.

An async driver is required here, not just preferred: hook/output
callbacks on the live bidi agent run on the same event loop that carries
the patient's audio, so a blocking pymongo call would stall the call.
"""

import structlog
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.core.config import Settings

log = structlog.get_logger(__name__)


class MongoConnection:
    """Owns the Motor client for the process lifetime.

    Constructed once during the FastAPI lifespan and closed on shutdown;
    never instantiated per-request.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: AsyncIOMotorClient | None = None

    async def connect(self) -> None:
        """Open the Mongo client connection pool.

        `tz_aware=True` is not optional here. BSON stores datetimes as UTC
        without an offset, so the default driver hands them back
        timezone-naive -- and comparing one of those to `datetime.now(UTC)`
        raises TypeError rather than returning a wrong answer. Every
        timestamp in this service is written as UTC-aware, so the read path
        has to match.

        Timeouts are set explicitly rather than left to the driver, whose
        defaults (30s server selection, 20s connect, no socket timeout) are
        batch-job numbers. Against a degraded network they turn a single
        unreachable replica into requests that hang for half a minute and
        then 500 -- observed against Atlas over a slow link, where the TLS
        handshake to the primary exceeded the connect timeout, the driver
        marked it Unknown, and every read blocked for the full selection
        window before raising `ServerSelectionTimeoutError`. Failing in
        seconds lets the caller retry and lets the API answer 503 while the
        patient is still watching.

        Motor connects lazily, so this issues a `ping` to fail fast at
        startup rather than at the first patient's call.

        Also warns (does not block startup) if running in production
        against a URI that is not `mongodb+srv://` -- Atlas's SRV scheme
        already implies TLS-in-transit and Atlas rejects plaintext
        connections outright, so this only catches a genuinely
        misconfigured `MONGO_URI` (e.g. a stray local/plaintext value)
        before it causes a confusing failure elsewhere.
        """
        if self._settings.app_env == "production" and not self._settings.mongo_uri.startswith(
            "mongodb+srv://"
        ):
            log.warning("mongo_uri_not_using_srv_scheme_in_production")

        self._client = AsyncIOMotorClient(
            self._settings.mongo_uri,
            tz_aware=True,
            serverSelectionTimeoutMS=self._settings.mongo_server_selection_timeout_ms,
            connectTimeoutMS=self._settings.mongo_connect_timeout_ms,
            socketTimeoutMS=self._settings.mongo_socket_timeout_ms,
        )
        await self._client.admin.command("ping")

    async def disconnect(self) -> None:
        """Close the Mongo client connection pool gracefully."""
        if self._client is not None:
            self._client.close()
            self._client = None

    @property
    def db(self) -> AsyncIOMotorDatabase:
        """The configured database handle.

        Raises RuntimeError if accessed before `connect()`.
        """
        if self._client is None:
            raise RuntimeError("MongoConnection.connect() has not been called")
        return self._client[self._settings.mongo_db_name]

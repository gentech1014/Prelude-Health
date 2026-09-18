"""How the API answers when the database cannot be reached.

Not a hypothetical. Running against Atlas over a degraded link produced a
`ServerSelectionTimeoutError` on `/booking/providers` -- the primary sat
behind a TLS handshake that would not complete inside the connect timeout,
so the driver marked it Unknown and no member matched `Primary()`.

What made that worse than the outage itself was the reaction to it.
`PyMongoError` is not a `PrescreeningError`, so it escaped every handler,
surfaced as an unhandled ASGI exception with a bare 500 and no body the
frontend could act on, and wrote a sixty-frame traceback through pymongo's
retry machinery into the log on *every* request.

These pin the replacement: a 503 the caller can retry, a body that says so,
and nothing about the cluster's internals in it.
"""

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mongomock_motor import AsyncMongoMockClient
from pymongo.errors import ServerSelectionTimeoutError

from app.api.v1.booking import router
from app.core.config import Settings, get_settings
from app.main import register_exception_handlers
from app.repositories.doctor_repository import DoctorRepository

_DRIVER_MESSAGE = (
    'No replica set members match selector "Primary()", Timeout: 30s, Topology '
    "Description: <TopologyDescription id: 6aa1918f, topology_type: "
    "ReplicaSetNoPrimary, servers: [<ServerDescription "
    "('ac-rp5zron-shard-00-01.gtkryau.mongodb.net', 27017) server_type: Unknown, "
    "error=NetworkTimeout('SSL handshake failed')>]>"
)
"""A real one, trimmed. The hostnames in it are the point -- see the leak test."""


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """The booking router, wired to the real handlers, with the database unreachable.

    `register_exception_handlers` is imported rather than re-declared here.
    The failure being tested is a handler that was never registered, and a
    test that installs its own copy of the handler cannot catch that.

    The driver error is raised from the repository rather than by faking a
    replica-set partition: what is under test is the app's reaction to a
    `PyMongoError` reaching a route, and producing a real one would be
    testing pymongo.
    """

    async def _unreachable(self: DoctorRepository, category: object) -> list[object]:
        raise ServerSelectionTimeoutError(_DRIVER_MESSAGE)

    monkeypatch.setattr(DoctorRepository, "list_for_category", _unreachable)

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    register_exception_handlers(app)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    class _Mongo:
        db = AsyncMongoMockClient(tz_aware=True)["database_outage_test"]

    app.state.mongo = _Mongo()
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None,  # isolate from the developer's real .env -- see tests/conftest.py
        intake_link_secret="outage-test-secret-32-characters-ok",  # noqa: S106 - test fixture
        booking_webhook_secret="outage-test-webhook-secret-32-chars",  # noqa: S106 - test fixture
        physician_api_key="outage-test-physician-key-32-charact",  # noqa: S106 - test fixture
        clinic_timezone="UTC",
    )

    with TestClient(app) as test_client:
        yield test_client


def test_an_unreachable_database_answers_503_not_500(client: TestClient) -> None:
    """503 says "try this again"; 500 says "this request will never work"."""
    response = client.get("/api/v1/booking/providers")

    assert response.status_code == 503
    assert "temporarily unreachable" in response.json()["detail"]


def test_the_error_body_leaks_no_cluster_topology(client: TestClient) -> None:
    """The driver's message names every node and the internal resolver.

    That belongs in the log, not in a response body served to a browser.
    """
    body = client.get("/api/v1/booking/providers").text

    assert "mongodb.net" not in body
    assert "ReplicaSetNoPrimary" not in body
    assert "SSL handshake" not in body


def test_a_route_that_needs_no_database_still_answers(client: TestClient) -> None:
    """A slow database must not take the whole surface down with it.

    `/booking/visit-types` is served from constants, and `/healthz`
    deliberately never touches Mongo -- so an orchestrator does not restart
    a pod that is healthy and merely waiting on its database.
    """
    assert client.get("/healthz").status_code == 200
    assert len(client.get("/api/v1/booking/visit-types").json()) == 7

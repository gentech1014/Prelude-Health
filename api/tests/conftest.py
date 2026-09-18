"""Shared pytest fixtures.

Uses `mongomock-motor` for an in-memory Motor-compatible database so unit
and integration tests never touch a real MongoDB instance.

Every `Settings(...)` built anywhere in this test suite passes
`_env_file=None`. Without it, `Settings`' `model_config` still points at
the real `.env` in the project root (pytest's cwd), so any field a test
does not explicitly override silently picks up whatever the developer has
configured locally -- a real secret, a real bucket, a real Google
credentials path. Tests must be reproducible from environment variables
and explicit kwargs alone.
"""

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest
from mongomock_motor import AsyncMongoMockClient
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.config import Settings
from app.core.constants import PrescreeningCategory, Sex
from app.integrations.calendar_delivery import CalendarDelivery
from app.integrations.doctor_calendar import DoctorCalendarClient
from app.integrations.google_calendar import GoogleCalendarClient
from app.integrations.storage import ObjectStorageClient
from app.models.question_bank import BankQuestion, QuestionSource
from app.models.session import PatientRef, Session
from app.repositories.doctor_repository import DoctorRepository
from app.repositories.question_bank_repository import QuestionBankRepository
from app.repositories.scheduling_event_repository import SchedulingEventRepository
from app.repositories.session_repository import SessionRepository
from app.repositories.slot_repository import SlotRepository
from app.repositories.sweeper_lock_repository import SweeperLockRepository
from app.schemas.booking import BookingConfirmedWebhook


@pytest.fixture
def settings() -> Settings:
    """Settings with deterministic secrets, so token tests are reproducible.

    `google_service_account_file` is deliberately left unset: `GoogleCalendarClient`
    treats that as "not configured" and no-ops instead of touching the
    network, so tests never need real Google credentials.
    """
    return Settings(
        _env_file=None,  # isolate from the developer's real .env -- see module docstring
        intake_link_secret="test-secret-that-is-long-enough-32",  # noqa: S106 - test fixture
        patient_app_base_url="https://example.test",
        booking_webhook_secret="test-webhook-secret-long-enough-32",  # noqa: S106 - test fixture
        physician_api_key="test-physician-api-key-long-enough-32",  # noqa: S106 - test fixture
        # Placeholders, never used against real AWS: credentials now come
        # from configuration rather than boto3's ambient chain, so a test
        # that reaches an AWS client must supply them here rather than
        # inheriting whatever the developer's machine happens to hold.
        aws_access_key_id="testing",
        aws_secret_access_key="testing",  # noqa: S106 - test fixture
    )


@pytest.fixture
async def mongo_db() -> AsyncIOMotorDatabase:
    """An isolated, in-memory Mongo-compatible database for a single test.

    `tz_aware=True` mirrors the real client in `app.db.mongo`: BSON has no
    offset, so without it stored timestamps read back naive and every
    comparison against an aware `now()` raises TypeError.
    """
    client = AsyncMongoMockClient(tz_aware=True)
    return client["test_db"]


@pytest.fixture
async def question_bank_repository(
    mongo_db: AsyncIOMotorDatabase,
) -> QuestionBankRepository:
    """An empty `QuestionBankRepository`, which is now a legitimate state.

    A reason nobody has been screened for yet holds nothing, and the call
    handles that by writing the whole screening itself -- so an empty bank
    is the cold-start case, not a broken fixture."""
    return QuestionBankRepository(mongo_db)


@pytest.fixture
async def seeded_question_bank_repository(
    mongo_db: AsyncIOMotorDatabase,
) -> AsyncIterator[QuestionBankRepository]:
    """A `QuestionBankRepository` pre-populated with one question per reason."""
    repository = QuestionBankRepository(mongo_db)
    await repository.seed(
        [
            BankQuestion.build(
                category, f"sample question for {category.value}", source=QuestionSource.SEED
            )
            for category in PrescreeningCategory
        ]
    )
    yield repository


@pytest.fixture
async def session_repository(mongo_db: AsyncIOMotorDatabase) -> SessionRepository:
    """An empty `SessionRepository` backed by the in-memory database."""
    return SessionRepository(mongo_db)


@pytest.fixture
async def doctor_repository(mongo_db: AsyncIOMotorDatabase) -> DoctorRepository:
    """An empty `DoctorRepository` backed by the in-memory database."""
    return DoctorRepository(mongo_db)


@pytest.fixture
async def slot_repository(mongo_db: AsyncIOMotorDatabase) -> SlotRepository:
    """An empty `SlotRepository`, indexes created -- several tests rely on
    the unique `(physician, starts_at)` index actually being enforced."""
    repository = SlotRepository(mongo_db)
    await repository.ensure_indexes()
    return repository


@pytest.fixture
async def scheduling_event_repository(mongo_db: AsyncIOMotorDatabase) -> SchedulingEventRepository:
    """An empty `SchedulingEventRepository` backed by the in-memory database."""
    repository = SchedulingEventRepository(mongo_db)
    await repository.ensure_indexes()
    return repository


@pytest.fixture
async def sweeper_lock_repository(mongo_db: AsyncIOMotorDatabase) -> SweeperLockRepository:
    """A `SweeperLockRepository` with its one lease document already seeded."""
    repository = SweeperLockRepository(mongo_db)
    await repository.ensure_seeded()
    return repository


@pytest.fixture
def calendar_client(settings: Settings) -> GoogleCalendarClient:
    """A `GoogleCalendarClient` with no service-account file -- always a no-op.

    Exercises the disabled path deliberately: no test in this suite should
    ever need real Google credentials or make a real network call.
    """
    return GoogleCalendarClient(settings)


@pytest.fixture
def calendar_delivery(
    settings: Settings, calendar_client: GoogleCalendarClient
) -> CalendarDelivery:
    """Delivery with neither route configured -- every append is a no-op.

    The real choice between the doctor's own grant and the shared service
    account is unit-tested in `tests/unit/test_calendar_delivery.py`; here
    it only has to be the right type and reach the network never.
    """
    return CalendarDelivery(DoctorCalendarClient(settings), calendar_client)


@pytest.fixture
def storage(settings: Settings) -> ObjectStorageClient:
    """An `ObjectStorageClient` with no real bucket -- fine for tests that
    never exercise the upload/download path (boto3 clients hold no
    connection until first used). Tests that do exercise it wrap the call
    in `moto.mock_aws` and create the bucket themselves."""
    return ObjectStorageClient(settings)


@pytest.fixture
def booking_webhook() -> BookingConfirmedWebhook:
    """A representative booking-confirmed payload."""
    return BookingConfirmedWebhook(
        appointment_id="appt_4471",
        patient_name="Asha Rao",
        patient_id="pt_2290",
        date_of_birth=date(1990, 5, 14),
        sex=Sex.FEMALE,
        physician="Dr. Mehta",
        scheduled_at=datetime(2026, 9, 3, 9, 30, tzinfo=UTC),
        booking_reason="Short of breath on stairs",
        contact_phone="+10000000000",
    )


@pytest.fixture
def sample_session() -> Session:
    """A session in its initial state, as `create_from_booking` would build it."""
    now = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    return Session(
        session_id="sess_8f2c1a",
        appointment_id="appt_4471",
        patient=PatientRef(
            name="Asha Rao",
            patient_id="pt_2290",
            date_of_birth=datetime(1990, 5, 14, tzinfo=UTC),
            sex=Sex.FEMALE,
        ),
        physician="Dr. Mehta",
        appointment_datetime=datetime(2026, 9, 3, 9, 30, tzinfo=UTC),
        booking_reason="Short of breath on stairs",
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def non_utc_session() -> Session:
    """A session whose appointment is 10:00 IST (+05:30) -- every existing
    fixture in this suite happens to sit at UTC+0, which is exactly why
    the "speaks the raw UTC hour as if it were local" bug went uncaught.
    Stored `appointment_datetime` is UTC-aware (04:30 UTC == 10:00 IST);
    `appointment_timezone` is what makes 10:00 the correct render."""
    now = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    return Session(
        session_id="sess_ist_test",
        appointment_id="appt_ist_test",
        patient=PatientRef(
            name="Asha Rao",
            patient_id="pt_2290",
            date_of_birth=datetime(1990, 5, 14, tzinfo=UTC),
            sex=Sex.FEMALE,
        ),
        physician="Dr. Mehta",
        appointment_datetime=datetime(2026, 9, 8, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata")),
        appointment_timezone="Asia/Kolkata",
        booking_reason="Short of breath on stairs",
        created_at=now,
        updated_at=now,
    )

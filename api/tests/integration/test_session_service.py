"""Integration tests for `SessionService` lifecycle orchestration."""

import pytest

from app.core.config import Settings
from app.core.constants import SessionState
from app.core.exceptions import InvalidSessionStateError
from app.core.security import verify_intake_token
from app.integrations.calendar_delivery import CalendarDelivery
from app.integrations.storage import ObjectStorageClient
from app.repositories.doctor_repository import DoctorRepository
from app.repositories.session_repository import SessionRepository
from app.schemas.booking import BookingConfirmedWebhook
from app.services.session_service import SessionService


class _RecordingNotificationService:
    """Captures the intake link instead of sending an SMS."""

    def __init__(self) -> None:
        self.sent: list[tuple[str | None, str]] = []

    async def send_intake_link(self, contact_phone: str | None, intake_url: str) -> None:
        self.sent.append((contact_phone, intake_url))


@pytest.fixture
def notifications() -> _RecordingNotificationService:
    return _RecordingNotificationService()


@pytest.fixture
def service(
    session_repository: SessionRepository,
    notifications: _RecordingNotificationService,
    settings: Settings,
    doctor_repository: DoctorRepository,
    calendar_delivery: CalendarDelivery,
    storage: ObjectStorageClient,
) -> SessionService:
    return SessionService(
        session_repository,
        notifications,  # type: ignore[arg-type]
        settings,
        doctor_repository,
        calendar_delivery,
        storage,
    )


async def test_booking_creates_a_session_carrying_appointment_context(
    service: SessionService, booking_webhook: BookingConfirmedWebhook
) -> None:
    """Appointment time and booking reason must reach the session.

    The live agent's system prompt greets the patient using both, so a
    session created without them cannot open the call correctly.
    """
    session = await service.create_from_booking(booking_webhook)

    assert session.appointment_datetime == booking_webhook.scheduled_at
    assert session.booking_reason == "Short of breath on stairs"
    assert session.patient.name == "Asha Rao"
    assert session.physician == "Dr. Mehta"


async def test_booking_sends_a_link_carrying_a_valid_token(
    service: SessionService,
    booking_webhook: BookingConfirmedWebhook,
    notifications: _RecordingNotificationService,
    settings: Settings,
) -> None:
    """The delivered link must actually open the session it was minted for."""
    session = await service.create_from_booking(booking_webhook)

    assert len(notifications.sent) == 1
    phone, url = notifications.sent[0]
    assert phone == "+10000000000"

    token = url.split("token=", 1)[1]
    assert verify_intake_token(session.session_id, token, settings.intake_link_secret)


async def test_booking_ends_in_notification_sent(
    service: SessionService,
    booking_webhook: BookingConfirmedWebhook,
    session_repository: SessionRepository,
) -> None:
    """BOOKING_CREATED -> AI_LINK_READY -> NOTIFICATION_SENT runs to completion."""
    session = await service.create_from_booking(booking_webhook)

    stored = await session_repository.get_by_id(session.session_id)
    assert stored is not None
    assert stored.status is SessionState.NOTIFICATION_SENT


async def test_duplicate_booking_webhook_is_idempotent(
    service: SessionService,
    booking_webhook: BookingConfirmedWebhook,
    notifications: _RecordingNotificationService,
) -> None:
    """A retried webhook must reuse the session and not re-notify the patient.

    Delivery retries are normal, and a second link would both confuse the
    patient and orphan the first session.
    """
    first = await service.create_from_booking(booking_webhook)
    second = await service.create_from_booking(booking_webhook)

    assert first.session_id == second.session_id
    assert len(notifications.sent) == 1


async def test_granting_consent_moves_the_session_in_progress(
    service: SessionService, booking_webhook: BookingConfirmedWebhook
) -> None:
    """Affirmative consent is what unlocks the live call."""
    created = await service.create_from_booking(booking_webhook)

    session = await service.record_consent(created.session_id, given=True)

    assert session.status is SessionState.IN_PROGRESS
    assert session.consent is not None
    assert session.consent.given is True


async def test_declining_consent_terminates_the_session(
    service: SessionService, booking_webhook: BookingConfirmedWebhook
) -> None:
    """A declined consent must end the journey, not leave it open."""
    created = await service.create_from_booking(booking_webhook)

    session = await service.record_consent(created.session_id, given=False)

    assert session.status is SessionState.DECLINED
    assert session.consent is not None
    assert session.consent.given is False


async def test_declined_consent_is_recorded_not_just_flagged(
    service: SessionService,
    booking_webhook: BookingConfirmedWebhook,
    session_repository: SessionRepository,
) -> None:
    """The decision and its timestamp persist, so the refusal is auditable."""
    created = await service.create_from_booking(booking_webhook)
    await service.record_consent(created.session_id, given=False)

    stored = await session_repository.get_by_id(created.session_id)
    assert stored is not None
    assert stored.consent is not None
    assert stored.consent.recorded_at is not None


async def test_consent_cannot_be_replayed_after_the_session_moved_on(
    service: SessionService, booking_webhook: BookingConfirmedWebhook
) -> None:
    """A finished session's consent decision must not be re-triggerable.

    Without this guard, a replayed or forged consent request against a
    DECLINED (or COMPLETED, or any later-state) session could silently
    overwrite a decision the patient already made.
    """
    created = await service.create_from_booking(booking_webhook)
    await service.record_consent(created.session_id, given=False)  # -> DECLINED

    with pytest.raises(InvalidSessionStateError):
        await service.record_consent(created.session_id, given=True)

"""Integration tests for `SessionRepository` against an in-memory Mongo."""

from datetime import UTC, datetime

from app.core.constants import SessionState, SymptomCategory
from app.models.consent import Consent
from app.models.report import Finding, PreScreeningReport
from app.models.session import Session
from app.models.transcript import ConversationTurn
from app.repositories.session_repository import SessionRepository


async def test_create_then_read_round_trips_every_field(
    session_repository: SessionRepository, sample_session: Session
) -> None:
    """A stored session must come back as an equivalent domain object.

    Guards the Mongo `_id` projection: without it, reading back would
    carry a stray field into the model.
    """
    await session_repository.create(sample_session)

    loaded = await session_repository.get_by_id(sample_session.session_id)

    assert loaded is not None
    assert loaded.patient.name == "Asha Rao"
    assert loaded.appointment_datetime == sample_session.appointment_datetime
    assert loaded.booking_reason == "Short of breath on stairs"
    assert loaded.status is SessionState.BOOKING_CREATED


async def test_unknown_session_returns_none(session_repository: SessionRepository) -> None:
    """A missing session is None, not an exception."""
    assert await session_repository.get_by_id("nope") is None


async def test_lookup_by_appointment_id_backs_webhook_idempotency(
    session_repository: SessionRepository, sample_session: Session
) -> None:
    """Retried booking webhooks find the existing session through this lookup."""
    await session_repository.create(sample_session)

    found = await session_repository.get_by_appointment_id("appt_4471")

    assert found is not None
    assert found.session_id == sample_session.session_id


async def test_turns_append_in_order(
    session_repository: SessionRepository, sample_session: Session
) -> None:
    """The transcript is append-only and must preserve call order."""
    await session_repository.create(sample_session)

    for index, (role, text) in enumerate(
        [("assistant", "Hi Asha."), ("user", "Out of breath."), ("assistant", "How long?")]
    ):
        await session_repository.append_turn(
            sample_session.session_id,
            ConversationTurn(
                role=role,  # type: ignore[arg-type]
                text=text,
                ts=datetime(2026, 9, 1, 9, index, tzinfo=UTC),
            ),
        )

    loaded = await session_repository.get_by_id(sample_session.session_id)
    assert loaded is not None
    assert [t.text for t in loaded.turns] == ["Hi Asha.", "Out of breath.", "How long?"]


async def test_status_change_returns_the_updated_session(
    session_repository: SessionRepository, sample_session: Session
) -> None:
    """Callers get the post-update document without a second query."""
    await session_repository.create(sample_session)

    updated = await session_repository.set_status(
        sample_session.session_id, SessionState.IN_PROGRESS
    )

    assert updated is not None
    assert updated.status is SessionState.IN_PROGRESS
    assert updated.updated_at > sample_session.updated_at


async def test_consent_is_persisted(
    session_repository: SessionRepository, sample_session: Session
) -> None:
    """Consent must survive the round trip -- the call is gated on it."""
    await session_repository.create(sample_session)

    await session_repository.set_consent(
        sample_session.session_id,
        Consent(given=True, recorded_at=datetime(2026, 9, 1, 9, 0, tzinfo=UTC)),
    )

    loaded = await session_repository.get_by_id(sample_session.session_id)
    assert loaded is not None
    assert loaded.consent is not None
    assert loaded.consent.given is True


async def test_report_is_attached_and_reloads_as_a_model(
    session_repository: SessionRepository, sample_session: Session
) -> None:
    """The stored report must rehydrate, including its gaps list."""
    await session_repository.create(sample_session)
    report = PreScreeningReport(
        chief_concern="Shortness of breath on exertion",
        category=SymptomCategory.LUNG,
        clinical_summary="Reports exertional dyspnea for two weeks. Smoking history not confirmed.",
        findings=[Finding(question="How long?", answer="Two weeks")],
        gaps=["Smoking history not confirmed"],
    )

    await session_repository.set_report(sample_session.session_id, report.model_dump())

    loaded = await session_repository.get_by_id(sample_session.session_id)
    assert loaded is not None
    assert loaded.report is not None
    assert loaded.report.gaps == ["Smoking history not confirmed"]


async def test_summary_failure_records_status_and_reason_together(
    session_repository: SessionRepository, sample_session: Session
) -> None:
    """A failed session must never be observed without an explanation."""
    await session_repository.create(sample_session)

    updated = await session_repository.set_summary_failure(sample_session.session_id, "max_tokens")

    assert updated is not None
    assert updated.status is SessionState.SUMMARY_FAILED
    assert updated.summary_error == "max_tokens"
    assert updated.report is None


async def test_updating_a_missing_session_returns_none(
    session_repository: SessionRepository,
) -> None:
    """Updates against a deleted session surface as None, not a silent upsert."""
    assert await session_repository.set_status("ghost", SessionState.COMPLETED) is None

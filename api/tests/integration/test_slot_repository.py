"""Integration tests for `SlotRepository`'s CAS-guarded reads and writes.

Every write here is a single `update_one` checked via `matched_count`,
never `find_one_and_update` with an `{"_id": 0}` projection -- see the
module docstring on `app.repositories.slot_repository` for the verified
`mongomock` bug that makes the latter unsafe whenever the guard field is
also the mutated field. These tests exist specifically to prove the safe
idiom actually behaves correctly under `mongomock`, not just in theory.
"""

from datetime import UTC, datetime, timedelta

from app.core.constants import SlotStatus
from app.models.slot import AppointmentSlot
from app.repositories.slot_repository import SlotRepository

_PHYSICIAN = "Dr. Mehta"
_NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def _free_slot(slot_id: str = "slot_1", *, starts_at: datetime | None = None) -> AppointmentSlot:
    return AppointmentSlot(
        slot_id=slot_id,
        physician=_PHYSICIAN,
        starts_at=starts_at or (_NOW + timedelta(hours=2)),
        ends_at=(starts_at or (_NOW + timedelta(hours=2))) + timedelta(minutes=30),
        status=SlotStatus.FREE,
        updated_at=_NOW,
    )


async def test_claim_moves_a_free_slot_to_held(slot_repository: SlotRepository) -> None:
    await slot_repository.create_if_missing(_free_slot())

    claimed = await slot_repository.claim(
        "slot_1",
        _PHYSICIAN,
        "sess_1",
        after=_NOW,
        before=_NOW + timedelta(hours=6),
        held_until=_NOW + timedelta(seconds=90),
    )

    assert claimed is True
    slot = await slot_repository.get_by_id("slot_1")
    assert slot is not None
    assert slot.status is SlotStatus.HELD
    assert slot.session_id == "sess_1"
    assert slot.version == 1


async def test_claim_fails_when_another_session_already_holds_it(
    slot_repository: SlotRepository,
) -> None:
    """The actual race this whole feature exists to prevent: two patients
    cannot both believe they hold the same slot."""
    still_future = datetime.now(UTC) + timedelta(seconds=90)
    await slot_repository.create_if_missing(_free_slot())
    first = await slot_repository.claim(
        "slot_1",
        _PHYSICIAN,
        "sess_1",
        after=_NOW,
        before=_NOW + timedelta(hours=6),
        held_until=still_future,
    )

    second = await slot_repository.claim(
        "slot_1",
        _PHYSICIAN,
        "sess_2",
        after=_NOW,
        before=_NOW + timedelta(hours=6),
        held_until=still_future,
    )

    assert first is True
    assert second is False
    slot = await slot_repository.get_by_id("slot_1")
    assert slot is not None
    assert slot.session_id == "sess_1"  # the second claim changed nothing


async def test_claim_treats_an_expired_hold_as_takeable(slot_repository: SlotRepository) -> None:
    """A HELD slot whose held_until has passed self-heals with no
    background job -- this predicate is the entire mechanism."""
    slot = _free_slot()
    slot.status = SlotStatus.HELD
    slot.session_id = "sess_abandoned"
    slot.held_until = datetime.now(UTC) - timedelta(seconds=1)
    await slot_repository.create_if_missing(slot)

    claimed = await slot_repository.claim(
        "slot_1",
        _PHYSICIAN,
        "sess_new",
        after=_NOW,
        before=_NOW + timedelta(hours=6),
        held_until=_NOW + timedelta(seconds=90),
    )

    assert claimed is True
    current = await slot_repository.get_by_id("slot_1")
    assert current is not None
    assert current.session_id == "sess_new"


async def test_claim_fails_outside_the_time_window(slot_repository: SlotRepository) -> None:
    """A slot that starts after the caller's own current appointment must
    never be claimable as "earlier" -- this is the guard against a stale
    or hallucinated slot_id moving the patient later instead."""
    await slot_repository.create_if_missing(_free_slot())

    claimed = await slot_repository.claim(
        "slot_1",
        _PHYSICIAN,
        "sess_1",
        after=_NOW,
        before=_NOW + timedelta(minutes=30),  # the slot starts 2h out -- outside this window
        held_until=_NOW,
    )

    assert claimed is False


async def test_release_refuses_when_session_id_does_not_match(
    slot_repository: SlotRepository,
) -> None:
    """A stale caller can never free a slot someone else now owns."""
    slot = _free_slot()
    slot.status = SlotStatus.BOOKED
    slot.session_id = "sess_owner"
    await slot_repository.create_if_missing(slot)

    released = await slot_repository.release(
        "slot_1", "sess_impostor", expected_status=SlotStatus.BOOKED
    )

    assert released is False
    current = await slot_repository.get_by_id("slot_1")
    assert current is not None
    assert current.status is SlotStatus.BOOKED
    assert current.session_id == "sess_owner"


async def test_release_frees_the_slot_the_owner_actually_holds(
    slot_repository: SlotRepository,
) -> None:
    slot = _free_slot()
    slot.status = SlotStatus.BOOKED
    slot.session_id = "sess_owner"
    await slot_repository.create_if_missing(slot)

    released = await slot_repository.release(
        "slot_1", "sess_owner", expected_status=SlotStatus.BOOKED
    )

    assert released is True
    current = await slot_repository.get_by_id("slot_1")
    assert current is not None
    assert current.status is SlotStatus.FREE
    assert current.session_id is None


async def test_promote_requires_the_slot_to_still_be_held_by_the_same_session(
    slot_repository: SlotRepository,
) -> None:
    slot = _free_slot()
    slot.status = SlotStatus.HELD
    slot.session_id = "sess_1"
    slot.held_until = _NOW + timedelta(seconds=90)
    await slot_repository.create_if_missing(slot)

    promoted = await slot_repository.promote("slot_1", "sess_1")
    wrong_session = await slot_repository.promote("slot_1", "sess_2")

    assert promoted is True
    assert wrong_session is False  # already BOOKED now, and for a different session anyway
    current = await slot_repository.get_by_id("slot_1")
    assert current is not None
    assert current.status is SlotStatus.BOOKED
    assert current.held_until is None


async def test_list_bookable_excludes_a_slot_still_actively_held(
    slot_repository: SlotRepository,
) -> None:
    slot = _free_slot()
    slot.status = SlotStatus.HELD
    slot.session_id = "sess_other"
    slot.held_until = datetime.now(UTC) + timedelta(seconds=60)  # not yet expired, for real
    await slot_repository.create_if_missing(slot)

    bookable = await slot_repository.list_bookable(
        _PHYSICIAN, after=_NOW, before=_NOW + timedelta(hours=6), limit=10
    )

    assert bookable == []


async def test_list_bookable_includes_free_and_expired_held_slots(
    slot_repository: SlotRepository,
) -> None:
    free_slot = _free_slot("slot_free", starts_at=_NOW + timedelta(hours=1))
    expired_held = _free_slot("slot_expired_held", starts_at=_NOW + timedelta(hours=2))
    expired_held.status = SlotStatus.HELD
    expired_held.session_id = "sess_abandoned"
    expired_held.held_until = datetime.now(UTC) - timedelta(seconds=1)
    await slot_repository.create_if_missing(free_slot)
    await slot_repository.create_if_missing(expired_held)

    bookable = await slot_repository.list_bookable(
        _PHYSICIAN, after=_NOW, before=_NOW + timedelta(hours=6), limit=10
    )

    assert {s.slot_id for s in bookable} == {"slot_free", "slot_expired_held"}


async def test_list_bookable_respects_limit_and_sort_order(slot_repository: SlotRepository) -> None:
    for i in range(5):
        await slot_repository.create_if_missing(
            _free_slot(f"slot_{i}", starts_at=_NOW + timedelta(hours=i + 1))
        )

    bookable = await slot_repository.list_bookable(
        _PHYSICIAN, after=_NOW, before=_NOW + timedelta(hours=10), limit=2
    )

    assert [s.slot_id for s in bookable] == ["slot_0", "slot_1"]


async def test_find_or_create_booked_creates_a_new_slot_when_none_exists(
    slot_repository: SlotRepository,
) -> None:
    starts_at = _NOW + timedelta(days=1)
    slot = await slot_repository.find_or_create_booked(
        _PHYSICIAN, starts_at, starts_at + timedelta(minutes=30), "sess_1"
    )

    assert slot.status is SlotStatus.BOOKED
    assert slot.session_id == "sess_1"


async def test_find_or_create_booked_claims_a_preexisting_free_slot(
    slot_repository: SlotRepository,
) -> None:
    """Backward-compatibility seam for a slot pre-seeded by
    scripts/seed_slots.py at this exact time."""
    starts_at = _NOW + timedelta(days=1)
    await slot_repository.create_if_missing(_free_slot("preseeded", starts_at=starts_at))

    slot = await slot_repository.find_or_create_booked(
        _PHYSICIAN, starts_at, starts_at + timedelta(minutes=30), "sess_1"
    )

    assert slot.slot_id == "preseeded"
    assert slot.status is SlotStatus.BOOKED
    assert slot.session_id == "sess_1"


async def test_ensure_indexes_enforces_one_slot_per_physician_per_time(
    slot_repository: SlotRepository,
) -> None:
    """The compound unique index is what makes `find_or_create_booked`
    race-safe -- not merely usually-correct."""
    starts_at = _NOW + timedelta(days=1)
    await slot_repository.collection.insert_one(
        _free_slot("first", starts_at=starts_at).model_dump()
    )

    duplicate = _free_slot("second", starts_at=starts_at)
    raised = False
    try:
        await slot_repository.collection.insert_one(duplicate.model_dump())
    except Exception:  # noqa: BLE001 - asserting a DuplicateKeyError was raised, not its exact type
        raised = True

    assert raised is True

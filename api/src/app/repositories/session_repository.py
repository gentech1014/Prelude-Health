"""Data access for the `session_chat_history` collection."""

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from pymongo import ReturnDocument

from app.core.constants import (
    IntakeScreen,
    PrescreeningCategory,
    PresentationType,
    SessionState,
)
from app.models.consent import Consent
from app.models.session import Session
from app.models.symptom_intake import SymptomAnswer
from app.models.transcript import ConversationTurn
from app.repositories.base import MongoRepository

# Mongo's own `_id` is never part of the domain model; the session is keyed
# by `session_id`. Project it away rather than relying on Pydantic to
# silently drop it.
_NO_MONGO_ID = {"_id": 0}


class SessionRepository(MongoRepository):
    """CRUD + append-only transcript writes, keyed by `session_id`."""

    collection_name = "session_chat_history"

    async def ensure_indexes(self) -> None:
        """Create the indexes this repository's correctness depends on.

        The unique index on `appointment_id` is not an optimization: it is
        what makes booking-webhook idempotency actually safe. Without it,
        two webhook deliveries racing each other can both pass the
        "does a session already exist?" check and create duplicates.
        """
        await self.collection.create_index("session_id", unique=True)
        await self.collection.create_index("appointment_id", unique=True)
        # Serves the booking UI's availability query, which runs once per
        # provider per page load and would otherwise scan every session.
        await self.collection.create_index([("physician", 1), ("appointment_datetime", 1)])

    async def create(self, session: Session) -> None:
        """Insert a new session document. Raises on a duplicate `session_id`."""
        await self.collection.insert_one(session.model_dump())

    async def get_by_id(self, session_id: str) -> Session | None:
        """Fetch one session, or None if `session_id` does not exist."""
        doc = await self.collection.find_one({"session_id": session_id}, _NO_MONGO_ID)
        return Session(**doc) if doc else None

    async def get_by_appointment_id(self, appointment_id: str) -> Session | None:
        """Fetch the session created for one appointment, or None.

        Used to make booking-webhook handling idempotent: a retried
        webhook must return the existing session instead of creating a
        duplicate.
        """
        doc = await self.collection.find_one({"appointment_id": appointment_id}, _NO_MONGO_ID)
        return Session(**doc) if doc else None

    async def list_booked_datetimes(
        self, physician: str, window_start: datetime, window_end: datetime
    ) -> list[datetime]:
        """Appointment start times already taken with one physician in a window.

        This is what keeps two patients from booking the same slot when the
        doctor has not connected their Google Calendar: their own free/busy
        is unavailable, but appointments booked *through this service* are
        still known. Cancelled and expired sessions are excluded -- their
        time is free again.

        Returns naive-or-aware datetimes exactly as Mongo stored them;
        `AvailabilityService` normalizes them to UTC before comparing, since
        BSON does not preserve the original offset.
        """
        cursor = self.collection.find(
            {
                "physician": physician,
                "appointment_datetime": {"$gte": window_start, "$lt": window_end},
                "status": {"$nin": [SessionState.DECLINED.value, SessionState.EXPIRED.value]},
            },
            {"_id": 0, "appointment_datetime": 1},
        )
        return [doc["appointment_datetime"] async for doc in cursor]

    async def append_turn(self, session_id: str, turn: ConversationTurn) -> None:
        """Append one finalized transcript turn.

        Called from `app.agents.bidi.outputs.MongoTranscriptWriter` on every
        final transcript event, so it must stay a single cheap round trip --
        it runs on the same event loop as the live call.

        Not an upsert: a turn for a session that does not exist is a bug
        worth surfacing, not a document worth conjuring.
        """
        await self.collection.update_one(
            {"session_id": session_id},
            {
                "$push": {"turns": turn.model_dump()},
                "$set": {"updated_at": datetime.now(UTC)},
            },
        )

    async def set_call_progress(
        self,
        session_id: str,
        screen: IntakeScreen,
        details: dict[str, dict[str, str]],
        selections: dict[str, dict[str, str]] | None = None,
        statuses: dict[str, dict[str, str]] | None = None,
    ) -> None:
        """Record how far the live call has got, so a reconnect can resume.

        Written by the navigation and detail-capture tools on every topic
        change, so it must stay one cheap round trip -- it runs on the same
        event loop as the patient's live audio.

        A whole-field `$set` rather than a merge: `CallProgress` in memory
        is already the merged view, and two writers racing on sub-keys
        would produce a resume marker that matches neither.

        `statuses` mirrors `details` key for key and says how well each
        value is actually known (see `app.core.constants.AnswerStatus`).
        Kept as its own field rather than folded into `collected_details`
        so every existing reader of that map -- the frontend prefill, the
        summarizer, the physician PDF -- is unaffected by its arrival.
        """
        await self._set(
            {
                "last_screen": screen,
                "collected_details": details,
                "collected_selections": selections or {},
                "collected_statuses": statuses or {},
            },
            session_id,
        )

    async def set_symptom_progress(
        self,
        session_id: str,
        categories: list[PrescreeningCategory],
        answers: list[SymptomAnswer],
        presentation: PresentationType | None = None,
    ) -> None:
        """Record the symptom conversation, so a reconnect resumes mid-question-set.

        Written on every asked question and every answer, so like
        `set_call_progress` it must stay one cheap round trip -- it runs on
        the same event loop as the patient's live audio.

        A whole-field `$set` for the same reason: the in-memory
        `SymptomQuestionPlan` is already the merged view, and two writers
        racing on list elements would produce a record matching neither.
        """
        await self._set(
            {
                "symptom_categories": [category.value for category in categories],
                "symptom_answers": [answer.model_dump() for answer in answers],
                "symptom_presentation": presentation.value if presentation else None,
            },
            session_id,
        )

    async def set_appointment_datetime(
        self, session_id: str, scheduled_at: datetime
    ) -> Session | None:
        """Move the appointment this session is preparing for.

        The stored time is what the agent's greeting, the intake message
        and the physician report all read from, so this is the single write
        a reschedule turns on -- the Calendar event is updated separately
        and best-effort.
        """
        return await self._set({"appointment_datetime": scheduled_at}, session_id)

    async def set_status(self, session_id: str, status: SessionState) -> Session | None:
        """Transition a session to a new lifecycle state.

        Returns the updated session so callers that need to read back the
        result do not have to issue a second query.
        """
        return await self._set({"status": status}, session_id)

    async def set_consent(self, session_id: str, consent: Consent) -> None:
        """Record the patient's consent decision.

        The caller (`SessionService.record_consent`) is responsible for
        also updating `status` -- a `given=False` consent must transition
        the session to DECLINED, not leave it as-is.
        """
        await self._set({"consent": consent.model_dump()}, session_id)

    async def set_report(self, session_id: str, report_json: dict[str, Any]) -> None:
        """Attach the summarization agent's structured output to a session."""
        await self._set({"report": report_json}, session_id)

    async def set_summary_failure(self, session_id: str, stop_reason: str | None) -> Session | None:
        """Mark summarization as failed and record why.

        Sets the status and the reason in one write, so a session can
        never be observed as SUMMARY_FAILED with no explanation attached.
        """
        return await self._set(
            {"status": SessionState.SUMMARY_FAILED, "summary_error": stop_reason},
            session_id,
        )

    async def set_document_ref(self, session_id: str, storage_key: str) -> Session | None:
        """Record that a supporting document reached object storage, and where.

        Authoritative over anything the transcript implies -- the agent can
        offer an upload that the patient never completes. The storage key
        (not a `s3://...` URI) is kept so a presigned download URL can be
        regenerated for it later, e.g. to reference it from the physician PDF.
        """
        return await self._set({"document_uploaded": True, "document_ref": storage_key}, session_id)

    async def set_document_summary(self, session_id: str, summary: str) -> Session | None:
        """Record the LLM-generated, type-only description of the uploaded
        supporting document. Best-effort by design -- see
        `SessionService._describe_document` -- so this is never guarded on
        status the way a lifecycle transition would be."""
        return await self._set({"document_summary": summary}, session_id)

    async def set_video_ref(self, session_id: str, video_ref: str) -> Session | None:
        """Attach the object-storage URI of the uploaded screen recording."""
        return await self._set({"video_ref": video_ref}, session_id)

    async def set_slot_id(self, session_id: str, slot_id: str) -> Session | None:
        """Record which `appointment_slots` document this session holds.

        Unguarded, matching `set_calendar_event_id`: this is the
        one-time initial assignment at booking, not a contested
        transition -- those go through `set_rescheduled`/`set_cancelled`.
        """
        return await self._set({"current_slot_id": slot_id}, session_id)

    async def set_calendar_event_id(self, session_id: str, event_id: str) -> Session | None:
        """Attach the Google Calendar event id created for this appointment.

        An empty string is a valid value -- it means Calendar delivery was
        not configured at booking time -- and is stored as-is so later
        best-effort delivery attempts can tell "nothing to update" apart
        from "not yet set."
        """
        return await self._set({"calendar_event_id": event_id}, session_id)

    async def set_status_guarded(
        self, session_id: str, new_status: SessionState, *, expected_status: Iterable[SessionState]
    ) -> Session | None:
        """Transition status only if currently one of `expected_status`.

        Unlike the unconditional `set_status`, this can never clobber a
        terminal state (e.g. CANCELLED) reached by a concurrent writer --
        see `SessionService.complete_call`.
        """
        return await self._guarded_set(
            {"status": new_status}, session_id, expected_status=expected_status
        )

    async def set_consent_and_status(
        self,
        session_id: str,
        consent: Consent,
        new_status: SessionState,
        *,
        expected_status: Iterable[SessionState],
    ) -> Session | None:
        """Record consent and transition status in one guarded write.

        Replaces what used to be two separate unconditional writes
        (`set_consent` then `set_status`) with a single precondition-checked
        one -- see `SessionService.record_consent`. A session already moved
        on (consent replayed, or cancelled out from under the request)
        cannot have this silently overwrite it.
        """
        return await self._guarded_set(
            {"consent": consent.model_dump(), "status": new_status},
            session_id,
            expected_status=expected_status,
        )

    async def set_cancelled(
        self,
        session_id: str,
        cancelled_at: datetime,
        reason: str | None,
        *,
        expected_status: Iterable[SessionState],
    ) -> Session | None:
        """Cancel a session, guarded so a concurrent cancel (voice tool vs.
        REST button) or an already-terminal session can never be double-applied.

        `None` back means the guard failed -- the caller (`SchedulingService
        .cancel_appointment`) re-reads to tell "someone already cancelled
        this" apart from "session not found."
        """
        return await self._guarded_set(
            {
                "status": SessionState.CANCELLED,
                "cancelled_at": cancelled_at,
                "cancellation_reason": reason,
            },
            session_id,
            expected_status=expected_status,
        )

    async def set_rescheduled(
        self,
        session_id: str,
        appointment_datetime: datetime,
        appointment_timezone: str,
        slot_id: str,
        *,
        expected_status: Iterable[SessionState],
        expected_slot_id: str,
    ) -> Session | None:
        """Move a session to a newly-claimed slot, guarded on both status
        and the OLD slot id -- see `SchedulingService.reschedule_appointment`.

        Guarding on `current_slot_id` (the field this write also changes)
        as well as `status` is exactly the case `_guarded_set` exists for:
        it is a compare-and-swap on the two facts that must not have
        changed since the new slot was read as available.
        """
        result = await self.collection.update_one(
            {
                "session_id": session_id,
                "status": {"$in": list(expected_status)},
                "current_slot_id": expected_slot_id,
            },
            {
                "$set": {
                    "appointment_datetime": appointment_datetime,
                    "appointment_timezone": appointment_timezone,
                    "current_slot_id": slot_id,
                    "updated_at": datetime.now(UTC),
                }
            },
        )
        if result.matched_count == 0:
            return None
        return await self.get_by_id(session_id)

    async def list_expirable(
        self, *, appointment_before: datetime, created_before: datetime, limit: int
    ) -> list[Session]:
        """Sessions that never got past the pre-call gate and are now
        stale -- used only by `SweeperService`. A session that never
        started (`NOTIFICATION_SENT`/`AI_LINK_READY`) or opened the link
        but never consented (`STARTED`) qualifies once its appointment
        time has passed, or the intake link's own TTL window has elapsed,
        whichever comes first.
        """
        cursor = self.collection.find(
            {
                "status": {
                    "$in": [
                        SessionState.NOTIFICATION_SENT.value,
                        SessionState.AI_LINK_READY.value,
                        SessionState.STARTED.value,
                    ]
                },
                "$or": [
                    {"appointment_datetime": {"$lt": appointment_before}},
                    {"created_at": {"$lt": created_before}},
                ],
            },
            _NO_MONGO_ID,
        ).limit(limit)
        return [Session(**doc) async for doc in cursor]

    async def _guarded_set(
        self, fields: dict[str, Any], session_id: str, *, expected_status: Iterable[SessionState]
    ) -> Session | None:
        """Apply a partial update only if `status` is currently one of
        `expected_status`. `None` means "not found OR wrong state" --
        callers disambiguate with one extra `get_by_id` on that rare
        failure path only, never on the hot path.

        Deliberately NOT built on `find_one_and_update` the way `_set()`
        is. Verified by execution against the installed `mongomock`:
        when a `find_one_and_update` filter guards on a field the same
        update also mutates (here, `status`), and the post-update value
        falls outside the filter's matching set, mongomock's
        `_find_and_modify` re-runs the *original* filter against the
        *already-mutated* document to fetch the "AFTER" image -- which no
        longer matches -- and returns `None` even though the write
        genuinely landed. Real MongoDB has no such bug; only the mock
        does, and it fails in the dangerous direction (reports "guard
        failed" when it actually succeeded). `update_one` plus
        `matched_count` has no equivalent trap and is what every guarded
        write in this codebase must use, not `_set()`'s idiom.
        """
        result = await self.collection.update_one(
            {"session_id": session_id, "status": {"$in": list(expected_status)}},
            {"$set": {**fields, "updated_at": datetime.now(UTC)}},
        )
        if result.matched_count == 0:
            return None
        return await self.get_by_id(session_id)

    async def _set(self, fields: dict[str, Any], session_id: str) -> Session | None:
        """Apply a partial update, always stamping `updated_at`.

        Safe to build on `find_one_and_update` (unlike `_guarded_set`)
        precisely because its filter (`session_id` alone) is never a
        field any caller also mutates.
        """
        doc = await self.collection.find_one_and_update(
            {"session_id": session_id},
            {"$set": {**fields, "updated_at": datetime.now(UTC)}},
            projection=_NO_MONGO_ID,
            return_document=ReturnDocument.AFTER,
        )
        return Session(**doc) if doc else None

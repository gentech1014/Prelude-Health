"""Integration tests for the intake WebSocket route.

This route is the service's only authorization boundary and was, until
these tests existed, completely uncovered. It had a defect that made
every single call fail: its dependencies were typed `Request`, which
FastAPI cannot satisfy inside a WebSocket route, so the handler raised
before running a single line of its own body.
"""

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mongomock_motor import AsyncMongoMockClient
from starlette.websockets import WebSocketDisconnect

from app.api.ws.intake import router
from app.core.config import Settings, get_settings
from app.core.constants import IntakeScreen, SessionState, Sex
from app.core.security import generate_intake_token, generate_ws_ticket
from app.integrations.google_calendar import GoogleCalendarClient
from app.models.consent import Consent
from app.models.session import PatientRef, Session
from app.repositories.session_repository import SessionRepository
from app.repositories.ws_ticket_repository import WsTicketRepository

_SECRET = "ws-test-secret-long-enough-for-validation"  # noqa: S105 - test constant
_WEBHOOK_SECRET = "ws-test-webhook-secret-long-enough-32"  # noqa: S105 - test constant
_PHYSICIAN_API_KEY = "ws-test-physician-api-key-long-enough"  # noqa: S105 - test constant
_POLICY_VIOLATION = 1008


def _session(
    session_id: str,
    *,
    consented: bool | None,
    status: SessionState = SessionState.STARTED,
    last_screen: IntakeScreen | None = None,
    collected: dict[str, dict[str, str]] | None = None,
    document_uploaded: bool = False,
) -> Session:
    now = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    consent = None if consented is None else Consent(given=consented, recorded_at=now)
    return Session(
        session_id=session_id,
        appointment_id=f"appt_{session_id}",
        patient=PatientRef(
            name="Asha Rao",
            patient_id="pt_2290",
            date_of_birth=datetime(1990, 5, 14, tzinfo=UTC),
            sex=Sex.FEMALE,
        ),
        physician="Dr. Mehta",
        appointment_datetime=datetime(2026, 9, 3, 9, 30, tzinfo=UTC),
        consent=consent,
        status=status,
        last_screen=last_screen,
        collected_details=collected or {},
        document_uploaded=document_uploaded,
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def client() -> Iterator[TestClient]:
    """A client whose app.state mirrors what the real lifespan installs."""
    app = FastAPI()
    app.include_router(router)

    database = AsyncMongoMockClient(tz_aware=True)["intake_test"]

    class _Mongo:
        db = database

    app.state.mongo = _Mongo()
    app.state.question_bank_service = object()
    settings = Settings(
        _env_file=None,  # isolate from the developer's real .env -- see tests/conftest.py
        intake_link_secret=_SECRET,
        booking_webhook_secret=_WEBHOOK_SECRET,
        physician_api_key=_PHYSICIAN_API_KEY,
    )
    # No service-account file configured -> a harmless no-op client, same
    # as `GoogleCalendarClient` degrades to in production when Calendar
    # delivery is not set up. No real Google credentials needed here.
    app.state.calendar_client = GoogleCalendarClient(settings)
    app.dependency_overrides[get_settings] = lambda: settings

    app.state.repository = SessionRepository(database)
    with TestClient(app) as test_client:
        yield test_client


def _ticket(session_id: str) -> str:
    return generate_ws_ticket(session_id, _SECRET, ttl_seconds=60)


def _nonce_of(ticket: str) -> str:
    return ticket.split(".")[2]


def _connect(client: TestClient, session_id: str, ticket: str) -> int:
    """Attempt the handshake and return the close code (0 if it opened)."""
    try:
        with client.websocket_connect(f"/ws/intake/{session_id}?ticket={ticket}"):
            return 0
    except WebSocketDisconnect as exc:
        return exc.code


def test_dependencies_resolve_inside_a_websocket_route(client: TestClient) -> None:
    """Regression: `Request`-typed deps made every intake call raise TypeError.

    Reaching a clean policy-violation close proves the handler body ran.
    """
    code = _connect(client, "sess_unknown", _ticket("sess_unknown"))

    assert code == _POLICY_VIOLATION


def test_forged_ticket_is_refused(client: TestClient) -> None:
    """A ticket not signed with the server secret must not open a call."""
    assert _connect(client, "sess_1", "ws.999999999.nonce.deadbeef") == _POLICY_VIOLATION


def test_ticket_for_another_session_is_refused(client: TestClient) -> None:
    """Session ids travel in URLs; the signature is what binds the ticket."""
    assert _connect(client, "sess_1", _ticket("sess_other")) == _POLICY_VIOLATION


def test_intake_token_cannot_be_used_as_a_ws_ticket(client: TestClient) -> None:
    """The purpose is signed, so the credentials are not interchangeable.

    Without this, the 48-hour token sitting in the patient's link would
    open the socket directly and the short-lived ticket would buy nothing.
    """
    intake = generate_intake_token("sess_1", _SECRET, 3600)

    assert _connect(client, "sess_1", intake) == _POLICY_VIOLATION


async def test_a_ticket_cannot_be_replayed(client: TestClient) -> None:
    """Spending a ticket must make the second use of it fail.

    Run against a DECLINED session so both attempts close on the state
    check: the close code is then the same either way and proves nothing,
    which is why the ledger is asserted directly.
    """
    await client.app.state.repository.create(
        _session("sess_replay", consented=False, status=SessionState.DECLINED)
    )
    ticket = _ticket("sess_replay")

    assert _connect(client, "sess_replay", ticket) == _POLICY_VIOLATION
    assert _connect(client, "sess_replay", ticket) == _POLICY_VIOLATION

    tickets = WsTicketRepository(client.app.state.mongo.db)
    assert await tickets.spend("sess_replay", _nonce_of(ticket), 60) is False


async def test_consented_session_in_the_wrong_state_is_refused(client: TestClient) -> None:
    """Consent alone is not a licence to open a socket.

    A finished-and-summarized session still carries an affirmative consent
    record forever. Without a state check, presenting a fresh ticket
    against one would open a second live call on a call that is already
    reported.
    """
    await client.app.state.repository.create(
        _session("sess_done", consented=True, status=SessionState.SUMMARY_READY)
    )

    assert _connect(client, "sess_done", _ticket("sess_done")) == _POLICY_VIOLATION


async def test_call_with_declined_consent_is_refused(client: TestClient) -> None:
    """A refusal is a decision, not a pause.

    `record_consent(False)` moves the session to DECLINED, so the state
    check is what blocks it -- "no consent yet" and "consent refused" are
    now genuinely different situations and only one of them may connect.
    """
    await client.app.state.repository.create(
        _session("sess_declined", consented=False, status=SessionState.DECLINED)
    )

    assert _connect(client, "sess_declined", _ticket("sess_declined")) == _POLICY_VIOLATION


@pytest.fixture
def client_with_allowed_origins() -> Iterator[TestClient]:
    """Same as `client`, but with `allowed_origins` configured (non-default)."""
    app = FastAPI()
    app.include_router(router)

    database = AsyncMongoMockClient(tz_aware=True)["intake_test_origin"]

    class _Mongo:
        db = database

    app.state.mongo = _Mongo()
    app.state.question_bank_service = object()
    settings = Settings(
        _env_file=None,
        intake_link_secret=_SECRET,
        booking_webhook_secret=_WEBHOOK_SECRET,
        physician_api_key=_PHYSICIAN_API_KEY,
        allowed_origins="https://allowed.example",
    )
    app.state.calendar_client = GoogleCalendarClient(settings)
    app.dependency_overrides[get_settings] = lambda: settings

    app.state.repository = SessionRepository(database)
    with TestClient(app) as test_client:
        yield test_client


def test_call_from_disallowed_origin_is_refused(
    client_with_allowed_origins: TestClient,
) -> None:
    """When `allowed_origins` is set, a mismatched (or absent) Origin must not open a call.

    `TestClient.websocket_connect` sends no Origin header by default, so
    this also covers the "no Origin at all" case -- treated the same as a
    disallowed one, not as an exemption.
    """
    ticket = _ticket("sess_bad_origin")

    assert _connect(client_with_allowed_origins, "sess_bad_origin", ticket) == _POLICY_VIOLATION


# ---------------------------------------------------------------------------
# The browser protocol
#
# These run against a stand-in agent, not Nova Sonic. The point is the
# contract between the browser and this route -- what the patient's screen
# is told, and what the model is handed -- and a real model connection
# would make every one of these tests a live Bedrock call.
# ---------------------------------------------------------------------------


class _StandInAgent:
    """Drives the route's I/O channels the way `BidiAgent.run` does.

    Reads inputs in a loop and records what the model would have received.
    Deliberately does not emit anything: outbound translation is covered by
    the tool contract tests, and what matters here is the inbound path.
    """

    def __init__(self) -> None:
        self.received: list[dict[str, object]] = []

    async def run(
        self,
        inputs: list[object],
        outputs: list[object],
        invocation_state: dict[str, object],
    ) -> None:
        del outputs, invocation_state
        channel = inputs[0]
        # `start`/`stop` exactly as `BidiAgent.run` calls them. Not
        # optional: the input channel drains the socket from a task it
        # starts there, so a stand-in that skips it reads nothing at all.
        await channel.start(self)  # type: ignore[attr-defined]
        try:
            while True:
                self.received.append(dict(await channel()))  # type: ignore[operator]
        finally:
            await channel.stop()  # type: ignore[attr-defined]


@pytest.fixture
def stand_in_agent(monkeypatch: pytest.MonkeyPatch) -> _StandInAgent:
    """Replace the live agent so no test opens a Bedrock stream."""
    agent = _StandInAgent()
    monkeypatch.setattr("app.api.ws.intake.build_live_agent", lambda *_args, **_kwargs: agent)
    return agent


async def test_first_frame_carries_the_resume_position_and_prefill(
    client: TestClient, stand_in_agent: _StandInAgent
) -> None:
    """A rejoining patient must land where the call got to, not at question one.

    This frame is what makes resume real from the browser's side: without
    it the frontend has no way to know the call is mid-flow.
    """
    await client.app.state.repository.create(
        _session(
            "sess_resume",
            consented=True,
            status=SessionState.INTERRUPTED,
            last_screen=IntakeScreen.MEDICATION,
            collected={"allergies": {"allergies": "penicillin"}},
        )
    )

    with client.websocket_connect(
        f"/ws/intake/sess_resume?ticket={_ticket('sess_resume')}"
    ) as socket:
        frame = socket.receive_json()

    assert frame["type"] == "connected"
    assert frame["screen"] == "medication"
    assert frame["prefill"] == {"allergies": {"allergies": "penicillin"}}
    # The browser must not guess the capture rate; a mismatch is inaudible
    # in code review and unintelligible in the patient's ear.
    assert frame["audio"]["sample_rate"] == 16_000


async def test_a_call_may_open_before_consent_so_the_patient_can_be_asked(
    client: TestClient, stand_in_agent: _StandInAgent
) -> None:
    """Consent is asked for *in* the call, so it cannot gate the socket.

    The patient has to hear the greeting before being asked anything. What
    replaces the gate is that the agent can do nothing but greet and ask
    until consent is recorded -- enforced in the tools, and covered by
    `tests/unit/test_tool_contracts.py`.
    """
    await client.app.state.repository.create(
        _session("sess_noconsent", consented=None, status=SessionState.STARTED)
    )

    with client.websocket_connect(
        f"/ws/intake/sess_noconsent?ticket={_ticket('sess_noconsent')}"
    ) as socket:
        assert socket.receive_json()["type"] == "connected"


async def test_the_agent_is_prompted_to_speak_first(
    client: TestClient, stand_in_agent: _StandInAgent
) -> None:
    """Nova Sonic does not open a conversation on its own.

    Verified live: given a system prompt telling it to greet the patient,
    the model sat silent through 25 seconds of audio and said nothing until
    it received an input event. Without this opener a patient who tapped
    Start would hear silence -- and one with no working microphone would
    never get the call started at all.
    """
    await client.app.state.repository.create(
        _session("sess_opener", consented=True, status=SessionState.IN_PROGRESS)
    )

    with client.websocket_connect(
        f"/ws/intake/sess_opener?ticket={_ticket('sess_opener')}"
    ) as socket:
        socket.receive_json()  # connected
        socket.send_json({"type": "client_ping"})
        assert socket.receive_json()["type"] == "pong"

    opener = stand_in_agent.received[0]
    assert opener["type"] == "bidi_text_input"
    assert opener["role"] == "user"
    # Marked as a stage direction, not phrased as something a patient said.
    assert "(system)" in str(opener["text"])


async def test_a_typed_field_reaches_the_model_as_the_patient_s_own_turn(
    client: TestClient, stand_in_agent: _StandInAgent
) -> None:
    """A typed answer must land in the same conversation a spoken one would.

    Otherwise the screens become a second data model the summarizer never
    sees, and a patient who cannot speak produces an empty report.
    """
    await client.app.state.repository.create(
        _session("sess_typed", consented=True, status=SessionState.IN_PROGRESS)
    )

    with client.websocket_connect(
        f"/ws/intake/sess_typed?ticket={_ticket('sess_typed')}"
    ) as socket:
        socket.receive_json()  # connected
        socket.send_json(
            {
                "type": "client_form_update",
                "screen": "medication",
                "field": "medications",
                "value": "Metformin 500mg",
            }
        )
        socket.send_json({"type": "client_ping"})
        assert socket.receive_json()["type"] == "pong"

    # The opener is always first; the patient's turn follows it. Injected
    # silence is filtered out -- it is transport keep-alive, not input.
    typed = [event for event in stand_in_agent.received if event["type"] == "bidi_text_input"]
    assert typed[1:] == [
        {
            "type": "bidi_text_input",
            "text": "(typed) Medications: Metformin 500mg",
            "role": "user",
        }
    ]

    # And it is in the transcript. Nothing else writes this one: the
    # transcript writer records what the model heard, never text it was
    # handed, so without this a typed answer reached the model and no
    # report.
    stored = await client.app.state.repository.get_by_id("sess_typed")
    assert stored is not None
    assert [(turn.role, turn.text) for turn in stored.turns] == [
        ("user", "(typed) Medications: Metformin 500mg")
    ]


async def test_a_typed_field_before_consent_is_not_collected(
    client: TestClient, stand_in_agent: _StandInAgent
) -> None:
    """The typed path was the one way around the consent boundary.

    It recorded straight into call progress, and the end-of-call flush
    persisted that -- so a call abandoned on the consent screen left
    collected answers behind for a patient who never agreed to any
    collection, and the stray marker then skipped `welcome` on every later
    open.
    """
    await client.app.state.repository.create(
        _session("sess_preconsent", consented=False, status=SessionState.STARTED)
    )

    with client.websocket_connect(
        f"/ws/intake/sess_preconsent?ticket={_ticket('sess_preconsent')}"
    ) as socket:
        socket.receive_json()  # connected
        socket.send_json(
            {
                "type": "client_form_update",
                "screen": "confirm-details",
                "field": "phone_number",
                "value": "+1 555 0100",
            }
        )
        socket.send_json({"type": "client_ping"})
        assert socket.receive_json()["type"] == "pong"

    typed = [event for event in stand_in_agent.received if event["type"] == "bidi_text_input"]
    assert typed[1:] == []  # the opener, and nothing else

    stored = await client.app.state.repository.get_by_id("sess_preconsent")
    assert stored is not None
    assert stored.turns == []
    assert stored.collected_details == {}


async def test_a_malformed_frame_is_answered_rather_than_hung_up_on(
    client: TestClient, stand_in_agent: _StandInAgent
) -> None:
    """A bad frame is a client bug or a probe -- never a reason to drop a call."""
    await client.app.state.repository.create(
        _session("sess_garbage", consented=True, status=SessionState.IN_PROGRESS)
    )

    with client.websocket_connect(
        f"/ws/intake/sess_garbage?ticket={_ticket('sess_garbage')}"
    ) as socket:
        socket.receive_json()  # connected
        socket.send_json({"type": "client_audio"})  # no audio payload
        error = socket.receive_json()

        socket.send_json({"type": "client_ping"})
        assert socket.receive_json()["type"] == "pong"  # the call survived

    assert error["type"] == "error"
    assert error["recoverable"] is True
    # Only the opener -- the bad frame reached the model as nothing at all.
    text = [event for event in stand_in_agent.received if event["type"] == "bidi_text_input"]
    assert len(text) == 1


async def test_muting_stops_audio_reaching_the_model(
    client: TestClient, stand_in_agent: _StandInAgent
) -> None:
    """Mute is a privacy control, so it cannot depend on the client honouring it."""
    await client.app.state.repository.create(
        _session("sess_mute", consented=True, status=SessionState.IN_PROGRESS)
    )
    frame = {"type": "client_audio", "audio": "AAAA"}

    with client.websocket_connect(f"/ws/intake/sess_mute?ticket={_ticket('sess_mute')}") as socket:
        socket.receive_json()  # connected
        socket.send_json({"type": "client_control", "action": "mute"})
        socket.send_json(frame)
        socket.send_json({"type": "client_control", "action": "unmute"})
        socket.send_json(frame)
        socket.send_json({"type": "client_ping"})
        assert socket.receive_json()["type"] == "pong"

    # Silence keeps the model's stream moving during gaps, so the client's
    # own payload is what distinguishes a real mic frame from filler.
    from_patient = [event for event in stand_in_agent.received if event.get("audio") == "AAAA"]
    assert len(from_patient) == 1


async def test_hanging_up_leaves_the_session_resumable_with_no_report(
    client: TestClient, stand_in_agent: _StandInAgent
) -> None:
    """Ending early is abandonment, not completion.

    A COMPLETED session gets summarized, so mislabelling this would put an
    empty report in front of a physician as though the call had finished.
    """
    await client.app.state.repository.create(
        _session("sess_hangup", consented=True, status=SessionState.IN_PROGRESS)
    )

    with client.websocket_connect(
        f"/ws/intake/sess_hangup?ticket={_ticket('sess_hangup')}"
    ) as socket:
        socket.receive_json()  # connected
        socket.send_json({"type": "client_control", "action": "hangup"})
        while (frame := socket.receive_json())["type"] != "call_ended":
            pass

    assert frame["reason"] == "interrupted"
    session = await client.app.state.repository.get_by_id("sess_hangup")
    assert session is not None
    assert session.status is SessionState.INTERRUPTED
    assert session.report is None


async def test_a_reconnect_is_told_to_resume_rather_than_to_begin(
    client: TestClient, stand_in_agent: _StandInAgent
) -> None:
    """The reported bug, end to end through the socket.

    A patient whose call dropped halfway rejoined and was greeted again,
    walked back to the consent screen, asked to tick a box they had
    already ticked, and re-asked what they had already answered. The
    resume block was in the system prompt the whole time -- but the last
    turn in the model's history was a user message saying "begin now,
    following your instructions from step 1", and a user turn outranks a
    system prompt.
    """
    await client.app.state.repository.create(
        _session(
            "sess_rejoin",
            consented=True,
            status=SessionState.INTERRUPTED,
            last_screen=IntakeScreen.MEDICATION,
            collected={"patient-concerns": {"concerns": "Sore knee"}},
        )
    )

    with client.websocket_connect(
        f"/ws/intake/sess_rejoin?ticket={_ticket('sess_rejoin')}"
    ) as socket:
        connected = socket.receive_json()

    assert connected["screen"] == IntakeScreen.MEDICATION.value

    opener = stand_in_agent.received[0]["text"]
    assert "must not start it over" in opener
    assert "Consent is already recorded" in opener
    assert "do NOT ask them to tick the consent box" in opener
    assert "medication" in opener


async def test_a_reconnect_stranded_on_the_consent_screen_resumes_past_it(
    client: TestClient, stand_in_agent: _StandInAgent
) -> None:
    """Consent is what leaves `confirm-details`, and the app owns that move.

    So a session holding consent *and* a `confirm-details` marker is one
    whose socket died between the two. Restoring the marker put the
    patient back in front of a consent card they had already ticked --
    which is exactly the screen they reported being shown.
    """
    await client.app.state.repository.create(
        _session(
            "sess_stranded",
            consented=True,
            status=SessionState.INTERRUPTED,
            last_screen=IntakeScreen.CONFIRM_DETAILS,
        )
    )

    with client.websocket_connect(
        f"/ws/intake/sess_stranded?ticket={_ticket('sess_stranded')}"
    ) as socket:
        connected = socket.receive_json()

    assert connected["screen"] == IntakeScreen.PATIENT_CONCERNS.value


async def test_a_first_connection_is_still_told_to_begin(
    client: TestClient, stand_in_agent: _StandInAgent
) -> None:
    """Nothing above may cost a genuinely new call its greeting."""
    await client.app.state.repository.create(_session("sess_new", consented=None))

    with client.websocket_connect(f"/ws/intake/sess_new?ticket={_ticket('sess_new')}") as socket:
        connected = socket.receive_json()

    assert connected["screen"] == IntakeScreen.WELCOME.value
    assert "from step 1" in stand_in_agent.received[0]["text"]


async def test_a_reschedule_asked_for_on_screen_reaches_the_model_as_a_request(
    client: TestClient, stand_in_agent: _StandInAgent
) -> None:
    """Tapping the button and saying it out loud must take the same path.

    The frame performs nothing on its own: it tells the model to offer
    times, so the patient hears the same offer either way and the screen
    is only ever moved by the tool that also fills it.
    """
    await client.app.state.repository.create(
        _session(
            "sess_resched_ask",
            consented=True,
            status=SessionState.IN_PROGRESS,
            last_screen=IntakeScreen.THANK_YOU,
        )
    )

    with client.websocket_connect(
        f"/ws/intake/sess_resched_ask?ticket={_ticket('sess_resched_ask')}"
    ) as socket:
        socket.receive_json()  # connected
        socket.send_json({"type": "client_reschedule_requested"})
        socket.send_json({"type": "client_ping"})
        while socket.receive_json()["type"] != "pong":
            pass

    turns = [event for event in stand_in_agent.received if event["type"] == "bidi_text_input"]
    assert "offer_appointment_times" in turns[1]["text"]


async def test_an_appointment_moved_on_screen_is_acknowledged_not_written_again(
    client: TestClient, stand_in_agent: _StandInAgent
) -> None:
    """The patient tapped a time and the REST route already saved it.

    What is left is server-side: they are back on the closing screen, and
    the model is told the new time -- read from the session, never from
    the browser -- so it confirms rather than calling the tool on top of a
    write that already happened.
    """
    await client.app.state.repository.create(
        _session(
            "sess_resched_done",
            consented=True,
            status=SessionState.IN_PROGRESS,
            last_screen=IntakeScreen.THANK_YOU,
        )
    )

    frames: list[dict[str, object]] = []
    with client.websocket_connect(
        f"/ws/intake/sess_resched_done?ticket={_ticket('sess_resched_done')}"
    ) as socket:
        socket.receive_json()  # connected
        socket.send_json({"type": "client_appointment_rescheduled"})
        socket.send_json({"type": "client_ping"})
        while True:
            frame = socket.receive_json()
            frames.append(frame)
            if frame["type"] == "pong":
                break

    assert [frame["screen"] for frame in frames if frame["type"] == "navigate"] == ["thank-you"]

    turns = [event for event in stand_in_agent.received if event["type"] == "bidi_text_input"]
    assert "Thursday 03 September at 9:30 in the morning" in turns[1]["text"]
    assert "already saved" in turns[1]["text"]


async def test_a_document_that_lands_makes_the_agent_ask_what_it_is(
    client: TestClient, stand_in_agent: _StandInAgent
) -> None:
    """The reported gap: an upload the call never learned about.

    The file goes to object storage over REST, which the socket cannot
    see. The agent is told not to wait for it and not to ask whether it
    finished -- correctly, because it takes as long as it takes -- so
    before this frame the only thing that ever moved the conversation on
    was the patient volunteering "I have uploaded it". They had no way of
    knowing they were expected to say that, so a patient who did exactly
    as they were asked sat holding a delivered file in silence.
    """
    await client.app.state.repository.create(
        _session(
            "sess_doc_ok",
            consented=True,
            status=SessionState.IN_PROGRESS,
            last_screen=IntakeScreen.RECENT_CARE,
            document_uploaded=True,
        )
    )

    with client.websocket_connect(
        f"/ws/intake/sess_doc_ok?ticket={_ticket('sess_doc_ok')}"
    ) as socket:
        socket.receive_json()  # connected
        socket.send_json({"type": "client_document_uploaded", "file_name": "results.pdf"})
        socket.send_json({"type": "client_ping"})
        while socket.receive_json()["type"] != "pong":
            pass

    turns = [event for event in stand_in_agent.received if event["type"] == "bidi_text_input"]
    assert "uploaded successfully" in turns[1]["text"]
    assert "what the report is" in turns[1]["text"]
    assert "results.pdf" in turns[1]["text"]


async def test_a_failed_upload_is_told_to_the_agent_rather_than_only_the_screen(
    client: TestClient, stand_in_agent: _StandInAgent
) -> None:
    """The more important half. A failure was silent to the call entirely.

    The agent went on to the next topic while the patient was still
    looking at an error, with nothing telling them whether to try again.
    """
    await client.app.state.repository.create(
        _session(
            "sess_doc_fail",
            consented=True,
            status=SessionState.IN_PROGRESS,
            last_screen=IntakeScreen.RECENT_CARE,
        )
    )

    with client.websocket_connect(
        f"/ws/intake/sess_doc_fail?ticket={_ticket('sess_doc_fail')}"
    ) as socket:
        socket.receive_json()  # connected
        socket.send_json({"type": "client_document_upload_failed"})
        socket.send_json({"type": "client_ping"})
        while socket.receive_json()["type"] != "pong":
            pass

    turns = [event for event in stand_in_agent.received if event["type"] == "bidi_text_input"]
    assert "failed to upload" in turns[1]["text"]
    assert "try" in turns[1]["text"]


async def test_a_claimed_upload_the_session_does_not_have_is_treated_as_a_failure(
    client: TestClient, stand_in_agent: _StandInAgent
) -> None:
    """The session document is the authority, exactly as it is for consent.

    A browser that says a file landed when none did must not make the
    agent thank the patient for something their doctor will never see.
    """
    await client.app.state.repository.create(
        _session(
            "sess_doc_lie",
            consented=True,
            status=SessionState.IN_PROGRESS,
            last_screen=IntakeScreen.RECENT_CARE,
            document_uploaded=False,
        )
    )

    with client.websocket_connect(
        f"/ws/intake/sess_doc_lie?ticket={_ticket('sess_doc_lie')}"
    ) as socket:
        socket.receive_json()  # connected
        socket.send_json({"type": "client_document_uploaded", "file_name": "nothing.pdf"})
        socket.send_json({"type": "client_ping"})
        while socket.receive_json()["type"] != "pong":
            pass

    turns = [event for event in stand_in_agent.received if event["type"] == "bidi_text_input"]
    assert "failed to upload" in turns[1]["text"]
    assert "nothing.pdf" not in turns[1]["text"]

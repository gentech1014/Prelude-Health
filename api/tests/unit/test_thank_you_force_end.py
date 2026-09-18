"""Forcing the call to end after `thank-you`, which the app owns rather than the model.

Step 17 of the prompt tells the model to call `end_session` right after
saying goodbye, but nothing guarantees it does: a skipped tool call, one
called with a farewell still queued behind it, or a model that simply
gets stuck all leave the patient connected to a call with nothing left to
say. `BrowserOutput`'s watchdog is the backstop -- silence after the
closing turn, rather than the model's word for it -- exactly like the
`welcome` watchdog in `test_welcome_transition.py`.

`force_complete`, not `hangup`, is the signal it raises: the screening
itself finished, so the call this ends must still be summarized, not
treated as a patient walking away mid-call.
"""

import asyncio

import pytest

from app.agents.bidi.call_state import CallProgress, LiveCallContext, UiEventBus
from app.api.ws import channels
from app.api.ws.channels import BrowserOutput, CallControl
from app.core.constants import IntakeScreen


class _NullSessions:
    async def set_call_progress(
        self, session_id: str, screen: IntakeScreen, details: dict[str, dict[str, str]]
    ) -> None:
        return None


def _context(screen: IntakeScreen, *, closing_started: bool = True) -> LiveCallContext:
    """A call sitting on `screen`, by default already into its goodbye.

    `closing_started` is what separates the two turns that both happen on
    `thank-you`: the last question of the call, and the farewell after it.
    Most of these tests are about the farewell, so it defaults to True --
    the one that is not says so explicitly.
    """
    return LiveCallContext(
        session_id="sess_8f2c1a",
        bus=UiEventBus(),
        progress=CallProgress(screen=screen),
        sessions=_NullSessions(),  # type: ignore[arg-type]
        consent_given=True,
        closing_started=closing_started,
    )


_AUDIO = {"type": "bidi_audio_stream", "audio": "AAAA", "sample_rate": 16_000}


@pytest.fixture
def control() -> CallControl:
    return CallControl()


@pytest.fixture
def context() -> LiveCallContext:
    return _context(IntakeScreen.THANK_YOU)


@pytest.fixture
def output(context: LiveCallContext, control: CallControl) -> BrowserOutput:
    return BrowserOutput(context, control)


@pytest.fixture
def quick_watchdog(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shrink both silence windows so these run in milliseconds, not seconds.

    Both, because the watchdog now picks between them: the short one once
    the goodbye has begun, and a much longer one while the patient may
    still be answering the last question.
    """
    monkeypatch.setattr(channels, "THANK_YOU_FORCE_END_SECONDS", 0.02)
    monkeypatch.setattr(channels, "THANK_YOU_IDLE_END_SECONDS", 0.6)


async def _settle(seconds: float = 0.1) -> None:
    await asyncio.sleep(seconds)


@pytest.mark.usefixtures("quick_watchdog")
async def test_silence_after_the_goodbye_force_ends_the_call(
    output: BrowserOutput, control: CallControl
) -> None:
    """The reported gap: the goodbye is said and `end_session` never comes."""
    await output(_AUDIO)

    await _settle()

    assert control.force_complete.is_set()
    assert not control.hangup.is_set()


@pytest.mark.usefixtures("quick_watchdog")
async def test_a_completion_event_that_never_arrives_does_not_block_it(
    output: BrowserOutput, control: CallControl
) -> None:
    """Nova Sonic has been observed to speak a turn in full and never send
    `bidi_response_complete` for it (see the `welcome` watchdog this one is
    modelled on). Arming only on that event would make the goodbye's own
    force-end depend on the exact signal already known to be unreliable, so
    audio chunks alone -- with no completion event at all -- must still
    arm and fire it.
    """
    await output(_AUDIO)
    await output(_AUDIO)

    await _settle()

    assert control.force_complete.is_set()


async def test_the_watchdog_never_arms_off_the_thank_you_screen(
    control: CallControl,
) -> None:
    """A turn on any other screen must never end the call."""
    output = BrowserOutput(_context(IntakeScreen.MEDICATION), control)

    await output(_AUDIO)
    await _settle()

    assert not control.force_complete.is_set()


@pytest.mark.usefixtures("quick_watchdog")
async def test_the_reschedule_branch_is_watched_on_the_long_window(
    control: CallControl,
) -> None:
    """`appointment-reschedule` is only reachable by answering the closing
    question, so a patient who goes silent there has left a call whose
    screening is already finished -- and nothing else would ever end it.

    The long window, never the eight-second one: they are reading a list
    of times, which takes longer than a goodbye.
    """
    context = _context(IntakeScreen.APPOINTMENT_RESCHEDULE, closing_started=False)
    output = BrowserOutput(context, control)

    await output(_AUDIO)
    await _settle()
    assert not control.force_complete.is_set()

    await _settle(0.6)
    assert control.force_complete.is_set()


async def test_end_session_closing_the_connection_beats_the_watchdog(
    output: BrowserOutput, control: CallControl
) -> None:
    """The model calling `end_session` in time must not also force-end the call.

    `stop()` is what the route calls once `agent.run()` returns -- which it
    does as soon as `end_session` closes the model connection -- so the
    watchdog must not still be pending afterwards.
    """
    await output(_AUDIO)
    await output.stop()

    await _settle()

    assert not control.force_complete.is_set()


@pytest.mark.usefixtures("quick_watchdog")
async def test_the_watchdog_does_not_end_the_call_on_the_last_question(
    control: CallControl,
) -> None:
    """`thank-you` carries a question as well as the goodbye.

    Step 15 asks whether there is anything the patient would like help
    with, on this same screen. The watchdog used to arm on any agent audio
    here, so it started counting the moment that question finished being
    *asked* -- and hung up on anyone who took more than eight seconds to
    think, marking the call completed and summarizing it without the
    answer they were in the middle of giving.
    """
    context = _context(IntakeScreen.THANK_YOU, closing_started=False)
    output = BrowserOutput(context, control)

    await output(_AUDIO)
    await _settle()

    assert not control.force_complete.is_set()


@pytest.mark.usefixtures("quick_watchdog")
async def test_a_patient_still_answering_is_never_cut_off(
    control: CallControl,
) -> None:
    """Speech while the long window is running defers the end, it does not race it."""
    context = _context(IntakeScreen.THANK_YOU, closing_started=False)
    output = BrowserOutput(context, control)

    await output(_AUDIO)
    # They start talking part-way through the idle window.
    await _settle(0.3)
    context.patient_activity += 1
    await _settle(0.5)

    assert not control.force_complete.is_set()


@pytest.mark.usefixtures("quick_watchdog")
async def test_a_patient_who_has_gone_still_ends_the_call(
    control: CallControl,
) -> None:
    """The other half: a declined last question never sets `closing_started`.

    Nothing would then arm the short window, so without the longer one a
    stalled model would hold the socket open indefinitely.
    """
    context = _context(IntakeScreen.THANK_YOU, closing_started=False)
    output = BrowserOutput(context, control)

    await output(_AUDIO)
    await _settle(0.8)

    assert control.force_complete.is_set()


# --------------------------------------------------------------------------
# Closing the call, rather than force-ending one that never closed
# --------------------------------------------------------------------------


async def test_a_patient_answering_into_silence_is_prompted_to_a_goodbye(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reported bug: the call sticks on `thank-you`, then speaks and cuts.

    Nova Sonic produces a turn only when something reaches its input
    stream, so a patient who answers the closing question and gets nothing
    back is simply stuck. Nothing was watching either: both closing
    watchdogs armed on agent *audio*, and the failure is precisely that
    there is none. What eventually broke the silence was the audio-gap
    filler happening to provoke a turn, which is what the patient
    experiences as the call sitting dead and then abruptly speaking.

    The answer is a nudge that makes the model say the goodbye out loud,
    not `force_complete`, which hangs up with nothing said at all.
    """
    monkeypatch.setattr(channels, "CLOSING_PROMPT_SILENCE_SECONDS", 0.02)
    context = _context(IntakeScreen.THANK_YOU, closing_started=False)
    BrowserOutput(context, CallControl())

    context.note_patient_activity()
    await asyncio.sleep(0.1)

    assert not context.nudges.empty()
    nudge = context.nudges.get_nowait()
    assert "end_session" in nudge
    assert "thank them for their time" in nudge


async def test_a_model_that_is_already_closing_is_never_interrupted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The nudge speaks into silence only, never over a live goodbye."""
    monkeypatch.setattr(channels, "CLOSING_PROMPT_SILENCE_SECONDS", 0.05)
    context = _context(IntakeScreen.THANK_YOU, closing_started=False)
    output = BrowserOutput(context, CallControl())

    context.note_patient_activity()
    await output(_AUDIO)  # the goodbye starts
    await asyncio.sleep(0.15)

    assert context.nudges.empty()


async def test_the_reschedule_screen_is_never_closed_out_from_under_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Choosing a new time is not an answer to the closing question."""
    monkeypatch.setattr(channels, "CLOSING_PROMPT_SILENCE_SECONDS", 0.02)
    context = _context(IntakeScreen.APPOINTMENT_RESCHEDULE, closing_started=False)
    BrowserOutput(context, CallControl())

    context.note_patient_activity()
    await asyncio.sleep(0.1)

    assert context.nudges.empty()


async def test_mid_call_activity_arms_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """A patient answering on `medication` is not closing anything."""
    monkeypatch.setattr(channels, "CLOSING_PROMPT_SILENCE_SECONDS", 0.02)
    context = _context(IntakeScreen.MEDICATION, closing_started=False)
    BrowserOutput(context, CallControl())

    context.note_patient_activity()
    await asyncio.sleep(0.1)

    assert context.nudges.empty()


async def test_stopping_the_call_unhooks_the_context(control: CallControl) -> None:
    """A watchdog must not outlive the call, nor keep the output alive."""
    context = _context(IntakeScreen.THANK_YOU)
    output = BrowserOutput(context, control)

    await output.stop()

    assert context.on_patient_activity is None

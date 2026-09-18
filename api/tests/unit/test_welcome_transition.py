"""Leaving the `welcome` screen, which the app owns rather than the model.

The bug this pins was reported three times: the introduction played over
the consent screen and the patient never saw `welcome` at all.

The cause is an ordering one, and no client-side heuristic can fix it. The
model calls `navigate_to_screen` at *generation* time, and Nova emits that
tool call before it streams the audio for the same turn -- so the navigate
frame reached the browser ahead of a single byte of greeting audio, the
browser measured an empty playback queue, concluded there was nothing to
wait for, and moved immediately.

So the move is emitted here instead, at the end of the model's turn, by
which point every audio chunk of the greeting is already queued ahead of
it on the wire.

That left the transition hanging on a single model event, which is the
second bug pinned here: Nova spoke the whole greeting and never sent
`completionEnd`, so the patient sat on the introduction screen for the
rest of the call while the assistant asked them to confirm details they
could not see. The watchdog below is the answer -- silence after speech,
rather than the model's word for it.

The third bug is that *either* route could fire partway through the
greeting, which is what the patient sees as the introduction stopping
dead mid-sentence. Nova closes a completion whenever it yields, so one
turn end is not the end of the greeting; and silence at the generator is
not silence at the patient's ear, because the whole greeting is queued in
a second or two and takes twenty to play. So the move now needs the
model's explicit `greeting_finished` signal, and the watchdog waits out
the audio still playing before it acts.
"""

import asyncio
from typing import Any

import pytest

from app.agents.bidi.call_state import CallProgress, LiveCallContext, UiEventBus
from app.api.ws import channels
from app.api.ws.channels import BrowserOutput, CallControl
from app.core.constants import IntakeScreen


class _NullSessions:
    async def set_call_progress(self, *args: Any, **kwargs: Any) -> None:
        return None


def _context() -> LiveCallContext:
    return LiveCallContext(
        session_id="sess_8f2c1a",
        bus=UiEventBus(),
        progress=CallProgress(screen=IntakeScreen.WELCOME),
        sessions=_NullSessions(),  # type: ignore[arg-type]
        consent_given=False,
    )


def _drain(bus: UiEventBus) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    while not bus._queue.empty():  # noqa: SLF001 - the queue is the assertion surface
        frames.append(bus._queue.get_nowait())  # noqa: SLF001
    return frames


_AUDIO = {"type": "bidi_audio_stream", "audio": "AAAA", "sample_rate": 16_000}
_TURN_END = {"type": "bidi_response_complete"}

# A second of speech, to tell "the agent stopped generating" apart from
# "the patient stopped hearing" -- the whole of the third bug above.
_LONG_AUDIO = {
    "type": "bidi_audio_stream",
    "audio": "A" * (16_000 * 2 * 4 // 3),
    "sample_rate": 16_000,
}


def _greeting_signalled(context: LiveCallContext) -> None:
    """What `navigate_to_screen` does when the model asks to leave `welcome`."""
    context.greeting_finished = True


@pytest.fixture
def context() -> LiveCallContext:
    return _context()


@pytest.fixture
def output(context: LiveCallContext) -> BrowserOutput:
    return BrowserOutput(context, CallControl())


async def test_the_greeting_screen_is_held_until_something_has_been_said(
    output: BrowserOutput, context: LiveCallContext
) -> None:
    """A turn that only made a tool call has not greeted anyone.

    Nova ends a completion for a tool call as readily as for speech, so
    without this guard the very first tool call of the call would skip the
    greeting just as surely as the old behaviour did.
    """
    await output(_TURN_END)

    assert context.progress.screen is IntakeScreen.WELCOME
    assert [frame["type"] for frame in _drain(context.bus)] == ["agent_speaking"]


async def test_the_move_is_emitted_after_every_chunk_of_the_greeting(
    output: BrowserOutput, context: LiveCallContext
) -> None:
    """Ordering on the wire is the whole fix.

    The browser holds a screen change until queued playback drains, which
    only works if the audio reached it first. Emitting the move at the end
    of the turn is what guarantees that.
    """
    await output(_AUDIO)
    await output(_AUDIO)
    _greeting_signalled(context)
    await output(_TURN_END)

    frames = [frame["type"] for frame in _drain(context.bus)]

    assert frames.index("navigate") > max(
        index for index, name in enumerate(frames) if name == "agent_audio"
    )
    assert context.progress.screen is IntakeScreen.CONFIRM_DETAILS


async def test_the_move_happens_once_and_does_not_repeat(
    output: BrowserOutput, context: LiveCallContext
) -> None:
    """Every later turn ends the same way; only the first one may move them."""
    await output(_AUDIO)
    _greeting_signalled(context)
    await output(_TURN_END)
    _drain(context.bus)

    await output(_AUDIO)
    await output(_TURN_END)

    assert "navigate" not in [frame["type"] for frame in _drain(context.bus)]
    assert context.progress.screen is IntakeScreen.CONFIRM_DETAILS


async def test_a_resumed_call_past_the_greeting_is_left_alone(
    output: BrowserOutput, context: LiveCallContext
) -> None:
    """Consent on record means the greeting already happened, in an earlier call."""
    context.progress.screen = IntakeScreen.MEDICATION

    await output(_AUDIO)
    await output(_TURN_END)

    assert context.progress.screen is IntakeScreen.MEDICATION
    assert "navigate" not in [frame["type"] for frame in _drain(context.bus)]


# --------------------------------------------------------------------------
# The watchdog, for when the model never says the turn ended
# --------------------------------------------------------------------------


@pytest.fixture
def quick_watchdog(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shrink the silence window so these run in milliseconds, not seconds."""
    monkeypatch.setattr(channels, "GREETING_SILENCE_SECONDS", 0.02)


async def _settle(seconds: float = 0.1) -> None:
    """Let the watchdog task run to completion."""
    await asyncio.sleep(seconds)


@pytest.mark.usefixtures("quick_watchdog")
async def test_silence_after_the_greeting_moves_them_on_without_the_model(
    output: BrowserOutput, context: LiveCallContext
) -> None:
    """The reported bug: the greeting is spoken in full and `completionEnd` never comes.

    Nothing else rescued the patient. The browser's own fallback disarms
    the instant the assistant speaks -- deliberately, so it cannot cut a
    greeting short -- so once the model went quiet without ending its turn,
    the introduction screen was where they stayed.
    """
    await output(_AUDIO)
    await output(_AUDIO)

    await _settle()

    assert context.progress.screen is IntakeScreen.CONFIRM_DETAILS
    assert "navigate" in [frame["type"] for frame in _drain(context.bus)]


@pytest.mark.usefixtures("quick_watchdog")
async def test_the_watchdog_does_not_fire_while_the_greeting_is_still_streaming(
    output: BrowserOutput, context: LiveCallContext
) -> None:
    """It measures the gap between chunks, so a turn still arriving keeps resetting it."""
    for _ in range(6):
        await output(_AUDIO)
        await asyncio.sleep(0.005)

    assert context.progress.screen is IntakeScreen.WELCOME


@pytest.mark.usefixtures("quick_watchdog")
async def test_the_model_ending_its_turn_still_wins_and_moves_them_once(
    output: BrowserOutput, context: LiveCallContext
) -> None:
    """The watchdog is a backstop, not a second mover: no duplicate navigate."""
    await output(_AUDIO)
    _greeting_signalled(context)
    await output(_TURN_END)
    frames = [frame["type"] for frame in _drain(context.bus)]

    await _settle()

    assert frames.count("navigate") == 1
    assert "navigate" not in [frame["type"] for frame in _drain(context.bus)]
    assert context.progress.screen is IntakeScreen.CONFIRM_DETAILS


@pytest.mark.usefixtures("quick_watchdog")
async def test_the_watchdog_is_dropped_when_the_call_stops(
    output: BrowserOutput, context: LiveCallContext
) -> None:
    """A pending task must not outlive the call and move a screen nobody is watching."""
    await output(_AUDIO)
    await output.stop()

    await _settle()

    assert context.progress.screen is IntakeScreen.WELCOME


async def test_a_silent_consent_screen_prompts_the_model_to_ask(
    monkeypatch: pytest.MonkeyPatch, context: LiveCallContext, output: BrowserOutput
) -> None:
    """Moving the patient to the consent screen is not the same as asking them.

    The app owns the navigation and it was reliable. The *question* is the
    model's, and nothing guaranteed it: Nova Sonic produces a turn only
    when something reaches its input stream, so a model that treated the
    navigate reply as the end of its turn left the patient looking at a
    consent screen in silence, with the greeting still showing as the last
    thing anyone had said. From their side the call had stopped.

    The screen watchdogs above could not cover this -- they move the
    screen, and the screen was already right.
    """
    monkeypatch.setattr(channels, "CONSENT_PROMPT_SILENCE_SECONDS", 0.02)

    await output(_AUDIO)
    _greeting_signalled(context)
    await output(_TURN_END)
    await asyncio.sleep(0.1)

    assert context.progress.screen is IntakeScreen.CONFIRM_DETAILS
    assert not context.nudges.empty()
    assert "consent box" in context.nudges.get_nowait()


async def test_a_turn_ending_partway_through_the_greeting_does_not_move_them(
    output: BrowserOutput, context: LiveCallContext
) -> None:
    """The reported bug: the welcome message is cut off in the middle.

    Nova closes a completion whenever it yields, so the greeting routinely
    spans more than one -- step 1's introduction and step 2's emergency
    notice need not arrive in the same turn. Moving on the first completion
    to carry any speech at all sent the patient to the consent screen with
    the notice still unsaid, and with the greeting caption frozen partway
    through it.

    Only the model knows when it has said everything, and asking to leave
    `welcome` is how step 3 has it say so.
    """
    await output(_AUDIO)
    await output(_TURN_END)

    assert context.progress.screen is IntakeScreen.WELCOME
    assert "navigate" not in [frame["type"] for frame in _drain(context.bus)]

    # The rest of the greeting, and then the model's own signal.
    await output(_AUDIO)
    _greeting_signalled(context)
    await output(_TURN_END)

    assert context.progress.screen is IntakeScreen.CONFIRM_DETAILS


@pytest.mark.usefixtures("quick_watchdog")
async def test_the_watchdog_waits_for_the_patient_to_finish_hearing_the_greeting(
    output: BrowserOutput, context: LiveCallContext
) -> None:
    """Silence at the generator is not silence at the ear.

    Nova streams the whole greeting in a second or two and the browser
    takes twenty to play it, so the gap after its last chunk clears the
    silence window immediately. This watchdog therefore fired on every
    call, a few seconds into an introduction the patient was still
    listening to -- finalizing the caption and publishing
    `agent_speaking: False` on speech that had barely started.
    """
    await output(_LONG_AUDIO)

    # Well past the silence window, and still mid-greeting.
    await asyncio.sleep(0.1)
    assert context.progress.screen is IntakeScreen.WELCOME
    assert context.remaining_playback_seconds() > 0

    await asyncio.sleep(context.remaining_playback_seconds() + 0.1)
    assert context.progress.screen is IntakeScreen.CONFIRM_DETAILS


async def test_a_model_that_does_ask_is_never_interrupted(
    monkeypatch: pytest.MonkeyPatch, context: LiveCallContext, output: BrowserOutput
) -> None:
    """The nudge speaks into silence only, never over a live turn."""
    monkeypatch.setattr(channels, "CONSENT_PROMPT_SILENCE_SECONDS", 0.05)

    await output(_AUDIO)
    await output(_TURN_END)
    # The model gets on with it, on the consent screen.
    await output(_AUDIO)
    await asyncio.sleep(0.15)

    assert context.nudges.empty()


async def test_the_fallback_route_also_gets_the_patient_asked(
    monkeypatch: pytest.MonkeyPatch, context: LiveCallContext, output: BrowserOutput
) -> None:
    """The route that actually strands people, reproduced.

    `bidi_response_complete` never arrives -- the documented Nova Sonic
    behaviour this whole fallback exists for -- so the silence watchdog
    navigates instead. That is also the run where the model has gone
    quiet, so arming the consent prompt only on the clean path left the
    patient on a silent consent screen: greeting still captioned, avatar
    still saying "Speaking...", nothing else ever happening.
    """
    monkeypatch.setattr(channels, "GREETING_SILENCE_SECONDS", 0.02)
    monkeypatch.setattr(channels, "CONSENT_PROMPT_SILENCE_SECONDS", 0.02)

    await output(_AUDIO)  # the greeting, and no completion event ever follows
    await asyncio.sleep(0.15)

    assert context.progress.screen is IntakeScreen.CONFIRM_DETAILS
    assert not context.nudges.empty()
    assert "consent box" in context.nudges.get_nowait()


async def test_the_fallback_route_tells_the_browser_the_greeting_ended(
    monkeypatch: pytest.MonkeyPatch, context: LiveCallContext, output: BrowserOutput
) -> None:
    """Navigating is not the same as saying the turn is over.

    Without this the browser is told the screen moved but never that the
    greeting finished, so the caption freezes on it and the avatar shows
    "Speaking..." for the rest of the call -- both visible on the consent
    screen while nothing is actually being said.
    """
    monkeypatch.setattr(channels, "GREETING_SILENCE_SECONDS", 0.02)
    monkeypatch.setattr(channels, "CONSENT_PROMPT_SILENCE_SECONDS", 5.0)

    await output(
        {
            "type": "bidi_transcript_stream",
            "role": "assistant",
            "text": "Hello, this is a short call.",
            "is_final": True,
        }
    )
    await output(_AUDIO)
    await asyncio.sleep(0.1)

    frames = _drain(context.bus)
    assert any(frame["type"] == "transcript" and frame["is_final"] for frame in frames), (
        "the greeting was never marked finished"
    )
    assert any(
        frame["type"] == "agent_speaking" and frame["speaking"] is False for frame in frames
    ), "the avatar was never told to stop speaking"

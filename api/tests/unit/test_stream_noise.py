"""The voice stream's teardown noise is filtered; nothing else is.

The risk in a filter like this is that it hides the failure it was meant
to sit next to. These tests pin both halves: the two known
post-completion artefacts are swallowed, and an ordinary error from the
same module and the same call still reaches the handler underneath.
"""

import asyncio
from concurrent.futures import InvalidStateError
from types import ModuleType
from typing import Any

from app.core.stream_noise import install_stream_noise_filter


def _raised_as_awscrt(exc: BaseException) -> BaseException:
    """Give `exc` a traceback frame belonging to `awscrt.aio.http`.

    The filter matches on where an exception came from, so a test that
    only constructs one proves nothing. Executing the raise inside a
    module whose `__name__` is `awscrt.aio.http` is what produces a real
    frame to match against.
    """
    module = ModuleType("awscrt.aio.http")
    module.__dict__["exc"] = exc
    try:
        exec("raise exc", module.__dict__)  # noqa: S102 - the frame is the point
    except BaseException as raised:  # noqa: BLE001 - re-raised only as a value
        return raised
    raise AssertionError("the exception was not raised")


async def _capture(exception: BaseException | None) -> list[dict[str, Any]]:
    """Install the filter, report one loop exception, return what got through."""
    loop = asyncio.get_running_loop()
    seen: list[dict[str, Any]] = []
    loop.set_exception_handler(lambda _loop, context: seen.append(context))

    restore = install_stream_noise_filter()
    try:
        loop.call_exception_handler({"message": "Task exception", "exception": exception})
    finally:
        restore()
    return seen


async def test_a_completed_stream_write_is_swallowed() -> None:
    """awscrt cancels its own request-body generator after the stream ends."""
    noise = _raised_as_awscrt(
        RuntimeError(
            "2080 (AWS_ERROR_HTTP_STREAM_HAS_COMPLETED): HTTP-stream has completed, "
            "action cannot be performed."
        )
    )

    assert await _capture(noise) == []


async def test_resolving_an_already_cancelled_future_is_swallowed() -> None:
    """The stream's completion callback resolves futures asyncio has cancelled."""
    noise = _raised_as_awscrt(InvalidStateError("CANCELLED: <Future at 0x1 state=cancelled>"))

    assert await _capture(noise) == []


async def test_a_real_failure_on_the_same_stream_still_gets_through() -> None:
    """The filter is by signature, not by module: other awscrt errors stay loud."""
    real = _raised_as_awscrt(RuntimeError("2058 (AWS_ERROR_HTTP_CONNECTION_CLOSED)"))

    assert len(await _capture(real)) == 1


async def test_an_unrelated_exception_is_untouched() -> None:
    """Anything not raised inside awscrt goes straight through."""
    assert len(await _capture(ValueError("something the app got wrong"))) == 1


async def test_a_context_with_no_exception_is_untouched() -> None:
    """asyncio reports some conditions as a message alone."""
    assert len(await _capture(None)) == 1


async def test_restoring_puts_the_previous_handler_back() -> None:
    """A filter that outlives its loop would swallow another loop's errors."""
    loop = asyncio.get_running_loop()
    original = loop.get_exception_handler()

    restore = install_stream_noise_filter()
    assert loop.get_exception_handler() is not original
    restore()

    assert loop.get_exception_handler() is original

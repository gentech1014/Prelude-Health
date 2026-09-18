"""Quietens awscrt's HTTP/2 teardown noise, and nothing else.

When a live voice stream ends, `awscrt.aio.http` unwinds a request-body
generator for a stream that has already completed. Two artefacts follow,
both after the call is over and both inside the library: the cancelled
generator task surfaces as "Task exception was never retrieved", and the
stream's completion callback resolves futures asyncio has already
cancelled, which the interpreter reports as an ignored `InvalidStateError`.
Neither is actionable -- the stream is closed either way -- and a
multi-frame traceback per call buries the log lines that do matter.

Filtered by signature rather than by silencing the handlers: an
`AWS_ERROR_HTTP_STREAM_HAS_COMPLETED` raised from `awscrt.aio.http`, or an
`InvalidStateError` on an already-cancelled future raised from that same
module. Everything else -- including every other awscrt error, on the same
stream -- reaches the handler that was installed before this one, so a
real failure in the voice stream still shows up in full.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable
from concurrent.futures import InvalidStateError
from typing import Any

import structlog

log = structlog.get_logger(__name__)

_AWSCRT_HTTP = "awscrt.aio.http"
_COMPLETED_STREAM = "AWS_ERROR_HTTP_STREAM_HAS_COMPLETED"


def _raised_inside_awscrt_http(exc: BaseException) -> bool:
    """Whether any frame in the traceback belongs to `awscrt.aio.http`.

    Matched on the frame's module name rather than a file path, so it
    holds wherever the package is installed.
    """
    traceback = exc.__traceback__
    while traceback is not None:
        if traceback.tb_frame.f_globals.get("__name__") == _AWSCRT_HTTP:
            return True
        traceback = traceback.tb_next
    return False


def _is_stream_teardown_noise(exc: BaseException | None) -> bool:
    """Whether this is one of the two known post-completion artefacts."""
    if exc is None or not _raised_inside_awscrt_http(exc):
        return False
    if _COMPLETED_STREAM in str(exc):
        return True
    return isinstance(exc, InvalidStateError) and "cancelled" in str(exc).lower()


def install_stream_noise_filter() -> Callable[[], None]:
    """Filter the two artefacts for the running loop and this process.

    Returns the function that puts both handlers back, so the filter lasts
    exactly as long as the app does -- `sys.unraisablehook` is global, and
    leaving a stale one installed would outlive the loop it was set up for.
    """
    loop = asyncio.get_running_loop()
    previous_loop_handler = loop.get_exception_handler()
    previous_unraisable_hook = sys.unraisablehook

    def handle_loop_exception(current_loop: asyncio.AbstractEventLoop, context: Any) -> None:
        if _is_stream_teardown_noise(context.get("exception")):
            log.debug("awscrt_stream_teardown_ignored", detail=context.get("message"))
            return
        if previous_loop_handler is None:
            current_loop.default_exception_handler(context)
        else:
            previous_loop_handler(current_loop, context)

    def handle_unraisable(unraisable: Any) -> None:
        if _is_stream_teardown_noise(unraisable.exc_value):
            log.debug("awscrt_stream_teardown_ignored", detail=unraisable.err_msg)
            return
        previous_unraisable_hook(unraisable)

    loop.set_exception_handler(handle_loop_exception)
    sys.unraisablehook = handle_unraisable

    def restore() -> None:
        loop.set_exception_handler(previous_loop_handler)
        sys.unraisablehook = previous_unraisable_hook

    return restore

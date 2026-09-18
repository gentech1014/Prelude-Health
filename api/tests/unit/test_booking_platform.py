"""Unit tests for the booking webhook's HMAC signature verification.

This is the only thing standing between a forged HTTP request and a
fabricated patient session, so its failure modes are worth pinning
explicitly -- same reasoning as `tests/unit/test_security.py` for the
intake token.
"""

import hashlib
import hmac

from app.core.config import Settings
from app.integrations.booking_platform import BookingPlatformClient

_SECRET = "webhook-test-secret-that-is-long-enough-32"  # noqa: S105 - test constant
_BODY = b'{"appointment_id":"appt_1","patient_name":"Asha Rao"}'


def _client(secret: str = _SECRET) -> BookingPlatformClient:
    settings = Settings(
        _env_file=None,  # isolate from the developer's real .env -- see tests/conftest.py
        intake_link_secret="unused-but-required-secret-32-chars",  # noqa: S106 - test fixture
        booking_webhook_secret=secret,
        physician_api_key="unused-but-required-key-32-characters",  # noqa: S106 - test fixture
    )
    return BookingPlatformClient(settings, http_client=None)  # type: ignore[arg-type]


def _sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_correctly_signed_body_verifies() -> None:
    """The happy path: a signature computed with the right secret verifies."""
    signature = _sign(_SECRET, _BODY)

    assert _client().verify_webhook_signature(_BODY, signature) is True


def test_signature_from_another_secret_is_rejected() -> None:
    """A signature computed under a different key must not verify."""
    signature = _sign("a-completely-different-secret-value", _BODY)

    assert _client().verify_webhook_signature(_BODY, signature) is False


def test_signature_for_different_body_is_rejected() -> None:
    """The signature is over these exact bytes -- a tampered body must fail.

    Guards against verifying against the parsed model instead of the raw
    bytes, which would let an attacker alter fields the parser ignores.
    """
    signature = _sign(_SECRET, _BODY)
    tampered_body = _BODY.replace(b"Asha Rao", b"Someone Else")

    assert _client().verify_webhook_signature(tampered_body, signature) is False


def test_garbage_signature_is_rejected_without_raising() -> None:
    """Malformed input at the boundary returns False, not an exception."""
    assert _client().verify_webhook_signature(_BODY, "not-a-hex-digest") is False

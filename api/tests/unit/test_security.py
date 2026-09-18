"""Unit tests for the signed intake-link token.

This token is the only thing standing between a leaked `session_id` and
someone else's pre-screening call, so its failure modes are worth pinning
explicitly.
"""

import time
from unittest.mock import patch

import pytest

from app.core.security import generate_intake_token, verify_intake_token

_SECRET = "test-secret"  # noqa: S105 - test constant, not a real credential


def test_freshly_issued_token_verifies() -> None:
    """The happy path: a token issued for a session validates for it."""
    token = generate_intake_token("sess_1", _SECRET, ttl_seconds=3600)

    assert verify_intake_token("sess_1", token, _SECRET) is True


def test_token_is_bound_to_its_session() -> None:
    """A valid token must not unlock a different patient's session."""
    token = generate_intake_token("sess_1", _SECRET, ttl_seconds=3600)

    assert verify_intake_token("sess_2", token, _SECRET) is False


def test_token_from_another_secret_is_rejected() -> None:
    """Signatures must not verify under a different signing key."""
    token = generate_intake_token("sess_1", "other-secret", ttl_seconds=3600)

    assert verify_intake_token("sess_1", token, _SECRET) is False


def test_expired_token_is_rejected() -> None:
    """A token past its expiry must fail even though its signature is valid."""
    token = generate_intake_token("sess_1", _SECRET, ttl_seconds=60)

    with patch.object(time, "time", return_value=time.time() + 120):
        assert verify_intake_token("sess_1", token, _SECRET) is False


def test_tampering_with_the_expiry_invalidates_the_signature() -> None:
    """Extending the deadline by hand must not extend the token's life.

    The expiry is signed alongside the session id precisely so it cannot
    be edited in transit.
    """
    token = generate_intake_token("sess_1", _SECRET, ttl_seconds=60)
    _, signature = token.split(".", 1)
    forged = f"{int(time.time()) + 99999}.{signature}"

    assert verify_intake_token("sess_1", forged, _SECRET) is False


@pytest.mark.parametrize(
    "malformed",
    ["", "not-a-token", "abc.def", "....", "123456"],
    ids=["empty", "no-separator", "non-numeric-expiry", "only-dots", "no-signature"],
)
def test_malformed_tokens_are_rejected_without_raising(malformed: str) -> None:
    """Garbage input returns False rather than throwing at the socket boundary."""
    assert verify_intake_token("sess_1", malformed, _SECRET) is False

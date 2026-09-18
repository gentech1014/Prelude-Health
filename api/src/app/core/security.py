"""Signed, expiring, purpose-bound tokens for the patient's session.

Three credentials, all HMAC-SHA256 over the same shape, separated by a
`purpose` field that is part of the signed payload:

- **intake** -- minted at booking, travels inside the emailed/SMS link, TTL in
  days. The only one that ever appears in a URL bar.
- **session** -- exchanged from an intake token at attach time and set as an
  `HttpOnly` cookie, TTL in hours. Page JavaScript cannot read it, so an XSS
  on the patient app cannot exfiltrate it.
- **ws** -- minted on demand by an authenticated session, TTL in seconds,
  spent on first use. Carries a nonce so it can be single-use; see
  `app.repositories.ws_ticket_repository`.

Binding the purpose into the signature is what stops the credentials being
interchangeable. Without it, a 48-hour intake token lifted from a URL would
open the WebSocket directly, and the short-lived ticket would be pointless.
Without any of it, `app.api.ws.intake` would accept any client that merely
knows or guesses a `session_id` -- those appear in URLs, logs, and webhook
payloads, so they are not secrets on their own.
"""

import hashlib
import hmac
import secrets
import time
from typing import Literal

TokenPurpose = Literal["intake", "session", "ws"]


def generate_scoped_token(
    session_id: str, secret: str, ttl_seconds: int, purpose: TokenPurpose
) -> str:
    """Return a `<purpose>.<expires_at>[.<nonce>].<signature>` token.

    A `ws` token carries a random nonce so the spend-once check has
    something to record; the other two purposes have no such need and
    omit it.
    """
    expires_at = int(time.time()) + ttl_seconds
    nonce = secrets.token_urlsafe(16) if purpose == "ws" else ""
    signature = _sign(session_id, expires_at, purpose, nonce, secret)
    parts = [purpose, str(expires_at)]
    if nonce:
        parts.append(nonce)
    parts.append(signature)
    return ".".join(parts)


def verify_scoped_token(
    session_id: str, token: str, secret: str, purpose: TokenPurpose
) -> str | None:
    """Verify a token was issued for this `session_id` *and* this `purpose`.

    Returns the token's nonce on success -- an empty string for purposes
    that carry none -- and `None` on any failure. A nonce is returned
    rather than a bare bool because the caller of a `ws` token must go on
    to spend that nonce; there is no way to recompute it from the outside.

    Uses a constant-time signature comparison so a wrong token leaks no
    timing information about the expected value.
    """
    parts = token.split(".")
    if len(parts) == 3:
        token_purpose, expires_at_str, signature = parts
        nonce = ""
    elif len(parts) == 4:
        token_purpose, expires_at_str, nonce, signature = parts
    else:
        return None

    if not hmac.compare_digest(token_purpose, purpose):
        return None

    try:
        expires_at = int(expires_at_str)
    except ValueError:
        return None

    if time.time() > expires_at:
        return None

    expected = _sign(session_id, expires_at, purpose, nonce, secret)
    if not hmac.compare_digest(expected, signature):
        return None

    return nonce


def generate_intake_token(session_id: str, secret: str, ttl_seconds: int) -> str:
    """Mint the long-lived token carried in the patient's intake link."""
    return generate_scoped_token(session_id, secret, ttl_seconds, "intake")


def verify_intake_token(session_id: str, token: str, secret: str) -> bool:
    """Verify an intake-link token. Accepts only `purpose=intake`."""
    return verify_scoped_token(session_id, token, secret, "intake") is not None


def generate_session_token(session_id: str, secret: str, ttl_seconds: int) -> str:
    """Mint the value stored in the `HttpOnly` browser session cookie."""
    return generate_scoped_token(session_id, secret, ttl_seconds, "session")


def verify_session_token(session_id: str, token: str, secret: str) -> bool:
    """Verify a browser session cookie. Accepts only `purpose=session`."""
    return verify_scoped_token(session_id, token, secret, "session") is not None


def generate_ws_ticket(session_id: str, secret: str, ttl_seconds: int) -> str:
    """Mint a short-lived, single-use ticket authorizing one WebSocket connect."""
    return generate_scoped_token(session_id, secret, ttl_seconds, "ws")


def verify_ws_ticket(session_id: str, token: str, secret: str) -> str | None:
    """Verify a WebSocket ticket and return its nonce for the caller to spend."""
    return verify_scoped_token(session_id, token, secret, "ws")


def _sign(session_id: str, expires_at: int, purpose: str, nonce: str, secret: str) -> str:
    payload = f"{purpose}:{session_id}:{expires_at}:{nonce}".encode()
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

"""Client for the scheduling/booking platform's API.

Exact vendor is not yet decided (see architecture doc, open items) —
this client is the seam that isolates that decision from the rest of
the service.
"""

import hashlib
import hmac

import httpx

from app.core.config import Settings


class BookingPlatformClient:
    """Thin wrapper around the scheduling platform's HTTP API."""

    def __init__(self, settings: Settings, http_client: httpx.AsyncClient) -> None:
        self._settings = settings
        self._http = http_client

    def verify_webhook_signature(self, raw_body: bytes, signature_header: str) -> bool:
        """Verify an inbound booking webhook was actually sent by the platform.

        Same HMAC-SHA256-over-raw-bytes construction as
        `app.core.security`'s intake tokens, compared in constant time so a
        mismatch cannot be timed to leak the expected signature. Returns a
        bool; the caller (`app.api.v1.webhooks.booking_confirmed`) is what
        turns a `False` into `BookingWebhookVerificationError`.
        """
        expected = hmac.new(
            self._settings.booking_webhook_secret.encode(), raw_body, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature_header)

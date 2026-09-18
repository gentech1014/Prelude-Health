"""Domain-level exceptions.

Kept distinct from FastAPI's `HTTPException` so that domain/service code
stays framework-agnostic. Each subclass carries a `status_code`; a single
generic handler in `app.main` translates any of these into an HTTP
response without the API layer needing to know about each error type.
"""


class PrescreeningError(Exception):
    """Base class for all domain errors raised by this service."""

    status_code: int = 500


class DatabaseUnavailableError(PrescreeningError):
    """Raised when the database could not be reached for an operation.

    A distinct type because it is the one failure here that is *nobody's
    fault and probably temporary*: a replica-set failover, a dropped VPN, a
    DNS resolver that stopped answering. The caller should be told to try
    again, not shown a stack trace -- so it carries 503 rather than
    inheriting the base 500, and `app.main` translates every driver-level
    error into one of these.
    """

    status_code = 503

    def __init__(self, message: str = "The database is temporarily unreachable") -> None:
        super().__init__(message)


class SessionNotFoundError(PrescreeningError):
    """Raised when a session_id does not match any stored session."""

    status_code = 404

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        super().__init__(f"No session found for session_id={session_id!r}")


class InvalidSessionStateError(PrescreeningError):
    """Raised when an operation is attempted from an illegal session state."""

    status_code = 409

    def __init__(self, session_id: str, current_state: str, expected: str) -> None:
        self.session_id = session_id
        self.current_state = current_state
        self.expected = expected
        super().__init__(
            f"Session {session_id!r} is in state {current_state!r}, expected {expected!r}"
        )


class ConsentNotRecordedError(PrescreeningError):
    """Raised when the live agent reaches for clinical content before consent.

    The socket is allowed to open before consent so the patient can hear
    the greeting and be asked -- but the screening questions are the
    clinical part of the call, and handing them over early would be
    collecting by another name. Enforced here rather than left to the
    prompt: an instruction is not a control.
    """

    status_code = 403

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        super().__init__(f"No affirmative consent on record for session_id={session_id!r}")


class SummarizationFailedError(PrescreeningError):
    """Raised when the summarization agent returns no structured output."""

    status_code = 502

    def __init__(self, session_id: str, stop_reason: str | None) -> None:
        self.session_id = session_id
        self.stop_reason = stop_reason
        super().__init__(
            f"Summarization failed for session_id={session_id!r} (stop_reason={stop_reason!r})"
        )


class BookingWebhookVerificationError(PrescreeningError):
    """Raised when an inbound booking webhook fails signature verification."""

    status_code = 401

    def __init__(self) -> None:
        super().__init__("Webhook signature verification failed")


class IntakeTokenInvalidError(PrescreeningError):
    """Raised when a patient-facing route's intake token is missing, forged, or expired."""

    status_code = 401

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        super().__init__(f"Invalid or expired intake token for session_id={session_id!r}")


class SessionAuthError(PrescreeningError):
    """Raised when a patient route's session cookie is missing, forged, or expired.

    Distinct from `IntakeTokenInvalidError`: that one means the *link*
    failed, and the patient needs a new link. This one means the browser
    session lapsed, and the patient only needs to re-open the link they
    already have -- a different message and a different recovery path.
    """

    status_code = 401

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        super().__init__(f"Missing or invalid session for session_id={session_id!r}")


class WsTicketInvalidError(PrescreeningError):
    """Raised when a WebSocket ticket is forged, expired, or already spent."""

    status_code = 401

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        super().__init__(f"Invalid or already-used WebSocket ticket for {session_id!r}")


class PhysicianAuthError(PrescreeningError):
    """Raised when a physician-facing route's X-API-Key header is missing or wrong."""

    status_code = 401

    def __init__(self) -> None:
        super().__init__("Missing or invalid X-API-Key")


class DoctorNotFoundError(PrescreeningError):
    """Raised when a booking request names a doctor with no calendar mapping on file."""

    status_code = 404

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"No doctor found with name={name!r}")


class SlotUnavailableError(PrescreeningError):
    """Raised when a slot is invalid, taken, too late, or belongs to another doctor.

    Covers every reason `reschedule_appointment` can fail its guarded claim:
    the slot doesn't exist, another patient just took it, it isn't earlier
    than the current appointment, or it's for a different physician. All
    four fail the same way -- the caller keeps their original appointment
    and is told to try a different slot -- so they share one error type.
    """

    status_code = 409

    def __init__(self, slot_id: str, reason: str) -> None:
        self.slot_id = slot_id
        self.reason = reason
        super().__init__(f"Slot {slot_id!r} unavailable: {reason}")


class StorageError(PrescreeningError):
    """Raised when an object-storage operation (upload, presigned URL) fails."""

    status_code = 502

    def __init__(self, operation: str, cause: Exception) -> None:
        self.operation = operation
        super().__init__(f"Object storage {operation!r} failed: {cause}")


class AwsCredentialsNotConfiguredError(PrescreeningError):
    """Raised when an AWS call is attempted with no credentials in this app's own config.

    A 503 rather than a 500, for the same reason as
    `GoogleOAuthNotConfiguredError`: nothing is broken, the deployment
    simply has not been given credentials, and an operator can fix it
    without a code change.

    Deliberately raised instead of falling through to boto3's ambient
    credential chain. That chain reads the machine's shared credentials
    file, its instance profile, and its environment -- so a developer with
    an expired SSO session, or a CI box with an unrelated role, gets a
    confusing `403 ExpiredTokenException` from the middle of a live patient
    call rather than a configuration error at the point of use.
    """

    status_code = 503

    def __init__(self, purpose: str) -> None:
        self.purpose = purpose
        super().__init__(
            f"No AWS credentials configured for {purpose}. Set AWS_ACCESS_KEY_ID and "
            "AWS_SECRET_ACCESS_KEY (plus AWS_SESSION_TOKEN for temporary credentials) "
            "in this service's environment."
        )


class GoogleOAuthNotConfiguredError(PrescreeningError):
    """Raised when doctor registration is attempted without OAuth client credentials.

    A 503 rather than a 500: nothing is broken, the deployment simply has
    not been given a Google OAuth client yet, and the operator can fix it
    without a code change.
    """

    status_code = 503

    def __init__(self) -> None:
        super().__init__(
            "Google sign-in is not configured on this deployment. "
            "Set GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET."
        )


class GoogleOAuthExchangeError(PrescreeningError):
    """Raised when Google rejects the authorization code or the token response is unusable."""

    status_code = 502

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Google token exchange failed: {reason}")


class DoctorRegistrationStateInvalidError(PrescreeningError):
    """Raised when an OAuth callback's `state` is unknown, expired, or already claimed."""

    status_code = 400

    def __init__(self) -> None:
        super().__init__("This registration link has expired or was already used. Start again.")


class AppointmentTimeUnavailableError(PrescreeningError):
    """Raised when a booking is submitted for a time that is no longer bookable.

    A 409, not a 422: the request was well-formed and was valid when the
    patient saw the time -- someone else simply took it first, and the
    booking UI has to re-fetch availability rather than fix its payload.

    Distinct from `SlotUnavailableError`, which names a specific
    `appointment_slots` document that could not be claimed. This one is
    raised before any slot id exists, from a free/busy check against the
    doctor's calendar, so a doctor name is all it can identify.
    """

    status_code = 409

    def __init__(self, doctor_name: str) -> None:
        self.doctor_name = doctor_name
        super().__init__(f"That time is no longer available with {doctor_name}. Pick another slot.")

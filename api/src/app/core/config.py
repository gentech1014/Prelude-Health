"""Application settings, loaded from environment variables.

Every value defaults to a safe local-dev shape where possible; secrets have
no default and must be supplied via the environment or `.env`.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed, validated application configuration.

    Instantiated once per process via `get_settings` and treated as
    immutable for the lifetime of the app.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- App ---
    app_env: str = "local"
    app_debug: bool = False
    app_host: str = "0.0.0.0"
    app_port: int = 8000

    # --- Mongo ---
    mongo_uri: str = "mongodb://localhost:27017"
    mongo_db_name: str = "prescreening"

    # The driver's own defaults are 30s server selection and 20s connect,
    # which are tuned for a batch job rather than for a service a patient is
    # waiting on. On a degraded network they turn one unreachable replica
    # into a request that hangs for half a minute before failing -- observed
    # against Atlas over a slow link, where TLS handshakes to the primary
    # exceeded the 20s connect timeout and every booking read blocked for
    # the full 30s. Failing in a few seconds is strictly better: the caller
    # can retry, and a voice call cannot wait either way.
    mongo_server_selection_timeout_ms: int = Field(
        default=5_000,
        description=(
            "How long to look for a suitable replica-set member before giving up. "
            "Covers a routine failover (a few seconds) without letting a genuinely "
            "unreachable cluster hold a request open."
        ),
    )
    mongo_connect_timeout_ms: int = Field(
        default=10_000,
        description=(
            "TCP connect plus TLS handshake budget per node. Generous relative to "
            "server selection because an Atlas handshake over a slow link can "
            "legitimately take seconds -- it is the total wait that has to be bounded."
        ),
    )
    mongo_socket_timeout_ms: int = Field(
        default=20_000,
        description=(
            "How long one already-established operation may block. Unset by default "
            "in the driver, which means a stalled socket never returns at all."
        ),
    )

    # --- AWS / Bedrock ---
    # Nova 2 Sonic is In-Region only and supports no cross-region inference,
    # so the region is constrained at load time. Catching a bad value here
    # beats a confusing failure deep inside the streaming client, mid-call.
    aws_region: Literal["us-east-1", "us-west-2", "eu-north-1", "ap-northeast-1"] = "us-east-1"

    # Credentials are read from this service's own configuration and used
    # explicitly, never left to boto3's ambient chain -- see
    # `app.core.aws.build_boto_session` for why that distinction matters.
    # These were previously absent, which meant a value set in `.env` was
    # silently dropped and the machine's own credentials were used instead.
    aws_access_key_id: str = Field(default="")
    aws_secret_access_key: str = Field(default="")
    aws_session_token: str = Field(
        default="",
        description=(
            "Required only for temporary credentials -- an STS/SSO key (one "
            "beginning `ASIA`) is rejected without it. Permanent IAM user keys "
            "(beginning `AKIA`) need no token, and leaving this blank for one is "
            "correct rather than an omission."
        ),
    )

    # --- Live voice model (Nova 2 Sonic, speech-to-speech) ---
    bidi_model_id: str = Field(
        default="amazon.nova-2-sonic-v1:0",
        description=(
            "Must be a bare model ID. Unlike the text models, "
            "`InvokeModelWithBidirectionalStream` rejects an "
            "application-inference-profile ARN with 'The provided model "
            "identifier is invalid' -- verified live against a profile that was "
            "ACTIVE, in the same account and region, and mapped to this exact "
            "model. An earlier comment here claimed Bedrock 'takes both', which "
            "is true of `summary_model_id` and not of this one."
        ),
    )
    bidi_voice: str = "matthew"
    bidi_endpointing_sensitivity: Literal["HIGH", "MEDIUM", "LOW"] = "MEDIUM"

    # --- Summarization model (text, runs after the call) ---
    # Same ARN-or-bare-ID rule as `bidi_model_id`. Unlike the live voice
    # model, this one is not constrained to `aws_region` -- see
    # `summary_model_region` below.
    summary_model_id: str = "global.anthropic.claude-sonnet-4-6"
    summary_model_region: str = Field(
        default="",
        description=(
            "Region to invoke the summarization model in. Falls back to "
            "`aws_region` when blank. Only needs to differ from "
            "`aws_region` when `summary_model_id` is an application "
            "inference profile ARN created in a different region than "
            "the one Nova Sonic is pinned to -- e.g. a Haiku profile in "
            "ap-south-1 alongside Nova Sonic pinned to ap-northeast-1."
        ),
    )

    # --- Appointment scheduling (slots, cancel/reschedule, sweeper) ---
    clinic_timezone: str = Field(
        default="Asia/Kolkata",
        description=(
            "Default IANA zone for appointment times. Overridden per doctor by "
            "`Doctor.timezone` when set. Resolved once at booking time and "
            "stamped onto `Session.appointment_timezone` -- never re-looked-up, "
            "so a later change to this setting cannot retroactively re-render "
            "an already-booked session in a different zone."
        ),
    )
    slot_duration_minutes: int = 30
    reschedule_min_lead_minutes: int = Field(
        default=60,
        description="Never offer a slot starting sooner than this from now.",
    )
    reschedule_offer_limit: int = Field(
        default=3, description="Max earlier slots the agent may mention in one call."
    )
    slot_hold_ttl_seconds: int = Field(
        default=90,
        description=(
            "How long a slot stays HELD while a reschedule is in flight before "
            "an availability query treats it as takeable again."
        ),
    )
    abandoned_slot_reclaim_minutes: int = Field(
        default=15,
        description=(
            "A BOOKED slot whose session is still IN_PROGRESS but hasn't "
            "written a transcript turn in this long is reclaimed by the "
            "sweeper. Not a true liveness signal -- a silent-but-connected "
            "patient looks identical to a dead process -- accepted tradeoff."
        ),
    )
    slot_retention_days: int = Field(
        default=400, description="TTL grace period before a past FREE/CANCELLED slot is purged."
    )
    sweep_interval_seconds: int = Field(
        default=60, description="How often the lifespan sweeper task ticks."
    )
    scheduling_event_retention_days: int = Field(
        default=365,
        description=(
            "TTL for the scheduling_events audit trail -- deliberately "
            "separate from slot_retention_days: slot storage hygiene and "
            "audit history are different lifetimes and must not share one."
        ),
    )

    # --- Booking / scheduling platform ---
    booking_platform_base_url: str = ""
    booking_platform_api_key: str = Field(default="")
    booking_webhook_secret: str = Field(
        min_length=32,
        description=(
            "HMAC key for the inbound booking-confirmed webhook. Required, and "
            "no default: an empty secret still produces a signature that "
            "verifies, so anyone could fabricate a booking event. "
            'Generate with: python -c "import secrets; print(secrets.token_urlsafe(32))"'
        ),
    )

    # --- Patient intake link ---
    patient_app_base_url: str = ""
    intake_link_secret: str = Field(
        min_length=32,
        description=(
            "HMAC key for patient intake links. Required, and no default: an "
            "empty secret still produces tokens that verify, so the only "
            "authorization on a patient call would be forgeable by anyone. "
            'Generate with: python -c "import secrets; print(secrets.token_urlsafe(32))"'
        ),
    )
    intake_link_ttl_seconds: int = 172_800  # 48h -- long enough to cover the pre-appointment window

    # --- Browser session (exchanged from the intake link at attach time) ---
    # The intake token is a bearer credential for PHI that travels in a URL.
    # It is exchanged once, at `POST /sessions/{id}/attach`, for an HttpOnly
    # cookie the page's own JavaScript cannot read -- so an XSS on the patient
    # app cannot exfiltrate it. See `app.core.security` for the token design.
    session_cookie_name: str = "prelude_health_session"
    session_cookie_ttl_seconds: int = 7_200  # 2h -- comfortably longer than one call
    session_cookie_secure: bool = Field(
        default=False,
        description=(
            "Send the session cookie only over HTTPS. Must be True anywhere "
            "real; False by default so local http://localhost dev works at all. "
            "Forced True when `session_cookie_samesite` is 'none', which "
            "browsers reject without Secure."
        ),
    )
    session_cookie_samesite: Literal["lax", "strict", "none"] = Field(
        default="lax",
        description=(
            "'lax' is correct whenever the patient app and this API share a "
            "registrable domain -- including localhost:5173 -> localhost:8000, "
            "since ports do not affect same-site. Set 'none' (which forces "
            "Secure) only when they are genuinely cross-site domains."
        ),
    )
    ws_ticket_ttl_seconds: int = 60
    """How long a WebSocket ticket stays valid. Deliberately tiny: it is minted
    on demand by an already-authenticated session and spent immediately."""

    # --- Physician-facing API access ---
    physician_api_key: str = Field(
        min_length=32,
        description=(
            "Shared secret required as the X-API-Key header on physician-facing "
            "routes (currently: GET /sessions/{id}/report). Required, and no "
            "default: without it the report endpoint would return PHI to anyone "
            "who can reach it. "
            'Generate with: python -c "import secrets; print(secrets.token_urlsafe(32))"'
        ),
    )

    # --- Notifications ---
    notification_provider_api_key: str = Field(default="")

    # --- Object storage ---
    storage_bucket_name: str = ""
    storage_region: str = Field(
        default="",
        description=(
            "AWS region the storage bucket actually lives in. S3 has no region "
            "constraint the way Nova Sonic does, so the bucket is not "
            "guaranteed to be in `aws_region` -- and unlike ordinary S3 API "
            "calls, pre-signed URLs do not auto-redirect across regions; a "
            "mismatch fails with 'AuthorizationQueryParametersError'. Falls "
            "back to `aws_region` when blank."
        ),
    )
    storage_endpoint_url: str = ""
    storage_access_key: str = Field(default="")
    storage_secret_key: str = Field(default="")
    storage_kms_key_id: str = Field(
        default="",
        description=(
            "KMS key ID/ARN for SSE-KMS on uploaded objects. Left blank, "
            "uploads use SSE-S3 (AES256) instead -- still encrypted at rest, "
            "just without a customer-managed key."
        ),
    )

    # --- Browser-facing transport security ---
    allowed_origins: str = Field(
        default="",
        description=(
            "Comma-separated list of allowed browser Origins for the intake "
            "WebSocket. A plain string, not list[str]: pydantic-settings "
            "expects a JSON array literal for list-typed env vars, which is "
            "an easy footgun in most hosting-panel env-var UIs. Left blank, "
            "the Origin check is skipped entirely (permissive) -- set this "
            "once the patient-facing frontend's real origin(s) are known."
        ),
    )

    # --- Google Calendar delivery (report + recording links on the doctor's event) ---
    google_service_account_file: str = Field(
        default="",
        description=(
            "Path to a Google service-account JSON key with Calendar API "
            "access. Optional: when blank, calendar delivery no-ops (logs a "
            "warning) instead of failing the call -- the written report is the "
            "primary deliverable and must never depend on Calendar being set "
            "up. The doctor's calendar must be shared with this service "
            "account's email (Editor access) for events to be created/updated."
        ),
    )

    # --- Doctor self-registration via Google OAuth ---
    # A doctor connects their *own* Google account here, unlike
    # `google_service_account_file` above, which is one shared identity a
    # doctor has to grant access to out of band. When these are blank the
    # registration route returns 503 rather than half-starting a flow that
    # cannot complete; existing service-account delivery is unaffected.
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = Field(default="")
    google_oauth_redirect_uri: str = Field(
        default="http://localhost:8000/api/v1/doctors/google/callback",
        description=(
            "Must be registered verbatim as an authorized redirect URI on the "
            "OAuth client in Google Cloud Console -- Google rejects the "
            "authorization request outright on any mismatch."
        ),
    )
    doctor_registration_ttl_seconds: int = 900
    """How long a pending registration survives before Mongo expires it. Long
    enough for a doctor to work through Google's consent screens, short enough
    that an abandoned attempt does not linger."""

    booking_app_base_url: str = Field(
        default="http://localhost:5173",
        description=(
            "Origin of the booking UI, used as the post-consent redirect "
            "target. The OAuth callback is a browser navigation, so it has to "
            "hand the doctor back to a page rather than return JSON."
        ),
    )

    # --- Clinic and assistant identity (served to the patient app) ---
    # The patient app must not carry its own copy of any of this: a
    # hardcoded clinic name or assistant name in the frontend is a value
    # that silently disagrees with the deployment the moment either
    # changes. All four are served on the session context response.
    clinic_name: str = Field(
        default="",
        description=(
            "Clinic/facility name shown to the patient on the consent copy and "
            "the appointment card. Blank means the patient app shows no "
            "facility name at all rather than inventing one."
        ),
    )
    clinic_location: str = Field(
        default="",
        description="Second line of the facility address, e.g. 'Downtown Clinic'. Optional.",
    )
    assistant_name: str = Field(
        default="Kiara",
        description=(
            "The intake assistant's display name, used in the patient app's "
            "badge and captions. Not the voice: that is `bidi_voice`."
        ),
    )
    assistant_role: str = Field(
        default="Pre-visit assistant",
        description="One-line role shown under the assistant's name.",
    )

    # --- Clinic scheduling window (drives bookable slot generation) ---
    clinic_timezone: str = Field(
        default="UTC",
        description=(
            "IANA zone the clinic's opening hours are expressed in, e.g. "
            "'Asia/Kolkata'. Slots are generated in this zone and served as "
            "UTC-offset timestamps, so a clinic's 9am stays 9am regardless of "
            "where the server or the patient's browser sits."
        ),
    )
    clinic_open_hour: int = Field(default=9, ge=0, le=23)
    clinic_close_hour: int = Field(default=17, ge=1, le=24)
    clinic_slot_minutes: int = Field(default=30, ge=5, le=240)
    clinic_open_weekdays: str = Field(
        default="0,1,2,3,4",
        description=(
            "Comma-separated `datetime.weekday()` values the clinic is open on "
            "(Monday=0). A string rather than list[int] for the same "
            "hosting-panel reason as `allowed_origins`."
        ),
    )
    booking_horizon_days: int = Field(
        default=14,
        ge=1,
        le=90,
        description="How far ahead the booking UI may offer slots.",
    )
    booking_lead_time_minutes: int = Field(
        default=60,
        ge=0,
        description=(
            "Minimum notice before a slot may be booked. Stops the UI offering "
            "a slot that starts in three minutes."
        ),
    )

    # --- Observability ---
    log_level: str = "INFO"
    log_json: bool = True

    # --- Moss (semantic question-bank lookup) ---
    # Entirely optional and additive: left blank, the question bank behaves
    # exactly as it always has (in-memory, Mongo-backed). Only set on a
    # deployment that has its own Moss project -- see
    # `app.services.moss_question_bank.MossQuestionBankClient`.
    moss_project_id: str = Field(default="")
    moss_project_key: str = Field(default="")
    moss_question_index: str = Field(default="Ai_prescreening_doc")


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide `Settings` singleton.

    Cached so repeated calls (e.g. from FastAPI dependencies) do not
    re-parse the environment on every request.
    """
    return Settings()

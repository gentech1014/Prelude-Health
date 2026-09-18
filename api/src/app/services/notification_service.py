"""Delivers the AI intake link to the patient (SMS/notification)."""

import structlog

log = structlog.get_logger(__name__)


class NotificationService:
    """Wraps the scheduling platform's notification capability.

    A thin wrapper rather than a direct integration call site so the
    delivery channel (SMS today, could add email/push) stays swappable.

    Deliberately a structured-log mock, not a real SMS integration: the BA
    scope is explicit that "the hackathon should not duplicate a separate
    SMS architecture" -- the real scheduling platform's own notification
    capability is what would deliver this in production, and the mock
    booking platform (`app.api.v1.mock_booking`) stands in for that here.
    This still makes the notification flow visibly happen in the logs,
    which is what the demo needs to show.
    """

    def compose_intake_message(
        self, patient_name: str, physician: str, appointment_label: str, intake_url: str
    ) -> str:
        """The exact SMS body the patient receives, link included.

        Composed here rather than at a call site so there is one wording to
        change, and so the booking screen can show the patient precisely what
        was sent instead of a re-typed approximation of it.

        Addressed by first name only: an SMS is not a secure channel, so it
        carries no date of birth, patient id, or reason for the visit -- just
        enough for the patient to recognize the appointment as theirs.
        """
        first_name = patient_name.strip().split(" ")[0] or patient_name.strip()
        return (
            f"Hi {first_name}, your appointment with {physician} is confirmed for "
            f"{appointment_label}. Before your visit, please complete a short "
            f"pre-screening call: {intake_url}"
        )

    async def send_intake_link(self, contact_phone: str | None, intake_url: str) -> None:
        """Log the patient's AI intake link as if it had just been sent.

        Never raises: an absent `contact_phone` is logged, not treated as
        a failure, since the mock platform has no real channel to fail on.

        Deliberately does not log `contact_phone` or `intake_url` verbatim:
        the phone number is PHI-adjacent and the URL embeds a signed,
        bearer-style session token -- either one landing in shipped logs
        would be a real leak. Only presence/absence is recorded.
        """
        log.info("intake_link_notification_sent", contact_phone_present=contact_phone is not None)

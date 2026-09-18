import { ChevronLeft, Signal, Video, Wifi } from 'lucide-react';
import { useMemo, type JSX } from 'react';
import { splitIntakeMessage } from '@/features/booking/intakeMessage';

const CLOCK_FORMATTER = new Intl.DateTimeFormat('en-US', {
  hour: 'numeric',
  minute: '2-digit',
  hour12: false,
});

const SENDER_NAME = 'CareWeave';

interface IntakeMessagePreviewProps {
  message: string;
  intakeUrl: string;
}

/**
 * A phone showing the text message the patient just received.
 *
 * The point is to make the hand-off visible: booking ends on this screen,
 * but the pre-screening call starts on the patient's own phone, and without
 * seeing the message it is not obvious that anything reaches them at all.
 *
 * The body is the server's own `notification_message`, not a re-typed
 * approximation — so if the clinic's wording changes, this changes with it.
 * The link is live and opens the real intake flow.
 */
export function IntakeMessagePreview({
  message,
  intakeUrl,
}: IntakeMessagePreviewProps): JSX.Element {
  // Fixed at mount: the message was sent now, and a clock that ticked would
  // redraw the whole preview every minute for no benefit.
  const sentAt = useMemo(() => CLOCK_FORMATTER.format(new Date()), []);
  const { before, link, after } = splitIntakeMessage(message, intakeUrl);

  return (
    <figure className="intake-preview">
      <div
        className="phone"
        role="img"
        aria-label="Preview of the text message sent to the patient"
      >
        <div className="phone__screen">
          <div className="phone__island" aria-hidden="true" />

          <div className="phone__status-bar" aria-hidden="true">
            <span className="phone__clock">{sentAt}</span>
            <span className="phone__status-icons">
              <Signal size={13} />
              <Wifi size={13} />
              <span className="phone__battery" />
            </span>
          </div>

          <header className="phone__app-bar" aria-hidden="true">
            <ChevronLeft size={20} className="phone__back" />
            <span className="phone__sender">
              <span className="phone__avatar">CW</span>
              <span className="phone__sender-name">{SENDER_NAME}</span>
            </span>
            <Video size={18} className="phone__back" />
          </header>

          <div className="phone__thread">
            <p className="phone__thread-time">
              <span>Today</span> {sentAt}
            </p>

            <p className="phone__bubble">
              {before}
              {link === null ? null : (
                // Opens the real intake flow, so the hand-off can be walked
                // end to end from here rather than only described.
                // The visible label is a short alias — the raw URL is hidden
                // so the bubble doesn't overflow with the full token string.
                <a className="phone__link" href={link} target="_blank" rel="noopener noreferrer">
                  cweave.app/join
                </a>
              )}
              {after}
            </p>
            <p className="phone__receipt">Delivered</p>
          </div>

          <div className="phone__composer" aria-hidden="true">
            <span className="phone__composer-field">Text Message</span>
          </div>

          <span className="phone__home-indicator" aria-hidden="true" />
        </div>
      </div>

      <figcaption className="intake-preview__caption">
        What the patient receives. The link opens the pre-screening call on their phone.
      </figcaption>
    </figure>
  );
}

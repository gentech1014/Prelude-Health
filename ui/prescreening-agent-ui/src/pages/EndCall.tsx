import { Home, Leaf } from 'lucide-react';
import { type JSX } from 'react';
import { useNavigate } from 'react-router-dom';
import { useIntakeCall } from '@/features/prescreening-session/useIntakeCall';
import { buildPrescreeningSessionRootPath } from '@/features/prescreening-session/prescreeningFlowSteps';
import { usePrescreeningSession } from '@/features/prescreening-session/usePrescreeningSession';
import { AssistantAvatar } from '@/features/welcome/AssistantAvatar';
import '@/pages/EndCall.css';

/**
 * The end call screen shown after the patient or agent hangs up.
 *
 * A call that finished on its own (`completed`) is done — the patient
 * closes the tab. One the patient cut short (`interrupted`, or a
 * connection that gave up retrying) is not: the session stays resumable,
 * so this offers to reopen it instead of a `Done` that would read as final.
 */
export function EndCall(): JSX.Element {
  const navigate = useNavigate();
  const { sessionId, session } = usePrescreeningSession();
  const { callDurationSeconds, endReason } = useIntakeCall();

  const wasCallCompleted = endReason === 'completed';

  const minutes = Math.floor(callDurationSeconds / 60);
  const seconds = callDurationSeconds % 60;
  const formattedDuration = `${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;

  return (
    <div className="end-call-page">
      <button
        type="button"
        className="end-call-home-button"
        onClick={() => navigate('/book')}
        aria-label="Back to booking"
      >
        <Home size={20} strokeWidth={2} />
      </button>

      <div className="end-call-content">
        <h1 className="end-call-patient-name">{session?.assistant.name ?? 'Kiara'}</h1>
        <p className="end-call-subtitle">Call ended</p>
        <p className="end-call-thank-you">
          {wasCallCompleted ? (
            <>
              Thank you for speaking with {session?.assistant.name ?? 'Kiara'}.<br />
              Take care!
            </>
          ) : (
            <>
              Your pre-visit screening is not finished.<br />
              What you shared so far is kept — call again to pick up where you left off.
            </>
          )}
        </p>
        
        <div className="end-call-avatar-wrapper">
          <AssistantAvatar />
        </div>

        <div className="end-call-duration-wrapper">
          <p className="end-call-duration-label">Call duration</p>
          <p className="end-call-duration-time">{formattedDuration}</p>
        </div>

        <div className="end-call-footer-message">
          <Leaf
            className="end-call-leaf-icon"
            color="var(--color-primary)"
            fill="var(--color-primary-subtle)"
            strokeWidth={1}
          />
          <p>
            Your information helps us<br />
            provide better care.
          </p>
        </div>
      </div>

      <div className="end-call-bottom-section">
        <div className="end-call-waves" aria-hidden="true">
          <svg className="end-call-waves__svg" viewBox="0 0 1440 320" preserveAspectRatio="none">
            <path
              fill="var(--color-primary)"
              fillOpacity="0.13"
              d="M0,192L48,202.7C96,213,192,235,288,234.7C384,235,480,213,576,213.3C672,213,768,235,864,250.7C960,267,1056,277,1152,261.3C1248,245,1344,203,1392,181.3L1440,160L1440,320L1392,320C1344,320,1248,320,1152,320C1056,320,960,320,864,320C768,320,672,320,576,320C480,320,384,320,288,320C192,320,96,320,48,320L0,320Z"
            />
            <path
              fill="var(--color-primary)"
              fillOpacity="0.22"
              d="M0,288L48,272C96,256,192,224,288,213.3C384,203,480,213,576,229.3C672,245,768,267,864,256C960,245,1056,203,1152,192C1248,181,1344,203,1392,213.3L1440,224L1440,320L1392,320C1344,320,1248,320,1152,320C1056,320,960,320,864,320C768,320,672,320,576,320C480,320,384,320,288,320C192,320,96,320,48,320L0,320Z"
            />
          </svg>
        </div>
        <button
          className="btn-primary end-call-done-button"
          onClick={() => {
            if (wasCallCompleted) {
              if (window.close) window.close();
              return;
            }
            // A full reload, not an in-app navigate: the call engine only
            // reconnects from a fresh mount, the same way reopening the
            // patient's own link resumes an interrupted session today.
            window.location.assign(buildPrescreeningSessionRootPath(sessionId));
          }}
        >
          {wasCallCompleted ? 'Done' : 'Call again'}
        </button>
      </div>
    </div>
  );
}

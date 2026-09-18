import type { JSX } from 'react';
import agentAvatarPhoto from '@/assets/agent-avatar.png';
import '@/features/welcome/AssistantAvatar.css';

/** The assistant's avatar: ripple rings around the portrait. */
export function AssistantAvatar(): JSX.Element {
  return (
    <div className="assistant-avatar">
      <div className="assistant-avatar__rings">
        <div className="assistant-avatar__ring assistant-avatar__ring--outer" />
        <div className="assistant-avatar__ring assistant-avatar__ring--mid" />
        <div className="assistant-avatar__core">
          {/* Decorative — the greeting heading right below already names the assistant. */}
          <img
            src={agentAvatarPhoto}
            alt=""
            aria-hidden="true"
            className="assistant-avatar__photo"
          />
        </div>
      </div>
    </div>
  );
}

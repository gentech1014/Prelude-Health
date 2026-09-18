import type { JSX } from 'react';
import agentAvatarPhoto from '@/assets/agent-avatar.png';
import '@/components/AgentProfileBadge.css';

interface AgentProfileBadgeProps {
  /** From the session context — the deployment names the assistant, not this app. */
  name: string;
}

/**
 * The assistant's photo with a name pill, fixed to the bottom-right corner
 * of the screen. Shared across every screen that shows them actively on
 * the call — do not re-create this avatar/badge markup inside a page.
 */
export function AgentProfileBadge({ name }: AgentProfileBadgeProps): JSX.Element {
  return (
    <div className="agent-profile-badge">
      {/* Decorative — the name pill below already identifies her for screen readers. */}
      <img
        src={agentAvatarPhoto}
        alt=""
        aria-hidden="true"
        className="agent-profile-badge__avatar"
      />
      <div className="agent-profile-badge__pill">
        <span className="agent-profile-badge__name">{name}</span>
      </div>
    </div>
  );
}

import type { ReactNode } from 'react';
import mobileOnlyIllustration from '@/assets/mobile-only-illustration.png';
import { useIsMobileViewport } from '@/hooks/useIsMobileViewport';

interface MobileOnlyGateProps {
  children: ReactNode;
}

// The product is portrait mobile only — desktop/tablet must see this message, never a stretched UI.
export function MobileOnlyGate({ children }: MobileOnlyGateProps): ReactNode {
  const isMobile = useIsMobileViewport();

  if (!isMobile) {
    return (
      <div
        role="status"
        style={{
          position: 'relative',
          // AppBackgroundScenery is a fixed, positioned element (z-index: 0),
          // which paints above static in-flow content regardless of DOM order —
          // this needs its own stacking position to actually sit in front of it.
          zIndex: 1,
          display: 'flex',
          flex: 1,
          alignItems: 'center',
          justifyContent: 'center',
          textAlign: 'center',
          padding: 'var(--space-6) var(--space-4)',
          background: 'var(--color-bg)',
        }}
      >
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            gap: 'var(--space-5)',
            maxWidth: 480,
          }}
        >
          {/* Decorative — the heading and description below already convey the message. */}
          <img
            src={mobileOnlyIllustration}
            alt=""
            aria-hidden="true"
            style={{ width: '100%', maxWidth: 400 }}
          />
          <div>
            <h1
              style={{
                margin: 0,
                fontSize: '1.75rem',
                fontWeight: 'var(--fw-bold)',
                color: 'var(--color-text)',
              }}
            >
              Let&apos;s continue on your phone
            </h1>
            <p
              style={{
                margin: 'var(--space-3) 0 0',
                fontSize: '1.1rem',
                color: 'var(--color-text-secondary)',
              }}
            >
              This experience is optimized for mobile, so please open this link on your phone to
              continue.
            </p>
          </div>
        </div>
      </div>
    );
  }

  return children;
}

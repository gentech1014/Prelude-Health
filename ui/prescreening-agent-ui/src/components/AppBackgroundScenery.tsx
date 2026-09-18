import type { JSX } from 'react';
import '@/components/AppBackgroundScenery.css';

/**
 * The app's shared backdrop — mounted once, behind the router, so every
 * screen (loader, welcome, whatever comes next) reads as one continuous
 * surface instead of each page painting its own flat background.
 */
export function AppBackgroundScenery(): JSX.Element {
  return (
    <div className="app-background-scenery" aria-hidden="true">
      <svg
        className="app-background-scenery__art"
        viewBox="0 0 1440 320"
        preserveAspectRatio="none"
      >
        {/* Left pale purple fill */}
        <path
          d="M 0 160 C 120 130, 250 140, 450 220 C 650 300, 850 290, 1050 240 C 1250 190, 1350 170, 1440 160 L 1440 320 L 0 320 Z"
          fill="var(--color-ai-accent)"
          fillOpacity="0.1"
        />
        {/* Back green fill */}
        <path
          d="M 0 220 C 250 190, 450 270, 750 220 C 1000 170, 1200 110, 1440 110 L 1440 320 L 0 320 Z"
          fill="var(--color-primary)"
          fillOpacity="0.11"
        />
        {/* Front green fill */}
        <path
          d="M 0 260 C 300 240, 600 320, 950 260 C 1200 210, 1350 220, 1440 240 L 1440 320 L 0 320 Z"
          fill="var(--color-primary)"
          fillOpacity="0.17"
        />
        {/* Thin purple stroke */}
        <path
          d="M 0 160 C 120 130, 250 140, 450 220 C 650 300, 850 290, 1050 240 C 1250 190, 1350 170, 1440 160"
          fill="none"
          stroke="var(--color-ai-accent)"
          strokeOpacity="0.4"
          strokeWidth="1.5"
          vectorEffect="non-scaling-stroke"
        />
      </svg>
    </div>
  );
}

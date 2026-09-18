import { PhoneCall } from 'lucide-react';
import type { CSSProperties, JSX } from 'react';
import '@/components/ModelAwakeningLoader.css';

// @types/react's CSSProperties intentionally omits an index signature for custom
// properties (kept closed on purpose) — an assertion is the documented escape hatch.
type StyleWithCustomProperties = CSSProperties & Record<`--${string}`, string>;

// Must match the core disc's CSS: `inset: 35%` → diameter = 100% − 2×35% = 30% of size.
// Icon is sized at half that diameter so it sits comfortably inside, never overflowing.
const CORE_DIAMETER_RATIO = 0.3;
const ICON_SIZE_RATIO = CORE_DIAMETER_RATIO * 0.5;

interface ModelAwakeningLoaderProps {
  size?: number;
  /** Status text (e.g. "Connecting…", "Preparing assistant…") — wire in once the calling flow provides it. */
  label?: string;
}

/**
 * The Model Loading / Awakening screen's primary visual: rippling Emerald
 * primary-color rings around a solid core holding a phone icon, communicating
 * "your assistant is connecting" — never a bare unexplained spinner (CLAUDE.md).
 */
export function ModelAwakeningLoader({
  size = 200,
  label,
}: ModelAwakeningLoaderProps): JSX.Element {
  return (
    <div
      className="model-awakening-loader"
      style={{ '--maal-size': `${size}px` } as StyleWithCustomProperties}
      role="status"
      aria-live="polite"
    >
      <div className="model-awakening-loader__rings">
        <div className="model-awakening-loader__ring model-awakening-loader__ring--4" />
        <div className="model-awakening-loader__ring model-awakening-loader__ring--3" />
        <div className="model-awakening-loader__ring model-awakening-loader__ring--2" />
        <div className="model-awakening-loader__core">
          {/* Lucide icons are stroke-only by default; fill with the same gold so it reads as a solid premium glyph. */}
          <PhoneCall
            aria-hidden="true"
            size={Math.round(size * ICON_SIZE_RATIO)}
            fill="currentColor"
          />
        </div>
      </div>
      {label ? (
        <p className="model-awakening-loader__label">{label}</p>
      ) : (
        <span
          style={{
            position: 'absolute',
            width: 1,
            height: 1,
            overflow: 'hidden',
            clip: 'rect(0 0 0 0)',
            whiteSpace: 'nowrap',
          }}
        >
          Connecting to your assistant
        </span>
      )}
    </div>
  );
}

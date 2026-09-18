import { motion, type Variants } from 'framer-motion';
import { useState, type JSX } from 'react';
import '@/features/thank-you/AnimatedSuccessTick.css';

const SIZE = 130;
const CENTER = 60;

// A rounded, lobed seal edge (like a verified badge) — not a plain circle.
// Alternating outer/inner points, smoothed into rounded bumps via quadratic
// curves through their midpoints (a standard blob-smoothing technique).
const LOBE_COUNT = 12;
const OUTER_RADIUS = 56;
const INNER_RADIUS = 44;

interface Point {
  x: number;
  y: number;
}

function midpoint(a: Point, b: Point): Point {
  return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
}

function buildLobedSealPath(): string {
  const totalPoints = LOBE_COUNT * 2;
  const raw: Point[] = Array.from({ length: totalPoints }, (_, i) => {
    const angle = (i / totalPoints) * Math.PI * 2 - Math.PI / 2;
    const radius = i % 2 === 0 ? OUTER_RADIUS : INNER_RADIUS;
    return { x: CENTER + radius * Math.cos(angle), y: CENTER + radius * Math.sin(angle) };
  });

  const start = midpoint(raw[raw.length - 1]!, raw[0]!);
  const segments = raw.map((point, i) => {
    const next = raw[(i + 1) % raw.length]!;
    const mid = midpoint(point, next);
    return `Q${point.x.toFixed(2)},${point.y.toFixed(2)} ${mid.x.toFixed(2)},${mid.y.toFixed(2)}`;
  });

  return `M${start.x.toFixed(2)},${start.y.toFixed(2)} ${segments.join(' ')} Z`;
}

const SEAL_PATH = buildLobedSealPath();

// Timed so the confetti fires exactly as the checkmark finishes drawing, and
// the ambient loop (see CSS) picks up shortly after the confetti settles.
const BADGE_DURATION_S = 0.35;
const CHECK_DELAY_S = 0.3;
const CHECK_DURATION_S = 0.4;
const CONFETTI_START_S = CHECK_DELAY_S + CHECK_DURATION_S;

const BADGE_VARIANTS: Variants = {
  hidden: { scale: 0.6, opacity: 0 },
  visible: { scale: 1, opacity: 1, transition: { duration: BADGE_DURATION_S, ease: 'easeOut' } },
};

const CHECK_VARIANTS: Variants = {
  hidden: { pathLength: 0, opacity: 0 },
  visible: {
    pathLength: 1,
    opacity: 1,
    transition: { duration: CHECK_DURATION_S, ease: 'easeInOut', delay: CHECK_DELAY_S },
  },
};

// On-brand palette only — no arbitrary confetti colors.
const CONFETTI_COLORS = [
  'var(--color-primary)',
  'var(--color-primary-hover)',
  'var(--color-primary-active)',
  'var(--color-ai-accent)',
  'var(--color-premium)',
  'var(--color-premium-border)',
];
const CONFETTI_COUNT = 36;
type ConfettiShape = 'circle' | 'square' | 'streamer';
const CONFETTI_SHAPES: readonly ConfettiShape[] = ['circle', 'square', 'streamer'];

interface ConfettiPiece {
  id: number;
  shape: ConfettiShape;
  width: number;
  height: number;
  color: string;
  delay: number;
  duration: number;
  midX: number;
  midY: number;
  finalX: number;
  finalY: number;
  spinMid: number;
  spinEnd: number;
}

function randomBetween(min: number, max: number): number {
  return min + Math.random() * (max - min);
}

/**
 * A sharp radial burst outward from the badge in every direction, then a
 * longer, slower fall under "gravity" — two distinct motions, not one slow
 * drift. The burst leg is a small fraction of the total duration (see
 * `times` below) so it reads as an explosion, with the fall taking the rest.
 */
function createConfettiPieces(): ConfettiPiece[] {
  return Array.from({ length: CONFETTI_COUNT }, (_, i) => {
    const angle = Math.random() * Math.PI * 2;
    const burstDistance = randomBetween(65, 135);
    const midX = Math.cos(angle) * burstDistance;
    const midY = Math.sin(angle) * burstDistance;
    const shape = CONFETTI_SHAPES[i % CONFETTI_SHAPES.length]!;
    const isStreamer = shape === 'streamer';

    return {
      id: i,
      shape,
      width: isStreamer ? randomBetween(10, 15) : randomBetween(5, 9),
      height: isStreamer ? randomBetween(3, 4) : randomBetween(5, 9),
      color: CONFETTI_COLORS[i % CONFETTI_COLORS.length]!,
      delay: CONFETTI_START_S + randomBetween(0, 0.12),
      duration: randomBetween(1.3, 2),
      midX,
      midY,
      finalX: midX + randomBetween(-45, 45),
      finalY: midY + randomBetween(170, 280),
      spinMid: randomBetween(120, 320) * (Math.random() < 0.5 ? -1 : 1),
      spinEnd: randomBetween(360, 640) * (Math.random() < 0.5 ? -1 : 1),
    };
  });
}

/**
 * The visual confirmation that the prescreening and appointment are properly
 * noted — a solid, rounded-lobed seal with a bold checkmark, front and
 * center on the Thank You screen. A rich one-time confetti burst fires the
 * moment the checkmark finishes drawing; once it settles, the badge itself
 * keeps a slow, gentle breathing glow — ambient, never a spin or an
 * expanding ring.
 */
export function AnimatedSuccessTick(): JSX.Element {
  const [confetti] = useState(createConfettiPieces);

  return (
    <div className="animated-success-tick-wrap" style={{ width: SIZE, height: SIZE }}>
      {confetti.map((piece) => (
        // Resting position is pre-centered on the badge in plain pixels (no
        // percentage/calc() mix with the animated offsets below) — framer-motion
        // only tweens smoothly between same-shaped values, and mixing '%' with
        // 'calc(% + px)' keyframes made it snap instead of animate.
        <motion.span
          key={piece.id}
          className="animated-success-tick__confetti"
          style={{
            left: SIZE / 2 - piece.width / 2,
            top: SIZE / 2 - piece.height / 2,
            width: piece.width,
            height: piece.height,
            background: piece.color,
            borderRadius:
              piece.shape === 'circle' ? '50%' : piece.shape === 'streamer' ? '2px' : '1px',
          }}
          initial={{ x: 0, y: 0, opacity: 0, rotate: 0 }}
          animate={{
            x: [0, piece.midX, piece.finalX],
            y: [0, piece.midY, piece.finalY],
            opacity: [0, 1, 1, 0],
            rotate: [0, piece.spinMid, piece.spinEnd],
          }}
          transition={{
            duration: piece.duration,
            delay: piece.delay,
            // Burst reaches full distance in the first ~18% of the duration
            // (a sharp outward flick), then the rest is the slow fall.
            times: [0, 0.18, 1],
            ease: ['easeOut', 'easeIn'],
            opacity: {
              duration: piece.duration,
              delay: piece.delay,
              times: [0, 0.04, 0.75, 1],
              ease: 'easeInOut',
            },
          }}
        />
      ))}

      <div className="animated-success-tick-loop">
        <motion.div
          className="animated-success-tick"
          style={{ width: SIZE, height: SIZE }}
          initial="hidden"
          animate="visible"
          variants={BADGE_VARIANTS}
          role="img"
          aria-label="Prescreening and appointment confirmed"
        >
          <svg width={SIZE} height={SIZE} viewBox="0 0 120 120" fill="none">
            <path d={SEAL_PATH} fill="var(--color-primary)" />
            <motion.path
              d="M36,64 L54,82 L88,46"
              stroke="var(--color-on-primary)"
              strokeWidth="10"
              strokeLinecap="round"
              strokeLinejoin="round"
              variants={CHECK_VARIANTS}
            />
          </svg>
        </motion.div>
      </div>
    </div>
  );
}

import type { Transition, Variants } from 'framer-motion';

// Same deceleration curve as the confirm-dialog sheet — settles instead of
// stopping abruptly, so a page's content reads as one smooth motion.
const SMOOTH_EASE: Transition['ease'] = [0.32, 0.72, 0, 1];

// Brief, consistent beat before content starts revealing itself on every page.
const CONTENT_REVEAL_DELAY_S = 0.15;

/** Orchestrates a page's content: staggers each PageSection in on mount, fades the page out on exit. */
export const PAGE_CONTENT_VARIANTS: Variants = {
  hidden: {},
  visible: { transition: { staggerChildren: 0.08, delayChildren: CONTENT_REVEAL_DELAY_S } },
  exit: { opacity: 0, transition: { duration: 0.15, ease: SMOOTH_EASE } },
};

/** One page block: fades in while rising from 10% below its resting position. */
export const PAGE_SECTION_VARIANTS: Variants = {
  hidden: { opacity: 0, y: '10%' },
  visible: { opacity: 1, y: 0, transition: { duration: 0.4, ease: SMOOTH_EASE } },
};

/**
 * Hover-lift + tap-scale for framer-driven cards (dashboard preview, booking's
 * provider/visit-type cards). Cards that only need this — nothing else
 * framer-motion offers — should reach for the CSS `.card-hover` class in
 * global.css instead; this is for cards already inside a `motion.div` tree.
 */
export const CARD_HOVER_TAP = {
  whileHover: { y: -3, boxShadow: 'var(--shadow-md)' },
  whileTap: { scale: 0.97 },
  transition: { duration: 0.2, ease: SMOOTH_EASE },
};

/** Bare tap-scale for one-off framer buttons that don't need hover-lift too. */
export const PRESSABLE_TAP = {
  whileTap: { scale: 0.96 },
};

/** Parent for a staggered list reveal — pair with LIST_ITEM_VARIANTS on each row/card. */
export const LIST_STAGGER_VARIANTS: Variants = {
  hidden: {},
  visible: { transition: { staggerChildren: 0.05 } },
};

/** One row/card within a LIST_STAGGER_VARIANTS parent: fades in while rising slightly. */
export const LIST_ITEM_VARIANTS: Variants = {
  hidden: { opacity: 0, y: 8 },
  visible: { opacity: 1, y: 0, transition: { duration: 0.3, ease: SMOOTH_EASE } },
};

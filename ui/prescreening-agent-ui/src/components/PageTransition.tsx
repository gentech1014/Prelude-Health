import { motion, type MotionStyle } from 'framer-motion';
import type { CSSProperties, JSX, ReactNode } from 'react';
import { PAGE_CONTENT_VARIANTS } from '@/lib/pageMotion';

interface PageTransitionProps {
  style?: CSSProperties;
  className?: string;
  children: ReactNode;
}

/**
 * Drop-in replacement for a page's `<main>` — orchestrates its PageSection
 * children into the same staggered reveal on every screen, and fades the
 * outgoing page out when AnimatePresence (in RootLayout) unmounts it.
 */
export function PageTransition({ style, className, children }: PageTransitionProps): JSX.Element {
  return (
    <motion.main
      className={className}
      // Plain CSSProperties includes SVG geometry props (x/y) typed as possibly
      // undefined, which conflicts with MotionStyle's own x/y under
      // exactOptionalPropertyTypes — none of that ever applies to a <main>.
      style={style as MotionStyle}
      variants={PAGE_CONTENT_VARIANTS}
      initial="hidden"
      animate="visible"
      exit="exit"
    >
      {children}
    </motion.main>
  );
}

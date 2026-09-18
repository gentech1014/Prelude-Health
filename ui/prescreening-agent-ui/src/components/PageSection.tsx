import { motion } from 'framer-motion';
import type { JSX, ReactNode } from 'react';
import { PAGE_SECTION_VARIANTS } from '@/lib/pageMotion';

interface PageSectionProps {
  children: ReactNode;
}

/**
 * One block of page content that fades in, rising from 10% below its resting
 * position, as part of the parent PageTransition's staggered reveal. Wrap
 * every top-level piece of a page's content with this — except the agent
 * avatar/badge, which animates its own cross-page position instead.
 */
export function PageSection({ children }: PageSectionProps): JSX.Element {
  return <motion.div variants={PAGE_SECTION_VARIANTS}>{children}</motion.div>;
}

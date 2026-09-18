import type { CSSProperties, JSX } from 'react';
import './Skeleton.css';

interface SkeletonProps {
  width?: string | number;
  height?: string | number;
  radius?: string;
  style?: CSSProperties;
}

/** A single shimmering placeholder block. Compose these to preserve real page layout while loading. */
export function Skeleton({
  width = '100%',
  height = 16,
  radius = 'var(--radius-sm)',
  style,
}: SkeletonProps): JSX.Element {
  return (
    <div
      className="skeleton"
      aria-hidden="true"
      style={{ width, height, borderRadius: radius, ...style }}
    />
  );
}

/** A skeleton standing in for a stat/result card — mirrors the real card's shape. */
export function SkeletonCard(): JSX.Element {
  return (
    <div className="skeleton-card" role="status" aria-label="Loading content">
      <Skeleton width="40%" height={12} />
      <Skeleton width="60%" height={28} />
      <Skeleton width="80%" height={12} />
    </div>
  );
}

import type { JSX } from 'react';

interface Swatch {
  label: string;
  varName: string;
}

const GROUPS: { title: string; swatches: Swatch[] }[] = [
  {
    title: 'Primary — Dusk Indigo',
    swatches: [
      { label: 'Primary', varName: '--color-primary' },
      { label: 'Primary hover', varName: '--color-primary-hover' },
      { label: 'Primary active', varName: '--color-primary-active' },
      { label: 'Primary subtle', varName: '--color-primary-subtle' },
    ],
  },
  {
    title: 'Foundation — Linen / Charcoal',
    swatches: [
      { label: 'Background', varName: '--color-bg' },
      { label: 'Surface', varName: '--color-surface' },
      { label: 'Surface alt', varName: '--color-surface-alt' },
      { label: 'Border', varName: '--color-border' },
    ],
  },
  {
    title: 'AI accent — Coral',
    swatches: [
      { label: 'AI accent', varName: '--color-ai-accent' },
      { label: 'AI accent subtle', varName: '--color-ai-accent-subtle' },
    ],
  },
  {
    title: 'Premium accent — Warm Gold',
    swatches: [
      { label: 'Premium', varName: '--color-premium' },
      { label: 'Premium subtle', varName: '--color-premium-subtle' },
    ],
  },
  {
    title: 'Clinical semantics (fixed)',
    swatches: [
      { label: 'Success', varName: '--color-success' },
      { label: 'Warning', varName: '--color-warning' },
      { label: 'Error', varName: '--color-error' },
      { label: 'Info', varName: '--color-info' },
    ],
  },
];

/** Renders every semantic token as a labeled swatch so a theme change is visually verifiable. */
export function PaletteSwatches(): JSX.Element {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
      {GROUPS.map((group) => (
        <div key={group.title}>
          <h3
            style={{
              margin: '0 0 var(--space-2)',
              fontSize: '0.8rem',
              textTransform: 'uppercase',
              letterSpacing: '0.04em',
              color: 'var(--color-text-secondary)',
            }}
          >
            {group.title}
          </h3>
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fill, minmax(76px, 1fr))',
              gap: 'var(--space-2)',
            }}
          >
            {group.swatches.map((swatch) => (
              <div
                key={swatch.varName}
                style={{ display: 'flex', flexDirection: 'column', gap: 4 }}
              >
                <div
                  style={{
                    height: 44,
                    borderRadius: 'var(--radius-md)',
                    border: '1px solid var(--color-border)',
                    background: `var(${swatch.varName})`,
                  }}
                />
                <span style={{ fontSize: '0.65rem', color: 'var(--color-text-secondary)' }}>
                  {swatch.label}
                </span>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

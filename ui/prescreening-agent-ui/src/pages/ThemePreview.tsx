import type { JSX } from 'react';
import { ModelAwakeningLoader } from '@/components/ModelAwakeningLoader';
import { ThemeToggle } from '@/components/ThemeToggle';
import { DashboardPreviewCard } from '@/features/theme-preview/DashboardPreviewCard';
import { PaletteSwatches } from '@/features/theme-preview/PaletteSwatches';
import { StatesGallery } from '@/features/theme-preview/StatesGallery';

/**
 * Style-guide screen for verifying the token theme (light/dark) and the
 * shared UI-state components. Not a screen in the enforced product flow —
 * placeholder for the real Mobile Check → Welcome Agent flow to come.
 */
export function ThemePreview(): JSX.Element {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', minHeight: '100%' }}>
      <header
        style={{
          position: 'sticky',
          top: 0,
          zIndex: 10,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: 'var(--space-3) var(--space-4)',
          background: 'var(--color-surface)',
          borderBottom: '1px solid var(--color-border)',
        }}
      >
        <div>
          <p style={{ margin: 0, fontSize: '0.7rem', color: 'var(--color-text-secondary)' }}>
            Style guide — not a product screen
          </p>
          <h1 style={{ margin: 0, fontSize: '1.05rem' }}>Prelude Health</h1>
        </div>
        <ThemeToggle />
      </header>

      <main
        style={{
          display: 'flex',
          flexDirection: 'column',
          gap: 'var(--space-5)',
          padding: 'var(--space-4)',
        }}
      >
        <Section title="Model loading">
          <div style={{ display: 'flex', justifyContent: 'center', padding: 'var(--space-4) 0' }}>
            <ModelAwakeningLoader />
          </div>
        </Section>

        <Section title="Dashboard preview">
          <DashboardPreviewCard />
        </Section>

        <Section title="Palette">
          <PaletteSwatches />
        </Section>

        <Section title="UI states">
          <StatesGallery />
        </Section>
      </main>
    </div>
  );
}

function Section({ title, children }: { title: string; children: JSX.Element }): JSX.Element {
  return (
    <section>
      <h2 style={{ margin: '0 0 var(--space-3)', fontSize: '0.95rem' }}>{title}</h2>
      {children}
    </section>
  );
}

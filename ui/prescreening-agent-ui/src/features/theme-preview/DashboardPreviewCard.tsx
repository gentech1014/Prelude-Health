import {
  AlertTriangle,
  CalendarPlus,
  CheckCircle2,
  FileCheck2,
  FileText,
  Info,
  Users,
} from 'lucide-react';
import type { JSX } from 'react';
import { StatusBadge } from '@/components/states';

const QUICK_ACTIONS = [
  { label: 'New appointment', icon: <CalendarPlus size={18} aria-hidden="true" /> },
  { label: 'All patients', icon: <Users size={18} aria-hidden="true" /> },
  { label: 'Generate report', icon: <FileText size={18} aria-hidden="true" /> },
] as const;

const WEEK_DENSITY = [
  { day: 'M', level: 'available' },
  { day: 'T', level: 'some' },
  { day: 'W', level: 'full' },
  { day: 'T', level: 'some' },
  { day: 'F', level: 'full' },
  { day: 'S', level: 'available' },
  { day: 'S', level: 'none' },
] as const;

// Density expressed with semantic tokens only — never the raw emerald scale directly.
const DENSITY_COLOR: Record<(typeof WEEK_DENSITY)[number]['level'], string> = {
  none: 'var(--color-surface-alt)',
  available: 'var(--color-primary-subtle)',
  some: 'var(--color-primary)',
  full: 'var(--color-primary-active)',
};

const RESULTS = [
  { label: 'Emily Smith — routine screening', tone: 'success' as const, statusLabel: 'Normal' },
  { label: 'Anna K. — lung risk flagged', tone: 'error' as const, statusLabel: 'High risk' },
  { label: 'David L. — metabolic shift', tone: 'warning' as const, statusLabel: 'Caution' },
];

const RESULT_ICON: Record<(typeof RESULTS)[number]['tone'], JSX.Element> = {
  success: <CheckCircle2 size={14} aria-hidden="true" />,
  error: <AlertTriangle size={14} aria-hidden="true" />,
  warning: <Info size={14} aria-hidden="true" />,
};

/**
 * Style-reference card: reuses the attached dashboard's visual language
 * (greeting, quick actions, stat chips, weekly density, status list) adapted
 * to a single mobile column with icons carrying every status, not color alone.
 */
export function DashboardPreviewCard(): JSX.Element {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
      <div
        style={{
          borderRadius: 'var(--radius-lg)',
          padding: 'var(--space-4)',
          background: 'var(--color-primary-active)',
          color: 'var(--color-on-primary)',
          borderTop: '2px solid var(--color-premium-border)',
        }}
      >
        <p style={{ margin: 0, fontSize: '0.8rem', opacity: 0.8 }}>Today's overview</p>
        <h3 style={{ margin: '0.25rem 0 var(--space-3)', fontSize: '1.15rem' }}>
          Good afternoon, Dr. Chen
        </h3>
        <div style={{ display: 'flex', gap: 'var(--space-2)', overflowX: 'auto' }}>
          {QUICK_ACTIONS.map((action) => (
            <div
              key={action.label}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                padding: '0.4rem 0.7rem',
                borderRadius: 'var(--radius-pill)',
                background: 'color-mix(in srgb, white 14%, transparent)',
                fontSize: '0.75rem',
                whiteSpace: 'nowrap',
                flexShrink: 0,
              }}
            >
              {action.icon}
              {action.label}
            </div>
          ))}
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 'var(--space-2)' }}>
        <StatCard
          icon={<FileCheck2 size={18} aria-hidden="true" />}
          value="05"
          label="Ready for review"
          tone="info"
        />
        <StatCard
          icon={<AlertTriangle size={18} aria-hidden="true" />}
          value="03"
          label="High-risk patients"
          tone="warning"
        />
      </div>

      <section
        style={{
          borderRadius: 'var(--radius-lg)',
          border: '1px solid var(--color-border)',
          background: 'var(--color-surface)',
          padding: 'var(--space-3)',
        }}
      >
        <h4 style={{ margin: '0 0 var(--space-2)', fontSize: '0.85rem' }}>This week</h4>
        <div style={{ display: 'flex', gap: 6 }}>
          {WEEK_DENSITY.map((cell, index) => (
            <div
              key={index}
              style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}
            >
              <div
                style={{
                  width: 28,
                  height: 28,
                  borderRadius: 'var(--radius-sm)',
                  background: DENSITY_COLOR[cell.level],
                  border: '1px solid var(--color-border)',
                }}
              />
              <span style={{ fontSize: '0.65rem', color: 'var(--color-text-secondary)' }}>
                {cell.day}
              </span>
            </div>
          ))}
        </div>
        <div
          style={{
            display: 'flex',
            gap: 'var(--space-3)',
            marginTop: 'var(--space-2)',
            flexWrap: 'wrap',
          }}
        >
          <Legend swatch={DENSITY_COLOR.available} label="Available" />
          <Legend swatch={DENSITY_COLOR.some} label="Booked" />
          <Legend swatch={DENSITY_COLOR.full} label="Full" />
        </div>
      </section>

      <section
        style={{
          borderRadius: 'var(--radius-lg)',
          border: '1px solid var(--color-border)',
          background: 'var(--color-surface)',
          padding: 'var(--space-3)',
        }}
      >
        <h4 style={{ margin: '0 0 var(--space-2)', fontSize: '0.85rem' }}>Today's results</h4>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-2)' }}>
          {RESULTS.map((result) => (
            <div
              key={result.label}
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                gap: 'var(--space-2)',
              }}
            >
              <span style={{ fontSize: '0.8rem', color: 'var(--color-text)' }}>{result.label}</span>
              <StatusBadge
                icon={RESULT_ICON[result.tone]}
                label={result.statusLabel}
                tone={result.tone}
              />
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

interface StatCardProps {
  icon: JSX.Element;
  value: string;
  label: string;
  tone: 'info' | 'warning';
}

function StatCard({ icon, value, label, tone }: StatCardProps): JSX.Element {
  return (
    <div
      style={{
        borderRadius: 'var(--radius-lg)',
        border: '1px solid var(--color-border)',
        background: 'var(--color-surface)',
        padding: 'var(--space-3)',
      }}
    >
      <span style={{ color: tone === 'info' ? 'var(--color-info)' : 'var(--color-warning)' }}>
        {icon}
      </span>
      <p style={{ margin: '0.4rem 0 0', fontSize: '1.4rem', fontWeight: 'var(--fw-bold)' }}>
        {value}
      </p>
      <p style={{ margin: 0, fontSize: '0.72rem', color: 'var(--color-text-secondary)' }}>
        {label}
      </p>
    </div>
  );
}

function Legend({ swatch, label }: { swatch: string; label: string }): JSX.Element {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
      <span
        style={{
          width: 10,
          height: 10,
          borderRadius: 3,
          background: swatch,
          border: '1px solid var(--color-border)',
        }}
      />
      <span style={{ fontSize: '0.7rem', color: 'var(--color-text-secondary)' }}>{label}</span>
    </div>
  );
}

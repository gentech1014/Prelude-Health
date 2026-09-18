import { Moon, Sun } from 'lucide-react';
import type { JSX } from 'react';
import { useTheme } from '@/hooks/useTheme';
import type { ThemeMode } from '@/lib/theme';

const MODE_ICON: Record<ThemeMode, JSX.Element> = {
  light: <Sun size={18} aria-hidden="true" />,
  dark: <Moon size={18} aria-hidden="true" />,
};

const MODE_LABEL: Record<ThemeMode, string> = {
  light: 'Light theme',
  dark: 'Dark theme',
};

// Two explicit themes only, light by default — toggles light <-> dark on tap.
export function ThemeToggle(): JSX.Element {
  const { mode, setMode } = useTheme();
  const nextMode: ThemeMode = mode === 'light' ? 'dark' : 'light';

  return (
    <button
      type="button"
      onClick={() => setMode(nextMode)}
      aria-label={`${MODE_LABEL[mode]}. Tap to switch to ${MODE_LABEL[nextMode].toLowerCase()}.`}
      title={MODE_LABEL[mode]}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        width: 36,
        height: 36,
        flexShrink: 0,
        borderRadius: 'var(--radius-md)',
        border: '1px solid var(--color-border)',
        background: 'var(--color-surface)',
        color: 'var(--color-text)',
      }}
    >
      {MODE_ICON[mode]}
    </button>
  );
}

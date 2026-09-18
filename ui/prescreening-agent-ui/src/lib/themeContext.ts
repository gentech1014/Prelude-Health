import { createContext } from 'react';
import type { ThemeMode } from '@/lib/theme';

export interface ThemeContextValue {
  mode: ThemeMode;
  setMode: (mode: ThemeMode) => void;
}

// Split from ThemeProvider so react-refresh/only-export-components stays happy
// (that rule flags a component file that also exports a non-component value).
export const ThemeContext = createContext<ThemeContextValue | undefined>(undefined);

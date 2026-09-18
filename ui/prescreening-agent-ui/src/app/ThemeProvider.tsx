import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { ThemeContext } from '@/lib/themeContext';
import {
  applyThemeMode,
  readStoredThemeMode,
  THEME_STORAGE_KEY,
  type ThemeMode,
} from '@/lib/theme';

interface ThemeProviderProps {
  children: ReactNode;
}

// App-wide theme state — justified as React Context (no extra store) since 2+ components
// (the toggle and the applied <html data-theme>) must share one source of truth.
export function ThemeProvider({ children }: ThemeProviderProps): ReactNode {
  const [mode, setModeState] = useState<ThemeMode>(readStoredThemeMode);

  useEffect(() => {
    applyThemeMode(mode);
  }, [mode]);

  const setMode = useCallback((nextMode: ThemeMode) => {
    setModeState(nextMode);
    window.localStorage.setItem(THEME_STORAGE_KEY, nextMode);
  }, []);

  const value = useMemo(() => ({ mode, setMode }), [mode, setMode]);

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

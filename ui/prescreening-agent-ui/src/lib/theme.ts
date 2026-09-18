// Two explicit themes only — light is always the default, per product decision.
// No 'system' mode: the patient always lands on a known, predictable theme.
export type ThemeMode = 'light' | 'dark';

export const THEME_STORAGE_KEY = 'prelude-health:theme-mode';

export function readStoredThemeMode(): ThemeMode {
  if (typeof window === 'undefined') return 'light';
  return window.localStorage.getItem(THEME_STORAGE_KEY) === 'dark' ? 'dark' : 'light';
}

export function applyThemeMode(mode: ThemeMode): void {
  document.documentElement.setAttribute('data-theme', mode);
}

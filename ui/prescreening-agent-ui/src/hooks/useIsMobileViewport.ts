import { useEffect, useState } from 'react';

// Matches mobile-only rule; kept as a simple width check — not a device sniff.
const MOBILE_MAX_WIDTH_QUERY = '(max-width: 967px)';

export function useIsMobileViewport(): boolean {
  const [isMobile, setIsMobile] = useState(
    () => typeof window !== 'undefined' && window.matchMedia(MOBILE_MAX_WIDTH_QUERY).matches,
  );

  useEffect(() => {
    const mediaQueryList = window.matchMedia(MOBILE_MAX_WIDTH_QUERY);
    const handleChange = (event: MediaQueryListEvent): void => setIsMobile(event.matches);

    mediaQueryList.addEventListener('change', handleChange);
    return () => mediaQueryList.removeEventListener('change', handleChange);
  }, []);

  return isMobile;
}

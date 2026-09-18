import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MobileOnlyGate } from '@/components/MobileOnlyGate';

function mockMatchMedia(matches: boolean): void {
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches,
    media: query,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }));
}

describe('MobileOnlyGate', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('renders children on a mobile viewport', () => {
    mockMatchMedia(true);

    render(
      <MobileOnlyGate>
        <p>Protected content</p>
      </MobileOnlyGate>,
    );

    expect(screen.getByText('Protected content')).toBeInTheDocument();
  });

  it('shows the mobile-only message on a non-mobile viewport', () => {
    mockMatchMedia(false);

    render(
      <MobileOnlyGate>
        <p>Protected content</p>
      </MobileOnlyGate>,
    );

    expect(screen.queryByText('Protected content')).not.toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent(/continue on your phone/i);
  });
});

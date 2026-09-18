import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { ThemeProvider } from '@/app/ThemeProvider';
import { ToastProvider } from '@/components/states';
import { ThemePreview } from '@/pages/ThemePreview';

describe('ThemePreview', () => {
  afterEach(() => {
    document.documentElement.removeAttribute('data-theme');
  });

  it('renders the style-guide sections', () => {
    render(
      <ThemeProvider>
        <ToastProvider>
          <ThemePreview />
        </ToastProvider>
      </ThemeProvider>,
    );

    expect(screen.getByRole('heading', { name: /prelude health/i })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /model loading/i })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /dashboard preview/i })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /^palette$/i })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /ui states/i })).toBeInTheDocument();
  });
});

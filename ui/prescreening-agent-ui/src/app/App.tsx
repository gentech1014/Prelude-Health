import { QueryClientProvider } from '@tanstack/react-query';
import { MotionConfig } from 'framer-motion';
import { RouterProvider } from 'react-router-dom';
import type { JSX } from 'react';
import { ErrorBoundary } from '@/components/ErrorBoundary';
import { ToastProvider } from '@/components/states';
import { queryClient } from '@/lib/queryClient';
import { router } from '@/app/router';
import { ThemeProvider } from '@/app/ThemeProvider';

export function App(): JSX.Element {
  return (
    <ErrorBoundary>
      <ThemeProvider>
        <QueryClientProvider client={queryClient}>
          <ToastProvider>
            {/* Honors the OS-level reduced-motion preference across every page transition. */}
            <MotionConfig reducedMotion="user">
              {/* The mobile-only gate lives on the prescreening call route, not
                  here: booking is a separate surface with its own desktop layout. */}
              <RouterProvider router={router} />
            </MotionConfig>
          </ToastProvider>
        </QueryClientProvider>
      </ThemeProvider>
    </ErrorBoundary>
  );
}

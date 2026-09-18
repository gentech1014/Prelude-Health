import { QueryClient } from '@tanstack/react-query';

// Single shared client — server state lives here, never duplicated into local/global state.
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      staleTime: 30_000,
    },
  },
});

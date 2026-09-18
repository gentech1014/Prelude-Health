/// <reference types="vitest/config" />
import { fileURLToPath } from 'node:url';
import react from '@vitejs/plugin-react';
import { defineConfig, type Plugin } from 'vite';

/** Hook-only modules: `useThing.ts`. Components live in `.tsx` and keep Fast Refresh. */
const HOOK_MODULE = /[\\/]use[A-Z][A-Za-z0-9]*\.ts$/;

/**
 * Full-reload instead of hot-patching when a hook module changes.
 *
 * Fast Refresh keys a component's hook signature on that component's own
 * source. Adding a hook to a hook it imports leaves the signature identical,
 * so React keeps the mounted fiber and the new render lands a `useState` on a
 * slot that held a `useRef` -- "Should have a queue", and the call dies.
 * Reloading loses the same call state the crash did, without the crash.
 */
function reloadOnHookModuleChange(): Plugin {
  return {
    name: 'reload-on-hook-module-change',
    enforce: 'post',
    handleHotUpdate({ file, server }) {
      if (!HOOK_MODULE.test(file)) return;
      server.hot.send({ type: 'full-reload' });
      return [];
    },
  };
}

// The triple-slash reference (Vitest's documented pattern) types the `test`
// key on Vite's own UserConfig without importing vitest/config's wrapped
// defineConfig, which typescript-eslint fails to resolve under type-aware linting.
export default defineConfig({
  plugins: [react(), reloadOnHookModuleChange()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    watch: {
      // Native fs events are unreliable under OneDrive-synced folders (common on
      // Windows, e.g. a project under Documents) — poll as a robust fallback.
      usePolling: true,
      interval: 100,
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./tests/setup.ts'],
    css: true,
  },
});

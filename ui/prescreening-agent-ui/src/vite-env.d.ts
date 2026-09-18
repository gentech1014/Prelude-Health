/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL: string;
  /**
   * Origin of the intake WebSocket. Separate from the REST base on
   * purpose: the backend mounts `/ws/intake/{id}` at the app root, outside
   * `/api/v1`, so the socket URL cannot be derived from the REST base by
   * string concatenation.
   */
  readonly VITE_WS_BASE_URL: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

import axios, { AxiosError } from 'axios';

/** Base of the REST API, e.g. `http://localhost:8000/api/v1`. */
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000/api/v1';

// Centralized API config per CLAUDE.md — never construct ad-hoc axios/fetch calls elsewhere.
export const apiClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: 15_000,
  headers: { 'Content-Type': 'application/json' },
  // The session credential is an HttpOnly cookie the backend sets at attach;
  // a browser only sends it cross-origin when this is on.
  withCredentials: true,
});

/** How a failed API call is described to the rest of the app. */
export type ApiErrorKind =
  | 'unauthorized'
  | 'not-found'
  | 'conflict'
  | 'unavailable'
  | 'offline'
  | 'timeout'
  | 'server'
  | 'malformed';

export class ApiError extends Error {
  readonly kind: ApiErrorKind;

  constructor(kind: ApiErrorKind, message: string) {
    super(message);
    this.name = 'ApiError';
    this.kind = kind;
  }
}

/**
 * Normalizes anything a request can throw into one `ApiError`, so screens
 * branch on a known kind instead of poking at axios internals. Deliberately
 * carries no server detail into `message` — CLAUDE.md requires errors that
 * explain the next step without exposing technical detail or PHI.
 */
export function toApiError(cause: unknown): ApiError {
  if (cause instanceof ApiError) return cause;

  if (cause instanceof AxiosError) {
    if (cause.code === 'ECONNABORTED') {
      return new ApiError('timeout', 'The request took too long. Check your connection and retry.');
    }
    if (cause.response === undefined) {
      return new ApiError('offline', 'You appear to be offline. Reconnect and try again.');
    }
    switch (cause.response.status) {
      case 401:
      case 403:
        return new ApiError('unauthorized', 'This link is no longer valid. Open your latest link.');
      case 404:
        return new ApiError('not-found', 'We could not find this pre-visit screening session.');
      case 409:
        return new ApiError('conflict', 'This step was already completed.');
      case 503:
        return new ApiError(
          'unavailable',
          'This feature is not available yet. Contact your administrator.',
        );
      default:
        return new ApiError('server', 'Something went wrong on our side. Try again shortly.');
    }
  }

  return new ApiError('malformed', 'We received an unexpected response. Try again shortly.');
}

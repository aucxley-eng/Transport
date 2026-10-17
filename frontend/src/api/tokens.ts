/**
 * Token storage for the SimpleJWT pair the backend issues.
 *
 * Kept in one place because both the request interceptor and the auth context
 * need to read the same values, and a stray `localStorage.getItem` elsewhere is
 * how a stale access token survives a logout.
 *
 * localStorage rather than sessionStorage: an operator dashboard is a
 * long-lived, single-tab tool, and losing the session on every tab close is
 * worse than the (already-present) XSS exposure of any JS-reachable store.
 */

const ACCESS_KEY = "transport.access";
const REFRESH_KEY = "transport.refresh";

export interface TokenPair {
  access: string;
  refresh: string;
}

export function getAccessToken(): string | null {
  return localStorage.getItem(ACCESS_KEY);
}

export function getRefreshToken(): string | null {
  return localStorage.getItem(REFRESH_KEY);
}

export function setTokens(pair: TokenPair): void {
  localStorage.setItem(ACCESS_KEY, pair.access);
  localStorage.setItem(REFRESH_KEY, pair.refresh);
}

export function clearTokens(): void {
  localStorage.removeItem(ACCESS_KEY);
  localStorage.removeItem(REFRESH_KEY);
}

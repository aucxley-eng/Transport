import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { fetchCurrentUser, login as loginRequest } from "@/api/auth";
import { ApiError, setSessionExpiredHandler } from "@/api/client";
import { clearTokens, getAccessToken, setTokens } from "@/api/tokens";
import type { CurrentUser } from "@/api/types";

/** A definitive "this token is no good" answer from the server. Anything else
 * (a 500, a dropped connection) is transient and must not destroy the session. */
function isUnauthorized(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401;
}

/**
 * Session state for the whole app.
 *
 * The important property is that `user` is only ever populated from the server's
 * `/me/` response, never from the token. A token that has not been verified says
 * nothing about whether the account still exists or is still active, and reading
 * claims out of it invites a UI that trusts a revoked session.
 */

interface AuthContextValue {
  user: CurrentUser | null;
  /** True until the initial token check resolves, so routes do not flash. */
  initialising: boolean;
  /** Set when the stored token could not be *checked* (network/server error),
   * as opposed to being rejected. The session is kept; the caller retries. */
  sessionError: string | null;
  login: (phone: string, password: string) => Promise<void>;
  logout: () => void;
  retry: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [initialising, setInitialising] = useState(true);
  const [sessionError, setSessionError] = useState<string | null>(null);

  const logout = useCallback(() => {
    clearTokens();
    setUser(null);
    setSessionError(null);
  }, []);

  // The client fires this when a refresh finally fails, so an expired session
  // clears the UI even if the request that discovered it was background fetches.
  useEffect(() => {
    setSessionExpiredHandler(() => setUser(null));
    return () => setSessionExpiredHandler(null);
  }, []);

  // Restore a session on a page reload: a stored token is only trusted once
  // `/me/` confirms it is still good. A network failure keeps the tokens and
  // surfaces a retry instead of silently signing the operator out.
  const restore = useCallback(async () => {
    setInitialising(true);
    setSessionError(null);
    if (getAccessToken() === null) {
      setInitialising(false);
      return;
    }
    try {
      setUser(await fetchCurrentUser());
    } catch (error) {
      if (isUnauthorized(error)) {
        clearTokens();
        setUser(null);
      } else {
        setSessionError(
          "Could not reach the server. Check your connection and try again.",
        );
      }
    } finally {
      setInitialising(false);
    }
  }, []);

  useEffect(() => {
    void restore();
  }, [restore]);

  const login = useCallback(async (phone: string, password: string) => {
    const pair = await loginRequest(phone, password);
    setTokens(pair);
    try {
      setUser(await fetchCurrentUser());
    } catch (error) {
      // A pair the server rejects is not a usable session; a network failure
      // leaves it in place so the operator can retry without signing in again.
      if (isUnauthorized(error)) clearTokens();
      throw error;
    }
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({ user, initialising, sessionError, login, logout, retry: restore }),
    [user, initialising, sessionError, login, logout, restore],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (context === null) {
    throw new Error("useAuth must be used inside an AuthProvider.");
  }
  return context;
}

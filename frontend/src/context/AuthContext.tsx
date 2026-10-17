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
import { setSessionExpiredHandler } from "@/api/client";
import { clearTokens, getAccessToken, setTokens } from "@/api/tokens";
import type { CurrentUser } from "@/api/types";

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
  login: (phone: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [initialising, setInitialising] = useState(true);

  const logout = useCallback(() => {
    clearTokens();
    setUser(null);
  }, []);

  // The client fires this when a refresh finally fails, so an expired session
  // clears the UI even if the request that discovered it was background fetches.
  useEffect(() => {
    setSessionExpiredHandler(() => setUser(null));
    return () => setSessionExpiredHandler(null);
  }, []);

  // Restore a session on a page reload: a stored token is only trusted once
  // `/me/` confirms it is still good.
  useEffect(() => {
    let cancelled = false;

    async function restore() {
      if (getAccessToken() === null) {
        setInitialising(false);
        return;
      }
      try {
        const me = await fetchCurrentUser();
        if (!cancelled) setUser(me);
      } catch {
        if (!cancelled) clearTokens();
      } finally {
        if (!cancelled) setInitialising(false);
      }
    }

    void restore();
    return () => {
      cancelled = true;
    };
  }, []);

  const login = useCallback(async (phone: string, password: string) => {
    const pair = await loginRequest(phone, password);
    setTokens(pair);
    try {
      setUser(await fetchCurrentUser());
    } catch (error) {
      // A token that cannot even load its own profile is not a usable session.
      clearTokens();
      throw error;
    }
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({ user, initialising, login, logout }),
    [user, initialising, login, logout],
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

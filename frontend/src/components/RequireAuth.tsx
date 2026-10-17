import { Navigate, useLocation } from "react-router-dom";

import { useAuth } from "@/context/AuthContext";

/**
 * Gate for authenticated routes.
 *
 * Renders nothing while the stored token is being verified, so a reload does not
 * bounce a signed-in operator to the login screen for one frame. This is a
 * convenience only: the API authorises every request server-side, and a user who
 * bypasses this gate still gets nothing but 401s.
 */
export function RequireAuth({ children }: { children: React.ReactNode }) {
  const { user, initialising } = useAuth();
  const location = useLocation();

  if (initialising) {
    return <div className="centred">Loading…</div>;
  }

  if (user === null) {
    // `from` lets the login screen return the operator to where they were.
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }

  return <>{children}</>;
}

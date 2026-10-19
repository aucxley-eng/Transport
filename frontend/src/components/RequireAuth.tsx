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
  const { user, initialising, sessionError, retry } = useAuth();
  const location = useLocation();

  if (initialising) {
    return <div className="centred">Loading…</div>;
  }

  // The token could not be checked because the server was unreachable. The
  // session is intact, so offer a retry rather than bouncing to the login
  // screen and making the operator sign in again.
  if (sessionError !== null && user === null) {
    return (
      <div className="centred">
        <p role="alert">{sessionError}</p>
        <button type="button" className="primary" onClick={retry}>
          Try again
        </button>
      </div>
    );
  }

  if (user === null) {
    // `from` lets the login screen return the operator to where they were.
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }

  return <>{children}</>;
}

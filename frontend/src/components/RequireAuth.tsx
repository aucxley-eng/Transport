import { Loader2, RefreshCw, ServerOff, ShieldCheck } from "lucide-react";
import { Navigate, useLocation } from "react-router-dom";

import { useAuth } from "@/context/AuthContext";
import { Button } from "@/components/ui/button";

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
    return <SessionGate title="Restoring your session" description="Verifying your access token…" loading />;
  }

  // The token could not be checked because the server was unreachable. The
  // session is intact, so offer a retry rather than bouncing to the login
  // screen and making the operator sign in again.
  if (sessionError !== null && user === null) {
    return (
      <SessionGate
        title="Connection interrupted"
        description={sessionError}
        onRetry={retry}
      />
    );
  }

  if (user === null) {
    // `from` lets the login screen return the operator to where they were.
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }

  return <>{children}</>;
}

function SessionGate({
  title,
  description,
  loading = false,
  onRetry,
}: {
  title: string;
  description: string;
  loading?: boolean;
  onRetry?: () => void;
}) {
  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-[color:var(--background)] p-4">
      <div className="pointer-events-none absolute -right-32 -top-32 h-96 w-96 rounded-full bg-[color:var(--primary)]/5 blur-3xl" />
      <div className="pointer-events-none absolute -bottom-32 -left-32 h-96 w-96 rounded-full bg-[color:var(--primary)]/5 blur-3xl" />

      <div className="relative w-full max-w-md">
        <div className="mb-8 flex items-center justify-center gap-2">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-[color:var(--primary)] to-[#00A896] shadow-lg shadow-[color:var(--primary)]/20">
            <div className="h-2 w-2 rounded-full bg-white" />
          </div>
          <span className="bg-gradient-to-r from-[color:var(--text)] to-[color:var(--primary)] bg-clip-text text-2xl font-bold text-transparent">
            Transport Ops
          </span>
        </div>

        <div className="relative overflow-hidden rounded-2xl border border-[color:var(--surface-border)] bg-[color:var(--surface)]/80 p-8 shadow-2xl shadow-slate-900/5 backdrop-blur-xl">
          <div className="absolute left-0 right-0 top-0 h-px bg-gradient-to-r from-transparent via-[color:var(--primary)]/30 to-transparent" />

          <div className="flex flex-col items-center text-center">
            <div
              className={
                loading
                  ? "flex h-16 w-16 items-center justify-center rounded-2xl bg-[color:var(--primary)]/10 text-[color:var(--primary)]"
                  : "flex h-16 w-16 items-center justify-center rounded-2xl bg-[color:var(--danger-bg)] text-[color:var(--danger)]"
              }
            >
              {loading ? (
                <Loader2 className="h-8 w-8 animate-spin" aria-hidden="true" />
              ) : (
                <ServerOff className="h-8 w-8" aria-hidden="true" />
              )}
            </div>

            <h1 className="mt-5 text-xl font-bold text-[color:var(--text)]">{title}</h1>
            <p
              role={loading ? undefined : "alert"}
              className="mt-2 max-w-sm text-sm leading-relaxed text-[color:var(--text-subtle)]"
            >
              {description}
            </p>

            {!loading && (
              <>
                <Button
                  type="button"
                  variant="primary"
                  size="lg"
                  className="mt-6 h-12 w-full rounded-xl bg-gradient-to-r from-[color:var(--primary)] to-[#00A896] shadow-lg shadow-[color:var(--primary)]/20 transition-all hover:shadow-xl hover:shadow-[color:var(--primary)]/30"
                  onClick={onRetry}
                  icon={<RefreshCw className="h-4 w-4" />}
                >
                  Try again
                </Button>

                <div className="mt-6 flex items-center gap-2 rounded-xl border border-[color:var(--surface-border)] bg-[color:var(--surface-muted)] px-4 py-3 text-left">
                  <ShieldCheck className="h-4 w-4 shrink-0 text-[color:var(--primary)]" aria-hidden="true" />
                  <p className="text-xs leading-relaxed text-[color:var(--text-subtle)]">
                    Your session is still active. Nothing was lost — retry once the
                    API is reachable.
                  </p>
                </div>
              </>
            )}

            {loading && (
              <div className="mt-8 h-1 w-40 overflow-hidden rounded-full bg-[color:var(--surface-muted)]">
                <div className="h-full w-1/2 animate-[loading_1.2s_ease-in-out_infinite] rounded-full bg-[color:var(--primary)]" />
              </div>
            )}
          </div>
        </div>

        <p className="mt-6 text-center text-xs text-[color:var(--text-subtle)]">
          © 2026 Kenya Transport &amp; Logistics
        </p>
      </div>

      <style>{`
        @keyframes loading {
          0% { transform: translateX(-100%); }
          100% { transform: translateX(300%); }
        }
      `}</style>
    </div>
  );
}

import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";

import { describeError } from "@/api/client";
import { useAuth } from "@/context/AuthContext";

/**
 * Operator sign-in.
 *
 * The field is a phone number, not an email or username: the user model's
 * USERNAME_FIELD is `phone`, and the API rejects anything else at the token
 * endpoint. The seeded local accounts are documented in the README.
 */

interface LocationState {
  from?: string;
}

export function LoginPage() {
  const { user, login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const [phone, setPhone] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const from = (location.state as LocationState | null)?.from ?? "/dispatch";

  // An operator who already holds a session should never see the login form.
  if (user !== null) {
    return <Navigate to={from} replace />;
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(phone.trim(), password);
      navigate(from, { replace: true });
    } catch (caught) {
      setError(describeError(caught));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="auth">
      <div className="auth__panel">
        <h1>Transport Ops</h1>
        <p className="auth__hint">
          Sign in with your staff phone number.
        </p>

        <form onSubmit={handleSubmit} noValidate>
          {error !== null && (
            <div className="alert" role="alert" style={{ marginBottom: 12 }}>
              {error}
            </div>
          )}

          <label className="field">
            <span>Phone</span>
            <input
              type="tel"
              value={phone}
              onChange={(event) => setPhone(event.target.value)}
              placeholder="+254700000001"
              autoComplete="username"
              autoFocus
              required
            />
          </label>

          <label className="field">
            <span>Password</span>
            <input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete="current-password"
              required
            />
          </label>

          <button
            type="submit"
            className="primary auth__submit"
            disabled={submitting || phone === "" || password === ""}
          >
            {submitting ? "Signing in…" : "Sign in"}
          </button>
        </form>
      </div>
    </div>
  );
}

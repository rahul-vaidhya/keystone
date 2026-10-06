import { FormEvent, useEffect, useState } from "react";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { Link, Navigate } from "react-router-dom";
import { ApiError } from "../types/auth";
import { useAuth } from "../hooks/useAuth";
import { clearSessionNotice, peekSessionNotice } from "../services/http";

export function LoginPage() {
  useDocumentTitle("Sign in");
  const { user, login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [orgId, setOrgId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [orgChoices, setOrgChoices] = useState<{ org_id: string; org_name: string }[]>(
    [],
  );
  const [submitting, setSubmitting] = useState(false);
  // U4: set by the HTTP layer when a session couldn't be recovered (401 + failed refresh).
  // Read in render (StrictMode-safe), cleared after mount so a later visit to /login
  // doesn't show a stale notice.
  const [notice] = useState<string | null>(() => peekSessionNotice());
  useEffect(() => {
    clearSessionNotice();
  }, []);

  if (user) return <Navigate to="/app" replace />;

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(email, password, orgId || undefined);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        const body = err.body as {
          org_choices?: { org_id: string; org_name: string }[];
        };
        if (body.org_choices?.length) {
          setOrgChoices(body.org_choices);
          setError("Pick your organization below, then sign in again.");
          setSubmitting(false);
          return;
        }
      }
      setError(err instanceof ApiError ? err.message : "Login failed");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center p-4">
      <div className="w-full max-w-md bg-surface border border-border rounded-lg p-6 space-y-6">
        <div>
          <h1 className="text-xl font-semibold">Sign in to Veratas</h1>
          <p className="text-sm text-muted mt-1">
            Source-grounded company knowledge base
          </p>
        </div>

        {notice && (
          <p
            className="text-sm border border-warning text-warning rounded-md px-3 py-2"
            role="status"
          >
            {notice}
          </p>
        )}

        <form onSubmit={onSubmit} className="space-y-4">
          <label className="block space-y-1">
            <span className="text-sm text-muted">Email</span>
            <input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full bg-bg border border-border rounded-md px-3 py-2 focus:outline-none focus:border-accent"
            />
          </label>

          <label className="block space-y-1">
            <span className="text-sm text-muted">Password</span>
            <input
              type="password"
              required
              minLength={8}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full bg-bg border border-border rounded-md px-3 py-2 focus:outline-none focus:border-accent"
            />
          </label>

          {orgChoices.length > 0 && (
            <label className="block space-y-1">
              <span className="text-sm text-muted">Organization</span>
              <select
                value={orgId}
                onChange={(e) => setOrgId(e.target.value)}
                required
                className="w-full bg-bg border border-border rounded-md px-3 py-2"
              >
                <option value="">Select organization…</option>
                {orgChoices.map((o) => (
                  <option key={o.org_id} value={o.org_id}>
                    {o.org_name}
                  </option>
                ))}
              </select>
            </label>
          )}

          {error && (
            <p className="text-sm text-danger" role="alert">
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={submitting}
            className="w-full bg-accent hover:opacity-90 disabled:opacity-50 text-white rounded-md py-2 font-medium transition"
          >
            {submitting ? "Signing in…" : "Sign in"}
          </button>
        </form>

        <p className="text-sm text-muted text-center">
          No account?{" "}
          <Link to="/signup" className="text-accent hover:underline">
            Create one
          </Link>
        </p>
      </div>
    </div>
  );
}

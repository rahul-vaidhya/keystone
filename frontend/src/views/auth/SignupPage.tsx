import { FormEvent, useState } from "react";
import { Link, Navigate } from "react-router-dom";
import { ApiError } from "../../models/auth";
import { useAuth } from "../../lib/auth";

export function SignupPage() {
  const { user, signup } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [orgName, setOrgName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  if (user) return <Navigate to="/app" replace />;

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await signup(email, password, orgName);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Signup failed");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center p-4">
      <div className="w-full max-w-md bg-surface border border-border rounded-lg p-6 space-y-6">
        <div>
          <h1 className="text-xl font-semibold">Create your workspace</h1>
          <p className="text-sm text-muted mt-1">
            You&apos;ll be the organization owner
          </p>
        </div>

        <form onSubmit={onSubmit} className="space-y-4">
          <label className="block space-y-1">
            <span className="text-sm text-muted">Organization name</span>
            <input
              type="text"
              required
              value={orgName}
              onChange={(e) => setOrgName(e.target.value)}
              className="w-full bg-bg border border-border rounded-md px-3 py-2 focus:outline-none focus:border-accent"
            />
          </label>

          <label className="block space-y-1">
            <span className="text-sm text-muted">Work email</span>
            <input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full bg-bg border border-border rounded-md px-3 py-2 focus:outline-none focus:border-accent"
            />
          </label>

          <label className="block space-y-1">
            <span className="text-sm text-muted">Password (min 8 characters)</span>
            <input
              type="password"
              required
              minLength={8}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full bg-bg border border-border rounded-md px-3 py-2 focus:outline-none focus:border-accent"
            />
          </label>

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
            {submitting ? "Creating…" : "Create account"}
          </button>
        </form>

        <p className="text-sm text-muted text-center">
          Already have an account?{" "}
          <Link to="/login" className="text-accent hover:underline">
            Sign in
          </Link>
        </p>
      </div>
    </div>
  );
}

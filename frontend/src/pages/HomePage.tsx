import { Link } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";

export function HomePage() {
  const { user } = useAuth();
  const name = user?.email?.split("@")[0] ?? "";

  return (
    <div className="flex-1 flex items-center justify-center px-6">
      <div className="w-full max-w-2xl text-center space-y-8">
        <h1 className="text-3xl font-semibold tracking-tight">
          Welcome, <span className="capitalize">{name}</span>
        </h1>

        <Link
          to="/app/notebooks"
          className="flex items-center gap-3 bg-surface border border-border rounded-full px-5 py-3 hover:border-accent transition-colors"
        >
          <span className="text-xl text-muted">+</span>
          <span className="flex-1 text-left text-sm text-muted">
            Ask Veratas about your documents…
          </span>
          <span className="text-xs text-accent border border-border rounded-md px-2 py-1">
            Open a notebook
          </span>
        </Link>

        <p className="text-sm text-muted">
          Chat lives inside notebooks — open or create one to start asking questions.
        </p>
      </div>
    </div>
  );
}

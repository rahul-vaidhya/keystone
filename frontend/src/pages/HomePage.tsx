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

        <div className="flex items-center gap-3 bg-surface border border-border rounded-full px-5 py-3 opacity-60">
          <span className="text-xl text-muted">+</span>
          <input
            type="text"
            placeholder="Ask Veratas about your documents…"
            disabled
            className="flex-1 bg-transparent text-sm focus:outline-none cursor-not-allowed placeholder:text-muted"
          />
          <span className="text-xs text-muted border border-border rounded-md px-2 py-1">
            Coming soon
          </span>
        </div>

        <p className="text-sm text-muted">
          Chat arrives once ingestion and retrieval are wired up.
        </p>
      </div>
    </div>
  );
}

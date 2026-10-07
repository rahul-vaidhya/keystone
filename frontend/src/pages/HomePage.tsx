import { Link } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";

// Fallback ONLY — used when the user has no real ``name`` on file (every user who
// signed up before this field existed, plus any invited member, since name
// collection isn't wired into the invite flow yet). Splits the email local-part on
// separators/digits and title-cases each remaining word-like segment, e.g.
// "j.smith23@company.com" -> "J Smith" instead of the old raw "J.smith23".
export function deriveDisplayNameFromEmail(email: string): string {
  const local = email.split("@")[0] ?? "";
  const words = local
    .split(/[._\-\d]+/)
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase());
  if (words.length > 0) return words.join(" ");
  // No letter-like segment at all (e.g. a purely numeric local part) — fall back to
  // just capitalizing whatever's there rather than showing an empty greeting.
  return local ? local.charAt(0).toUpperCase() + local.slice(1).toLowerCase() : "";
}

export function HomePage() {
  const { user } = useAuth();
  const displayName =
    user?.name?.trim() || (user?.email ? deriveDisplayNameFromEmail(user.email) : "");

  return (
    <div className="flex-1 flex items-center justify-center px-6">
      <div className="w-full max-w-2xl text-center space-y-8">
        <h1 className="text-3xl font-semibold tracking-tight">
          Welcome, <span>{displayName}</span>
        </h1>

        <Link
          to="/app/notebooks"
          className="flex items-center gap-3 bg-surface border border-border rounded-full px-5 py-3 hover:border-accent transition-colors"
        >
          <span className="text-xl text-muted">+</span>
          <span className="flex-1 text-left text-sm text-muted">
            Ask Keystone about your documents…
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

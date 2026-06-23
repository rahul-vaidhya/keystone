import { NavLink, useNavigate } from "react-router-dom";
import { useAuth } from "../../lib/auth";

const ROLE_LABEL: Record<string, string> = {
  owner: "Owner",
  admin: "Admin",
  member: "Member",
};

function NavItem({
  to,
  label,
  disabled,
}: {
  to: string;
  label: string;
  disabled?: boolean;
}) {
  if (disabled) {
    return (
      <span className="flex items-center gap-2 px-3 py-2 rounded-md text-sm text-muted opacity-50 cursor-not-allowed">
        {label}
      </span>
    );
  }
  return (
    <NavLink
      to={to}
      end
      className={({ isActive }) =>
        `flex items-center gap-2 px-3 py-2 rounded-md text-sm transition hover:bg-surface ${
          isActive ? "bg-surface text-accent" : "text-muted hover:text-text"
        }`
      }
    >
      {label}
    </NavLink>
  );
}

export function Sidebar() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const canManageTeam = user?.role === "owner" || user?.role === "admin";

  async function handleLogout() {
    await logout();
    navigate("/login");
  }

  const initial = user?.email?.[0]?.toUpperCase() ?? "?";

  return (
    <aside className="w-60 shrink-0 bg-bg border-r border-border flex flex-col h-screen">
      <div className="px-4 py-4">
        <span className="font-semibold tracking-tight text-lg">Veratas</span>
      </div>

      <nav className="flex-1 px-2 space-y-1">
        <NavItem to="/app" label="Home" />
        <NavItem to="" label="New chat" disabled />
        <NavItem to="" label="Search" disabled />
      </nav>

      <div className="px-2 pb-3 space-y-1 border-t border-border pt-3">
        <div className="flex items-center gap-2 px-3 py-2">
          <span className="w-7 h-7 rounded-full bg-accent text-white text-xs flex items-center justify-center shrink-0">
            {initial}
          </span>
          <div className="min-w-0 flex-1">
            <p className="text-sm truncate">{user?.email}</p>
            <p className="text-xs text-muted">
              {user ? (ROLE_LABEL[user.role] ?? user.role) : ""}
            </p>
          </div>
          {canManageTeam && (
            <button
              type="button"
              onClick={() => navigate("/app/users")}
              title="Team settings"
              aria-label="Team settings"
              className="text-muted hover:text-text transition p-1 rounded-md hover:bg-surface"
            >
              ⚙
            </button>
          )}
        </div>
        <button
          type="button"
          onClick={() => void handleLogout()}
          className="w-full flex items-center gap-2 text-sm text-muted hover:text-danger hover:border-danger transition px-3 py-2 rounded-md border border-border hover:bg-surface"
        >
          <span aria-hidden="true">⏻</span>
          Sign out
        </button>
      </div>
    </aside>
  );
}

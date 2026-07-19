import { useEffect } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";

const ROLE_LABEL: Record<string, string> = {
  owner: "Owner",
  admin: "Admin",
  member: "Member",
};

function NavItem({
  to,
  label,
  disabled,
  onNavigate,
}: {
  to: string;
  label: string;
  disabled?: boolean;
  onNavigate?: () => void;
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
      onClick={onNavigate}
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

export function Sidebar({
  isOpen,
  onClose,
}: {
  isOpen: boolean;
  onClose: () => void;
}) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const canManageTeam = user?.role === "owner" || user?.role === "admin";

  async function handleLogout() {
    await logout();
    navigate("/login");
  }

  // Mobile off-canvas drawer: close on Escape. Only listens while open, and only
  // matters below `lg:` (the drawer is visually inert at `lg:`+ regardless).
  useEffect(() => {
    if (!isOpen) return;
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, onClose]);

  const initial = user?.email?.[0]?.toUpperCase() ?? "?";

  return (
    <>
      {/* Backdrop: mobile-only, dismisses the drawer on click. Absent at `lg:`+. */}
      {isOpen && (
        <div
          className="fixed inset-0 bg-black/40 z-30 lg:hidden"
          onClick={onClose}
          aria-hidden="true"
        />
      )}
      <aside
        id="app-sidebar"
        className={`fixed inset-y-0 left-0 z-40 transition-transform duration-200 ${
          isOpen ? "translate-x-0" : "-translate-x-full"
        } lg:translate-x-0 lg:static w-60 shrink-0 bg-bg border-r border-border flex flex-col h-screen`}
      >
        <div className="px-4 py-4">
          <span className="font-semibold tracking-tight text-lg">Veratas</span>
        </div>

        <nav aria-label="Main navigation" className="flex-1 px-2 space-y-1">
          <NavItem to="/app" label="Home" onNavigate={onClose} />
          <NavItem to="/app/repository" label="Repository" onNavigate={onClose} />
          <NavItem to="/app/notebooks" label="Notebooks" onNavigate={onClose} />
          <NavItem to="/app/search" label="Search" onNavigate={onClose} />
          {canManageTeam && (
            <NavItem to="/app/access-roles" label="Access Roles" onNavigate={onClose} />
          )}
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
    </>
  );
}

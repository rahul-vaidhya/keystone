import { useEffect, useState } from "react";
import { Outlet, useLocation } from "react-router-dom";
import { Sidebar } from "./Sidebar";

// Per-route document titles for the static app pages. Notebook detail pages are
// deliberately absent — NotebookPage sets its own title from the notebook name
// (useDocumentTitle), and this effect must not overwrite it.
const ROUTE_TITLES: Record<string, string> = {
  "/app": "Home",
  "/app/repository": "Repository",
  "/app/notebooks": "Notebooks",
  "/app/search": "Search",
  "/app/access-roles": "Access Roles",
  "/app/embed": "Embed widgets",
  "/app/users": "Team",
};

export function AppShell() {
  // Mobile-only off-canvas sidebar toggle. Lifted here (rather than kept local to
  // Sidebar) because the hamburger trigger lives in this top bar while the drawer
  // itself renders inside Sidebar — both need the same isOpen/onClose. Irrelevant
  // at `lg:`+, where the static desktop sidebar (Sidebar's own `lg:` classes) and
  // this top bar (`lg:hidden`) make the toggle inert.
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const { pathname } = useLocation();

  useEffect(() => {
    const title = ROUTE_TITLES[pathname.replace(/\/+$/, "") || "/app"];
    if (title) document.title = `${title} · Veratas`;
  }, [pathname]);

  return (
    // F1: fixed-viewport shell. The shell itself never scrolls (h-dvh +
    // overflow-hidden); each page owns its scroll region via `flex-1 min-h-0` +
    // `overflow-y-auto` on its inner panes, so the sidebar, notebook header/tabs
    // and the chat input stay pinned. The Outlet wrapper is a scroll fallback for
    // any page that doesn't manage its own overflow.
    <div className="flex h-dvh overflow-hidden">
      <Sidebar isOpen={isSidebarOpen} onClose={() => setIsSidebarOpen(false)} />
      <div className="flex-1 min-w-0 min-h-0 flex flex-col">
        <div className="lg:hidden shrink-0 flex items-center gap-3 px-4 py-3 border-b border-border">
          <button
            type="button"
            onClick={() => setIsSidebarOpen((open) => !open)}
            aria-expanded={isSidebarOpen}
            aria-controls="app-sidebar"
            aria-label={isSidebarOpen ? "Close menu" : "Open menu"}
            className="text-xl leading-none text-muted hover:text-text transition p-1 rounded-md hover:bg-surface"
          >
            {isSidebarOpen ? "✕" : "☰"}
          </button>
          <span className="font-semibold tracking-tight text-lg">Veratas</span>
        </div>
        <div className="flex-1 min-h-0 flex flex-col overflow-y-auto">
          <Outlet />
        </div>
      </div>
    </div>
  );
}

import { useState } from "react";
import { Outlet } from "react-router-dom";
import { Sidebar } from "./Sidebar";

export function AppShell() {
  // Mobile-only off-canvas sidebar toggle. Lifted here (rather than kept local to
  // Sidebar) because the hamburger trigger lives in this top bar while the drawer
  // itself renders inside Sidebar — both need the same isOpen/onClose. Irrelevant
  // at `lg:`+, where the static desktop sidebar (Sidebar's own `lg:` classes) and
  // this top bar (`lg:hidden`) make the toggle inert.
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);

  return (
    <div className="flex min-h-screen">
      <Sidebar isOpen={isSidebarOpen} onClose={() => setIsSidebarOpen(false)} />
      <div className="flex-1 min-w-0 flex flex-col">
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
        <Outlet />
      </div>
    </div>
  );
}

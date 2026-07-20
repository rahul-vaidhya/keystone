import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";
import { Sidebar } from "./Sidebar";

vi.mock("../hooks/useAuth", () => ({ useAuth: vi.fn() }));

function mockUser(role: "owner" | "admin" | "member" = "owner") {
  vi.mocked(useAuth).mockReturnValue({
    user: {
      id: "u-1",
      org_id: "org-1",
      email: "u@test.com",
      name: null,
      role,
      is_active: true,
      created_at: "2026-01-01T00:00:00Z",
    },
    loading: false,
    login: vi.fn(),
    signup: vi.fn(),
    acceptInvite: vi.fn(),
    logout: vi.fn(),
  });
}

function renderSidebar(isOpen: boolean, onClose: () => void = vi.fn()) {
  return render(
    <MemoryRouter>
      <Sidebar isOpen={isOpen} onClose={onClose} />
    </MemoryRouter>,
  );
}

describe("Sidebar (mobile off-canvas drawer)", () => {
  beforeEach(() => {
    mockUser("owner");
  });

  it("keeps the drawer translated off-screen when closed", () => {
    renderSidebar(false);
    expect(screen.getByRole("complementary")).toHaveClass("-translate-x-full");
  });

  it("slides the drawer into view when open", () => {
    renderSidebar(true);
    expect(screen.getByRole("complementary")).toHaveClass("translate-x-0");
  });

  it("renders no backdrop when closed", () => {
    renderSidebar(false);
    expect(document.querySelector(".bg-black\\/40")).not.toBeInTheDocument();
  });

  it("renders a backdrop when open and calls onClose when it is clicked", () => {
    const onClose = vi.fn();
    renderSidebar(true, onClose);
    const backdrop = document.querySelector(".bg-black\\/40");
    expect(backdrop).toBeInTheDocument();
    fireEvent.click(backdrop!);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("calls onClose when Escape is pressed while open", () => {
    const onClose = vi.fn();
    renderSidebar(true, onClose);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("does not call onClose on Escape when already closed", () => {
    const onClose = vi.fn();
    renderSidebar(false, onClose);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).not.toHaveBeenCalled();
  });

  it("calls onClose when a nav link is clicked (so navigating closes the drawer)", () => {
    const onClose = vi.fn();
    renderSidebar(true, onClose);
    fireEvent.click(screen.getByText("Repository"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

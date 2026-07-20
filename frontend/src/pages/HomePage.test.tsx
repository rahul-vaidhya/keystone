import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";
import { HomePage, deriveDisplayNameFromEmail } from "./HomePage";

vi.mock("../hooks/useAuth", () => ({ useAuth: vi.fn() }));

function mockUser(overrides: { name: string | null; email: string }) {
  vi.mocked(useAuth).mockReturnValue({
    user: {
      id: "u-1",
      org_id: "org-1",
      email: overrides.email,
      name: overrides.name,
      role: "member",
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

function renderHomePage() {
  return render(
    <MemoryRouter>
      <HomePage />
    </MemoryRouter>,
  );
}

describe("HomePage — welcome greeting", () => {
  beforeEach(() => {
    vi.mocked(useAuth).mockReset();
  });

  it("shows the real name when the user has one on file", () => {
    mockUser({ name: "Uma Reviewer", email: "uxreview@company.com" });
    renderHomePage();

    const heading = screen.getByRole("heading");
    expect(heading.textContent).toContain("Welcome,");
    expect(screen.getByText("Uma Reviewer")).toBeInTheDocument();
  });

  it("falls back to the improved email-derived name when name is null", () => {
    mockUser({ name: null, email: "j.smith23@company.com" });
    renderHomePage();

    // Never the old raw split-on-@ text ("J.smith23") — a real word-split, no
    // stray dot, no stray trailing digits.
    expect(screen.getByText("J Smith")).toBeInTheDocument();
    expect(screen.queryByText("J.smith23")).not.toBeInTheDocument();
  });

  it("renders the greeting even when there is no user", () => {
    vi.mocked(useAuth).mockReturnValue({
      user: null,
      loading: false,
      login: vi.fn(),
      signup: vi.fn(),
      acceptInvite: vi.fn(),
      logout: vi.fn(),
    });
    renderHomePage();

    expect(screen.getByRole("heading").textContent).toContain("Welcome,");
  });
});

describe("deriveDisplayNameFromEmail", () => {
  it("splits on dots/digits and title-cases each word", () => {
    expect(deriveDisplayNameFromEmail("j.smith23@company.com")).toBe("J Smith");
  });

  it("handles underscores and hyphens as separators too", () => {
    expect(deriveDisplayNameFromEmail("jane_doe-99@company.com")).toBe("Jane Doe");
  });

  it("title-cases a single-word local part", () => {
    expect(deriveDisplayNameFromEmail("uxreview@company.com")).toBe("Uxreview");
  });

  it("falls back to a capitalized raw local part when there is no letter segment", () => {
    expect(deriveDisplayNameFromEmail("12345@company.com")).toBe("12345");
  });
});

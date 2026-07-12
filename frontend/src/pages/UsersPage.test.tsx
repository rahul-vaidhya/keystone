import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { authApi } from "../services/authService";
import { useAuth } from "../hooks/useAuth";
import { UsersPage } from "./UsersPage";

vi.mock("../services/authService", () => ({
  authApi: {
    listUsers: vi.fn(),
    changeRole: vi.fn(),
    invite: vi.fn(),
    getOrg: vi.fn(),
    renameOrg: vi.fn(),
  },
}));

vi.mock("../hooks/useAuth", () => ({ useAuth: vi.fn() }));

function mockUser(role: "owner" | "admin" | "member" = "owner") {
  vi.mocked(useAuth).mockReturnValue({
    user: {
      id: "u-1",
      org_id: "org-1",
      email: "u@test.com",
      role,
      created_at: "2026-01-01T00:00:00Z",
    },
    loading: false,
    login: vi.fn(),
    signup: vi.fn(),
    logout: vi.fn(),
  });
}

function renderWithClient(ui: React.ReactElement) {
  const queryClient = new QueryClient();
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

describe("UsersPage", () => {
  beforeEach(() => {
    vi.mocked(authApi.listUsers).mockReset().mockResolvedValue([
      { id: "u-1", org_id: "org-1", email: "u@test.com", role: "owner", created_at: "" },
    ]);
    vi.mocked(authApi.getOrg).mockReset().mockResolvedValue({ id: "org-1", name: "Acme" });
    vi.mocked(authApi.renameOrg).mockReset();
    vi.mocked(authApi.invite).mockReset();
    vi.mocked(authApi.changeRole).mockReset();
  });

  it("shows the invite form and submits it for an owner", async () => {
    mockUser("owner");
    vi.mocked(authApi.invite).mockResolvedValue({
      id: "u-2",
      org_id: "org-1",
      email: "newbie@test.com",
      role: "member",
      created_at: "",
    });

    renderWithClient(<UsersPage />);
    await waitFor(() => expect(screen.getByText("Acme")).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "newbie@test.com" },
    });
    fireEvent.change(screen.getByLabelText("Initial password"), {
      target: { value: "password123" },
    });
    fireEvent.click(screen.getByText("Invite"));

    await waitFor(() =>
      expect(authApi.invite).toHaveBeenCalledWith("newbie@test.com", "password123", "member"),
    );
  });

  it("hides the invite form and org-rename control for a plain member", async () => {
    mockUser("member");

    renderWithClient(<UsersPage />);
    await waitFor(() => expect(screen.getByText("Acme")).toBeInTheDocument());

    expect(screen.queryByLabelText("Email")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Rename organization")).not.toBeInTheDocument();
  });

  it("lets an admin rename the organization", async () => {
    mockUser("admin");
    vi.mocked(authApi.renameOrg).mockResolvedValue({ id: "org-1", name: "New Name" });

    renderWithClient(<UsersPage />);
    await waitFor(() => expect(screen.getByText("Acme")).toBeInTheDocument());

    fireEvent.click(screen.getByLabelText("Rename organization"));
    const input = screen.getByDisplayValue("Acme");
    fireEvent.change(input, { target: { value: "New Name" } });
    fireEvent.click(screen.getByText("Save"));

    await waitFor(() => expect(authApi.renameOrg).toHaveBeenCalledWith("New Name"));
  });
});

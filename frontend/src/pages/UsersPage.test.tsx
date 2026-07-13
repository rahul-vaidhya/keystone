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
    setUserActive: vi.fn(),
    changePassword: vi.fn(),
  },
}));

vi.mock("../services/http", () => ({ setStoredAccessToken: vi.fn() }));

vi.mock("../hooks/useAuth", () => ({ useAuth: vi.fn() }));

function mockUser(role: "owner" | "admin" | "member" = "owner") {
  vi.mocked(useAuth).mockReturnValue({
    user: {
      id: "u-1",
      org_id: "org-1",
      email: "u@test.com",
      role,
      is_active: true,
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
      {
        id: "u-1",
        org_id: "org-1",
        email: "u@test.com",
        role: "owner",
        is_active: true,
        created_at: "",
      },
      {
        id: "u-2",
        org_id: "org-1",
        email: "member@test.com",
        role: "member",
        is_active: true,
        created_at: "",
      },
    ]);
    vi.mocked(authApi.getOrg).mockReset().mockResolvedValue({ id: "org-1", name: "Acme" });
    vi.mocked(authApi.renameOrg).mockReset();
    vi.mocked(authApi.invite).mockReset();
    vi.mocked(authApi.changeRole).mockReset();
    vi.mocked(authApi.setUserActive).mockReset();
    vi.mocked(authApi.changePassword).mockReset();
  });

  it("shows the invite form and submits it for an owner", async () => {
    mockUser("owner");
    vi.mocked(authApi.invite).mockResolvedValue({
      id: "u-2",
      org_id: "org-1",
      email: "newbie@test.com",
      role: "member",
      is_active: true,
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

  it("lets an owner remove a member, with a confirm guard", async () => {
    mockUser("owner");
    vi.mocked(authApi.setUserActive).mockResolvedValue({
      id: "u-2",
      org_id: "org-1",
      email: "member@test.com",
      role: "member",
      is_active: false,
      created_at: "",
    });
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);

    renderWithClient(<UsersPage />);
    await waitFor(() => expect(screen.getByText("member@test.com")).toBeInTheDocument());

    fireEvent.click(screen.getByText("Remove"));

    expect(confirmSpy).toHaveBeenCalled();
    await waitFor(() => expect(authApi.setUserActive).toHaveBeenCalledWith("u-2", false));
    confirmSpy.mockRestore();
  });

  it("does not remove a member when the confirm dialog is cancelled", async () => {
    mockUser("owner");
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);

    renderWithClient(<UsersPage />);
    await waitFor(() => expect(screen.getByText("member@test.com")).toBeInTheDocument());

    fireEvent.click(screen.getByText("Remove"));

    expect(confirmSpy).toHaveBeenCalled();
    expect(authApi.setUserActive).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  it("hides remove/reactivate controls for a plain member", async () => {
    mockUser("member");

    renderWithClient(<UsersPage />);
    await waitFor(() => expect(screen.getByText("member@test.com")).toBeInTheDocument());

    expect(screen.queryByText("Remove")).not.toBeInTheDocument();
  });

  it("shows a Reactivate button for a deactivated member and calls setUserActive(true)", async () => {
    mockUser("owner");
    vi.mocked(authApi.listUsers).mockResolvedValue([
      {
        id: "u-1",
        org_id: "org-1",
        email: "u@test.com",
        role: "owner",
        is_active: true,
        created_at: "",
      },
      {
        id: "u-3",
        org_id: "org-1",
        email: "removed@test.com",
        role: "member",
        is_active: false,
        created_at: "",
      },
    ]);
    vi.mocked(authApi.setUserActive).mockResolvedValue({
      id: "u-3",
      org_id: "org-1",
      email: "removed@test.com",
      role: "member",
      is_active: true,
      created_at: "",
    });

    renderWithClient(<UsersPage />);
    await waitFor(() => expect(screen.getByText("removed@test.com")).toBeInTheDocument());

    fireEvent.click(screen.getByText("Reactivate"));

    await waitFor(() => expect(authApi.setUserActive).toHaveBeenCalledWith("u-3", true));
  });

  it("lets a user change their own password", async () => {
    mockUser("member");
    vi.mocked(authApi.changePassword).mockResolvedValue({
      access_token: "new-token",
      token_type: "bearer",
    });

    renderWithClient(<UsersPage />);
    await waitFor(() => expect(screen.getByText("Acme")).toBeInTheDocument());

    fireEvent.click(screen.getByText("Change your password"));
    fireEvent.change(screen.getByLabelText("Current password"), {
      target: { value: "oldpass123" },
    });
    fireEvent.change(screen.getByLabelText("New password"), {
      target: { value: "newpass456" },
    });
    fireEvent.click(screen.getByText("Update password"));

    await waitFor(() =>
      expect(authApi.changePassword).toHaveBeenCalledWith("oldpass123", "newpass456"),
    );
  });
});

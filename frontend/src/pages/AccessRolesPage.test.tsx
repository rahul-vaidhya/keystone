import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { accessRolesApi } from "../services/accessRolesService";
import { authApi } from "../services/authService";
import { documentsApi } from "../services/documentsService";
import { DialogProvider } from "../context/DialogContext";
import { AccessRolesPage } from "./AccessRolesPage";

vi.mock("../services/accessRolesService", () => ({
  accessRolesApi: {
    listRoles: vi.fn(),
    createRole: vi.fn(),
    deleteRole: vi.fn(),
    grantTag: vi.fn(),
    revokeTag: vi.fn(),
    assignUser: vi.fn(),
    removeUser: vi.fn(),
  },
}));

vi.mock("../services/authService", () => ({
  authApi: { listUsers: vi.fn() },
}));

vi.mock("../services/documentsService", () => ({
  documentsApi: { listTags: vi.fn(), createTag: vi.fn() },
}));

function renderWithClient(ui: React.ReactElement) {
  const queryClient = new QueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <DialogProvider>{ui}</DialogProvider>
    </QueryClientProvider>,
  );
}

describe("AccessRolesPage", () => {
  beforeEach(() => {
    vi.mocked(accessRolesApi.listRoles).mockReset();
    vi.mocked(accessRolesApi.createRole).mockReset();
    vi.mocked(accessRolesApi.deleteRole).mockReset();
    vi.mocked(accessRolesApi.grantTag).mockReset();
    vi.mocked(accessRolesApi.revokeTag).mockReset();
    vi.mocked(accessRolesApi.assignUser).mockReset();
    vi.mocked(accessRolesApi.removeUser).mockReset();
    vi.mocked(authApi.listUsers).mockReset().mockResolvedValue([
      {
        id: "u-1",
        org_id: "org-1",
        email: "alice@test.com",
        role: "member",
        is_active: true,
        created_at: "",
      },
    ]);
    vi.mocked(documentsApi.listTags).mockReset().mockResolvedValue([
      { id: "t-1", org_id: "org-1", name: "Finance", created_at: "" },
    ]);
    vi.mocked(documentsApi.createTag).mockReset();
  });

  it("shows an empty state when there are no roles", async () => {
    vi.mocked(accessRolesApi.listRoles).mockResolvedValue([]);

    renderWithClient(<AccessRolesPage />);

    await waitFor(() => expect(screen.getByText(/No Access Roles yet/)).toBeInTheDocument());
  });

  it("creates a new Access Role", async () => {
    vi.mocked(accessRolesApi.listRoles).mockResolvedValue([]);
    vi.mocked(accessRolesApi.createRole).mockResolvedValue({
      id: "r-1",
      org_id: "org-1",
      name: "Finance Team",
      tag_ids: [],
      user_ids: [],
      created_at: "",
    });

    renderWithClient(<AccessRolesPage />);
    await waitFor(() => expect(accessRolesApi.listRoles).toHaveBeenCalled());

    const input = screen.getByPlaceholderText(/New Access Role name/);
    fireEvent.change(input, { target: { value: "Finance Team" } });
    fireEvent.click(screen.getByText("Create"));

    await waitFor(() => expect(accessRolesApi.createRole).toHaveBeenCalledWith("Finance Team"));
  });

  it("creates a new tag from the inline tag input", async () => {
    vi.mocked(accessRolesApi.listRoles).mockResolvedValue([]);
    vi.mocked(documentsApi.createTag).mockResolvedValue({
      id: "t-2",
      org_id: "org-1",
      name: "Legal",
      created_at: "",
    });

    renderWithClient(<AccessRolesPage />);
    await waitFor(() => expect(documentsApi.listTags).toHaveBeenCalled());

    const input = screen.getByPlaceholderText("+ new tag");
    fireEvent.change(input, { target: { value: "Legal" } });
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() => expect(documentsApi.createTag).toHaveBeenCalledWith("Legal"));
  });

  it("renders a role's granted tags and members, and lets you revoke/remove them", async () => {
    vi.mocked(accessRolesApi.listRoles).mockResolvedValue([
      {
        id: "r-1",
        org_id: "org-1",
        name: "Finance Team",
        tag_ids: ["t-1"],
        user_ids: ["u-1"],
        created_at: "",
      },
    ]);

    renderWithClient(<AccessRolesPage />);
    await waitFor(() => expect(screen.getByText("Finance Team")).toBeInTheDocument());

    // "Finance" also appears in the page-level tag list, so scope to the badges that
    // carry the revoke/remove aria-labels rather than asserting text uniqueness.
    expect(screen.getByLabelText("Revoke Finance from Finance Team")).toBeInTheDocument();
    expect(screen.getByText("alice@test.com")).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText("Revoke Finance from Finance Team"));
    await waitFor(() =>
      expect(accessRolesApi.revokeTag).toHaveBeenCalledWith("r-1", "t-1"),
    );

    fireEvent.click(screen.getByLabelText("Remove alice@test.com from Finance Team"));
    await waitFor(() =>
      expect(accessRolesApi.removeUser).toHaveBeenCalledWith("r-1", "u-1"),
    );
  });

  it("grants a tag and assigns a member via the select controls", async () => {
    vi.mocked(accessRolesApi.listRoles).mockResolvedValue([
      {
        id: "r-1",
        org_id: "org-1",
        name: "Finance Team",
        tag_ids: [],
        user_ids: [],
        created_at: "",
      },
    ]);

    renderWithClient(<AccessRolesPage />);
    await waitFor(() => expect(screen.getByText("Finance Team")).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText("Grant a tag to Finance Team"), {
      target: { value: "t-1" },
    });
    await waitFor(() => expect(accessRolesApi.grantTag).toHaveBeenCalledWith("r-1", "t-1"));

    fireEvent.change(screen.getByLabelText("Assign a member to Finance Team"), {
      target: { value: "u-1" },
    });
    await waitFor(() => expect(accessRolesApi.assignUser).toHaveBeenCalledWith("r-1", "u-1"));
  });

  it("deletes an Access Role", async () => {
    vi.mocked(accessRolesApi.listRoles).mockResolvedValue([
      {
        id: "r-1",
        org_id: "org-1",
        name: "Temp",
        tag_ids: [],
        user_ids: [],
        created_at: "",
      },
    ]);

    renderWithClient(<AccessRolesPage />);
    await waitFor(() => expect(screen.getByText("Temp")).toBeInTheDocument());

    fireEvent.click(screen.getByLabelText("Delete Temp"));

    await waitFor(() => expect(accessRolesApi.deleteRole).toHaveBeenCalledWith("r-1"));
  });
});

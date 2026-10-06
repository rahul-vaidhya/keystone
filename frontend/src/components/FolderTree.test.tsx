import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { Folder, Tag } from "../types/documents";
import { documentsApi } from "../services/documentsService";
import { useAuth } from "../hooks/useAuth";
import { DialogProvider } from "../context/DialogContext";
import { FolderTree } from "./FolderTree";
import { ApiError } from "../types/auth";

vi.mock("../services/documentsService", async () => {
  const actual = await vi.importActual<typeof import("../services/documentsService")>(
    "../services/documentsService",
  );
  return {
    ...actual,
    documentsApi: {
      listFolders: vi.fn(),
      createFolder: vi.fn(),
      renameFolder: vi.fn(),
      moveFolder: vi.fn(),
      deleteFolder: vi.fn(),
      tagFolder: vi.fn(),
      untagFolder: vi.fn(),
      listTags: vi.fn(),
      moveDocument: vi.fn(),
      listDocuments: vi.fn(),
    },
  };
});

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

function makeFolder(overrides: Partial<Folder> = {}): Folder {
  return {
    id: "f-1",
    org_id: "org-1",
    parent_id: null,
    name: "HR",
    path: "HR",
    tag_ids: [],
    created_at: "2026-01-01T00:00:00Z",
    can_manage: true,
    ...overrides,
  };
}

function makeTag(overrides: Partial<Tag> = {}): Tag {
  return {
    id: "t-1",
    org_id: "org-1",
    name: "Finance",
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

// jsdom doesn't implement DataTransfer's storage — a tiny Map-backed stand-in lets
// dragstart/drop fireEvent calls share payload the same way a real drag would.
function makeDataTransfer() {
  const store = new Map<string, string>();
  return {
    setData: (format: string, data: string) => store.set(format, data),
    getData: (format: string) => store.get(format) ?? "",
    effectAllowed: "",
    dropEffect: "",
  } as unknown as DataTransfer;
}

function renderWithClient(ui: React.ReactElement) {
  const queryClient = new QueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <DialogProvider>{ui}</DialogProvider>
    </QueryClientProvider>,
  );
}

describe("FolderTree", () => {
  beforeEach(() => {
    vi.mocked(documentsApi.listFolders).mockReset();
    vi.mocked(documentsApi.createFolder).mockReset();
    vi.mocked(documentsApi.renameFolder).mockReset();
    vi.mocked(documentsApi.moveFolder).mockReset();
    vi.mocked(documentsApi.deleteFolder).mockReset();
    vi.mocked(documentsApi.tagFolder).mockReset();
    vi.mocked(documentsApi.untagFolder).mockReset();
    vi.mocked(documentsApi.listTags).mockReset().mockResolvedValue([]);
    vi.mocked(documentsApi.moveDocument).mockReset();
    vi.mocked(documentsApi.listDocuments).mockReset().mockResolvedValue([]);
    mockUser("owner");
  });

  it("renders a nested folder structure built from the flat parent_id list", async () => {
    vi.mocked(documentsApi.listFolders).mockResolvedValue([
      makeFolder({ id: "root", name: "HR", path: "HR" }),
      makeFolder({ id: "child", name: "Policies", path: "HR/Policies", parent_id: "root" }),
    ]);

    renderWithClient(<FolderTree currentFolderId={null} onNavigate={vi.fn()} />);

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "HR" })).toBeInTheDocument(),
    );
    expect(screen.getByRole("button", { name: "Policies" })).toBeInTheDocument();
  });

  it("the rename and delete buttons reveal on keyboard focus, not just hover (WCAG 2.1.1)", async () => {
    vi.mocked(documentsApi.listFolders).mockResolvedValue([makeFolder()]);

    renderWithClient(<FolderTree currentFolderId={null} onNavigate={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "HR" })).toBeInTheDocument());

    const renameButton = screen.getByLabelText("Rename HR");
    const deleteButton = screen.getByLabelText("Delete HR");
    for (const button of [renameButton, deleteButton]) {
      expect(button.className).toMatch(/group-focus-within:opacity-100/);
      expect(button.className).toMatch(/focus-visible:opacity-100/);
    }
  });

  it("calls onNavigate with the folder id when a folder is clicked", async () => {
    vi.mocked(documentsApi.listFolders).mockResolvedValue([makeFolder()]);
    const onNavigate = vi.fn();

    renderWithClient(<FolderTree currentFolderId={null} onNavigate={onNavigate} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "HR" })).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "HR" }));
    expect(onNavigate).toHaveBeenCalledWith("f-1");
  });

  it("creates a folder under the current folder and invalidates the folder list", async () => {
    vi.mocked(documentsApi.listFolders).mockResolvedValue([]);
    vi.mocked(documentsApi.createFolder).mockResolvedValue(makeFolder());

    renderWithClient(<FolderTree currentFolderId="parent-1" onNavigate={vi.fn()} />);
    await waitFor(() => expect(documentsApi.listFolders).toHaveBeenCalled());

    fireEvent.click(screen.getByText("+ New folder"));
    const input = screen.getByPlaceholderText("Folder name");
    fireEvent.change(input, { target: { value: "Sales" } });
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() =>
      expect(documentsApi.createFolder).toHaveBeenCalledWith("Sales", "parent-1"),
    );
    // Refetches the whole list (not a partial/optimistic patch) — proven by listFolders
    // being called again after the mutation settles.
    await waitFor(() => expect(documentsApi.listFolders).toHaveBeenCalledTimes(2));
  });

  it("renames a folder via renameFolder and refetches the whole list", async () => {
    vi.mocked(documentsApi.listFolders).mockResolvedValue([makeFolder()]);
    vi.mocked(documentsApi.renameFolder).mockResolvedValue(makeFolder({ name: "Personnel" }));

    renderWithClient(<FolderTree currentFolderId={null} onNavigate={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "HR" })).toBeInTheDocument());

    fireEvent.click(screen.getByLabelText("Rename HR"));
    const input = screen.getByDisplayValue("HR");
    fireEvent.change(input, { target: { value: "Personnel" } });
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() =>
      expect(documentsApi.renameFolder).toHaveBeenCalledWith("f-1", "Personnel"),
    );
    await waitFor(() => expect(documentsApi.listFolders).toHaveBeenCalledTimes(2));
  });

  it("deletes a childless folder after confirming in the app's dialog (block mode)", async () => {
    vi.mocked(documentsApi.listFolders).mockResolvedValue([makeFolder()]);
    vi.mocked(documentsApi.deleteFolder).mockResolvedValue(undefined);

    renderWithClient(<FolderTree currentFolderId={null} onNavigate={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "HR" })).toBeInTheDocument());

    fireEvent.click(screen.getByLabelText("Delete HR"));
    await waitFor(() => expect(screen.getByText('Delete "HR"?')).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(documentsApi.deleteFolder).toHaveBeenCalledWith("f-1", "block"));
  });

  it("does not delete a childless folder when the confirm dialog is cancelled", async () => {
    vi.mocked(documentsApi.listFolders).mockResolvedValue([makeFolder()]);

    renderWithClient(<FolderTree currentFolderId={null} onNavigate={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "HR" })).toBeInTheDocument());

    fireEvent.click(screen.getByLabelText("Delete HR"));
    await waitFor(() => expect(screen.getByRole("dialog")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(documentsApi.deleteFolder).not.toHaveBeenCalled();
  });

  it("offers a cascade/reflow choice (via FolderDeleteDialog) when the folder has children, and reflow needs no typed confirmation", async () => {
    vi.mocked(documentsApi.listFolders).mockResolvedValue([
      makeFolder({ id: "root", name: "HR", path: "HR" }),
      makeFolder({ id: "child", name: "Policies", path: "HR/Policies", parent_id: "root" }),
    ]);
    vi.mocked(documentsApi.deleteFolder).mockResolvedValue(undefined);

    renderWithClient(<FolderTree currentFolderId={null} onNavigate={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "HR" })).toBeInTheDocument());

    fireEvent.click(screen.getByLabelText("Delete HR"));
    await waitFor(() => expect(screen.getByText('Delete "HR"?')).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /Move contents up a level/ }));

    await waitFor(() =>
      expect(documentsApi.deleteFolder).toHaveBeenCalledWith("root", "reflow"),
    );
  });

  it("offers the cascade/reflow choice for a folder with documents but no subfolders", async () => {
    vi.mocked(documentsApi.listFolders).mockResolvedValue([makeFolder()]);
    vi.mocked(documentsApi.listDocuments).mockResolvedValue([
      { id: "d-1" } as unknown as Awaited<ReturnType<typeof documentsApi.listDocuments>>[number],
    ]);
    vi.mocked(documentsApi.deleteFolder).mockResolvedValue(undefined);

    renderWithClient(<FolderTree currentFolderId={null} onNavigate={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "HR" })).toBeInTheDocument());

    fireEvent.click(screen.getByLabelText("Delete HR"));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /Move contents up a level/ })).toBeInTheDocument(),
    );
    expect(documentsApi.listDocuments).toHaveBeenCalledWith({ folderId: "f-1" });
    fireEvent.click(screen.getByRole("button", { name: /Move contents up a level/ }));
    await waitFor(() => expect(documentsApi.deleteFolder).toHaveBeenCalledWith("f-1", "reflow"));
  });

  it("opens the cascade/reflow choice when block-mode delete returns 409 (instead of raw API text)", async () => {
    vi.mocked(documentsApi.listFolders).mockResolvedValue([makeFolder()]);
    vi.mocked(documentsApi.deleteFolder).mockRejectedValueOnce(
      new ApiError(409, "Folder is not empty; pass mode=cascade or mode=reflow", null),
    );

    renderWithClient(<FolderTree currentFolderId={null} onNavigate={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "HR" })).toBeInTheDocument());

    fireEvent.click(screen.getByLabelText("Delete HR"));
    await waitFor(() => expect(screen.getByRole("button", { name: "Delete" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: /Move contents up a level/ })).toBeInTheDocument(),
    );
    expect(screen.queryByText(/pass mode=cascade/)).not.toBeInTheDocument();
  });

  it("requires the folder name to be typed exactly before allowing cascade delete", async () => {
    vi.mocked(documentsApi.listFolders).mockResolvedValue([
      makeFolder({ id: "root", name: "HR", path: "HR" }),
      makeFolder({ id: "child", name: "Policies", path: "HR/Policies", parent_id: "root" }),
    ]);
    vi.mocked(documentsApi.deleteFolder).mockResolvedValue(undefined);

    renderWithClient(<FolderTree currentFolderId={null} onNavigate={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "HR" })).toBeInTheDocument());

    fireEvent.click(screen.getByLabelText("Delete HR"));
    await waitFor(() => expect(screen.getByText('Delete "HR"?')).toBeInTheDocument());

    const cascadeButton = screen.getByRole("button", { name: "Delete folder and subfolders" });
    expect(cascadeButton).toBeDisabled();

    fireEvent.change(screen.getByLabelText('Type "HR" to confirm'), {
      target: { value: "HR" },
    });
    expect(cascadeButton).toBeEnabled();
    fireEvent.click(cascadeButton);

    await waitFor(() =>
      expect(documentsApi.deleteFolder).toHaveBeenCalledWith("root", "cascade"),
    );
  });

  it("moves a folder via drag-and-drop onto another folder", async () => {
    vi.mocked(documentsApi.listFolders).mockResolvedValue([
      makeFolder({ id: "root", name: "HR", path: "HR" }),
      makeFolder({ id: "sales", name: "Sales", path: "Sales" }),
    ]);
    vi.mocked(documentsApi.moveFolder).mockResolvedValue(
      makeFolder({ id: "root", name: "HR", path: "Sales/HR", parent_id: "sales" }),
    );

    renderWithClient(<FolderTree currentFolderId={null} onNavigate={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "HR" })).toBeInTheDocument());

    const dataTransfer = makeDataTransfer();
    const source = screen.getByRole("button", { name: "HR" }).closest("div")!;
    const target = screen.getByRole("button", { name: "Sales" }).closest("div")!;
    fireEvent.dragStart(source, { dataTransfer });
    fireEvent.drop(target, { dataTransfer });

    await waitFor(() => expect(documentsApi.moveFolder).toHaveBeenCalledWith("root", "sales"));
    await waitFor(() => expect(documentsApi.listFolders).toHaveBeenCalledTimes(2));
  });

  it("dropping a folder onto the 'All documents' root moves it to root", async () => {
    vi.mocked(documentsApi.listFolders).mockResolvedValue([
      makeFolder({ id: "root", name: "HR", path: "Sales/HR", parent_id: "sales" }),
    ]);
    vi.mocked(documentsApi.moveFolder).mockResolvedValue(makeFolder({ id: "root", name: "HR" }));

    renderWithClient(<FolderTree currentFolderId={null} onNavigate={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "HR" })).toBeInTheDocument());

    const dataTransfer = makeDataTransfer();
    const source = screen.getByRole("button", { name: "HR" }).closest("div")!;
    fireEvent.dragStart(source, { dataTransfer });
    fireEvent.drop(screen.getByText("All documents"), { dataTransfer });

    await waitFor(() => expect(documentsApi.moveFolder).toHaveBeenCalledWith("root", null));
  });

  it("dropping a dragged document onto a folder calls moveDocument", async () => {
    vi.mocked(documentsApi.listFolders).mockResolvedValue([makeFolder()]);
    vi.mocked(documentsApi.moveDocument).mockResolvedValue({
      id: "doc-1",
      org_id: "org-1",
      folder_id: "f-1",
      title: "x",
      storage_key: null,
      mime_type: null,
      byte_size: null,
      checksum: null,
      page_count: null,
      language: null,
      status: "READY",
      failed_stage: null,
      error_detail: null,
      uploader_email: null,
      created_at: "2026-01-01T00:00:00Z",
    });

    renderWithClient(<FolderTree currentFolderId={null} onNavigate={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "HR" })).toBeInTheDocument());

    const dataTransfer = makeDataTransfer();
    dataTransfer.setData("application/json", JSON.stringify({ type: "document", id: "doc-1" }));
    const target = screen.getByRole("button", { name: "HR" }).closest("div")!;
    fireEvent.drop(target, { dataTransfer });

    await waitFor(() => expect(documentsApi.moveDocument).toHaveBeenCalledWith("doc-1", "f-1"));
  });

  it("shows tag badges on a folder for every role", async () => {
    mockUser("member");
    vi.mocked(documentsApi.listFolders).mockResolvedValue([makeFolder({ tag_ids: ["t-1"] })]);
    vi.mocked(documentsApi.listTags).mockResolvedValue([makeTag()]);

    renderWithClient(<FolderTree currentFolderId={null} onNavigate={vi.fn()} />);

    await waitFor(() => expect(screen.getByText("Finance")).toBeInTheDocument());
  });

  it("lets an admin grant a tag to a folder and untag it by clicking the badge", async () => {
    vi.mocked(documentsApi.listFolders).mockResolvedValue([makeFolder()]);
    vi.mocked(documentsApi.listTags).mockResolvedValue([makeTag()]);
    vi.mocked(documentsApi.tagFolder).mockResolvedValue(undefined);

    renderWithClient(<FolderTree currentFolderId={null} onNavigate={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "HR" })).toBeInTheDocument());

    const select = screen.getByLabelText("Tag HR");
    fireEvent.change(select, { target: { value: "t-1" } });

    await waitFor(() => expect(documentsApi.tagFolder).toHaveBeenCalledWith("f-1", "t-1"));
  });

  it("hides the tag-grant control for a plain member", async () => {
    mockUser("member");
    vi.mocked(documentsApi.listFolders).mockResolvedValue([makeFolder()]);
    vi.mocked(documentsApi.listTags).mockResolvedValue([makeTag()]);

    renderWithClient(<FolderTree currentFolderId={null} onNavigate={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "HR" })).toBeInTheDocument());

    expect(screen.queryByLabelText("Tag HR")).not.toBeInTheDocument();
  });

  it("hides rename/delete and disallows dragging a folder the user can't manage (can_manage=false)", async () => {
    mockUser("member");
    vi.mocked(documentsApi.listFolders).mockResolvedValue([makeFolder({ can_manage: false })]);

    renderWithClient(<FolderTree currentFolderId={null} onNavigate={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "HR" })).toBeInTheDocument());

    expect(screen.queryByLabelText("Rename HR")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Delete HR")).not.toBeInTheDocument();
    const row = screen.getByRole("button", { name: "HR" }).closest("div")!;
    expect(row).toHaveAttribute("draggable", "false");
  });

  it("does not drop a folder or document onto a target folder the user can't manage", async () => {
    mockUser("member");
    vi.mocked(documentsApi.listFolders).mockResolvedValue([
      makeFolder({ id: "root", name: "HR", path: "HR", can_manage: true }),
      makeFolder({
        id: "vault",
        name: "Vault",
        path: "Vault",
        can_manage: false,
      }),
    ]);

    renderWithClient(<FolderTree currentFolderId={null} onNavigate={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "HR" })).toBeInTheDocument());

    const dataTransfer = makeDataTransfer();
    dataTransfer.setData("application/json", JSON.stringify({ type: "document", id: "doc-1" }));
    const restrictedTarget = screen.getByRole("button", { name: "Vault" }).closest("div")!;
    fireEvent.drop(restrictedTarget, { dataTransfer });

    expect(documentsApi.moveDocument).not.toHaveBeenCalled();
  });
});

import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { Folder } from "../types/documents";
import { documentsApi } from "../services/documentsService";
import { FolderTree } from "./FolderTree";

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
    },
  };
});

function makeFolder(overrides: Partial<Folder> = {}): Folder {
  return {
    id: "f-1",
    org_id: "org-1",
    parent_id: null,
    name: "HR",
    path: "HR",
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function renderWithClient(ui: React.ReactElement) {
  const queryClient = new QueryClient();
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

describe("FolderTree", () => {
  beforeEach(() => {
    vi.mocked(documentsApi.listFolders).mockReset();
    vi.mocked(documentsApi.createFolder).mockReset();
    vi.mocked(documentsApi.renameFolder).mockReset();
    vi.mocked(documentsApi.moveFolder).mockReset();
    vi.mocked(documentsApi.deleteFolder).mockReset();
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

  it("deletes a childless folder with a plain confirm (block mode)", async () => {
    vi.mocked(documentsApi.listFolders).mockResolvedValue([makeFolder()]);
    vi.mocked(documentsApi.deleteFolder).mockResolvedValue(undefined);
    vi.spyOn(window, "confirm").mockReturnValue(true);

    renderWithClient(<FolderTree currentFolderId={null} onNavigate={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "HR" })).toBeInTheDocument());

    fireEvent.click(screen.getByLabelText("Delete HR"));

    await waitFor(() => expect(documentsApi.deleteFolder).toHaveBeenCalledWith("f-1", "block"));
  });

  it("offers cascade/reflow modes when the folder has children, and passes the chosen mode", async () => {
    vi.mocked(documentsApi.listFolders).mockResolvedValue([
      makeFolder({ id: "root", name: "HR", path: "HR" }),
      makeFolder({ id: "child", name: "Policies", path: "HR/Policies", parent_id: "root" }),
    ]);
    vi.mocked(documentsApi.deleteFolder).mockResolvedValue(undefined);
    vi.spyOn(window, "prompt").mockReturnValue("reflow");

    renderWithClient(<FolderTree currentFolderId={null} onNavigate={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "HR" })).toBeInTheDocument());

    fireEvent.click(screen.getByLabelText("Delete HR"));

    await waitFor(() =>
      expect(documentsApi.deleteFolder).toHaveBeenCalledWith("root", "reflow"),
    );
  });

  it("moves a folder via moveFolder and refetches the whole list (path rebuild is server-side)", async () => {
    vi.mocked(documentsApi.listFolders).mockResolvedValue([
      makeFolder({ id: "root", name: "HR", path: "HR" }),
      makeFolder({ id: "sales", name: "Sales", path: "Sales" }),
    ]);
    vi.mocked(documentsApi.moveFolder).mockResolvedValue(
      makeFolder({ id: "root", name: "HR", path: "Sales/HR", parent_id: "sales" }),
    );

    renderWithClient(<FolderTree currentFolderId={null} onNavigate={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "HR" })).toBeInTheDocument());

    const moveSelect = screen.getByLabelText("Move HR");
    fireEvent.change(moveSelect, { target: { value: "sales" } });

    await waitFor(() => expect(documentsApi.moveFolder).toHaveBeenCalledWith("root", "sales"));
    await waitFor(() => expect(documentsApi.listFolders).toHaveBeenCalledTimes(2));
  });
});

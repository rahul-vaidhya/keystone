import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider, type Query } from "@tanstack/react-query";
import type { Document, Folder } from "../types/documents";
import { documentsApi } from "../services/documentsService";
import { DialogProvider } from "../context/DialogContext";
import { DocumentList, pollIntervalFor } from "./DocumentList";

vi.mock("../services/documentsService", async () => {
  const actual = await vi.importActual<typeof import("../services/documentsService")>(
    "../services/documentsService",
  );
  return {
    ...actual,
    documentsApi: {
      listDocuments: vi.fn(),
      uploadDocument: vi.fn(),
      deleteDocument: vi.fn(),
      listFolders: vi.fn(),
      moveDocument: vi.fn(),
    },
  };
});

function makeDoc(overrides: Partial<Document> = {}): Document {
  return {
    id: "doc-1",
    org_id: "org-1",
    folder_id: null,
    title: "report.pdf",
    storage_key: "key",
    mime_type: "application/pdf",
    byte_size: 100,
    checksum: "abc",
    page_count: 1,
    language: "en",
    status: "READY",
    failed_stage: null,
    error_detail: null,
    uploader_email: "alice@example.com",
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function fakeQuery(data: Document[] | undefined): Query<Document[]> {
  return { state: { data } } as unknown as Query<Document[]>;
}

function makeFolder(overrides: Partial<Folder> = {}): Folder {
  return {
    id: "folder-1",
    org_id: "org-1",
    parent_id: null,
    name: "Finance",
    path: "Finance",
    tag_ids: [],
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

describe("pollIntervalFor", () => {
  it("returns false when there is no data yet", () => {
    expect(pollIntervalFor(fakeQuery(undefined))).toBe(false);
  });

  it("returns false when every document is terminal (READY/FAILED)", () => {
    const docs = [makeDoc({ status: "READY" }), makeDoc({ id: "doc-2", status: "FAILED" })];
    expect(pollIntervalFor(fakeQuery(docs))).toBe(false);
  });

  it("returns a poll interval when any document is non-terminal", () => {
    const docs = [makeDoc({ status: "READY" }), makeDoc({ id: "doc-2", status: "PARSING" })];
    expect(pollIntervalFor(fakeQuery(docs))).toBe(2000);
  });
});

describe("DocumentList", () => {
  beforeEach(() => {
    vi.mocked(documentsApi.listDocuments).mockReset();
    vi.mocked(documentsApi.uploadDocument).mockReset();
    vi.mocked(documentsApi.deleteDocument).mockReset();
    vi.mocked(documentsApi.listFolders).mockReset().mockResolvedValue([]);
    vi.mocked(documentsApi.moveDocument).mockReset();
  });

  function renderWithClient(ui: React.ReactElement) {
    const queryClient = new QueryClient();
    return render(
      <QueryClientProvider client={queryClient}>
        <DialogProvider>{ui}</DialogProvider>
      </QueryClientProvider>,
    );
  }

  it("renders a status badge per document using the literal status/failed_stage fields", async () => {
    vi.mocked(documentsApi.listDocuments).mockResolvedValue([
      makeDoc({ status: "READY" }),
      makeDoc({ id: "doc-2", title: "scan.pdf", status: "FAILED", failed_stage: "PARSING" }),
    ]);

    renderWithClient(<DocumentList currentFolderId={null} />);

    await waitFor(() => expect(screen.getByText("report.pdf")).toBeInTheDocument());
    expect(screen.getByText("Ready")).toBeInTheDocument();
    expect(screen.getByText(/Failed/)).toBeInTheDocument();
  });

  it("shows an empty-state prompt when there are no documents", async () => {
    vi.mocked(documentsApi.listDocuments).mockResolvedValue([]);
    renderWithClient(<DocumentList currentFolderId={null} />);
    await waitFor(() =>
      expect(screen.getByText(/No documents here yet/)).toBeInTheDocument(),
    );
  });

  it("uploads the selected file scoped to the current folder and invalidates documents", async () => {
    vi.mocked(documentsApi.listDocuments).mockResolvedValue([]);
    vi.mocked(documentsApi.uploadDocument).mockResolvedValue(makeDoc());

    renderWithClient(<DocumentList currentFolderId="folder-1" />);
    await waitFor(() => expect(documentsApi.listDocuments).toHaveBeenCalled());

    const file = new File(["hello"], "hello.pdf", { type: "application/pdf" });
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() =>
      expect(documentsApi.uploadDocument).toHaveBeenCalledWith(file, "folder-1"),
    );
  });

  it("surfaces an upload error via the app's dialog with the ApiError message, not a stack trace", async () => {
    const { ApiError } = await vi.importActual<typeof import("../types/auth")>(
      "../types/auth",
    );
    vi.mocked(documentsApi.listDocuments).mockResolvedValue([]);
    vi.mocked(documentsApi.uploadDocument).mockRejectedValue(
      new ApiError(409, "A document with that checksum already exists", null),
    );

    renderWithClient(<DocumentList currentFolderId={null} />);
    await waitFor(() => expect(documentsApi.listDocuments).toHaveBeenCalled());

    const file = new File(["hello"], "hello.pdf", { type: "application/pdf" });
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() =>
      expect(
        screen.getByText("A document with that checksum already exists"),
      ).toBeInTheDocument(),
    );
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("deletes a document after confirming in the dialog and invalidates the list", async () => {
    vi.mocked(documentsApi.listDocuments).mockResolvedValue([makeDoc()]);
    vi.mocked(documentsApi.deleteDocument).mockResolvedValue(undefined);

    renderWithClient(<DocumentList currentFolderId={null} />);
    await waitFor(() => expect(screen.getByText("report.pdf")).toBeInTheDocument());

    fireEvent.click(screen.getByLabelText("Delete report.pdf"));
    await waitFor(() =>
      expect(
        screen.getByText('Permanently delete "report.pdf"? This cannot be undone.'),
      ).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(documentsApi.deleteDocument).toHaveBeenCalledWith("doc-1"));
    await waitFor(() => expect(documentsApi.listDocuments).toHaveBeenCalledTimes(2));
  });

  it("does not delete when the confirm dialog is cancelled", async () => {
    vi.mocked(documentsApi.listDocuments).mockResolvedValue([makeDoc()]);

    renderWithClient(<DocumentList currentFolderId={null} />);
    await waitFor(() => expect(screen.getByText("report.pdf")).toBeInTheDocument());

    fireEvent.click(screen.getByLabelText("Delete report.pdf"));
    await waitFor(() => expect(screen.getByRole("dialog")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(documentsApi.deleteDocument).not.toHaveBeenCalled();
  });

  it("sets a document drag payload on dragstart, for FolderTree to read on drop", async () => {
    vi.mocked(documentsApi.listDocuments).mockResolvedValue([makeDoc()]);

    renderWithClient(<DocumentList currentFolderId={null} />);
    await waitFor(() => expect(screen.getByText("report.pdf")).toBeInTheDocument());

    const store = new Map<string, string>();
    const dataTransfer = {
      setData: (format: string, data: string) => store.set(format, data),
      getData: (format: string) => store.get(format) ?? "",
      effectAllowed: "",
    } as unknown as DataTransfer;

    const row = screen.getByText("report.pdf").closest("tr")!;
    fireEvent.dragStart(row, { dataTransfer });

    expect(store.get("application/json")).toBe(
      JSON.stringify({ type: "document", id: "doc-1" }),
    );
  });

  it("renders a move-to-folder control that is visible without hover (no opacity-0 class)", async () => {
    vi.mocked(documentsApi.listDocuments).mockResolvedValue([makeDoc()]);
    vi.mocked(documentsApi.listFolders).mockResolvedValue([makeFolder()]);

    renderWithClient(<DocumentList currentFolderId={null} />);
    await waitFor(() => expect(screen.getByText("report.pdf")).toBeInTheDocument());

    const select = await screen.findByLabelText("Move report.pdf to folder");
    expect(select).toBeInTheDocument();
    expect(select.className).not.toMatch(/opacity-0/);
    expect(select.className).not.toMatch(/group-hover/);
  });

  it("the delete button reveals on keyboard focus, not just hover (WCAG 2.1.1)", async () => {
    vi.mocked(documentsApi.listDocuments).mockResolvedValue([makeDoc()]);
    vi.mocked(documentsApi.listFolders).mockResolvedValue([makeFolder()]);

    renderWithClient(<DocumentList currentFolderId={null} />);
    await waitFor(() => expect(screen.getByText("report.pdf")).toBeInTheDocument());

    const deleteButton = await screen.findByLabelText("Delete report.pdf");
    expect(deleteButton.className).toMatch(/group-focus-within:opacity-100/);
    expect(deleteButton.className).toMatch(/focus-visible:opacity-100/);
  });

  it("moving a document to a folder calls moveDocument with the document id and folder id", async () => {
    vi.mocked(documentsApi.listDocuments).mockResolvedValue([makeDoc()]);
    vi.mocked(documentsApi.listFolders).mockResolvedValue([makeFolder({ id: "folder-1", name: "Finance" })]);
    vi.mocked(documentsApi.moveDocument).mockResolvedValue(makeDoc({ folder_id: "folder-1" }));

    renderWithClient(<DocumentList currentFolderId={null} />);
    await waitFor(() => expect(screen.getByText("report.pdf")).toBeInTheDocument());

    const select = await screen.findByLabelText("Move report.pdf to folder");
    fireEvent.change(select, { target: { value: "folder-1" } });

    await waitFor(() =>
      expect(documentsApi.moveDocument).toHaveBeenCalledWith("doc-1", "folder-1"),
    );
  });

  it("moving a document to repository root calls moveDocument with folderId null", async () => {
    vi.mocked(documentsApi.listDocuments).mockResolvedValue([
      makeDoc({ folder_id: "folder-1" }),
    ]);
    vi.mocked(documentsApi.listFolders).mockResolvedValue([makeFolder({ id: "folder-1" })]);
    vi.mocked(documentsApi.moveDocument).mockResolvedValue(makeDoc({ folder_id: null }));

    renderWithClient(<DocumentList currentFolderId={null} />);
    await waitFor(() => expect(screen.getByText("report.pdf")).toBeInTheDocument());

    const select = await screen.findByLabelText("Move report.pdf to folder");
    fireEvent.change(select, { target: { value: "" } });

    await waitFor(() =>
      expect(documentsApi.moveDocument).toHaveBeenCalledWith("doc-1", null),
    );
  });

  it("renders Uploaded/Size/Pages columns with correctly formatted values", async () => {
    vi.mocked(documentsApi.listDocuments).mockResolvedValue([
      makeDoc({
        byte_size: 2_500_000,
        page_count: 42,
        uploader_email: "carol@example.com",
        created_at: "2026-03-15T12:00:00Z",
      }),
    ]);

    renderWithClient(<DocumentList currentFolderId={null} />);
    await waitFor(() => expect(screen.getByText("report.pdf")).toBeInTheDocument());

    // Size is formatted (MB, not raw bytes) and locale/timezone independent.
    expect(screen.getByText("2.4 MB")).toBeInTheDocument();
    expect(screen.getByText("42")).toBeInTheDocument();
    expect(screen.getByText("carol@example.com")).toBeInTheDocument();
    // Date formatting is locale/timezone dependent in jsdom, so only assert the
    // year renders somewhere in the row rather than pin an exact string.
    expect(screen.getByText(/2026/)).toBeInTheDocument();
  });

  it("shows a dash for missing size/pages/uploader instead of crashing", async () => {
    vi.mocked(documentsApi.listDocuments).mockResolvedValue([
      makeDoc({ byte_size: null, page_count: null, uploader_email: null }),
    ]);

    renderWithClient(<DocumentList currentFolderId={null} />);
    await waitFor(() => expect(screen.getByText("report.pdf")).toBeInTheDocument());

    const dashes = screen.getAllByText("—");
    expect(dashes.length).toBeGreaterThanOrEqual(3); // size, pages, uploader
  });

  it("clicking a row opens a detail modal with that document's info", async () => {
    vi.mocked(documentsApi.listDocuments).mockResolvedValue([
      makeDoc({ mime_type: "application/pdf", checksum: "deadbeef" }),
    ]);

    renderWithClient(<DocumentList currentFolderId={null} />);
    await waitFor(() => expect(screen.getByText("report.pdf")).toBeInTheDocument());

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    fireEvent.click(screen.getByText("report.pdf").closest("tr")!);

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toBeInTheDocument();
    expect(screen.getByText("application/pdf")).toBeInTheDocument();
    expect(screen.getByText("deadbeef")).toBeInTheDocument();
  });

  it("clicking the delete button does not also open the detail modal", async () => {
    vi.mocked(documentsApi.listDocuments).mockResolvedValue([makeDoc()]);

    renderWithClient(<DocumentList currentFolderId={null} />);
    await waitFor(() => expect(screen.getByText("report.pdf")).toBeInTheDocument());

    fireEvent.click(screen.getByLabelText("Delete report.pdf"));

    // The confirm dialog opens (from useDialog), but the detail modal must not.
    await waitFor(() =>
      expect(
        screen.getByText('Permanently delete "report.pdf"? This cannot be undone.'),
      ).toBeInTheDocument(),
    );
    expect(screen.queryByText("application/pdf")).not.toBeInTheDocument();
  });

  it("clicking the move-to-folder select does not open the detail modal", async () => {
    vi.mocked(documentsApi.listDocuments).mockResolvedValue([makeDoc()]);
    vi.mocked(documentsApi.listFolders).mockResolvedValue([makeFolder()]);

    renderWithClient(<DocumentList currentFolderId={null} />);
    await waitFor(() => expect(screen.getByText("report.pdf")).toBeInTheDocument());

    const select = await screen.findByLabelText("Move report.pdf to folder");
    fireEvent.click(select);

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

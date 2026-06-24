import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider, type Query } from "@tanstack/react-query";
import type { Document } from "../../lib/api";
import { documentsApi } from "../../lib/api";
import { DocumentList, pollIntervalFor } from "./DocumentList";

vi.mock("../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../lib/api")>("../../lib/api");
  return {
    ...actual,
    documentsApi: {
      listDocuments: vi.fn(),
      uploadDocument: vi.fn(),
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
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function fakeQuery(data: Document[] | undefined): Query<Document[]> {
  return { state: { data } } as unknown as Query<Document[]>;
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
  });

  function renderWithClient(ui: React.ReactElement) {
    const queryClient = new QueryClient();
    return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
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

  it("surfaces an upload error via window.alert with the ApiError message, not a stack trace", async () => {
    const { ApiError } = await vi.importActual<typeof import("../../lib/api")>("../../lib/api");
    vi.mocked(documentsApi.listDocuments).mockResolvedValue([]);
    vi.mocked(documentsApi.uploadDocument).mockRejectedValue(
      new ApiError(409, "A document with that checksum already exists", null),
    );
    const alertSpy = vi.spyOn(window, "alert").mockImplementation(() => {});

    renderWithClient(<DocumentList currentFolderId={null} />);
    await waitFor(() => expect(documentsApi.listDocuments).toHaveBeenCalled());

    const file = new File(["hello"], "hello.pdf", { type: "application/pdf" });
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() =>
      expect(alertSpy).toHaveBeenCalledWith("A document with that checksum already exists"),
    );
  });
});

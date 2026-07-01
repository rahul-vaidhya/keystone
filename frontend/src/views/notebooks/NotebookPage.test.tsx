import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { Document } from "../../models/documents";
import type { Notebook } from "../../models/knowledge";
import { notebooksApi } from "../../controllers/notebooksController";
import { documentsApi } from "../../controllers/documentsController";
import { NotebookPage } from "./NotebookPage";

vi.mock("../../controllers/notebooksController", () => ({
  notebooksApi: {
    get: vi.fn(),
    listDocuments: vi.fn(),
    attachDocument: vi.fn(),
    detachDocument: vi.fn(),
  },
}));

vi.mock("../../controllers/documentsController", () => ({
  documentsApi: {
    listDocuments: vi.fn(),
  },
}));

vi.mock("../../controllers/chatController", () => ({
  chatApi: {
    streamAsk: vi.fn(() => () => {}),
  },
}));

function makeNotebook(overrides: Partial<Notebook> = {}): Notebook {
  return {
    id: "nb-1",
    org_id: "org-1",
    name: "Chemistry",
    description: null,
    created_by: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

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

function renderPage(notebookId = "nb-1") {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[`/notebooks/${notebookId}`]}>
        <Routes>
          <Route path="/notebooks/:notebookId" element={<NotebookPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("NotebookPage", () => {
  beforeEach(() => {
    vi.mocked(notebooksApi.get).mockReset();
    vi.mocked(notebooksApi.listDocuments).mockReset();
    vi.mocked(notebooksApi.attachDocument).mockReset();
    vi.mocked(notebooksApi.detachDocument).mockReset();
    vi.mocked(documentsApi.listDocuments).mockReset();
  });

  it("renders the notebook name in the header", async () => {
    vi.mocked(notebooksApi.get).mockResolvedValue(makeNotebook({ name: "Chemistry" }));
    vi.mocked(notebooksApi.listDocuments).mockResolvedValue([]);
    vi.mocked(documentsApi.listDocuments).mockResolvedValue([]);

    renderPage();

    await waitFor(() => expect(screen.getByText("Chemistry")).toBeInTheDocument());
  });

  it("lists documents currently in the notebook with a status badge", async () => {
    vi.mocked(notebooksApi.get).mockResolvedValue(makeNotebook());
    vi.mocked(notebooksApi.listDocuments).mockResolvedValue([
      makeDoc({ id: "doc-1", title: "report.pdf", status: "READY" }),
    ]);
    vi.mocked(documentsApi.listDocuments).mockResolvedValue([
      makeDoc({ id: "doc-1", title: "report.pdf", status: "READY" }),
    ]);

    renderPage();

    await waitFor(() => expect(screen.getByText("report.pdf")).toBeInTheDocument());
    expect(screen.getByText("Ready")).toBeInTheDocument();
  });

  it("shows an 'add from repository' section for docs not yet in the notebook", async () => {
    vi.mocked(notebooksApi.get).mockResolvedValue(makeNotebook());
    vi.mocked(notebooksApi.listDocuments).mockResolvedValue([]);
    vi.mocked(documentsApi.listDocuments).mockResolvedValue([
      makeDoc({ id: "doc-2", title: "other.pdf", status: "READY" }),
    ]);

    renderPage();

    await waitFor(() => expect(screen.getByText("other.pdf")).toBeInTheDocument());
    expect(screen.getByLabelText("Add other.pdf to notebook")).toBeInTheDocument();
  });

  it("detaches a document via notebooksApi.detachDocument and invalidates", async () => {
    vi.mocked(notebooksApi.get).mockResolvedValue(makeNotebook());
    vi.mocked(notebooksApi.listDocuments).mockResolvedValue([
      makeDoc({ id: "doc-1", title: "report.pdf" }),
    ]);
    vi.mocked(documentsApi.listDocuments).mockResolvedValue([
      makeDoc({ id: "doc-1", title: "report.pdf" }),
    ]);
    vi.mocked(notebooksApi.detachDocument).mockResolvedValue(undefined);

    renderPage();

    await waitFor(() => expect(screen.getByText("report.pdf")).toBeInTheDocument());
    fireEvent.click(screen.getByLabelText("Remove report.pdf from notebook"));

    await waitFor(() =>
      expect(notebooksApi.detachDocument).toHaveBeenCalledWith("nb-1", "doc-1"),
    );
  });

  it("attaches a document via notebooksApi.attachDocument and invalidates", async () => {
    vi.mocked(notebooksApi.get).mockResolvedValue(makeNotebook());
    vi.mocked(notebooksApi.listDocuments).mockResolvedValue([]);
    vi.mocked(documentsApi.listDocuments).mockResolvedValue([
      makeDoc({ id: "doc-2", title: "other.pdf", status: "READY" }),
    ]);
    vi.mocked(notebooksApi.attachDocument).mockResolvedValue(undefined);

    renderPage();

    await waitFor(() => expect(screen.getByText("other.pdf")).toBeInTheDocument());
    fireEvent.click(screen.getByLabelText("Add other.pdf to notebook"));

    await waitFor(() =>
      expect(notebooksApi.attachDocument).toHaveBeenCalledWith("nb-1", "doc-2"),
    );
  });

  it("shows the empty-state message when notebook has no documents", async () => {
    vi.mocked(notebooksApi.get).mockResolvedValue(makeNotebook());
    vi.mocked(notebooksApi.listDocuments).mockResolvedValue([]);
    vi.mocked(documentsApi.listDocuments).mockResolvedValue([]);

    renderPage();

    await waitFor(() =>
      expect(screen.getByText(/No documents yet/)).toBeInTheDocument(),
    );
  });
});

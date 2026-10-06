import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { Document } from "../types/documents";
import type { Notebook } from "../types/knowledge";
import { ApiError } from "../types/auth";
import { notebooksApi } from "../services/notebooksService";
import { documentsApi } from "../services/documentsService";
import { useAuth } from "../hooks/useAuth";
import { DialogProvider } from "../context/DialogContext";
import { NotebookPage } from "./NotebookPage";

vi.mock("../hooks/useAuth", () => ({ useAuth: vi.fn() }));

vi.mock("../services/notebooksService", () => ({
  notebooksApi: {
    get: vi.fn(),
    update: vi.fn(),
    listDocuments: vi.fn(),
    attachDocument: vi.fn(),
    detachDocument: vi.fn(),
    listShares: vi.fn(() => Promise.resolve([])),
    share: vi.fn(),
    unshare: vi.fn(),
    getOverview: vi.fn(() => Promise.reject(new ApiError(404, "not found", null))),
    generateOverview: vi.fn(),
  },
}));

vi.mock("../services/documentsService", () => ({
  documentsApi: {
    listDocuments: vi.fn(),
  },
}));

vi.mock("../services/chatService", () => ({
  chatApi: {
    streamAsk: vi.fn(() => () => {}),
    listMessages: vi.fn(() => Promise.resolve([])),
  },
}));

// created_by defaults to the mocked user's own id ("u-1") so existing owner-flow tests
// (attach/detach visible, etc.) keep their prior behavior unchanged.
function makeNotebook(overrides: Partial<Notebook> = {}): Notebook {
  return {
    id: "nb-1",
    org_id: "org-1",
    name: "Chemistry",
    description: null,
    created_by: "u-1",
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
    uploader_email: null,
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function renderPage(notebookId = "nb-1") {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <DialogProvider>
        <MemoryRouter initialEntries={[`/notebooks/${notebookId}`]}>
          <Routes>
            <Route path="/notebooks/:notebookId" element={<NotebookPage />} />
          </Routes>
        </MemoryRouter>
      </DialogProvider>
    </QueryClientProvider>,
  );
}

describe("NotebookPage", () => {
  beforeEach(() => {
    vi.mocked(notebooksApi.get).mockReset();
    vi.mocked(notebooksApi.listDocuments).mockReset();
    vi.mocked(notebooksApi.attachDocument).mockReset();
    vi.mocked(notebooksApi.detachDocument).mockReset();
    vi.mocked(notebooksApi.listShares).mockReset().mockResolvedValue([]);
    vi.mocked(notebooksApi.share).mockReset();
    vi.mocked(notebooksApi.unshare).mockReset();
    vi.mocked(notebooksApi.getOverview)
      .mockReset()
      .mockRejectedValue(new ApiError(404, "not found", null));
    vi.mocked(notebooksApi.generateOverview).mockReset();
    vi.mocked(documentsApi.listDocuments).mockReset();
    vi.mocked(useAuth).mockReturnValue({
      user: {
        id: "u-1",
        org_id: "org-1",
        email: "u@test.com",
        name: null,
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

  it("the detach and add buttons reveal on keyboard focus, not just hover (WCAG 2.1.1)", async () => {
    vi.mocked(notebooksApi.get).mockResolvedValue(makeNotebook());
    vi.mocked(notebooksApi.listDocuments).mockResolvedValue([
      makeDoc({ id: "doc-1", title: "report.pdf" }),
    ]);
    vi.mocked(documentsApi.listDocuments).mockResolvedValue([
      makeDoc({ id: "doc-1", title: "report.pdf" }),
      makeDoc({ id: "doc-2", title: "other.pdf", status: "READY" }),
    ]);

    renderPage();

    await waitFor(() => expect(screen.getByText("report.pdf")).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText("other.pdf")).toBeInTheDocument());

    const detachButton = screen.getByLabelText("Remove report.pdf from notebook");
    const addButton = screen.getByLabelText("Add other.pdf to notebook");
    for (const button of [detachButton, addButton]) {
      expect(button.className).toMatch(/group-focus-within:opacity-100/);
      expect(button.className).toMatch(/focus-visible:opacity-100/);
    }
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

  it("shows a Share button for the notebook's owner", async () => {
    vi.mocked(notebooksApi.get).mockResolvedValue(makeNotebook({ created_by: "u-1" }));
    vi.mocked(notebooksApi.listDocuments).mockResolvedValue([]);
    vi.mocked(documentsApi.listDocuments).mockResolvedValue([]);

    renderPage();

    await waitFor(() => expect(screen.getByText("Share")).toBeInTheDocument());
  });

  it("hides Share, detach, and attach for a notebook shared with (not owned by) the user", async () => {
    vi.mocked(notebooksApi.get).mockResolvedValue(makeNotebook({ created_by: "someone-else" }));
    vi.mocked(notebooksApi.listDocuments).mockResolvedValue([
      makeDoc({ id: "doc-1", title: "report.pdf" }),
    ]);
    vi.mocked(documentsApi.listDocuments).mockResolvedValue([
      makeDoc({ id: "doc-1", title: "report.pdf" }),
      makeDoc({ id: "doc-2", title: "other.pdf", status: "READY" }),
    ]);

    renderPage();

    await waitFor(() => expect(screen.getByText("report.pdf")).toBeInTheDocument());

    expect(screen.getByText("Shared with you")).toBeInTheDocument();
    expect(screen.queryByText("Share")).not.toBeInTheDocument();
    expect(screen.queryByText("Rename")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Remove report.pdf from notebook")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Add other.pdf to notebook")).not.toBeInTheDocument();
  });

  it("defaults to the Chat tab and switches to Overview on click", async () => {
    vi.mocked(notebooksApi.get).mockResolvedValue(makeNotebook());
    vi.mocked(notebooksApi.listDocuments).mockResolvedValue([]);
    vi.mocked(documentsApi.listDocuments).mockResolvedValue([]);

    renderPage();

    await waitFor(() => expect(screen.getByText("Chemistry")).toBeInTheDocument());
    // Chat's own empty-state placeholder is visible by default.
    await waitFor(() =>
      expect(screen.getByText(/This notebook has no documents yet/)).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByText("Overview"));

    await waitFor(() => expect(screen.getByText(/No overview yet/)).toBeInTheDocument());
  });

  it("lets the owner rename the notebook via a dialog (PATCH name)", async () => {
    vi.mocked(notebooksApi.get).mockResolvedValue(makeNotebook({ name: "Chemistry" }));
    vi.mocked(notebooksApi.listDocuments).mockResolvedValue([]);
    vi.mocked(documentsApi.listDocuments).mockResolvedValue([]);
    vi.mocked(notebooksApi.update).mockResolvedValue(makeNotebook({ name: "Organic Chem" }));

    renderPage();
    await waitFor(() => expect(screen.getByText("Rename")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Rename"));

    const input = screen.getByLabelText("Notebook name");
    expect(input).toHaveValue("Chemistry");
    fireEvent.change(input, { target: { value: "  Organic Chem " } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(notebooksApi.update).toHaveBeenCalledWith("nb-1", { name: "Organic Chem" }),
    );
  });

  it("shows an access-denied state (not Loading/empty chat) for a 403 notebook", async () => {
    vi.mocked(notebooksApi.get).mockRejectedValue(
      new ApiError(403, "You don't have access to this notebook", null),
    );
    vi.mocked(notebooksApi.listDocuments).mockRejectedValue(new ApiError(403, "no", null));
    vi.mocked(documentsApi.listDocuments).mockResolvedValue([]);

    renderPage();

    await waitFor(() =>
      expect(screen.getByText("You don't have access to this notebook")).toBeInTheDocument(),
    );
    expect(screen.getByRole("link", { name: /Back to Notebooks/ })).toHaveAttribute(
      "href",
      "/app/notebooks",
    );
    expect(screen.queryByText("Chat")).not.toBeInTheDocument();
    expect(screen.queryByText(/Loading/)).not.toBeInTheDocument();
  });

  it("shows a not-found state for a 404 notebook", async () => {
    vi.mocked(notebooksApi.get).mockRejectedValue(new ApiError(404, "Notebook not found", null));
    vi.mocked(notebooksApi.listDocuments).mockRejectedValue(new ApiError(404, "no", null));
    vi.mocked(documentsApi.listDocuments).mockResolvedValue([]);

    renderPage("does-not-exist");

    await waitFor(() => expect(screen.getByText("Notebook not found")).toBeInTheDocument());
    expect(screen.queryByText("Chat")).not.toBeInTheDocument();
  });
});

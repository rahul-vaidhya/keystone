import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import type { Document } from "../types/documents";
import type { Notebook } from "../types/knowledge";
import type { ContextBlock, SparseSearchResponse } from "../types/retrieval";
import { notebooksApi } from "../services/notebooksService";
import { documentsApi } from "../services/documentsService";
import { retrievalApi } from "../services/retrievalService";
import { DialogProvider } from "../context/DialogContext";
import { SearchPage } from "./SearchPage";

vi.mock("../services/notebooksService", () => ({
  notebooksApi: {
    list: vi.fn(),
  },
}));

vi.mock("../services/documentsService", () => ({
  documentsApi: {
    listDocuments: vi.fn(),
  },
}));

vi.mock("../services/retrievalService", () => ({
  retrievalApi: {
    search: vi.fn(),
    sparseSearch: vi.fn(),
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
    uploader_email: null,
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function makeHit(overrides: Partial<ContextBlock> = {}): ContextBlock {
  return {
    index: 1,
    document_id: "doc-1",
    chunk_id: "chunk-1",
    char_start: 0,
    char_end: 20,
    content: "The octet rule states that atoms tend to...",
    distance: 0.32,
    ...overrides,
  };
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <DialogProvider>
        <MemoryRouter>
          <SearchPage />
        </MemoryRouter>
      </DialogProvider>
    </QueryClientProvider>,
  );
}

describe("SearchPage", () => {
  beforeEach(() => {
    vi.mocked(notebooksApi.list).mockReset();
    vi.mocked(documentsApi.listDocuments).mockReset();
    vi.mocked(retrievalApi.search).mockReset();
    vi.mocked(retrievalApi.sparseSearch).mockReset();
  });

  it("renders the notebook picker populated from notebooksApi.list", async () => {
    vi.mocked(notebooksApi.list).mockResolvedValue([
      makeNotebook({ id: "nb-1", name: "Chemistry" }),
      makeNotebook({ id: "nb-2", name: "Finance" }),
    ]);
    vi.mocked(documentsApi.listDocuments).mockResolvedValue([]);

    renderPage();

    await waitFor(() =>
      expect(screen.getByRole("option", { name: "Chemistry" })).toBeInTheDocument(),
    );
    expect(screen.getByRole("option", { name: "Finance" })).toBeInTheDocument();
  });

  it("submits a query with the selected notebook and renders the returned hits", async () => {
    vi.mocked(notebooksApi.list).mockResolvedValue([makeNotebook({ id: "nb-1", name: "Chemistry" })]);
    vi.mocked(documentsApi.listDocuments).mockResolvedValue([
      makeDoc({ id: "doc-1", title: "kech104.pdf" }),
    ]);
    vi.mocked(retrievalApi.search).mockResolvedValue({
      query: "octet rule",
      results: [makeHit({ document_id: "doc-1", content: "The octet rule states..." })],
    });

    renderPage();

    await waitFor(() =>
      expect(screen.getByRole("option", { name: "Chemistry" })).toBeInTheDocument(),
    );
    fireEvent.change(screen.getByLabelText("Notebook"), { target: { value: "nb-1" } });
    fireEvent.change(screen.getByPlaceholderText("Search this notebook's documents…"), {
      target: { value: "octet rule" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));

    await waitFor(() =>
      expect(retrievalApi.search).toHaveBeenCalledWith({
        notebook_id: "nb-1",
        query: "octet rule",
        k: 8,
      }),
    );
    await waitFor(() => expect(screen.getByText("kech104.pdf")).toBeInTheDocument());
    expect(screen.getByText("The octet rule states...")).toBeInTheDocument();
  });

  it("shows a 'create a notebook first' empty state when there are no notebooks", async () => {
    vi.mocked(notebooksApi.list).mockResolvedValue([]);
    vi.mocked(documentsApi.listDocuments).mockResolvedValue([]);

    renderPage();

    await waitFor(() =>
      expect(screen.getByText(/Create a notebook first to search its documents/)).toBeInTheDocument(),
    );
    expect(screen.queryByLabelText("Notebook")).not.toBeInTheDocument();
  });

  it("shows 'No matching passages found.' when a search returns zero results", async () => {
    vi.mocked(notebooksApi.list).mockResolvedValue([makeNotebook({ id: "nb-1", name: "Chemistry" })]);
    vi.mocked(documentsApi.listDocuments).mockResolvedValue([]);
    vi.mocked(retrievalApi.search).mockResolvedValue({ query: "nothing", results: [] });

    renderPage();

    await waitFor(() =>
      expect(screen.getByRole("option", { name: "Chemistry" })).toBeInTheDocument(),
    );
    fireEvent.change(screen.getByLabelText("Notebook"), { target: { value: "nb-1" } });
    fireEvent.change(screen.getByPlaceholderText("Search this notebook's documents…"), {
      target: { value: "nothing" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));

    await waitFor(() => expect(screen.getByText("No matching passages found.")).toBeInTheDocument());
  });

  it("opens the citation panel with the right excerpt when a hit is clicked", async () => {
    vi.mocked(notebooksApi.list).mockResolvedValue([makeNotebook({ id: "nb-1", name: "Chemistry" })]);
    vi.mocked(documentsApi.listDocuments).mockResolvedValue([
      makeDoc({ id: "doc-1", title: "kech104.pdf" }),
    ]);
    vi.mocked(retrievalApi.search).mockResolvedValue({
      query: "octet rule",
      results: [
        makeHit({
          document_id: "doc-1",
          chunk_id: "chunk-9",
          content: "Exact excerpt about the octet rule.",
        }),
      ],
    });

    renderPage();

    await waitFor(() =>
      expect(screen.getByRole("option", { name: "Chemistry" })).toBeInTheDocument(),
    );
    fireEvent.change(screen.getByLabelText("Notebook"), { target: { value: "nb-1" } });
    fireEvent.change(screen.getByPlaceholderText("Search this notebook's documents…"), {
      target: { value: "octet rule" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));

    await waitFor(() => expect(screen.getByText("kech104.pdf")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Exact excerpt about the octet rule."));

    await waitFor(() => expect(screen.getByText("Source [1]")).toBeInTheDocument());
    expect(screen.getAllByText("Exact excerpt about the octet rule.").length).toBeGreaterThan(0);
  });

  it("surfaces a retrievalApi.search rejection via the dialog", async () => {
    vi.mocked(notebooksApi.list).mockResolvedValue([makeNotebook({ id: "nb-1", name: "Chemistry" })]);
    vi.mocked(documentsApi.listDocuments).mockResolvedValue([]);
    vi.mocked(retrievalApi.search).mockRejectedValue(new Error("Search failed. Please try again."));

    renderPage();

    await waitFor(() =>
      expect(screen.getByRole("option", { name: "Chemistry" })).toBeInTheDocument(),
    );
    fireEvent.change(screen.getByLabelText("Notebook"), { target: { value: "nb-1" } });
    fireEvent.change(screen.getByPlaceholderText("Search this notebook's documents…"), {
      target: { value: "anything" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));

    await waitFor(() =>
      expect(screen.getByText("Search failed. Please try again.")).toBeInTheDocument(),
    );
  });

  it("runs a Boolean query against /retrieval/sparse-search and shows the merge trace", async () => {
    vi.mocked(notebooksApi.list).mockResolvedValue([makeNotebook({ id: "nb-1", name: "Chemistry" })]);
    vi.mocked(documentsApi.listDocuments).mockResolvedValue([
      makeDoc({ id: "doc-1", title: "kech104.pdf" }),
    ]);
    const response: SparseSearchResponse = {
      query: "hydrogen AND bond NOT covalent",
      mode: "boolean",
      analysis: {
        raw_tokens: ["hydrogen", "bond", "covalent"],
        casefolded: ["hydrogen", "bond", "covalent"],
        stop_words_removed: [],
        kept_tokens: ["hydrogen", "bond", "covalent"],
        stems: ["hydrogen", "bond", "coval"],
        terms: [],
        ranked: null,
        phrase: null,
        boolean: {
          operators: ["AND", "NOT"],
          clauses: [
            {
              operands: [],
              steps: [
                { op: "START", term: "hydrogen", df: 12, result_size: 12 },
                { op: "AND NOT", term: "coval", df: 20, result_size: 4 },
              ],
              result_size: 4,
            },
          ],
          union_steps: [{ op: "OR", term: "clause 1", df: 4, result_size: 4 }],
        },
      },
      index_stats: {
        n_docs: 110,
        vocabulary_size: 2000,
        avg_postings_length: 3,
        zones: ["body", "heading"],
        champion_r: 50,
        cached: false,
        build_ms: 40,
        query_ms: 0.5,
        total_ms: 60,
      },
      total_matches: 4,
      results: [
        {
          rank: 1,
          chunk_id: "chunk-7",
          document_id: "doc-1",
          document_title: "kech104.pdf",
          heading: "Hydrogen Bonding",
          content: "A hydrogen bond is weaker.",
          snippet: "A hydrogen bond is weaker.",
          char_start: 10,
          char_end: 36,
          page_start: 31,
          page_end: 31,
          score: null,
          contributions: [],
          matched_terms: ["hydrogen", "bond"],
          highlights: ["hydrogen", "bond"],
          phrase_matches: [],
        },
      ],
    };
    vi.mocked(retrievalApi.sparseSearch).mockResolvedValue(response);

    renderPage();

    await waitFor(() =>
      expect(screen.getByRole("option", { name: "Chemistry" })).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("tab", { name: "Boolean" }));
    expect(screen.getByRole("tab", { name: "Boolean" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText(/Postings are merged in increasing-df order/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Notebook"), { target: { value: "nb-1" } });
    fireEvent.change(screen.getByLabelText("Query"), {
      target: { value: "hydrogen AND bond NOT covalent" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));

    await waitFor(() =>
      expect(retrievalApi.sparseSearch).toHaveBeenCalledWith(
        expect.objectContaining({
          notebook_id: "nb-1",
          query: "hydrogen AND bond NOT covalent",
          mode: "boolean",
        }),
      ),
    );
    expect(retrievalApi.search).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.getByText("Query analysis")).toBeInTheDocument());
    expect(screen.getByText(/START/).closest("li")).toHaveTextContent("hydrogen (df 12) = 12");
    expect(screen.getByLabelText("Index statistics")).toHaveTextContent("N = 110 chunks");

    fireEvent.click(screen.getByRole("button", { name: "Open source #1" }));
    await waitFor(() => expect(screen.getByText("Source [1]")).toBeInTheDocument());
    expect(screen.getByText("Page 31")).toBeInTheDocument();
  });

  it("shows ranked options only on the Ranked tab and sends them", async () => {
    vi.mocked(notebooksApi.list).mockResolvedValue([makeNotebook({ id: "nb-1", name: "Chemistry" })]);
    vi.mocked(documentsApi.listDocuments).mockResolvedValue([]);
    vi.mocked(retrievalApi.sparseSearch).mockRejectedValue(new Error("x"));

    renderPage();

    await waitFor(() =>
      expect(screen.getByRole("option", { name: "Chemistry" })).toBeInTheDocument(),
    );
    expect(screen.queryByRole("radiogroup", { name: "Scoring scheme" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Ranked (tf-idf / BM25)" }));
    fireEvent.click(screen.getByRole("radio", { name: "tf-idf (lnc.ltc)" }));
    fireEvent.click(screen.getByLabelText("Champion lists"));
    fireEvent.change(screen.getByLabelText("Notebook"), { target: { value: "nb-1" } });
    fireEvent.change(screen.getByLabelText("Query"), { target: { value: "octet rule" } });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));

    await waitFor(() =>
      expect(retrievalApi.sparseSearch).toHaveBeenCalledWith({
        notebook_id: "nb-1",
        query: "octet rule",
        mode: "ranked",
        k: 10,
        scheme: "tfidf",
        use_champions: true,
        idf_threshold: 0,
        zone_weights: { heading: 2, body: 1 },
      }),
    );
  });
});

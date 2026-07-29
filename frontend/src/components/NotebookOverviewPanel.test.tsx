import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { NotebookOverviewPanel } from "./NotebookOverviewPanel";
import { notebooksApi } from "../services/notebooksService";
import { ApiError } from "../types/auth";
import type { Document } from "../types/documents";
import type { NotebookOverview } from "../types/knowledge";

vi.mock("../services/notebooksService", () => ({
  notebooksApi: {
    getOverview: vi.fn(),
    generateOverview: vi.fn(),
  },
}));

function makeOverview(overrides: Partial<NotebookOverview> = {}): NotebookOverview {
  return {
    id: "ov-1",
    notebook_id: "nb-1",
    content: "This notebook covers onboarding [1] and billing [2].",
    citations: [
      {
        marker: 1,
        document_id: "doc-1",
        chunk_id: null,
        char_start: null,
        char_end: null,
        content: "onboarding extract",
        page_start: null,
        page_end: null,
        citation_type: "section",
        section_id: "sec-1",
        heading: "Onboarding",
      },
      {
        marker: 2,
        document_id: "doc-1",
        chunk_id: null,
        char_start: null,
        char_end: null,
        content: "billing extract",
        page_start: null,
        page_end: null,
        citation_type: "section",
        section_id: "sec-2",
        heading: "Billing",
      },
    ],
    generated_at: "2026-01-01T00:00:00Z",
    generated_by: "u-1",
    source_document_count: 1,
    stale: false,
    ...overrides,
  };
}

const documents: Document[] = [
  {
    id: "doc-1",
    org_id: "org-1",
    folder_id: null,
    title: "report.pdf",
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
  },
];

describe("NotebookOverviewPanel", () => {
  beforeEach(() => {
    vi.mocked(notebooksApi.getOverview).mockReset();
    vi.mocked(notebooksApi.generateOverview).mockReset();
  });

  it("shows the empty state and a Generate button when no overview exists yet (404)", async () => {
    vi.mocked(notebooksApi.getOverview).mockRejectedValue(new ApiError(404, "not found", null));

    render(<NotebookOverviewPanel notebookId="nb-1" documents={documents} />);

    await waitFor(() => expect(screen.getByText(/No overview yet/)).toBeInTheDocument());
    expect(screen.getByText("Generate Overview")).toBeInTheDocument();
  });

  it("renders the cached overview's content and citation markers on load", async () => {
    vi.mocked(notebooksApi.getOverview).mockResolvedValue(makeOverview());

    render(<NotebookOverviewPanel notebookId="nb-1" documents={documents} />);

    await waitFor(() =>
      expect(screen.getByText(/This notebook covers onboarding/)).toBeInTheDocument(),
    );
    expect(screen.getByText("[1]")).toBeInTheDocument();
    expect(screen.getByText("[2]")).toBeInTheDocument();
    expect(screen.getByText("Regenerate")).toBeInTheDocument();
  });

  it("shows a stale banner when the cached overview is stale", async () => {
    vi.mocked(notebooksApi.getOverview).mockResolvedValue(makeOverview({ stale: true }));

    render(<NotebookOverviewPanel notebookId="nb-1" documents={documents} />);

    await waitFor(() => expect(screen.getByText(/may be out of date/)).toBeInTheDocument());
  });

  it("clicking a citation marker opens the source panel with its heading and content", async () => {
    vi.mocked(notebooksApi.getOverview).mockResolvedValue(makeOverview());

    render(<NotebookOverviewPanel notebookId="nb-1" documents={documents} />);

    await waitFor(() => expect(screen.getByText("[1]")).toBeInTheDocument());
    fireEvent.click(screen.getByText("[1]"));

    expect(screen.getByText("Source [1]")).toBeInTheDocument();
    expect(screen.getByText("Section: Onboarding")).toBeInTheDocument();
    expect(screen.getByText("onboarding extract")).toBeInTheDocument();
  });

  it("clicking Generate calls notebooksApi.generateOverview and renders the result", async () => {
    vi.mocked(notebooksApi.getOverview).mockRejectedValue(new ApiError(404, "not found", null));
    vi.mocked(notebooksApi.generateOverview).mockResolvedValue(makeOverview());

    render(<NotebookOverviewPanel notebookId="nb-1" documents={documents} />);

    await waitFor(() => expect(screen.getByText("Generate Overview")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Generate Overview"));

    await waitFor(() => expect(notebooksApi.generateOverview).toHaveBeenCalledWith("nb-1"));
    await waitFor(() =>
      expect(screen.getByText(/This notebook covers onboarding/)).toBeInTheDocument(),
    );
  });

  it("shows a clear error message when generation is refused (409) without crashing", async () => {
    vi.mocked(notebooksApi.getOverview).mockRejectedValue(new ApiError(404, "not found", null));
    vi.mocked(notebooksApi.generateOverview).mockRejectedValue(
      new ApiError(409, "Can't generate an overview yet — this notebook has no enriched sections.", null),
    );

    render(<NotebookOverviewPanel notebookId="nb-1" documents={documents} />);

    await waitFor(() => expect(screen.getByText("Generate Overview")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Generate Overview"));

    await waitFor(() =>
      expect(screen.getByText(/Can't generate an overview yet/)).toBeInTheDocument(),
    );
  });

  it("re-fetches the overview when the notebookId prop changes", async () => {
    vi.mocked(notebooksApi.getOverview).mockResolvedValue(makeOverview());

    const { rerender } = render(
      <NotebookOverviewPanel notebookId="nb-1" documents={documents} />,
    );
    await waitFor(() => expect(notebooksApi.getOverview).toHaveBeenCalledWith("nb-1"));

    rerender(<NotebookOverviewPanel notebookId="nb-2" documents={documents} />);
    await waitFor(() => expect(notebooksApi.getOverview).toHaveBeenCalledWith("nb-2"));
  });
});
